"""Package real Windows technical verification; never certify physical hardware."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import platform
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import zipfile

from pipeline.cctv.events import protect_output, read_json
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]


def snapshot(paths: list[Path]) -> dict:
    result = {}
    for path in paths:
        reject_link(path)
        before = path.stat()
        sha = digest(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"Input changed while hashing: {path}")
        result[str(path)] = dict(bytes=after.st_size, mtime_ns=after.st_mtime_ns, sha256=sha)
    return result


def code_snapshot() -> dict:
    paths = [p for p in ROOT.iterdir() if p.is_file() and p.suffix in {'.py', '.ps1', '.toml', '.ini', '.txt', '.json', '.yaml', '.yml'}]
    for folder in ('app', 'pipeline', 'scripts', 'configs', 'requirements', 'static/cctv'):
        directory = ROOT / folder
        reject_link(directory)
        for p in directory.rglob('*'):
            if '__pycache__' not in p.parts:
                reject_link(p)
                if p.is_file():
                    paths.append(p)
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)}


def validate_reference(manifest, reference: Path) -> dict:
    reject_link(reference)
    data = read_json(reference)
    rows = data.get('videos')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Reference inventory must contain a nonempty videos list')
    expected = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid reference video row')
        vid, camera, size, sha = (row.get(k) for k in ('video_id', 'camera_id', 'bytes', 'sha256'))
        if (not isinstance(vid, str) or not vid or vid in expected
                or not isinstance(camera, str) or not camera
                or type(size) is not int or size <= 0
                or not isinstance(sha, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', sha)):
            raise ValueError('Invalid or duplicate reference video metadata')
        expected[vid] = dict(camera_id=camera, bytes=size, sha256=sha.lower())
    actual = {}
    for video in manifest.videos:
        facts = snapshot([video.source])[str(video.source)]
        actual[video.video_id] = dict(camera_id=video.camera_id, bytes=facts['bytes'], sha256=facts['sha256'])
    if actual != expected:
        raise ValueError('Reference inventory does not match local video IDs/cameras/bytes/SHA256')
    return actual


def command_facts(command: list[str]) -> dict:
    try:
        run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, errors='replace', timeout=60)
        return dict(exit_code=run.returncode, stdout=run.stdout.strip(), stderr=run.stderr.strip())
    except (OSError, subprocess.SubprocessError) as exc:
        return dict(exit_code=None, error=str(exc))


def _package_evidence(output: Path) -> None:
    files = sorted(p for p in output.rglob('*') if p.is_file() and p.name != '.ingest.lock')
    save_json(output / 'checksums.json', {p.relative_to(output).as_posix(): digest(p) for p in files})
    archive = output / 'second-machine-evidence.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as package:
        for path in [*files, output / 'checksums.json']:
            package.write(path, path.relative_to(output).as_posix())
    save_json(output / 'archive-checksum.json', dict(file=archive.name, sha256=digest(archive)))


def run_handover(args) -> tuple[dict, Path]:
    if not args.origin_machine.strip():
        raise ValueError('OriginMachine cannot be blank')
    manifest = load_manifest(args.manifest)
    reference = args.reference_inventory.absolute()
    protected = [manifest.raw_root, manifest.path, reference]
    protected += [ROOT / name for name in ('app', 'pipeline', 'scripts', 'configs', 'requirements', 'static', '.git', '.venv')]
    protected += [p for p in ROOT.iterdir() if p.is_file()]
    output = args.output or ROOT / 'outputs' / ('second-machine-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output = protect_output(output, protected + [ROOT / 'outputs' / 'tests', ROOT / 'outputs' / 'preflight'])
    if output.exists():
        raise ValueError('Output must be a fresh, nonexistent directory; partial and foreign outputs are refused')
    # Existing runtime scripts write here. Reject links and input overlaps before invoking them.
    test_root = ROOT / 'outputs' / 'tests'
    writable = [
        test_root / name for name in (
            'data', 'keyframes', 'maps', 'videos', 'model-cache',
            'ultralytics', 'matplotlib', 'telemetry', 'core-tests.txt',
        )
    ] + [ROOT / 'outputs' / 'preflight' / 'runtime-checks.json']
    for path in writable:
        protect_output(path, protected)
        reject_link(path)
        entries = path.rglob('*') if path.is_dir() else ()
        for entry in entries:
            reject_link(entry)
            if entry.name.endswith('_state.json') or entry.name in {'inventory.json', 'annotations.jsonl'}:
                raise ValueError(f'Refusing runtime destination containing sealed/review artifacts: {entry}')
    sources = [manifest.path, reference, *[v.source for v in manifest.videos]]
    # Mismatch exits without creating any output or running writable probes.
    validated = validate_reference(manifest, reference)
    before = snapshot(sources)
    code_before = code_snapshot()
    git = command_facts(['git', 'rev-parse', 'HEAD'])
    head = git.get('stdout') if git.get('exit_code') == 0 else None
    if args.expected_commit and (not re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', args.expected_commit) or head != args.expected_commit.lower()):
        raise ValueError('ExpectedCommit must equal the full local git HEAD')
    host = socket.gethostname()
    differs = host.casefold() != args.origin_machine.strip().casefold()
    report = dict(schema_version=1, demo_only=True, operational_ready=False,
                  quality_metrics=None, M8='PENDING', physical_host_verified=False,
                  hostname=host, origin_machine=args.origin_machine, host_identity_differs=differs,
                  second_machine_status='PENDING_HUMAN_VERIFICATION' if differs else 'NOT_RUN',
                  execution_scope='host-name-different; physical independence unverified' if differs else 'local rehearsal',
                  started_at=datetime.now(timezone.utc).isoformat(), technical_outcome='FAILED', failures=[],
                  expected_commit=args.expected_commit, git_head=head,
                  code_identity='working-copy file hashes; git HEAD is not a claim of a clean checkout',
                  source_verification=validated)
    output.mkdir(parents=True, exist_ok=False)
    with output_lease(output):
        save_json(output / 'source-before.json', before)
        save_json(output / 'code-before.json', code_before)
        save_json(output / 'reference-inventory.json', read_json(reference))
        environment = dict(hostname=host, python=sys.version, python_executable=sys.executable,
                           os=platform.platform(), machine=platform.machine(), powershell=args.powershell_version,
                           packages=sorted([dict(name=d.metadata['Name'], version=d.version) for d in importlib.metadata.distributions()], key=lambda d: (d['name'] or '').lower()),
                           gpu=command_facts(['nvidia-smi', '--query-gpu=name,driver_version,memory.total', '--format=csv']),
                           git=git, git_working_copy=command_facts(['git', 'status', '--porcelain']))
        save_json(output / 'environment.json', environment)
        runtime_start = datetime.now(timezone.utc).timestamp()
        script = str(ROOT / 'scripts' / 'Test-Windows.ps1').replace("'", "''")
        runtime_command = (
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
            "$OutputEncoding = [Console]::OutputEncoding; "
            f"& '{script}'; if (-not $?) {{ exit 1 }}"
        )
        try:
            with (output / 'verification.log').open('w', encoding='utf-8') as log:
                run = subprocess.run([args.powershell_executable, '-NoProfile', '-Command', runtime_command], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            report['runtime_exit_code'] = run.returncode
            if run.returncode:
                report['failures'].append(f'Test-Windows.ps1 exited {run.returncode}')
        except (OSError, subprocess.SubprocessError) as exc:
            report['failures'].append(f'Runtime invocation failed: {exc}')
        # Only current-run evidence is copied; stale prior passing logs are not evidence.
        evidence = output / 'runtime-evidence'
        evidence.mkdir()
        for path in (ROOT / 'outputs' / 'tests' / 'core-tests.txt', ROOT / 'outputs' / 'preflight' / 'runtime-checks.json'):
            reject_link(path)
            if path.exists() and path.stat().st_mtime >= runtime_start:
                shutil.copyfile(path, evidence / path.name)
        try:
            after = snapshot(sources)
            code_after = code_snapshot()
            save_json(output / 'source-after.json', after)
            save_json(output / 'code-after.json', code_after)
            report['source_unchanged'] = before == after
            report['code_unchanged'] = code_before == code_after
            report['git_head_after'] = command_facts(['git', 'rev-parse', 'HEAD'])
            if before != after or code_before != code_after or report['git_head_after'] != git:
                report['failures'].append('Source metadata/hash or working-copy code/commit changed during verification')
        except (OSError, ValueError) as exc:
            report['failures'].append(f'Post-verification integrity failed: {exc}')
        if not report['failures']:
            report['technical_outcome'] = 'PASSED_TECHNICAL_VERIFICATION'
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        save_json(output / 'second-machine-report.json', report)
        _package_evidence(output)
    return report, output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-inventory', type=Path, required=True)
    parser.add_argument('--origin-machine', required=True)
    parser.add_argument('--manifest', type=Path, default=Path('configs/cctv.demo.json'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--expected-commit')
    parser.add_argument('--powershell-version', required=True)
    parser.add_argument('--powershell-executable', required=True)
    args = parser.parse_args()
    try:
        report, output = run_handover(args)
        print(json.dumps(dict(output=str(output), **report), ensure_ascii=False, indent=2))
        return 0 if not report['failures'] else 1
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Second-machine verification failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
