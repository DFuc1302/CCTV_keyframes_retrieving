"""Consumer safety boundaries without invoking writable Windows verification."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from pipeline.cctv.ingest import digest, save_json
from pipeline.cctv.manifest import load_manifest
from scripts.verify_second_machine_handover import _package_evidence, run_handover, snapshot, validate_reference


class SecondMachineSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.raw = self.root / 'raw'
        self.raw.mkdir()
        self.source = self.raw / 'one.mov'
        self.source.write_bytes(b'preserve-original-video')
        self.manifest = self.root / 'manifest.json'
        save_json(self.manifest, dict(schema_version=1, raw_root=str(self.raw),
                  cameras=[dict(camera_id='CAM')], videos=[dict(video_id='ONE', camera_id='CAM', path='one.mov')]))
        self.reference = self.root / 'inventory.json'
        self.row = dict(video_id='ONE', camera_id='CAM', bytes=self.source.stat().st_size,
                        sha256=digest(self.source), source_path='Z:/origin/irrelevant.mov', mtime_ns=1)
        save_json(self.reference, dict(videos=[self.row]))
        self.args = argparse.Namespace(manifest=self.manifest, reference_inventory=self.reference,
                                      origin_machine='origin', output=self.root / 'fresh', expected_commit=None,
                                      powershell_version='unused', powershell_executable='must-not-be-run')

    def test_portable_reference_ignores_origin_path_and_mtime(self):
        before = snapshot([self.source, self.manifest, self.reference])
        actual = validate_reference(load_manifest(self.manifest), self.reference)
        self.assertEqual(actual['ONE']['sha256'], self.row['sha256'])
        self.assertEqual(snapshot([self.source, self.manifest, self.reference]), before)

    def test_mismatches_refuse_execution_and_preserve_inputs(self):
        for change in (dict(camera_id='OTHER'), dict(bytes=1), dict(sha256='0' * 64), dict(video_id='OTHER')):
            with self.subTest(change=change):
                save_json(self.reference, dict(videos=[dict(self.row, **change)]))
                before = snapshot([self.source, self.manifest, self.reference])
                with self.assertRaises(ValueError):
                    run_handover(self.args)
                self.assertFalse(self.args.output.exists())
                self.assertEqual(snapshot([self.source, self.manifest, self.reference]), before)

    def test_raw_overlap_and_foreign_output_preserve_bytes(self):
        foreign = self.root / 'foreign'
        foreign.mkdir()
        sealed = foreign / 'sealed.json'
        sealed.write_bytes(b'keep sealed')
        before = snapshot([self.source, self.manifest, self.reference, sealed])
        for output in (self.raw, self.raw / 'child', self.root, foreign):
            with self.subTest(output=output):
                self.args.output = output
                with self.assertRaises(ValueError):
                    run_handover(self.args)
                self.assertEqual(snapshot([self.source, self.manifest, self.reference, sealed]), before)
        self.assertFalse((self.raw / 'child').exists())

    def test_duplicate_and_invalid_reference_metadata_are_rejected(self):
        for rows in ([self.row, self.row], [dict(self.row, bytes=True)], [dict(self.row, sha256='bad')]):
            save_json(self.reference, dict(videos=rows))
            before = snapshot([self.source, self.reference])
            with self.assertRaises(ValueError):
                validate_reference(load_manifest(self.manifest), self.reference)
            self.assertEqual(snapshot([self.source, self.reference]), before)

    def test_archive_checksums_address_actual_nested_zip_members(self):
        output = self.root / 'package'
        nested = output / 'runtime-evidence'
        nested.mkdir(parents=True)
        (nested / 'core-tests.txt').write_text('verified runtime\n', encoding='utf-8')
        (output / 'second-machine-report.json').write_text('{"quality_metrics":null}', encoding='utf-8')
        _package_evidence(output)
        archive = output / 'second-machine-evidence.zip'
        with zipfile.ZipFile(archive) as package:
            checks = json.loads(package.read('checksums.json'))
            self.assertEqual(set(checks), {'runtime-evidence/core-tests.txt', 'second-machine-report.json'})
            for member, expected in checks.items():
                self.assertEqual(hashlib.sha256(package.read(member)).hexdigest(), expected)
        seal = json.loads((output / 'archive-checksum.json').read_text())
        self.assertEqual(digest(archive), seal['sha256'])
