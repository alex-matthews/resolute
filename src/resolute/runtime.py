"""Production wiring: build the engine/store/executor stack from settings.

Tests bypass this and inject fixture components directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Policy, Settings, load_policy, load_settings
from .engine.engine import DecisionEngine
from .executor import Executor
from .judge.judge import Judge
from .judge.provider import OpenAICompatProvider
from .metadata.source import LiveEvidenceSource
from .seerr.client import SeerrClient
from .sonarr.client import SonarrClient
from .store.db import Store


@dataclass
class Runtime:
    settings: Settings
    policy: Policy
    engine: DecisionEngine
    store: Store
    executor: Executor
    seerr: SeerrClient
    sonarr: SonarrClient | None


def build_runtime(config_file: str | None = None) -> Runtime:
    settings = load_settings(config_file)
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    policy = load_policy(settings.policy_path, required=True)

    seerr = SeerrClient(settings.seerr.url, settings.seerr.api_key)
    sonarr = (
        SonarrClient(settings.sonarr.url, settings.sonarr.api_key)
        if settings.sonarr.api_key
        else None
    )

    judge = None
    if settings.judge.enabled and settings.judge.provider == "openai_compat":
        judge = Judge(
            OpenAICompatProvider(
                base_url=settings.judge.base_url,
                api_key=settings.judge.api_key,
                model=settings.judge.model,
                timeout_seconds=settings.judge.timeout_seconds,
            )
        )

    engine = DecisionEngine(
        settings=settings,
        policy=policy,
        evidence_source=LiveEvidenceSource(seerr, sonarr),
        judge=judge,
    )
    store = Store(settings.db_path)
    executor = Executor(settings, seerr=seerr, sonarr=sonarr)
    return Runtime(
        settings=settings,
        policy=policy,
        engine=engine,
        store=store,
        executor=executor,
        seerr=seerr,
        sonarr=sonarr,
    )


def create_production_app(config_file: str | None = None):
    from .api.app import create_app

    rt = build_runtime(config_file)
    return create_app(rt.settings, rt.policy, rt.engine, rt.store, rt.executor, rt.seerr)
