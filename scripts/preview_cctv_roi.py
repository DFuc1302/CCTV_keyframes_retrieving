"""Draw reviewable ROI overlays on committed tracking previews, without model loading."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import load_manifest
from pipeline.cctv.traffic import load_config


def prepare(manifest_path: Path, roi_path: Path, tracking: Path, output: Path) -> dict:
    """Resume identical rendered images; refuse changed or unowned output paths."""
    manifest = load_manifest(manifest_path, require_files=False)
    rois = load_config(roi_path, manifest)
    tracking, output = tracking.absolute(), output.absolute()
    reject_link(output)
    for protected in (
        manifest.raw_root,
        tracking,
        manifest_path.resolve().parent,
        roi_path.resolve().parent,
    ):
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError("Output overlaps protected input directory")
    tracking_state = json.loads((tracking / "track_state.json").read_text())
    if tracking_state["status"] != "complete":
        raise ValueError("Tracking is incomplete")
    signature = {
        "roi": digest(roi_path),
        "manifest": digest(manifest_path),
        "code": digest(Path(__file__)),
        "inputs": {},
    }
    selected = []
    for video in manifest.videos:
        roi = next(r for r in rois.cameras if r.camera_id == video.camera_id)
        if roi.polygon is None:
            raise ValueError(f"Missing ROI geometry: {video.camera_id}")
        candidates = sorted(
            n
            for n in tracking_state["publication"]
            if n.startswith(f"previews/{video.video_id}-")
        )
        if not candidates:
            continue
        name = candidates[0]
        source = tracking / name
        reject_link(source)
        if digest(source) != tracking_state["publication"][name]:
            raise ValueError("Tracking preview changed")
        signature["inputs"][name] = tracking_state["publication"][name]
        selected.append((video, roi, source))
    if not selected:
        raise ValueError("No committed previews")
    state_path = output / "roi_preview_state.json"
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Foreign output preserved")
    output.mkdir(parents=True, exist_ok=True)
    reject_link(state_path)
    reject_link(output / ".ingest.lock")
    with output_lease(output):
        state = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {"signature": signature, "artifacts": {}}
        )
        if state["signature"] != signature:
            raise ValueError("Preview recipe changed; use a NEW output directory")
        result = {"done": 0, "skipped": 0, "error": 0}
        for video, roi, source in tqdm(selected, desc="ROI previews", unit="image"):
            try:
                destination = output / f"{video.video_id}.png"
                reject_link(destination)
                if destination.exists():
                    if digest(destination) != state["artifacts"].get(destination.name):
                        raise ValueError("Changed/unowned preview preserved")
                    result["skipped"] += 1
                    continue
                with Image.open(source) as image:
                    rgb = image.convert("RGB")
                    points = [
                        (round(x * (rgb.width - 1)), round(y * (rgb.height - 1)))
                        for x, y in roi.polygon
                    ]
                    overlay = Image.new("RGBA", rgb.size)
                    draw = ImageDraw.Draw(overlay)
                    draw.polygon(
                        points,
                        fill=(255, 180, 0, 35),
                        outline=(255, 180, 0, 255),
                        width=5,
                    )
                    for index, point in enumerate(points):
                        draw.text(
                            point,
                            str(index),
                            fill="white",
                            stroke_width=2,
                            stroke_fill="black",
                        )
                    composed = Image.alpha_composite(
                        rgb.convert("RGBA"), overlay
                    ).convert("RGB")
                    banner = ImageDraw.Draw(composed)
                    banner.rectangle(
                        (0, rgb.height - 48, rgb.width, rgb.height), fill="black"
                    )
                    banner.text(
                        (15, rgb.height - 36),
                        f"{video.camera_id} / {video.video_id} | ROI: {roi.status.upper()} | DEMO GEOMETRY",
                        fill="orange",
                        font_size=24,
                    )
                    temporary = output / f".{video.video_id}.tmp"
                    reject_link(temporary)
                    composed.save(temporary, format="PNG")
                    checksum = hashlib.sha256(temporary.read_bytes()).hexdigest()
                    state["artifacts"][destination.name] = checksum
                    save_json(state_path, state)
                    temporary.replace(destination)
                result["done"] += 1
            except Exception as exc:
                result["error"] += 1
                tqdm.write(f"Preview error {video.video_id}: {exc}")
        return result


def main() -> int:
    """Create draft/approved previews in an isolated folder."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--roi-config", type=Path, required=True)
    parser.add_argument("--tracking-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare(
            args.manifest, args.roi_config, args.tracking_dir, args.output_dir
        )
        print(json.dumps(result, indent=2))
        return 1 if result["error"] else 0
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"ROI preview error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
