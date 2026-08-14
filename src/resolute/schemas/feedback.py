"""Feedback records: the shadow-mode disagreement signal (ADR-0003 —
prose editing is the calibration mechanism)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from .core import FeedbackVerdict


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    verdict: FeedbackVerdict
    reason_tag: str | None = None  # free text (the v1 taxonomy died with ADR-0003)
    comment: str | None = None
    source: str = "api"


class FeedbackRecord(FeedbackIn):
    feedback_id: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
