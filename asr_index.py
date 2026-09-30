"""Backward-compatible entry point for ASR indexing."""

import sys
from pipeline.asr import index as _implementation


if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
