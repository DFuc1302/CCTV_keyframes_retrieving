"""Cache the official legacy YOLOv8m asset with a recorded local SHA-256."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from pipeline.cctv.ingest import digest, output_lease, reject_link, save_json

URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8m.pt"
SHA256 = "5d4a90cdc7a21786cc59cd19778e9eafff836df9e2da32524737c7ee6efe4fe5"


def download(offline: bool = False) -> dict:
    """Reuse only a checksum-matching owned cache; inference never downloads."""
    root = Path(__file__).resolve().parents[1] / "outputs/model-cache/yolo"
    reject_link(root)
    root.mkdir(parents=True, exist_ok=True)
    weights, descriptor = root / "yolov8m.pt", root / "yolo.json"
    for path in (weights, descriptor, root / ".ingest.lock"):
        reject_link(path)
    with output_lease(root):
        if descriptor.exists():
            config = json.loads(descriptor.read_text(encoding="utf-8"))
            if (
                config.get("url") != URL
                or config.get("path") != str(weights)
                or config.get("sha256") != SHA256
                or digest(weights) != SHA256
            ):
                raise ValueError(
                    "YOLO cache differs from recorded descriptor; preserve and investigate"
                )
            return config
        if weights.exists():
            raise ValueError("Unowned weights preserved; use a clean cache")
        if offline:
            raise ValueError("YOLO cache unavailable offline; download once first")
        with tempfile.TemporaryDirectory(prefix=".download-", dir=root) as folder:
            staged = Path(folder) / "weights.pt"
            with (
                urlopen(
                    Request(URL, headers={"User-Agent": "CCTV-VGU/1.0"}), timeout=60
                ) as response,
                staged.open("wb") as handle,
            ):
                size = 0
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    size += len(block)
                    if size > 200 * 1024 * 1024:
                        raise ValueError("Unexpectedly large YOLO asset")
                    handle.write(block)
            if size < 1024 * 1024:
                raise ValueError("Downloaded asset is too small")
            if digest(staged) != SHA256:
                raise ValueError(
                    "Official asset differs from pinned checksum; not published"
                )
            config = {
                "model": "yolov8m",
                "revision": "v8.3.0",
                "url": URL,
                "path": str(weights),
                "sha256": digest(staged),
            }
            # Leave a descriptor if interrupted after publication; offline verification
            # fails explicitly rather than silently adopting unrelated weights.
            save_json(descriptor, config)
            staged.replace(weights)
        return config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(download(args.offline), indent=2))
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(1, f"YOLO cache error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
