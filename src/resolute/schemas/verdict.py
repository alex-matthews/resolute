"""Strict contract for LLM judge output. Anything that fails validation is discarded."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .core import ActionType, Confidence, Resolution


class VerdictLane(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: Resolution
    confidence: Confidence
    reasons: list[str] = Field(min_length=1, max_length=8)


class VerdictAutomation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: Resolution
    confidence: Confidence
    action: ActionType


class ModelVerdict(BaseModel):
    """The only shape a request-path judgment may return. extra='forbid'
    rejects hallucinated fields."""

    model_config = ConfigDict(extra="forbid")

    objective: VerdictLane
    household: VerdictLane
    automation: VerdictAutomation
    risk_flags: list[str] = Field(default_factory=list, max_length=8)
    questions: list[str] = Field(default_factory=list, max_length=4)


class ObjectiveVerdict(BaseModel):
    """The only shape an objective-worth invocation may return (ADR-0003).
    No household or automation lanes: the invocation carries no household
    context, so the model has nothing legitimate to say about either."""

    model_config = ConfigDict(extra="forbid")

    objective: VerdictLane
    risk_flags: list[str] = Field(default_factory=list, max_length=8)


MODEL_VERDICT_JSON_SCHEMA = ModelVerdict.model_json_schema()
