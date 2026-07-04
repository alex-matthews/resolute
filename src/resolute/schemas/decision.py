"""Decision output: recommendation lanes, action plan, and audit trail."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from .core import ActionType, AutomationMode, Confidence, Resolution, TriggerSource, WRITE_ACTIONS
from .evidence import EvidenceBundle
from .request import DecisionRequest
from .verdict import ModelVerdict


class ScoreComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    contribution: float
    note: str


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: Resolution
    confidence: Confidence
    reasons: list[str] = Field(default_factory=list)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActionType
    params: dict[str, int | str | bool | None] = Field(default_factory=dict)
    requires_approval: bool = True
    note: str | None = None

    @property
    def is_write(self) -> bool:
        return self.type in WRITE_ACTIONS


class ModelInvolvement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    used: bool = False
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    evidence_hash: str | None = None
    raw_output: str | None = None
    error: str | None = None
    latency_ms: int | None = None


class Decision(BaseModel):
    """The full decision record, durable in the store and returned by API/CLI."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    request: DecisionRequest
    evidence: EvidenceBundle

    title: str | None = None
    year: int | None = None
    seasons: list[int] = Field(default_factory=list)
    trigger: TriggerSource = TriggerSource.MANUAL_API
    mode: AutomationMode = AutomationMode.SHADOW

    objective: Recommendation
    household: Recommendation
    final_resolution: Resolution
    confidence: Confidence

    score: float = 0.0
    score_components: list[ScoreComponent] = Field(default_factory=list)
    top_reasons: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    metadata_gaps: list[str] = Field(default_factory=list)

    model_involvement: ModelInvolvement = Field(default_factory=ModelInvolvement)
    verdict: ModelVerdict | None = None

    action_plan: list[Action] = Field(default_factory=list)
    executed_actions: list[ActionType] = Field(default_factory=list)

    feedback_options: list[str] = Field(
        default_factory=lambda: ["agree", "prefer_1080p", "prefer_2160p", "manual_review"]
    )

    # Shadow-mode delta vs the current Sonarr/Seerr state, when observable.
    shadow_delta: str | None = None
