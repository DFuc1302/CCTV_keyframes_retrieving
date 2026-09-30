"""Backward-compatible entry point for data health checks."""

import sys
from app.services import data_health as _implementation

sys.modules[__name__] = _implementation
