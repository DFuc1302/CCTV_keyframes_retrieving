"""Download the public legacy CLIP revision into this project's output cache."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from huggingface_hub import snapshot_download

from pipeline.cctv.ingest import reject_link, save_json

MODEL = "openai/clip-vit-large-patch14"
REVISION = "32bd64288804d66eefd0ccbe215aa642df71cc41"


def main() -> int:
    """Reuse the cache on rerun, without relying on account tokens or admin rights."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline", action="store_true", help="Use only an existing local snapshot"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / "outputs" / "model-cache"
    reject_link(output)
    output.mkdir(parents=True, exist_ok=True)
    reject_link(output / "clip.json")
    reject_link(output / "huggingface")
    path = snapshot_download(
        MODEL,
        revision=REVISION,
        token=False,
        cache_dir=str(output / "huggingface"),
        allow_patterns=["*.json", "merges.txt", "model.safetensors"],
        local_files_only=args.offline,
    )
    if not (Path(path) / "model.safetensors").is_file():
        raise RuntimeError(
            "Cached snapshot has no CLIP weights; rerun with network access"
        )
    config = {"model": MODEL, "revision": REVISION, "path": path}
    save_json(output / "clip.json", config)
    print(json.dumps(config, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
