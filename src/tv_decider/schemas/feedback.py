"""Feedback records: household overrides feeding future calibration."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from .core import FeedbackVerdict


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    verdict: FeedbackVerdict
    reason_tag: str | None = None  # must match policy.feedback_reason_tags when set
    comment: str | None = None
    source: str = "api"


class FeedbackRecord(FeedbackIn):
    feedback_id: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
