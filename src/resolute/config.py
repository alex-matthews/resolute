"""Runtime settings (env / yaml) and the editable household policy file."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .schemas.core import AutomationMode


class HouseholdPolicy(BaseModel):
    """Household preference as versioned prose (ADR-0003).

    The file is ordinary text/markdown — showcase genres, children's content,
    franchises, space sensitivity, whatever the household cares about — and it
    names household members, so it is sensitive runtime configuration (Secret
    mount), not a published ConfigMap. Disagreeing with recurring decisions
    means editing this prose, not calibrating weights."""

    model_config = ConfigDict(extra="forbid")

    prose: str = ""
    source_path: str | None = None

    @property
    def content_hash(self) -> str:
        """Stable fingerprint stored with each decision for audit; the prose
        itself never leaves the runtime."""
        import hashlib

        return hashlib.sha256(self.prose.encode()).hexdigest()[:16]


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
    webhook_shared_secret: str = ""  # if set, X-Resolute-Token header must match


class SonarrSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "http://sonarr.default.svc.cluster.local"
    api_key: str = ""


class DowngradeSettings(BaseModel):
    """ADR-0002 downgrade executor. Report-only (dry-run) is always available;
    actually executing a reclaim requires admin_confirm_enabled *and* the
    allow_writes master switch. Ships off at every level."""

    model_config = ConfigDict(extra="forbid")

    admin_confirm_enabled: bool = False
    # Sonarr profile a reclaim targets. Load-bearing invariant (ADR-0002): this
    # profile must EXCLUDE 2160p from its quality list; the executor verifies.
    target_profile_name: str = "HD-1080p"
    # Handoffs whose council decision is older than this are stale and blocked.
    max_decision_age_days: int = 7


class JudgeSettings(BaseModel):
    """The primary decision model (ADR-0003). Settings key stays `judge` so
    the deployed RESOLUTE_JUDGE__* env surface survives the v2 cutover.

    With the model disabled or unreachable, Resolute runs degraded: every
    normal decision takes the conservative fallback (1080p + hold, no write).
    That is deliberate — there is no second, deterministic decision engine."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: str = "openai_compat"  # openai_compat | none
    base_url: str = "http://litellm.default.svc.cluster.local/v1"
    api_key: str = ""
    model: str = "claude-haiku-4-5"
    timeout_seconds: float = 30.0
    # v1 relic, accepted and ignored: a deployed RESOLUTE_JUDGE__JUDGE_AMBIGUOUS_ONLY
    # or config-file key must not crash startup on upgrade (extra="forbid" would).
    judge_ambiguous_only: bool | None = None


class Settings(BaseSettings):
    """Service settings. Env vars use the RESOLUTE_ prefix with __ nesting, e.g.

    RESOLUTE_MODE=shadow  RESOLUTE_SEERR__API_KEY=...  RESOLUTE_JUDGE__ENABLED=true
    """

    model_config = SettingsConfigDict(
        env_prefix="RESOLUTE_", env_nested_delimiter="__", extra="ignore"
    )

    mode: AutomationMode = AutomationMode.SHADOW
    # Master switch: even auto_* modes cannot write while this is false.
    allow_writes: bool = False
    # auto_approve additionally requires this explicit opt-in.
    auto_approve_enabled: bool = False
    # Required for POST /api/decisions/{id}/execute (X-Resolute-Operator-Token header).
    # While unset, HTTP-mediated execution is disabled entirely; the CLI still works.
    execute_token: str = ""
    # Optional bearer for all other /api/* endpoints (X-Resolute-Api-Token header).
    # The webhook keeps its own shared secret; health/ready/metrics stay open.
    # Recommended once the judge is enabled: decision endpoints can spend money.
    api_token: str = ""

    db_path: Path = Path("data/resolute.db")
    # Household preference prose (ADR-0003). Sensitive: mounted from a Secret,
    # not a git ConfigMap — it names household members.
    household_policy_path: Path = Path("config/household.md")

    listen_host: str = "0.0.0.0"
    listen_port: int = 8080
    # Metrics on a separate listener (home-operations org convention: main app
    # on 8080, /metrics on 8081, kept off the possibly-exposed main port).
    metrics_port: int = 8081
    metrics_enabled: bool = True
    log_level: str = "INFO"

    seerr: SeerrSettings = Field(default_factory=SeerrSettings)
    sonarr: SonarrSettings = Field(default_factory=SonarrSettings)
    judge: JudgeSettings = Field(default_factory=JudgeSettings)
    downgrade: DowngradeSettings = Field(default_factory=DowngradeSettings)

    @model_validator(mode="after")
    def _auto_writes_require_webhook_secret(self) -> Settings:
        """Auto modes execute writes from the webhook path, so an unauthenticated
        webhook plus auto writes would be an open write-capable endpoint.
        Refuse the combination outright rather than trusting deployment topology."""
        if (
            self.allow_writes
            and self.mode in (AutomationMode.AUTO_PROFILE, AutomationMode.AUTO_APPROVE)
            and not self.seerr.webhook_shared_secret
        ):
            raise ValueError(
                f"mode={self.mode} with allow_writes=true requires "
                "seerr.webhook_shared_secret: refusing to run an unauthenticated "
                "write-capable webhook endpoint"
            )
        return self


def load_settings(config_file: str | os.PathLike[str] | None = None) -> Settings:
    """Load settings from an optional YAML file, with env vars taking precedence."""
    file_values: dict = {}
    path = Path(config_file) if config_file else Path(os.environ.get("RESOLUTE_CONFIG_FILE", ""))
    if path and path.is_file():
        file_values = yaml.safe_load(path.read_text()) or {}
    return Settings(**file_values)


def load_household_policy(path: str | os.PathLike[str], required: bool = False) -> HouseholdPolicy:
    """Load the household preference prose (ADR-0003).

    `required=True` is the production serve path: the image deliberately
    ships no household file, so a missing file there means the Secret mount
    is broken and the service must fail fast instead of silently deciding
    with no household voice. Ad-hoc CLI/fixture runs tolerate its absence
    (empty prose: the model decides on evidence alone).
    """
    p = Path(path)
    if not p.is_file():
        if required:
            raise FileNotFoundError(
                f"household policy file not found at {p}: mount the "
                "resolute-household Secret at /config/household.md (or set "
                "RESOLUTE_HOUSEHOLD_POLICY_PATH). The image deliberately "
                "ships no household file."
            )
        return HouseholdPolicy()
    return HouseholdPolicy(prose=p.read_text(), source_path=str(p))
