"""Backward-compatible entry point for Redis serving helpers."""

import sys
from app.services import redis_serving as _implementation

sys.modules[__name__] = _implementation
