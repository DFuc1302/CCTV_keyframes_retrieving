"""Backward-compatible entry point for the search application."""

import sys
from app import main as _implementation

sys.modules[__name__] = _implementation

