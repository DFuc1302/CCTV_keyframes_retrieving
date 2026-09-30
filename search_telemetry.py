"""Backward-compatible entry point for search telemetry."""

import sys
from app.services import telemetry as _implementation

sys.modules[__name__] = _implementation
