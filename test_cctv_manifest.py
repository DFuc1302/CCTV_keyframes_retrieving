"""Validate CCTV manifest identities, paths and timezones without a dataset."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from pipeline.cctv.manifest import ManifestError, load_manifest


class CCTVManifestTests(unittest.TestCase):
    """Exercise stable identity, Windows paths and timestamps without media."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.raw = self.root / "inputs"
        self.raw.mkdir()
        (self.raw / "video dấu cách.avi").write_bytes(b"synthetic fixture")
        self.path = self.root / "manifest.json"
        self.payload = {
            "schema_version": 1, "raw_root": "inputs",
            "cameras": [{"camera_id": "CAM_01", "roi": [[0, 0], [1, 0], [1, 1]]}],
            "videos": [{"video_id": "TRAFFIC_001", "camera_id": "CAM_01", "path": "video dấu cách.avi",
                        "start_time": "2026-09-30T12:00:00+07:00"}],
        }

    def write(self) -> None:
        self.path.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")

    def test_arbitrary_unicode_filename_keeps_stable_video_id(self) -> None:
        self.write()
        manifest = load_manifest(self.path)
        self.assertEqual(manifest.videos[0].video_id, "TRAFFIC_001")
        self.assertEqual(manifest.videos[0].source, self.raw / "video dấu cách.avi")

    def test_case_insensitive_identity_collision(self) -> None:
        self.payload["videos"].append({"video_id": "traffic_001", "camera_id": "CAM_01", "path": "another.avi"})
        self.write()
        with self.assertRaisesRegex(ManifestError, "duplicate video_id"):
            load_manifest(self.path)

    def test_duplicate_source_and_unknown_camera_rejected(self) -> None:
        self.payload["videos"].append({"video_id": "TRAFFIC_002", "camera_id": "CAM_01", "path": "video dấu cách.avi"})
        self.write()
        with self.assertRaisesRegex(ManifestError, "assigned twice"):
            load_manifest(self.path)
        self.payload["videos"] = [{"video_id": "TRAFFIC_001", "camera_id": "MISSING", "path": "video dấu cách.avi"}]
        self.write()
        with self.assertRaisesRegex(ManifestError, "unknown camera"):
            load_manifest(self.path)

    def test_windows_unsafe_paths_and_reserved_id(self) -> None:
        for unsafe in ("../outside.avi", r"..\outside.avi", r"C:\outside.avi", "clip.avi:secret", "folder//clip.avi"):
            with self.subTest(path=unsafe):
                self.payload["videos"][0]["path"] = unsafe
                self.write()
                with self.assertRaises(ManifestError):
                    load_manifest(self.path)
        self.payload["videos"][0].update(path="video dấu cách.avi", video_id="CON")
        self.write()
        with self.assertRaisesRegex(ManifestError, "reserved"):
            load_manifest(self.path)

    def test_timezone_and_roi_validation(self) -> None:
        self.payload["videos"][0]["start_time"] = "2026-09-30T12:00:00"
        self.write()
        with self.assertRaisesRegex(ManifestError, "timezone"):
            load_manifest(self.path)
        self.payload["videos"][0].pop("start_time")
        for roi in ([[0, 0], [1.2, 0], [1, 1]], [[0, 0], [0.5, 0.5], [1, 1]]):
            self.payload["cameras"][0]["roi"] = roi
            self.write()
            with self.assertRaises(ManifestError):
                load_manifest(self.path)

    def test_structure_only_template_does_not_require_data(self) -> None:
        self.payload.update(raw_root="not-created", videos=[])
        self.write()
        self.assertEqual(load_manifest(self.path, require_files=False).videos, ())
        with self.assertRaisesRegex(ManifestError, "raw_root"):
            load_manifest(self.path)


if __name__ == "__main__":
    unittest.main()
