"""Canonical decision request: every trigger path normalizes into this."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .core import TriggerSource


class DecisionRequest(BaseModel):
    """The single input shape for the decision engine.

    Seerr webhooks, manual CLI/API calls, and scheduled review all produce this.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    year: int | None = None
    seasons: list[int] = Field(default_factory=list)
    trigger: TriggerSource = TriggerSource.MANUAL_API
    requester: str | None = None

    # External identity. At least one of tmdb_id/tvdb_id/title is required to decide.
    tmdb_id: int | None = None
    tvdb_id: int | None = None

    # Seerr context, present when triggered by or reconstructed from a Seerr request.
    seerr_request_id: int | None = None

    # Force the LLM judge even when the deterministic band is unambiguous.
    force_judge: bool = False

    def identity_hint(self) -> str:
        parts = [self.title or "?"]
        if self.year:
            parts.append(str(self.year))
        if self.tmdb_id:
            parts.append(f"tmdb:{self.tmdb_id}")
        return " ".join(parts)
