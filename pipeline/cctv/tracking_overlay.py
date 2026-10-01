"""CPU H264 tracking previews from immutable ledgers and exact native source PTS."""

from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
from contextlib import closing
from fractions import Fraction
from pathlib import Path

import av
from PIL import Image, ImageDraw
from PIL import __version__ as pillow_version
from tqdm import tqdm

from pipeline.cctv import track
from pipeline.cctv.events import protect_output, read_json
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import load_manifest
from pipeline.cctv.track import TrackFrame, decoded_frames, sampled_frames

LOG = logging.getLogger(__name__)


def draw_overlay(image: Image.Image, record: TrackFrame, width: int) -> Image.Image:
    """Draw stored predictions and a scope banner; never infer ROI approval."""
    target_width = min(width, image.width) // 2 * 2
    target_height = max(2, round(image.height * target_width / image.width) // 2 * 2)
    result = image.resize((target_width, target_height))
    draw = ImageDraw.Draw(result)
    for observation in record.observations:
        color = (
            64 + observation.track_id * 67 % 192,
            64 + observation.track_id * 101 % 192,
            64 + observation.track_id * 37 % 192,
        )
        box = observation.detection.bbox
        xy = (
            box[0] * (target_width - 1),
            box[1] * (target_height - 1),
            box[2] * (target_width - 1),
            box[3] * (target_height - 1),
        )
        draw.rectangle(xy, outline=color, width=2)
        draw.text(
            (xy[0], max(0, xy[1] - 12)),
            f"ID {observation.track_id} {observation.detection.label}",
            fill=color,
            stroke_width=1,
            stroke_fill="black",
        )
    draw.rectangle((0, 0, target_width, 37), fill="black")
    draw.text(
        (6, 3),
        f"{record.locator.camera_id}/{record.locator.video_id} {record.segment_id} "
        f"source PTS {record.locator.timestamp_sec:.3f}s",
        fill="white",
    )
    draw.text(
        (6, 20),
        "PREDICTIONS / ROI PENDING / IDs scoped by run-video-segment",
        fill="orange",
    )
    return result


def render(
    conn: sqlite3.Connection,
    source: Path,
    video_id: str,
    camera_id: str,
    run_id: str,
    start: float,
    duration: float,
    width: int,
    target: Path,
    sample_interval: float = 0.0,
) -> dict:
    """Keep one decoded frame and one ledger row; abort an invalid clip, not skip it."""
    count = conn.execute(
        "SELECT COUNT(*) FROM frames WHERE video_id=?", (video_id,)
    ).fetchone()[0]
    if count < 2:
        raise ValueError("Overlay requires at least two stored frames")
    records = iter(
        conn.execute(
            "SELECT frame_idx,payload FROM frames WHERE video_id=? ORDER BY frame_idx",
            (video_id,),
        )
    )
    first_pts, first_stamp, base, stream, previous, written = (
        None,
        None,
        None,
        None,
        None,
        0,
    )
    with (
        closing(decoded_frames(source, start, duration)) as native,
        av.open(str(target), mode="w", format="mp4") as output,
    ):
        for decoded in tqdm(
            sampled_frames(native, start, sample_interval),
            total=count,
            desc=video_id,
            unit="frame",
        ):
            try:
                row = next(records, None)
                if row is None:
                    raise ValueError(
                        "Decoded source has more frames than sealed tracking"
                    )
                frame = TrackFrame.model_validate_json(row[1])
                loc = frame.locator
                if (
                    loc.video_id != video_id
                    or loc.camera_id != camera_id
                    or frame.run_id != run_id
                    or row[0] != decoded.frame_idx
                    or loc.frame_idx != decoded.frame_idx
                    or frame.source_pts != decoded.source_pts
                    or frame.origin_pts != decoded.origin_pts
                    or frame.time_base != decoded.time_base
                    or (frame.width, frame.height) != decoded.image.size
                    or not math.isclose(
                        loc.timestamp_sec,
                        decoded.timestamp_sec,
                        rel_tol=0,
                        abs_tol=1e-9,
                    )
                ):
                    raise ValueError("Decoded source/ledger locator mismatch")
                if not sample_interval and previous is not None and decoded.frame_idx != previous + 1:
                    raise ValueError("Tracking frames are not consecutive")
                previous = decoded.frame_idx
                with draw_overlay(decoded.image, frame, width) as annotated:
                    if stream is None:
                        first_pts, first_stamp = (
                            decoded.source_pts,
                            decoded.timestamp_sec,
                        )
                        base = Fraction(*decoded.time_base)
                        stream = output.add_stream("libx264", rate=2 if sample_interval else 25)
                        stream.width, stream.height = annotated.size
                        stream.pix_fmt = "yuv420p"
                        stream.time_base = base
                        stream.codec_context.time_base = base
                        stream.codec_context.thread_count = 2
                        stream.options = {"preset": "veryfast", "crf": "23"}
                    if Fraction(*decoded.time_base) != base:
                        raise ValueError("Source timebase changed")
                    encoded = av.VideoFrame.from_image(annotated)
                    encoded.pts, encoded.time_base = (
                        decoded.source_pts - first_pts,
                        base,
                    )
                    for packet in stream.encode(encoded):
                        output.mux(packet)
                written += 1
            except Exception:
                LOG.exception(
                    "Overlay frame failed: %s frame %s", video_id, decoded.frame_idx
                )
                raise
            finally:
                decoded.image.close()
        if stream is None or next(records, None) is not None or written != count:
            raise ValueError("Decoded source has fewer frames than sealed tracking")
        for packet in stream.encode():
            output.mux(packet)
    # Decode the output to verify that the encoded presentation timeline survived muxing.
    expected = conn.execute(
        "SELECT payload FROM frames WHERE video_id=? ORDER BY frame_idx", (video_id,)
    )
    verified = 0
    with av.open(str(target)) as output:
        for decoded in output.decode(video=0):
            row = next(expected, None)
            if row is None or decoded.pts is None:
                raise ValueError("Overlay output frame count/PTS mismatch")
            record = TrackFrame.model_validate_json(row[0])
            stamp = float(decoded.pts * decoded.time_base)
            if not math.isclose(
                stamp,
                record.locator.timestamp_sec - first_stamp,
                rel_tol=0,
                abs_tol=1e-6,
            ):
                raise ValueError("Overlay output PTS differs from source timeline")
            verified += 1
    if verified != written or next(expected, None) is not None:
        raise ValueError("Overlay output verification incomplete")
    return dict(
        video_id=video_id,
        camera_id=camera_id,
        frames=written,
        source_start_sec=first_stamp,
        playback_to_source_offset_sec=first_stamp,
        pts_verified=True,
        roi_approved=False,
        operational_ready=False,
    )


def run(manifest_path: Path, tracking: Path, output: Path, width: int = 960) -> dict:
    """Render bounded clips with per-video checkpoints; preserve all source packages."""
    if not 320 <= width <= 1920 or width % 2:
        raise ValueError("Width must be even and between 320 and 1920")
    manifest = load_manifest(manifest_path)
    tracking = tracking.absolute()
    reject_link(tracking)
    state = read_json(tracking / "track_state.json")
    publication = read_json(tracking / "tracking_manifest.json")
    if (
        state["status"] != "complete"
        or state["signature"]["pipeline"] != "cctv-track-v1"
    ):
        raise ValueError("Tracking publication is incomplete")
    if (
        publication["signature"] != state["signature"]
        or publication["run_id"] != state["run_id"]
    ):
        raise ValueError("Tracking manifest/checkpoint mismatch")
    signature = state["signature"]
    if signature["manifest_sha256"] != digest(manifest.path):
        raise ValueError("Tracking belongs to another source manifest")
    duration, start = signature["duration_sec"], signature["start_sec"]
    if (
        (duration is not None and (not math.isfinite(duration) or duration <= 0))
        or not math.isfinite(start)
        or start < 0
    ):
        raise ValueError("Invalid tracking start/duration")
    videos = {v.video_id: v for v in manifest.videos}
    selected = publication["videos"]
    if (
        not selected
        or len(selected) > 24
        or len(set(selected)) != len(selected)
        or set(selected) != set(signature["sources"])
    ):
        raise ValueError("Invalid tracking video coverage")
    inputs = [
        manifest.path,
        tracking / "track_state.json",
        Path(__file__).resolve(),
        Path(track.__file__).resolve(),
    ]
    for name in ("tracks.db", "frames.jsonl", "tracking_manifest.json"):
        path = tracking / name
        reject_link(path)
        if digest(path) != state["publication"][name]:
            raise ValueError("Tracking artifact changed: " + name)
        inputs.append(path)
    for video_id in selected:
        if (
            video_id not in videos
            or digest(videos[video_id].source) != signature["sources"][video_id]
        ):
            raise ValueError("Raw video checksum mismatch")
        inputs.append(videos[video_id].source)
    before = {str(p): (digest(p), p.stat().st_mtime_ns) for p in inputs}
    recipe = dict(
        pipeline="cctv-tracking-overlay-v1",
        width=width,
        av=av.__version__,
        pillow=pillow_version,
        inputs={p: sha for p, (sha, _) in before.items()},
    )
    output = protect_output(
        output,
        [manifest.raw_root, manifest.path.parent, tracking, Path(__file__).parent],
    )
    checkpoint = output / "overlay_state.json"
    if output.exists() and not checkpoint.exists() and any(output.iterdir()):
        raise ValueError("Foreign output preserved")
    output.mkdir(parents=True, exist_ok=True)
    reject_link(checkpoint)
    with (
        output_lease(output),
        closing(
            sqlite3.connect(f"{(tracking / 'tracks.db').as_uri()}?mode=ro", uri=True)
        ) as conn,
    ):
        saved = (
            read_json(checkpoint)
            if checkpoint.exists()
            else dict(signature=recipe, videos={}, status="incomplete")
        )
        if saved["signature"] != recipe:
            raise ValueError("Inputs/recipe changed; choose a NEW output")
        if not set(saved["videos"]).issubset(selected):
            raise ValueError("Unexpected overlay checkpoint videos")
        completed, skipped, errors = [], 0, []
        for video_id in selected:
            target = output / f"{video_id}.mp4"
            reject_link(target)
            if video_id in saved["videos"]:
                entry = saved["videos"][video_id]
                if digest(target) != entry["sha256"]:
                    raise ValueError("Edited overlay preserved: " + video_id)
                skipped += 1
                completed.append(entry)
                continue
            if target.exists():
                raise ValueError("Unowned overlay preserved: " + video_id)
            temporary = output / f".{video_id}.partial.mp4"
            reject_link(temporary)
            if temporary.exists():
                raise ValueError("Partial output preserved; choose a NEW output")
            try:
                video = videos[video_id]
                entry = render(
                    conn,
                    video.source,
                    video_id,
                    video.camera_id,
                    state["run_id"],
                    start,
                    duration,
                    width,
                    temporary,
                    sample_interval=signature.get("sample_interval_sec", 0),
                )
                if before != {
                    str(p): (digest(p), p.stat().st_mtime_ns) for p in inputs
                }:
                    raise ValueError("Overlay inputs changed during rendering")
                temporary.replace(target)
                entry["sha256"] = digest(target)
                saved["videos"][video_id] = entry
                save_json(checkpoint, saved)
                completed.append(entry)
            except (
                OSError,
                ValueError,
                RuntimeError,
                sqlite3.Error,
                av.FFmpegError,
            ) as exc:
                LOG.exception("Overlay video failed: %s", video_id)
                errors.append(dict(video_id=video_id, error=str(exc)))
        if errors:
            saved.update(status="incomplete", errors=errors)
            save_json(checkpoint, saved)
            raise ValueError("Overlay clips failed; see checkpoint/log")
        if before != {str(p): (digest(p), p.stat().st_mtime_ns) for p in inputs}:
            raise ValueError("Overlay inputs changed")
        if saved["status"] != "complete":
            saved.update(status="complete", errors=[])
            save_json(checkpoint, saved)
    return dict(
        videos=completed,
        skipped=skipped,
        operational_ready=False,
        id_scope="run_id/camera_id/video_id/segment_id/track_id",
        gpu_inference=False,
    )


def main() -> int:
    """Export local previews without rewriting tracking, ROI, raw footage or CLIP."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "tracking-dir", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--width", type=int, default=960)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        print(
            json.dumps(
                run(args.manifest, args.tracking_dir, args.output, args.width), indent=2
            )
        )
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        sqlite3.Error,
        av.FFmpegError,
    ) as exc:
        parser.exit(1, f"Tracking overlay failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
