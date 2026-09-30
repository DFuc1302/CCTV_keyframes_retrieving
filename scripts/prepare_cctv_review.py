"""Prepare clean reference frames and pending review forms without assigning labels."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from contextlib import closing
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from tqdm import tqdm

from pipeline.cctv.benchmark_events import AnnotationSet, SplitManifest
from pipeline.cctv.events import protect_output, read_json
from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json
from pipeline.cctv.manifest import load_manifest
from pipeline.cctv.search import CctvSearch
from pipeline.cctv.traffic import load_config


def prepare(
    manifest_path: Path,
    annotations: Path,
    splits: Path,
    rois: Path,
    index: Path,
    previews: Path,
    output: Path,
) -> dict:
    """Publish a resumable preparation bundle; pending is never a negative label."""
    manifest = load_manifest(manifest_path)
    labels = AnnotationSet.model_validate_json(
        annotations.read_text(encoding="utf-8-sig")
    )
    grouping = SplitManifest.model_validate_json(splits.read_text(encoding="utf-8-sig"))
    roi_config = load_config(rois, manifest)
    labels.bind(manifest)
    grouping.bind(manifest)
    if (
        labels.mode != "independent"
        or not 1 <= len(labels.intervals) <= 24
        or any(r.review_status != "pending" for r in labels.intervals)
        or grouping.mode != "independent"
        or any(
            r.grouping_status != "unverified" or r.split != "unassigned"
            for r in grouping.videos
        )
        or any(r.status != "pending" or r.polygon is None for r in roi_config.cameras)
    ):
        raise ValueError(
            "Preparation requires pending labels/ROI and unverified, unassigned groups"
        )
    index, previews = index.absolute(), previews.absolute()
    search = CctvSearch(index)
    frames_root = Path(search.manifest["keyframes_root"]).resolve()
    preview_state = read_json(previews / "preview_state.json")
    if preview_state["pipeline"] != "cctv-preview-v1":
        raise ValueError("Unsupported preview recipe")
    files = [
        manifest.path,
        annotations.absolute(),
        splits.absolute(),
        rois.absolute(),
        Path(__file__).resolve(),
        previews / "preview_state.json",
        index / "index_state.json",
        *[index / n for n in read_json(index / "index_state.json")["artifacts"]],
    ]
    sources = {v.video_id: v for v in manifest.videos}
    jobs = []
    with closing(search.connect()) as conn:
        for i, interval in enumerate(labels.intervals):
            source = sources[interval.video_id]
            entry = preview_state["videos"][source.video_id]
            video = previews / f"{source.video_id}.mp4"
            if (
                entry["status"] != "complete"
                or entry["file"] != video.name
                or Path(entry["signature"]["source"]).resolve()
                != source.source.resolve()
                or digest(source.source) != entry["signature"]["sha256"]
                or digest(video) != entry["output_sha256"]
                or interval.end_sec + entry["seek_offset_sec"] > entry["duration_sec"]
            ):
                raise ValueError("Source/preview changed or interval outside video")
            files.extend((source.source, video))
            rows = conn.execute(
                "SELECT f.file,c.timestamp_sec,c.frame_idx FROM frames f JOIN cctv_frames c ON f.id=c.id "
                "WHERE c.video_id=? AND c.camera_id=? AND c.timestamp_sec>=? AND c.timestamp_sec<? ORDER BY c.timestamp_sec,f.file",
                (
                    source.video_id,
                    source.camera_id,
                    interval.start_sec,
                    interval.end_sec,
                ),
            ).fetchall()
            if len(rows) < 2:
                raise ValueError(
                    "Each interval needs at least two clean indexed reference frames"
                )
            n = min(6, len(rows))
            selected = [rows[round(k * (len(rows) - 1) / (n - 1))] for k in range(n)]
            for name, _, _ in selected:
                target = (frames_root / name).absolute()
                reject_link(target)
                if not target.is_relative_to(frames_root):
                    raise ValueError("Keyframe path escapes registered root")
                files.append(target)
            jobs.append(
                (
                    f"{source.video_id}-{i:03d}",
                    interval,
                    selected,
                    entry["seek_offset_sec"],
                )
            )
    output = protect_output(
        output,
        [
            manifest.raw_root,
            frames_root,
            index,
            previews,
            manifest.path.parent,
            annotations.absolute().parent,
            splits.absolute().parent,
            rois.absolute().parent,
            Path(__file__).parent,
        ],
    )
    files = sorted(set(files))
    for path in files:
        reject_link(path)
    before = {str(p): (digest(p), p.stat().st_mtime_ns) for p in files}
    signature = dict(
        pipeline="cctv-review-preparation-v1",
        inputs={p: sha for p, (sha, _) in before.items()},
        selection="up-to-six-evenly-spaced-indexed-frames-within-pending-half-open-interval",
    )
    state_path = output / "review_state.json"
    if output.exists() and not state_path.exists() and any(output.iterdir()):
        raise ValueError("Foreign output preserved; choose a new directory")
    output.mkdir(parents=True, exist_ok=True)
    reject_link(state_path)
    with output_lease(output):
        state = (
            read_json(state_path)
            if state_path.exists()
            else dict(signature=signature, status="processing", artifacts={}, jobs={})
        )
        if state["signature"] != signature:
            raise ValueError("Inputs/recipe changed; choose a NEW output directory")
        for name, sha in state["artifacts"].items():
            target = output / name
            reject_link(target)
            if target.exists() and digest(target) != sha:
                raise ValueError("Edited bundle artifact preserved: " + name)
        if state["status"] == "complete":
            if any(not (output / name).is_file() for name in state["artifacts"]):
                raise ValueError("Complete bundle artifact missing")
            report = read_json(output / "review_manifest.json")
            return dict(
                passed=True,
                intervals=len(report["intervals"]),
                samples=sum(len(r["samples"]) for r in report["intervals"]),
                skipped=len(report["intervals"]),
                preparation_only=True,
                operational_ready=False,
                protected_files=len(files),
                reviewed_intervals=0,
                approved_cameras=0,
            )

        def publish(name: str, data: bytes) -> None:
            target = output / name
            reject_link(target)
            sha = hashlib.sha256(data).hexdigest()
            if target.exists():
                if state["artifacts"].get(name) != sha or digest(target) != sha:
                    raise ValueError("Changed/unowned output preserved: " + name)
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".tmp")
            reject_link(temporary)
            temporary.write_bytes(data)
            state["artifacts"][name] = sha
            save_json(state_path, state)
            temporary.replace(target)

        records, errors, skipped = [], [], 0
        for job, interval, selected, offset in tqdm(
            jobs, desc="Prepare review intervals", unit="interval"
        ):
            try:
                samples = []
                sheet = Image.new("RGB", (960, 3 * 302), "white")
                for k, (name, pts, frame_idx) in enumerate(selected):
                    source = frames_root / name
                    try:
                        payload = source.read_bytes()
                        if (
                            hashlib.sha256(payload).hexdigest()
                            != before[str(source)][0]
                        ):
                            raise ValueError("Reference image changed")
                        with Image.open(BytesIO(payload)) as opened:
                            image = opened.convert("RGB")
                        leaf = f"labeling/frames/{job}/{k:02d}.jpg"
                        publish(leaf, payload)
                        thumb = image.copy()
                        thumb.thumbnail((480, 270))
                        x, y = (k % 2) * 480, (k // 2) * 302
                        sheet.paste(thumb, (x, y))
                        ImageDraw.Draw(sheet).text(
                            (x + 8, y + 276),
                            f"PTS {pts:.3f}s | source frame {frame_idx}",
                            fill="black",
                        )
                        samples.append(
                            dict(path=leaf, timestamp_sec=pts, frame_idx=frame_idx)
                        )
                    except Exception as exc:
                        tqdm.write(f"Reference error {job}/{k}: {exc}")
                        raise ValueError("Incomplete reference interval") from exc
                encoded = BytesIO()
                sheet.save(encoded, format="PNG")
                sheet_name = f"labeling/sheets/{job}.png"
                publish(sheet_name, encoded.getvalue())
                # ROI drafts are deliberately absent from the labeling page.
                roi = next(
                    r for r in roi_config.cameras if r.camera_id == interval.camera_id
                )
                with Image.open(
                    frames_root / selected[len(selected) // 2][0]
                ) as opened:
                    overlay = opened.convert("RGB")
                points = [
                    (round(x * (overlay.width - 1)), round(y * (overlay.height - 1)))
                    for x, y in roi.polygon
                ]
                draw = ImageDraw.Draw(overlay)
                draw.line(points + [points[0]], fill="orange", width=5)
                for vertex, point in enumerate(points):
                    draw.text(
                        point,
                        str(vertex),
                        fill="white",
                        stroke_width=2,
                        stroke_fill="black",
                    )
                draw.rectangle((0, 0, overlay.width, 32), fill="black")
                draw.text(
                    (8, 8), f"{interval.camera_id} | ROI PENDING - DRAFT", fill="orange"
                )
                encoded = BytesIO()
                overlay.save(encoded, format="PNG")
                roi_name = f"roi/{job}.png"
                publish(roi_name, encoded.getvalue())
                records.append(
                    dict(
                        job_id=job,
                        camera_id=interval.camera_id,
                        video_id=interval.video_id,
                        start_sec=interval.start_sec,
                        end_sec=interval.end_sec,
                        seek_sec=interval.start_sec + offset,
                        sheet=sheet_name,
                        roi_image=roi_name,
                        samples=samples,
                    )
                )
                skipped += int(state["jobs"].get(job) == "complete")
                state["jobs"][job] = "complete"
                save_json(state_path, state)
            except Exception as exc:
                errors.append(dict(job_id=job, error=str(exc)))
                tqdm.write(f"Interval error {job}: {exc}")
        if before != {str(p): (digest(p), p.stat().st_mtime_ns) for p in files}:
            raise ValueError("Protected inputs changed during preparation")
        if errors:
            state.update(status="processing", errors=errors)
            save_json(state_path, state)
            return dict(
                passed=False, errors=errors, skipped=skipped, operational_ready=False
            )
        publish("forms/annotations.pending.json", annotations.read_bytes())
        publish("forms/splits.pending.json", splits.read_bytes())
        publish("forms/roi.pending.json", rois.read_bytes())
        publish("labeling/index.html", page(records, roi=False).encode("utf-8"))
        publish("roi/index.html", page(records, roi=True).encode("utf-8"))
        report = dict(
            schema_version=1,
            signature=signature,
            preparation_only=True,
            operational_ready=False,
            reviewed_intervals=0,
            approved_cameras=0,
            split_ready=False,
            intervals=records,
            caveat="Existing six demo intervals are not a representative test set. Clean frames do not establish blind independent review.",
        )
        publish(
            "review_manifest.json",
            (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        state.update(status="complete", errors=[])
        save_json(state_path, state)
        return dict(
            passed=True,
            intervals=len(records),
            samples=sum(len(r["samples"]) for r in records),
            skipped=skipped,
            preparation_only=True,
            operational_ready=False,
            protected_files=len(files),
            reviewed_intervals=0,
            approved_cameras=0,
        )


def page(records: list[dict], *, roi: bool) -> str:
    """Render an escaped local reference page, keeping labeling free of ROI hints."""
    title = "ROI nháp — chưa duyệt" if roi else "Footage sạch — chưa gán nhãn"
    cards = []
    for row in records:
        image = "../" + (row["roi_image"] if roi else row["sheet"])
        video = html.escape(row["video_id"], quote=True)
        cards.append(
            f'<article><h2>{html.escape(row["camera_id"])} / {video}</h2><p>Source PTS [{row["start_sec"]}, {row["end_sec"]}) giây</p><img src="{html.escape(image, quote=True)}" alt="{title}"><video controls preload="none" src="http://127.0.0.1:8080/cctv/video/{video}" data-seek="{row["seek_sec"]}"></video><button type="button">Mở video tại đầu khoảng</button></article>'
        )
    return (
        '<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'
        + title
        + "</title><style>body{font-family:system-ui;margin:24px auto;max-width:1000px;padding:16px;background:#f5f7f8}article{background:white;padding:16px;margin:24px 0}img,video{display:block;max-width:100%;margin:12px 0}video{width:100%}button{padding:12px}</style><h1>"
        + title
        + "</h1><p>Chuẩn bị review, chưa có ground truth. Xem toàn bộ khoảng video; ảnh tham chiếu không đủ để kết luận sự kiện. Video cần demo tại localhost:8080. Không dùng review ứng viên làm nhãn độc lập.</p>"
        + "".join(cards)
        + '<script>document.querySelectorAll("article").forEach(card=>{const video=card.querySelector("video");card.querySelector("button").addEventListener("click",()=>{if(video.readyState){video.currentTime=Number(video.dataset.seek)}else{video.addEventListener("loadedmetadata",()=>{video.currentTime=Number(video.dataset.seek)},{once:true});video.load()}})})</script></html>'
    )


def main() -> int:
    """Prepare local review aids with CPU imaging and no model downloads."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "manifest",
        "annotations",
        "splits",
        "rois",
        "index",
        "previews",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare(
            args.manifest,
            args.annotations,
            args.splits,
            args.rois,
            args.index,
            args.previews,
            args.output,
        )
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        parser.exit(1, f"Review preparation failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
