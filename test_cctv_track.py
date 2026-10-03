"""Check tracking segments, native timestamps and replay without real YOLO weights."""

from __future__ import annotations

import hashlib
import io
import math
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pipeline.cctv.ingest import digest, save_json
from pipeline.cctv.detect import canonical
from pipeline.cctv.stitch import load_mapping, stitch_tracks
from pipeline.cctv.track import (
    PIPELINE as TRACK_PIPELINE,
    DecodedFrame,
    TrackFrame,
    decoded_frames,
    publish as publish_tracking,
    track_videos,
)


class FakeTracker:
    """Stable one-object IDs with explicit segment resets; not a tracking benchmark."""

    loads = 0
    closes = 0

    def __init__(self, *args):
        FakeTracker.loads += 1
        self.resets = 0

    def reset(self):
        self.resets += 1

    def process(self, image):
        return [
            {
                "track_id": 1,
                "detection": {
                    "label": "car",
                    "confidence": 0.8,
                    "bbox": [0.1, 0.2, 0.6, 0.8],
                },
            }
        ]

    def memory(self):
        return {}

    def close(self):
        FakeTracker.closes += 1


def fake_reader(path, start, duration):
    """Six consecutive decoded ordinals with PTS, no inferred frame-rate timestamps."""
    for n in range(6):
        stamp = n * 0.4
        if stamp >= start and (duration is None or stamp < start + duration):
            yield DecodedFrame(
                n,
                stamp,
                100 + n * 400,
                100,
                (1, 1000),
                Image.new("RGB", (64, 48), (n, n, n)),
            )


class TrackingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.raw = self.root / "raw"
        self.raw.mkdir()
        (self.raw / "source.mov").write_bytes(b"synthetic read-only raw source")
        config = self.root / "configs"
        config.mkdir()
        self.manifest = config / "manifest.json"
        save_json(
            self.manifest,
            {
                "schema_version": 1,
                "raw_root": str(self.raw),
                "cameras": [{"camera_id": "N001"}],
                "videos": [
                    {"video_id": "N001-V001", "camera_id": "N001", "path": "source.mov"}
                ],
            },
        )
        self.traffic = config / "traffic.json"
        save_json(
            self.traffic,
            {
                "schema_version": 1,
                "cameras": [{"camera_id": "N001", "status": "pending"}],
            },
        )
        self.tracker = config / "tracker.yaml"
        self.tracker.write_bytes(
            (Path(__file__).parent / "configs/cctv.bytetrack.yaml").read_bytes()
        )
        cache = self.root / "cache"
        cache.mkdir()
        weights = cache / "fake.pt"
        weights.write_bytes(b"fake weights never loaded by torch")
        self.model = cache / "model.json"
        save_json(
            self.model,
            {
                "model": "fake",
                "revision": "test",
                "path": str(weights),
                "sha256": digest(weights),
            },
        )
        self.output = self.root / "tracks"
        FakeTracker.loads = FakeTracker.closes = 0

    def run_track(self, factory=FakeTracker, reader=fake_reader, **kwargs):
        return track_videos(
            self.manifest,
            self.traffic,
            self.model,
            self.tracker,
            self.output,
            duration=2.4,
            segment_seconds=1.0,
            device="cpu",
            backend_factory=factory,
            reader_factory=reader,
            **kwargs,
        )

    def test_complete_segment_scope_empty_frames_and_resume(self):
        class EmptyFrame(FakeTracker):
            def process(self, image):
                return [] if image.getpixel((0, 0))[0] == 1 else super().process(image)

        source = (
            digest(self.raw / "source.mov"),
            (self.raw / "source.mov").stat().st_mtime_ns,
        )
        result = self.run_track(EmptyFrame)
        self.assertEqual(
            (result["segments_done"], result["frames_done"], result["error"]), (3, 6, 0)
        )
        records = [
            TrackFrame.model_validate_json(line)
            for line in (self.output / "frames.jsonl").read_text().splitlines()
        ]
        self.assertEqual([r.locator.frame_idx for r in records], list(range(6)))
        self.assertEqual(records[1].observations, ())
        keys = {r.observations[0].track_key for r in records if r.observations}
        self.assertEqual(len(keys), 3)
        before = {
            p: (digest(p), p.stat().st_mtime_ns)
            for p in self.output.rglob("*")
            if p.is_file() and p.suffix != ".lock"
        }
        result = self.run_track(EmptyFrame)
        self.assertEqual((result["frames_skipped"], result["model_loaded"]), (6, False))
        self.assertEqual((FakeTracker.loads, FakeTracker.closes), (1, 1))
        for path, expected in before.items():
            self.assertEqual((digest(path), path.stat().st_mtime_ns), expected)
        self.assertEqual(
            (
                digest(self.raw / "source.mov"),
                (self.raw / "source.mov").stat().st_mtime_ns,
            ),
            source,
        )

    def test_interrupted_segment_replays_without_duplicating_committed_one(self):
        class Interrupted(FakeTracker):
            def process(self, image):
                if self.resets == 2:
                    raise KeyboardInterrupt()
                return super().process(image)

        with self.assertRaises(KeyboardInterrupt):
            self.run_track(Interrupted)
        with closing(sqlite3.connect(self.output / "tracks.db")) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM frames").fetchone()[0], 3
            )
        result = self.run_track()
        self.assertEqual((result["frames_done"], result["frames_skipped"]), (3, 3))
        self.assertEqual(FakeTracker.closes, 2)

    def test_frame_failure_discards_segment_and_continues_next_segment(self):
        class BadFrame(FakeTracker):
            def process(self, image):
                if image.getpixel((0, 0))[0] == 3:
                    raise RuntimeError("synthetic frame failure")
                return super().process(image)

        result = self.run_track(BadFrame)
        self.assertEqual((result["frames_done"], result["error"]), (4, 1))
        self.assertFalse((self.output / "tracks.jsonl").exists())
        result = self.run_track()
        self.assertEqual(
            (result["frames_done"], result["frames_skipped"], result["error"]),
            (2, 4, 0),
        )

    def test_video_ids_isolate_reused_track_ids(self):
        data = json.loads(self.manifest.read_text())
        (self.raw / "other.mov").write_bytes(b"another synthetic input")
        data["videos"].append(
            {"video_id": "N001-V002", "camera_id": "N001", "path": "other.mov"}
        )
        save_json(self.manifest, data)
        result = self.run_track()
        self.assertEqual(result["frames_done"], 12)
        rows = [
            json.loads(line)
            for line in (self.output / "tracks.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            len(
                {
                    (r["locator"]["video_id"], r["segment_id"], r["track_id"])
                    for r in rows
                }
            ),
            6,
        )
        self.assertEqual(FakeTracker.loads, 1)

    def test_corrupt_artifacts_and_recipe_changes_are_preserved(self):
        self.run_track()
        with self.assertRaisesRegex(ValueError, "NEW output"):
            self.run_track(start=0.1)
        target = self.output / "tracks.jsonl"
        target.write_text("damaged")
        with self.assertRaisesRegex(ValueError, "artifact changed"):
            self.run_track()
        self.assertEqual(target.read_text(), "damaged")

    def test_publication_interruption_resumes_without_model(self):
        def interrupt(path, value):
            if path.name == "track_state.json" and value.get("status") == "complete":
                raise KeyboardInterrupt()
            save_json(path, value)

        with patch("pipeline.cctv.track.save_json", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_track()
        result = self.run_track()
        self.assertEqual((result["frames_skipped"], result["model_loaded"]), (6, False))
        self.assertEqual(FakeTracker.loads, 1)

    def test_foreign_and_raw_output_are_not_touched(self):
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("original")
        with self.assertRaisesRegex(ValueError, "Foreign"):
            self.run_track()
        self.assertEqual(marker.read_text(), "original")
        self.output = self.raw / "derived"
        with self.assertRaisesRegex(ValueError, "overlaps protected"):
            self.run_track()
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg fixture generator required")
    def test_real_decoder_preserves_offset_vfr_pts_and_ordinals(self):
        path = self.raw / "vfr.mov"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=64x48:r=8:d=1.5",
                "-vf",
                r"select=not(eq(n\,2)),setpts=PTS+0.25/TB",
                "-fps_mode",
                "passthrough",
                "-c:v",
                "libx264",
                "-video_track_timescale",
                "1000",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        frames = list(decoded_frames(path, 0, None))
        try:
            self.assertEqual(len(frames), 11)
            self.assertEqual([f.frame_idx for f in frames], list(range(11)))
            self.assertAlmostEqual(frames[0].timestamp_sec, 0)
            self.assertAlmostEqual(frames[2].timestamp_sec, 0.375)
            self.assertGreater(frames[0].origin_pts, 0)
        finally:
            for frame in frames:
                frame.image.close()

    def test_frame_contract_rejects_fabricated_pts(self):
        self.run_track()
        first = json.loads((self.output / "frames.jsonl").read_text().splitlines()[0])
        first["source_pts"] += 100
        with self.assertRaisesRegex(ValueError, "source PTS"):
            TrackFrame.model_validate_json(json.dumps(first))

    def test_real_bytetrack_recovers_low_score_detection_without_new_id(self):
        """Exercise real CPU association, including the second low-confidence stage."""
        import numpy as np

        (self.root / "sdk-cache").mkdir()
        with patch.dict(
            "os.environ",
            {
                "YOLO_CONFIG_DIR": str(self.root / "sdk-cache"),
                "YOLO_AUTOINSTALL": "false",
            },
        ):
            from types import SimpleNamespace

            import yaml
            from ultralytics.engine.results import Boxes
            from ultralytics.trackers.byte_tracker import BYTETracker

            tracker = BYTETracker(
                SimpleNamespace(**yaml.safe_load(self.tracker.read_text()))
            )

            def boxes(x, score):
                return Boxes(
                    np.array([[x, 10, x + 20, 35, score, 2]], dtype=np.float32),
                    (48, 64),
                )

            first = tracker.update(boxes(10, 0.8))
            rescued = tracker.update(boxes(11, 0.2))
            self.assertEqual(len(rescued), 1)
            self.assertEqual(int(first[0][4]), int(rescued[0][4]))
            missing = tracker.update(
                Boxes(np.empty((0, 6), dtype=np.float32), (48, 64))
            )
            self.assertEqual(len(missing), 0)
            recovered = tracker.update(boxes(12, 0.8))
            self.assertEqual(int(first[0][4]), int(recovered[0][4]))

    def test_corrupt_partial_segment_blocks_resume_before_model_load(self):
        class Interrupted(FakeTracker):
            def process(self, image):
                if self.resets == 2:
                    raise KeyboardInterrupt()
                return super().process(image)

        with self.assertRaises(KeyboardInterrupt):
            self.run_track(Interrupted)
        with closing(sqlite3.connect(self.output / "tracks.db")) as conn:
            with conn:
                conn.execute("UPDATE frames SET payload='{}' WHERE frame_idx=0")
        with self.assertRaisesRegex(ValueError, "Corrupt segment"):
            self.run_track()
        self.assertEqual(FakeTracker.loads, 1)


class StitchingTests(unittest.TestCase):
    """Synthetic geometry is a hypothesis, never reidentification ground truth."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.raw = self.root / "source.mov"
        self.raw.write_bytes(b"immutable synthetic raw source")

    @staticmethod
    def vehicle(track_id=1, x=0.3, y=0.5, label="car", width=0.06, height=0.06):
        return (track_id, label, x, y, width, height)

    def publication(self, frames, name="tracking", tag="fixture"):
        """Use the real track publisher and its complete ledger/checkpoint format."""
        output = self.root / name
        output.mkdir()
        signature = {
            "pipeline": TRACK_PIPELINE,
            "start_sec": 0.0,
            "duration_sec": None,
            "segment_seconds": 10.0,
            "sample_interval_sec": 0.5,
            "sources": {"synthetic": digest(self.raw)},
            "recipe": {"synthetic_test": tag},
        }
        run = "track-" + hashlib.sha256(canonical(signature).encode()).hexdigest()[:16]
        state = {
            "signature": signature, "run_id": run, "status": "processing",
            "videos_done": sorted({r[0] for r in frames}), "roi_ready": False,
        }
        preview = io.BytesIO()
        with Image.new("RGB", (32, 32)) as image:
            image.save(preview, format="JPEG")
        preview_bytes = preview.getvalue()
        with closing(sqlite3.connect(output / "tracks.db")) as conn:
            conn.execute("CREATE TABLE metadata (signature TEXT NOT NULL)")
            conn.execute("INSERT INTO metadata VALUES (?)", (canonical(signature),))
            conn.execute("CREATE TABLE frames (video_id TEXT,frame_idx INTEGER,segment_id TEXT,timestamp_sec REAL,payload TEXT,PRIMARY KEY(video_id,frame_idx))")
            conn.execute("CREATE TABLE segments (video_id TEXT,segment_id TEXT,frames INTEGER,sha256 TEXT,preview BLOB,preview_sha256 TEXT,PRIMARY KEY(video_id,segment_id))")
            indices, segments = {}, {}
            for video, camera, stamp, vehicles in sorted(frames, key=lambda r: (r[0], r[2])):
                index = indices.get(video, 0)
                indices[video] = index + 1
                sid = f"seg-{int(stamp // 10):06d}"
                locator = {"camera_id": camera, "video_id": video, "timestamp_sec": stamp, "frame_idx": index}
                observations = [
                    {
                        "schema_version": 1, "kind": "track", "run_id": run,
                        "segment_id": sid, "track_id": tid, "locator": locator,
                        "detection": {"label": label, "confidence": 0.8, "bbox": [x - w / 2, y - h, x + w / 2, y]},
                    }
                    for tid, label, x, y, w, h in vehicles
                ]
                payload = canonical({
                    "schema_version": 1, "kind": "track_frame", "run_id": run,
                    "segment_id": sid, "locator": locator, "source_pts": 1000 + round(stamp * 1000),
                    "origin_pts": 1000, "time_base": [1, 1000], "width": 1000, "height": 1000,
                    "observations": observations,
                })
                TrackFrame.model_validate_json(payload)
                conn.execute("INSERT INTO frames VALUES (?,?,?,?,?)", (video, index, sid, stamp, payload))
                segments.setdefault((video, sid), []).append(payload)
            for (video, sid), payloads in segments.items():
                checksum = hashlib.sha256("".join(p + "\n" for p in payloads).encode()).hexdigest()
                conn.execute("INSERT INTO segments VALUES (?,?,?,?,?,?)", (
                    video, sid, len(payloads), checksum, preview_bytes,
                    hashlib.sha256(preview_bytes).hexdigest(),
                ))
            conn.commit()
            publish_tracking(conn, output, state)
        return output

    def boundary(self, outgoing, incoming, name="tracking"):
        frames = [("N001-V001", "N001", t, v) for t, v in (*outgoing, *incoming)]
        return self.publication(frames, name)

    @staticmethod
    def snapshot(folder):
        return {
            p.relative_to(folder).as_posix(): (digest(p), p.stat().st_mtime_ns)
            for p in folder.rglob("*") if p.is_file() and p.name != ".ingest.lock"
        }

    def test_three_segments_preserve_original_observations_and_resume(self):
        frames = [
            ("N001-V001", "N001", n * 0.5, [self.vehicle(11 + n // 20, 0.15 + n * 0.0075)])
            for n in range(60)
        ]
        tracking = self.publication(frames)
        original = self.snapshot(tracking)
        raw_before = (digest(self.raw), self.raw.stat().st_mtime_ns)
        output = self.root / "stitched"
        self.assertEqual(stitch_tracks(tracking, output), output)
        mapping = load_mapping(output, tracking)
        self.assertEqual(set(mapping.values()), {1})
        self.assertEqual({k[3] for k in mapping}, {"seg-000000", "seg-000001", "seg-000002"})
        original_rows = [json.loads(line) for line in (tracking / "tracks.jsonl").read_text().splitlines()]
        stitched_rows = [json.loads(line) for line in (output / "stitched_tracks.jsonl").read_text().splitlines()]
        for row in stitched_rows:
            self.assertEqual(row.pop("global_track_id"), 1)
        self.assertEqual(stitched_rows, original_rows)
        published = self.snapshot(output)
        stitch_tracks(tracking, output)
        self.assertEqual(self.snapshot(output), published)
        other = self.root / "repeat"
        stitch_tracks(tracking, other)
        for name in ("stitched_mapping.json", "stitched_tracks.jsonl"):
            self.assertEqual((output / name).read_bytes(), (other / name).read_bytes())
        self.assertEqual(self.snapshot(tracking), original)
        self.assertEqual((digest(self.raw), self.raw.stat().st_mtime_ns), raw_before)

    def test_distance_gap_direction_class_area_and_aspect_gates(self):
        outgoing = [(t, [self.vehicle(1, x)]) for t, x in ((9.0, 0.3), (9.5, 0.325), (9.75, 0.3375))]
        cases = {
            "distant": [(10.0, [self.vehicle(2, 0.8)]), (10.5, [self.vehicle(2, 0.825)])],
            "opposite_velocity": [(t, [self.vehicle(2, x)]) for t, x in ((10.0, 0.35), (10.25, 0.3375), (10.5, 0.325))],
            "long_gap": [(10.9, [self.vehicle(2, 0.395)])],
            "bicycle_truck": [(10.0, [self.vehicle(2, 0.35, label="bicycle")])],
            "area": [(10.0, [self.vehicle(2, 0.35, width=0.12)])],
            "aspect": [(10.0, [self.vehicle(2, 0.35, width=0.09, height=0.04)])],
        }
        for name, incoming in cases.items():
            with self.subTest(name=name):
                case_outgoing = outgoing
                if name == "bicycle_truck":
                    case_outgoing = [(t, [self.vehicle(1, x, label="truck")]) for t, x in ((9.0, 0.3), (9.5, 0.325), (9.75, 0.3375))]
                tracking = self.boundary(case_outgoing, incoming, name)
                output = self.root / ("stitched-" + name)
                stitch_tracks(tracking, output)
                self.assertEqual(len(set(load_mapping(output, tracking).values())), 2)
        tracking = self.boundary(outgoing, [(10.0, [self.vehicle(2, 0.35, label="truck")])], "compatible-truck")
        output = self.root / "stitched-compatible"
        stitch_tracks(tracking, output)
        self.assertEqual(len(set(load_mapping(output, tracking).values())), 1)

    def test_only_consecutive_segments_and_one_second_endpoints_link(self):
        cases = {
            "early_tail": ([(8.9, [self.vehicle()])], [(10.0, [self.vehicle(2)])]),
            "late_head": ([(9.75, [self.vehicle()])], [(11.1, [self.vehicle(2)])]),
            "skipped_segment": ([(9.75, [self.vehicle()])], [(20.0, [self.vehicle(2)])]),
        }
        for name, (outgoing, incoming) in cases.items():
            with self.subTest(name=name):
                tracking = self.boundary(outgoing, incoming, name)
                output = self.root / ("stitched-" + name)
                stitch_tracks(tracking, output, max_gap_sec=20)
                self.assertEqual(len(set(load_mapping(output, tracking).values())), 2)

    def test_actual_pts_prediction_uses_terminal_three_samples(self):
        # An early sample points in the opposite direction; only the terminal
        # three samples in the one-second tail predict x=0.70 at t=10.
        outgoing = [(t, [self.vehicle(1, x, width=0.01, height=0.01)]) for t, x in (
            (9.0, 0.9), (9.4, 0.4), (9.6, 0.5), (9.8, 0.6),
        )]
        incoming = [(10.0, [self.vehicle(2, 0.7, width=0.01, height=0.01)])]
        tracking = self.boundary(outgoing, incoming)
        output = self.root / "stitched"
        stitch_tracks(tracking, output, max_distance_diagonals=0.1)
        self.assertEqual(len(set(load_mapping(output, tracking).values())), 1)

    def test_invalid_assignment_edge_cannot_displace_two_valid_links(self):
        diagonal = math.hypot(0.06, 0.06)
        outgoing = [(t, [
            self.vehicle(1, 0.2, 0.4),
            self.vehicle(2, 0.2 + 2.4 * diagonal, 0.4),
        ]) for t in (9.25, 9.75)]
        incoming = [(t, [
            self.vehicle(11, 0.2 + 2.4 * diagonal, 0.4),
            self.vehicle(12, 0.2 + 2.55 * diagonal, 0.4 + 0.5 * diagonal),
        ]) for t in (10.0, 10.5)]
        # The ungated 1->12 edge is >2.5 diagonals and cheaper in total than
        # 1->11 plus 2->12. Gating before assignment retains both valid links.
        tracking = self.boundary(outgoing, incoming)
        output = self.root / "stitched"
        stitch_tracks(tracking, output)
        ids = {(key[3], key[4]): gid for key, gid in load_mapping(output, tracking).items()}
        self.assertEqual(ids[("seg-000000", 1)], ids[("seg-000001", 11)])
        self.assertEqual(ids[("seg-000000", 2)], ids[("seg-000001", 12)])
        self.assertNotEqual(ids[("seg-000000", 1)], ids[("seg-000000", 2)])

    def test_equal_optimal_assignments_remain_unlinked(self):
        # The two assignments have equal total residual without any local
        # row/column ties. Arbitrary Hungarian tie-breaking is not identity proof.
        outgoing = [(t, [self.vehicle(1, 0.2), self.vehicle(2, 0.23)]) for t in (9.25, 9.75)]
        incoming = [(t, [self.vehicle(11, 0.30), self.vehicle(12, 0.33)]) for t in (10.0, 10.5)]
        tracking = self.boundary(outgoing, incoming)
        output = self.root / "stitched"
        stitch_tracks(tracking, output)
        self.assertEqual(len(set(load_mapping(output, tracking).values())), 4)

    def test_video_camera_and_run_isolation(self):
        frames = [
            (video, camera, t, [self.vehicle(1 if t < 10 else 2)])
            for video, camera in (("N001-V001", "N001"), ("N001-V002", "N001"), ("N002-V001", "N002"))
            for t in (9.25, 9.75, 10.0, 10.5)
        ]
        tracking = self.publication(frames)
        output = self.root / "stitched"
        stitch_tracks(tracking, output)
        mapping = load_mapping(output, tracking)
        by_video = {}
        for key, gid in mapping.items():
            by_video.setdefault(key[2], set()).add(gid)
        self.assertTrue(all(len(ids) == 1 for ids in by_video.values()))
        self.assertEqual(len(set(mapping.values())), 3)
        other_run = self.publication(frames, "other-run", tag="different-run")
        with self.assertRaises(ValueError):
            load_mapping(output, other_run)

    def test_incomplete_and_tampered_input_is_preserved(self):
        for name in ("tracks.db", "frames.jsonl", "tracks.jsonl", "tracking_manifest.json", "previews/N001-V001-seg-000000.jpg", "track_state.json"):
            with self.subTest(name=name):
                tracking = self.publication([("N001-V001", "N001", 9.5, [self.vehicle()])], "input-" + name.replace("/", "-"))
                target = tracking / name
                if name == "track_state.json":
                    state = json.loads(target.read_text())
                    state["status"] = "processing"
                    save_json(target, state)
                else:
                    target.write_bytes(target.read_bytes() + b"edited")
                before = self.snapshot(tracking)
                output = self.root / ("output-" + name.replace("/", "-"))
                with self.assertRaises(ValueError):
                    stitch_tracks(tracking, output)
                self.assertEqual(self.snapshot(tracking), before)
                self.assertFalse(output.exists())

    def test_tampered_outputs_and_changed_settings_are_preserved(self):
        tracking = self.boundary([(9.75, [self.vehicle()])], [(10.0, [self.vehicle(2)])])
        for name in ("stitched_tracks.jsonl", "stitched_mapping.json", "stitch_state.json"):
            with self.subTest(name=name):
                output = self.root / ("output-" + name)
                stitch_tracks(tracking, output)
                target = output / name
                target.write_bytes(target.read_bytes() + b"edited")
                before = self.snapshot(output)
                with self.assertRaises(ValueError):
                    load_mapping(output, tracking)
                with self.assertRaises(ValueError):
                    stitch_tracks(tracking, output)
                self.assertEqual(self.snapshot(output), before)
        output = self.root / "settings"
        stitch_tracks(tracking, output)
        before = self.snapshot(output)
        with self.assertRaisesRegex(ValueError, "NEW output"):
            stitch_tracks(tracking, output, max_gap_sec=0.5)
        self.assertEqual(self.snapshot(output), before)

    def test_loader_binds_all_five_local_scope_fields_even_after_rehashing(self):
        tracking = self.boundary([(9.75, [self.vehicle()])], [(10.0, [self.vehicle(2)])])
        replacement = {"run_id": "track-other", "camera_id": "N999", "video_id": "N999-V999", "segment_id": "seg-999999", "track_id": 999}
        for field, value in replacement.items():
            with self.subTest(field=field):
                output = self.root / ("scope-" + field)
                stitch_tracks(tracking, output)
                document_path = output / "stitched_mapping.json"
                document = json.loads(document_path.read_text())
                document["mapping"][0][field] = value
                save_json(document_path, document)
                state_path = output / "stitch_state.json"
                state = json.loads(state_path.read_text())
                state["artifacts"]["stitched_mapping.json"] = digest(document_path)
                state["seal_sha256"] = hashlib.sha256(canonical({k: v for k, v in state.items() if k != "seal_sha256"}).encode()).hexdigest()
                save_json(state_path, state)
                with self.assertRaises(ValueError):
                    load_mapping(output, tracking)

    def test_interrupted_partial_publication_resumes_without_overwriting(self):
        tracking = self.boundary([(9.75, [self.vehicle()])], [(10.0, [self.vehicle(2)])])
        output = self.root / "stitched"
        original_replace = Path.replace

        def interrupt(path, destination):
            if path.name == "stitched_tracks.jsonl":
                raise KeyboardInterrupt()
            return original_replace(path, destination)

        with patch("pipeline.cctv.stitch.Path.replace", new=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                stitch_tracks(tracking, output)
        installed = output / "stitched_mapping.json"
        before = (digest(installed), installed.stat().st_mtime_ns)
        with self.assertRaises(ValueError):
            load_mapping(output, tracking)
        stitch_tracks(tracking, output)
        self.assertEqual((digest(installed), installed.stat().st_mtime_ns), before)
        self.assertEqual(len(set(load_mapping(output, tracking).values())), 1)

    def test_foreign_and_protected_outputs_are_not_touched(self):
        tracking = self.publication([("N001-V001", "N001", 9.5, [self.vehicle()])])
        output = self.root / "foreign"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("user data")
        before = self.snapshot(output)
        with self.assertRaisesRegex(ValueError, "Foreign"):
            stitch_tracks(tracking, output)
        self.assertEqual(self.snapshot(output), before)
        for protected in (tracking, tracking / "derived", tracking.parent):
            with self.subTest(protected=str(protected)):
                before = self.snapshot(tracking)
                with self.assertRaisesRegex(ValueError, "protected"):
                    stitch_tracks(tracking, protected)
                self.assertEqual(self.snapshot(tracking), before)

    def test_linked_output_and_input_are_rejected_without_mutation(self):
        tracking = self.publication([("N001-V001", "N001", 9.5, [self.vehicle()])])
        link = self.root / "linked"
        try:
            link.symlink_to(tracking, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not permit directory symlinks")
        before = self.snapshot(tracking)
        with self.assertRaisesRegex(ValueError, "link"):
            stitch_tracks(tracking, link)
        with self.assertRaisesRegex(ValueError, "link"):
            stitch_tracks(link, self.root / "stitched")
        self.assertTrue(link.is_symlink())
        self.assertEqual(self.snapshot(tracking), before)

    def test_boundary_tracklet_memory_ceiling_fails_without_publication(self):
        outgoing = [
            (9.0, [self.vehicle(i) for i in range(300)]),
            (9.5, [self.vehicle(i) for i in range(300, 513)]),
        ]
        tracking = self.boundary(outgoing, [(10.0, [])])
        before = self.snapshot(tracking)
        output = self.root / "stitched"
        with self.assertRaisesRegex(ValueError, "memory bound"):
            stitch_tracks(tracking, output)
        self.assertFalse((output / "stitched_mapping.json").exists())
        self.assertFalse((output / "stitched_tracks.jsonl").exists())
        with self.assertRaises(ValueError):
            load_mapping(output, tracking)
        self.assertEqual(self.snapshot(tracking), before)

    def test_empty_observation_frames_publish_a_complete_empty_mapping(self):
        tracking = self.publication([
            ("N001-V001", "N001", stamp, []) for stamp in (0.0, 9.5, 10.0, 19.5, 20.0)
        ])
        output = self.root / "stitched"
        stitch_tracks(tracking, output)
        self.assertEqual(load_mapping(output, tracking), {})
        self.assertEqual((output / "stitched_tracks.jsonl").read_bytes(), b"")

    def test_invalid_gates_reject_before_creating_outputs(self):
        tracking = self.publication([("N001-V001", "N001", 9.5, [self.vehicle()])])
        for field in ("max_gap_sec", "max_distance_diagonals"):
            for n, value in enumerate((0, -1, float("nan"), float("inf"), True)):
                with self.subTest(field=field, value=value):
                    output = self.root / f"invalid-{field}-{n}"
                    with self.assertRaises(ValueError):
                        stitch_tracks(tracking, output, **{field: value})
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
