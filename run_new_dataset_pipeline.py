"""Backward-compatible entry point for the new-dataset pipeline."""

import sys
from pipeline import new_dataset as _implementation


if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
