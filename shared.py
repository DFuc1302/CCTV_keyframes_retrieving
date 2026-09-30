"""Backward-compatible entry point for shared runtime helpers."""

import sys
from app import shared as _implementation

# Share private state and configuration with package imports and legacy patches.
sys.modules[__name__] = _implementation
