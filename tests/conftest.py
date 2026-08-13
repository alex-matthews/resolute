import json
from pathlib import Path

import pytest

from resolute.config import HouseholdPolicy, Settings
from resolute.engine.engine import DecisionEngine
from resolute.judge.judge import Judge
from resolute.metadata.source import FixtureEvidenceSource
from resolute.store.db import Store

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        household_policy_path=tmp_path / "missing-household.md",
    )


@pytest.fixture
def policy() -> HouseholdPolicy:
    """Household prose (ADR-0003). Fixture keeps the historic name `policy`
    because it fills the same positional slot in DecisionEngine/create_app."""
    return HouseholdPolicy(
        prose=(
            "Star Wars and Dune belong at the best quality we can store.\n"
            "The Great British Bake Off is background viewing: 1080p.\n"
            "Nature and space documentaries are the reason the 4K TV exists.\n"
            "Be conservative when space is tight."
        )
    )


class CannedProvider:
    """Repeats one canned response forever; records every call."""

    name = "static"

    def __init__(self, response: str, model: str = "canned-test") -> None:
        self.model = model
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.response


def make_verdict(
    resolution: str = "2160p",
    confidence: str = "high",
    action: str | None = None,
    *,
    objective_resolution: str | None = None,
    risk_flags: list[str] | None = None,
    questions: list[str] | None = None,
    reasons: list[str] | None = None,
) -> str:
    """A schema-valid ModelVerdict JSON string for canned providers."""
    if action is None:
        action = f"set_seerr_request_profile_{resolution}"
    reasons = reasons or ["canned test reasoning"]
    return json.dumps(
        {
            "objective": {
                "resolution": objective_resolution or resolution,
                "confidence": confidence,
                "reasons": reasons,
            },
            "household": {
                "resolution": resolution,
                "confidence": confidence,
                "reasons": reasons,
            },
            "automation": {
                "resolution": resolution,
                "confidence": confidence,
                "action": action,
            },
            "risk_flags": risk_flags or [],
            "questions": questions or [],
        }
    )


def canned_judge(
    resolution: str = "2160p", confidence: str = "high", action: str | None = None
) -> Judge:
    return Judge(CannedProvider(make_verdict(resolution, confidence, action)))


@pytest.fixture
def evidence_source() -> FixtureEvidenceSource:
    return FixtureEvidenceSource(FIXTURES / "evidence")


@pytest.fixture
def engine(settings, policy, evidence_source) -> DecisionEngine:
    """Engine with a decisive canned model: the common flow-test setup."""
    return DecisionEngine(settings, policy, evidence_source, judge=canned_judge())


@pytest.fixture
def engine_no_model(settings, policy, evidence_source) -> DecisionEngine:
    """Engine in degraded operation (model disabled): everything falls back."""
    return DecisionEngine(settings, policy, evidence_source)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "store.db")


def load_fixture(*parts: str) -> dict | list:
    return json.loads((FIXTURES.joinpath(*parts)).read_text())


@pytest.fixture
def webhook_payload() -> dict:
    return load_fixture("seerr", "webhook_media_pending.json")


@pytest.fixture
def movie_webhook_payload() -> dict:
    return load_fixture("seerr", "webhook_movie_pending.json")
