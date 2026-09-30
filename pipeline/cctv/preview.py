"""Create resumable H.264 MP4 previews without modifying indexed assets or raw MOV."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path

from tqdm import tqdm

from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import identity
from pipeline.cctv.search import CctvSearch

LOG = logging.getLogger("cctv.preview")


def probe(path: Path) -> dict:
    """Read duration, codec and first presentation timestamp through ffprobe."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-read_intervals",
            "%+#16",
            "-show_entries",
            "stream=codec_name:format=duration:frame=best_effort_timestamp_time",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    timestamps = [
        float(f["best_effort_timestamp_time"])
        for f in payload.get("frames", [])
        if "best_effort_timestamp_time" in f
    ]
    if not timestamps:
        raise ValueError(f"No decoded video frames: {path}")
    return {
        "codec": payload["streams"][0]["codec_name"],
        "duration": float(payload["format"]["duration"]),
        "first_pts": min(timestamps),
    }


def prepare_previews(index_dir: Path, output: Path) -> dict[str, int]:
    """Remux sequentially, checkpoint hash/PTS, and refuse foreign or changed output."""
    search = CctvSearch(index_dir)
    with closing(search.connect()) as conn:
        sources = conn.execute(
            "SELECT DISTINCT video_id,source_video FROM cctv_frames ORDER BY video_id"
        ).fetchall()
    output = output.absolute()
    reject_link(output)
    for protected in (
        search.root,
        Path(search.manifest["source"]),
        *(Path(p).resolve().parent for _, p in sources),
    ):
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError(
                "Preview output must not overlap index, extraction or raw directories"
            )
    state_path = output / "preview_state.json"
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Use an empty preview directory; foreign files are preserved")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg and ffprobe required")
    output.mkdir(parents=True, exist_ok=True)
    for path in (state_path, output / ".ingest.lock", output / "preview.log"):
        reject_link(path)
    counts = {"done": 0, "skipped": 0, "error": 0}
    with output_lease(output):
        state = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.exists()
            else {"pipeline": "cctv-preview-v1", "videos": {}}
        )
        if state.get("pipeline") != "cctv-preview-v1" or not isinstance(
            state.get("videos"), dict
        ):
            raise ValueError("Invalid preview checkpoint")
        save_json(state_path, state)
        handler = logging.FileHandler(output / "preview.log", encoding="utf-8")
        LOG.addHandler(handler)
        LOG.setLevel(logging.INFO)
        try:
            for vid, raw_path in tqdm(sources, desc="MP4 previews", unit="video"):
                try:
                    identity(vid, "video_id")
                    source = Path(raw_path).resolve()
                    target = output / f"{vid}.mp4"
                    reject_link(target)
                    checksum = digest(source)
                    previous = state["videos"].get(vid)
                    signature = {
                        "source": str(source),
                        "sha256": checksum,
                        "recipe": "h264-copy-start-zero-v1",
                    }
                    if previous and previous.get("signature") != signature:
                        raise ValueError(
                            "Source changed; use a NEW preview output directory"
                        )
                    if previous and previous.get("status") == "complete":
                        if digest(target) != previous["output_sha256"]:
                            raise ValueError(
                                "Preview changed; preserved for investigation"
                            )
                        counts["skipped"] += 1
                        continue
                    if (
                        previous
                        and previous.get("status") == "publishing"
                        and target.exists()
                    ):
                        entry = previous["publish_entry"]
                        if digest(target) != entry["output_sha256"]:
                            raise ValueError(
                                "Interrupted preview publication differs from checkpoint"
                            )
                        state["videos"][vid] = entry
                        save_json(state_path, state)
                        counts["skipped"] += 1
                        continue
                    if target.exists():
                        raise ValueError(
                            "Unpublished preview exists; preserved, use a new directory"
                        )
                    state["videos"][vid] = {
                        "signature": signature,
                        "status": "processing",
                    }
                    save_json(state_path, state)
                    original = probe(source)
                    if original["codec"] != "h264":
                        raise ValueError(
                            "MP4 preview currently supports H.264 input only"
                        )
                    with tempfile.TemporaryDirectory(
                        prefix=".preview-", dir=output
                    ) as temporary:
                        staged = Path(temporary) / "preview.mp4"
                        result = subprocess.run(
                            [
                                "ffmpeg",
                                "-nostdin",
                                "-v",
                                "error",
                                "-copyts",
                                "-start_at_zero",
                                "-i",
                                str(source),
                                "-map",
                                "0:v:0",
                                "-an",
                                "-sn",
                                "-dn",
                                "-c:v",
                                "copy",
                                "-avoid_negative_ts",
                                "disabled",
                                "-movflags",
                                "+faststart",
                                str(staged),
                            ],
                            capture_output=True,
                            text=True,
                        )
                        if result.returncode:
                            raise RuntimeError(result.stderr[-2000:])
                        generated = probe(staged)
                        if (
                            generated["codec"] != "h264"
                            or abs(generated["duration"] - original["duration"]) > 0.5
                        ):
                            raise ValueError("Preview codec/duration mismatch")
                        if digest(source) != checksum:
                            raise ValueError("Source changed during remux")
                        entry = {
                            "signature": signature,
                            "status": "complete",
                            "file": target.name,
                            "output_sha256": digest(staged),
                            "duration_sec": generated["duration"],
                            "seek_offset_sec": generated["first_pts"],
                        }
                        # Publish checkpoint before rename while lock is held. A missing file on
                        # interruption is retried through the processing checkpoint below.
                        state["videos"][vid].update(
                            status="publishing", publish_entry=entry
                        )
                        save_json(state_path, state)
                        staged.replace(target)
                        state["videos"][vid] = entry
                        save_json(state_path, state)
                    counts["done"] += 1
                    LOG.info("done | %s", vid)
                except Exception as exc:
                    counts["error"] += 1
                    LOG.exception("video_error | %s", vid)
                    tqdm.write(f"ERROR {vid}: {exc}")
            return counts
        finally:
            LOG.removeHandler(handler)
            handler.close()


def main() -> int:
    """CLI preview preparation, separate from HTTP startup/model inference."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        counts = prepare_previews(args.index_dir, args.output_dir)
        print(json.dumps(counts, indent=2))
        return 1 if counts["error"] else 0
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted; rerun to resume.\n")
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Preview error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
