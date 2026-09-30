"""Shared validation for published, uncalibrated traffic signals."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from pipeline.cctv.congestion import DraftWindow
from pipeline.cctv.traffic import Contract, TrafficWindow, Unit


class Signal(Contract):
    """Traffic evidence is an input to review, not a confirmed incident."""

    window: DraftWindow | TrafficWindow
    coverage: Unit
    motion_pairs: Annotated[int, Field(ge=0)]
    evidence_sufficient: bool
    status: Literal[
        "insufficient_evidence", "no_candidate", "slow_dense", "congestion_candidate"
    ]
    persistent_sec: Annotated[float, Field(ge=0)]
    thresholds_calibrated: Literal[False]
    demo_only: bool

    @model_validator(mode="after")
    def consistent(self):
        if self.demo_only != (self.window.kind == "draft_window"):
            raise ValueError("Traffic draft flag/schema mismatch")
        if (self.status == "insufficient_evidence") == self.evidence_sufficient:
            raise ValueError("Signal status/evidence mismatch")
        if self.evidence_sufficient and (
            self.motion_pairs == 0 or self.window.median_motion_px_per_sec is None
        ):
            raise ValueError("Motion evidence is missing")
        return self
