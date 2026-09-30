"""Backward-compatible entry point for the conversational search service."""

import sys
from app.services import kisc as _implementation

sys.modules[__name__] = _implementation
