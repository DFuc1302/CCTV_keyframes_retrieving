"""Full-video sparse tracking, PTS overlay and recipe safety without GPU weights."""
import json
import unittest
from unittest.mock import patch

import av
from PIL import Image

import test_cctv_track as fixtures
from pipeline.cctv.track import DecodedFrame, track_videos
from pipeline.cctv import tracking_overlay


def reader(path, start, duration):
    for n in range(640):
        stamp = n / 10
        if stamp >= start and (duration is None or stamp < start + duration):
            yield DecodedFrame(n, stamp, n, 0, (1, 10), Image.new("RGB", (64, 48)))


class FullVideoTests(unittest.TestCase):
    setUp = fixtures.TrackingTests.setUp

    def run_track(self, reader=reader, **kwargs):
        return track_videos(self.manifest, self.traffic, self.model, self.tracker,
                            self.output, segment_seconds=5, device="cpu",
                            backend_factory=fixtures.FakeTracker, reader_factory=reader, **kwargs)

    def test_full_sampled_overlay_and_resume(self):
        result = self.run_track(reader=reader, duration=None, sample_interval=0.5)
        self.assertEqual(result["frames_done"], 128)
        output = self.root / "overlay"
        with patch.object(tracking_overlay, "decoded_frames", side_effect=reader):
            tracking_overlay.run(self.manifest, self.output, output)
        with av.open(str(output / "N001-V001.mp4")) as video:
            stamps = [float(f.pts*f.time_base) for f in video.decode(video=0)]
        self.assertEqual(len(stamps), 128)
        self.assertAlmostEqual(stamps[-1], 63.5)
        self.assertEqual(self.run_track(reader=reader, duration=None, sample_interval=0.5)["frames_done"], 0)
        with self.assertRaises(ValueError):
            self.run_track(reader=reader, duration=None, sample_interval=0)

    def test_range_sampling_preserves_source_indices(self):
        self.run_track(reader=reader, start=30, duration=10, sample_interval=0.5)
        rows = [json.loads(line) for line in (self.output / "frames.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[0]["locator"]["frame_idx"], 300)
        self.assertEqual(rows[-1]["locator"]["timestamp_sec"], 39.5)


if __name__ == "__main__":
    unittest.main()
