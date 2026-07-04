import json
from pathlib import Path

import pytest

from resolute.config import Policy, RequesterPolicy, Settings
from resolute.engine.engine import DecisionEngine
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
        policy_path=tmp_path / "missing-policy.yaml",
    )


@pytest.fixture
def policy() -> Policy:
    return Policy(
        franchises_2160p=["star wars", "dune"],
        titles_1080p=["great british bake off"],
        requesters={"alex": RequesterPolicy(bias_2160p=0.5)},
    )


@pytest.fixture
def evidence_source() -> FixtureEvidenceSource:
    return FixtureEvidenceSource(FIXTURES / "evidence")


@pytest.fixture
def engine(settings, policy, evidence_source) -> DecisionEngine:
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
