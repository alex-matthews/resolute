"""Runtime settings (env / yaml) and the editable household policy file."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .schemas.core import AutomationMode


class RequesterPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bias_2160p: float = 0.0  # additive score bias, positive favors 2160p
    note: str | None = None


class PolicyWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visual_genre: float = 1.6
    network_tier: float = 1.0
    era: float = 0.8
    acclaim: float = 1.0
    episode_burden: float = 1.4
    requester_preference: float = 1.0
    franchise_priority: float = 3.0
    storage_pressure: float = 1.2


class PolicyThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uhd_score: float = 2.0  # score >= uhd_score -> 2160p
    hd_score: float = -0.5  # score <= hd_score -> 1080p; between -> ambiguous
    high_confidence_margin: float = 1.5  # distance beyond threshold for high confidence


class Policy(BaseModel):
    """Household policy vocabulary. Small, editable, versioned in git."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    storage_pressure: str = "low"  # low | medium | high
    max_episodes_2160p: int = 80  # above this, 2160p requires high confidence

    weights: PolicyWeights = Field(default_factory=PolicyWeights)
    thresholds: PolicyThresholds = Field(default_factory=PolicyThresholds)

    # Genre vocabulary (lowercase match against TMDB genres/keywords).
    visual_genres: list[str] = Field(
        default_factory=lambda: [
            "documentary",
            "sci-fi & fantasy",
            "science fiction",
            "animation",
            "action & adventure",
            "war & politics",
        ]
    )
    low_payoff_genres: list[str] = Field(
        default_factory=lambda: ["talk", "news", "reality", "soap", "comedy"]
    )
    premium_networks: list[str] = Field(
        default_factory=lambda: [
            "hbo",
            "max",
            "apple tv+",
            "netflix",
            "disney+",
            "amazon",
            "prime video",
        ]
    )

    # Hard overrides: guardrails pin these regardless of score or judge opinion.
    franchises_2160p: list[str] = Field(default_factory=list)
    titles_1080p: list[str] = Field(default_factory=list)

    requesters: dict[str, RequesterPolicy] = Field(default_factory=dict)

    feedback_reason_tags: list[str] = Field(
        default_factory=lambda: [
            "showcase",
            "background_watch",
            "storage",
            "prestige_exception",
            "kids_content",
            "rewatch_favorite",
            "bad_metadata",
        ]
    )


class SeerrSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "http://seerr.default.svc.cluster.local"
    api_key: str = ""
    # Human names of the two existing Sonarr profiles as Seerr exposes them.
    profile_name_1080p: str = "HD-1080p"
    profile_name_2160p: str = "Ultra-HD"
    # Which webhook notification types trigger a decision.
    trigger_notification_types: list[str] = Field(
        default_factory=lambda: ["MEDIA_PENDING", "MEDIA_AUTO_APPROVED"]
    )
    webhook_shared_secret: str = ""  # if set, X-TVD-Token header must match


class SonarrSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "http://sonarr.default.svc.cluster.local"
    api_key: str = ""


class JudgeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: str = "openai_compat"  # openai_compat | none
    base_url: str = "http://litellm.default.svc.cluster.local/v1"
    api_key: str = ""
    model: str = "claude-haiku-4-5"
    timeout_seconds: float = 30.0
    # Only consult the judge inside the ambiguous score band unless force_judge is set.
    judge_ambiguous_only: bool = True


class Settings(BaseSettings):
    """Service settings. Env vars use the TVD_ prefix with __ nesting, e.g.

    TVD_MODE=shadow  TVD_SEERR__API_KEY=...  TVD_JUDGE__ENABLED=true
    """

    model_config = SettingsConfigDict(
        env_prefix="TVD_", env_nested_delimiter="__", extra="ignore"
    )

    mode: AutomationMode = AutomationMode.SHADOW
    # Master switch: even auto_* modes cannot write while this is false.
    allow_writes: bool = False
    # auto_approve additionally requires this explicit opt-in.
    auto_approve_enabled: bool = False
    # Required for POST /api/decisions/{id}/execute (X-TVD-Operator-Token header).
    # While unset, HTTP-mediated execution is disabled entirely; the CLI still works.
    execute_token: str = ""
    # Optional bearer for all other /api/* endpoints (X-TVD-Api-Token header).
    # The webhook keeps its own shared secret; health/ready/metrics stay open.
    # Recommended once the judge is enabled: decision endpoints can spend money.
    api_token: str = ""

    db_path: Path = Path("data/tv-decider.db")
    policy_path: Path = Path("config/policy.yaml")

    listen_host: str = "0.0.0.0"
    listen_port: int = 8130
    log_level: str = "INFO"

    seerr: SeerrSettings = Field(default_factory=SeerrSettings)
    sonarr: SonarrSettings = Field(default_factory=SonarrSettings)
    judge: JudgeSettings = Field(default_factory=JudgeSettings)


def load_settings(config_file: str | os.PathLike[str] | None = None) -> Settings:
    """Load settings from an optional YAML file, with env vars taking precedence."""
    file_values: dict = {}
    path = Path(config_file) if config_file else Path(os.environ.get("TVD_CONFIG_FILE", ""))
    if path and path.is_file():
        file_values = yaml.safe_load(path.read_text()) or {}
    return Settings(**file_values)


def load_policy(path: str | os.PathLike[str]) -> Policy:
    p = Path(path)
    if not p.is_file():
        return Policy()
    data = yaml.safe_load(p.read_text()) or {}
    return Policy(**data)
