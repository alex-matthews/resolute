import pytest
from fastapi.testclient import TestClient

from resolute.api.app import create_app, create_metrics_app
from resolute.engine.engine import DecisionEngine
from resolute.executor import Executor
from resolute.schemas import AutomationMode

from test_executor import FakeSeerr, FakeSonarr


OPERATOR_TOKEN = "test-operator-token"


@pytest.fixture
def api(settings, policy, evidence_source, store):
    settings.execute_token = OPERATOR_TOKEN
    engine = DecisionEngine(settings, policy, evidence_source)
    executor = Executor(settings, seerr=FakeSeerr(), sonarr=FakeSonarr())
    app = create_app(settings, policy, engine, store, executor)
    return TestClient(app), settings, executor


def test_health_and_ready(api):
    client, _, _ = api
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz").json()
    assert ready["status"] == "ready"
    assert ready["mode"] == "shadow"


def test_post_decision_and_get(api):
    client, _, _ = api
    response = client.post(
        "/api/decisions",
        json={"title": "Severance", "year": 2022, "tmdb_id": 95396, "requester": "alex"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["final_resolution"] == "2160p"
    assert body["confidence"] == "high"
    assert body["mode"] == "shadow"

    fetched = client.get(f"/api/decisions/{body['decision_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["decision_id"] == body["decision_id"]

    assert client.get("/api/decisions/NOPE").status_code == 404
    assert client.get("/api/decisions").json()[0]["decision_id"] == body["decision_id"]


def test_decision_mode_override(api):
    client, _, _ = api
    response = client.post(
        "/api/decisions",
        json={"title": "Severance", "tmdb_id": 95396, "mode": "recommend"},
    )
    assert response.json()["mode"] == "recommend"


def test_webhook_decides_pending_tv_request(api, webhook_payload):
    client, _, executor = api
    response = client.post("/api/webhooks/seerr", json=webhook_payload)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "decided"
    assert body["final_resolution"] == "2160p"
    action_types = [a["type"] for a in body["action_plan"]]
    assert "set_seerr_request_profile_2160p" in action_types
    assert "approve_seerr_request" in action_types
    # shadow mode: nothing executed, no writes on the fake clients
    assert body["executed_actions"] == []
    assert executor.seerr.profile_updates == []


def test_webhook_skips_movies_and_non_triggers(api, movie_webhook_payload, webhook_payload):
    client, _, _ = api
    response = client.post("/api/webhooks/seerr", json=movie_webhook_payload)
    assert response.json()["status"] == "skipped"

    webhook_payload["notification_type"] = "MEDIA_AVAILABLE"
    response = client.post("/api/webhooks/seerr", json=webhook_payload)
    assert response.json()["status"] == "skipped"


def test_webhook_rejects_garbage(api):
    client, _, _ = api
    assert client.post("/api/webhooks/seerr", json={"nope": 1}).status_code == 422


def test_webhook_shared_secret(settings, policy, evidence_source, store, webhook_payload):
    settings.seerr.webhook_shared_secret = "s3cret"
    engine = DecisionEngine(settings, policy, evidence_source)
    client = TestClient(create_app(settings, policy, engine, store, None))
    assert client.post("/api/webhooks/seerr", json=webhook_payload).status_code == 401
    ok = client.post(
        "/api/webhooks/seerr", json=webhook_payload, headers={"X-Resolute-Token": "s3cret"}
    )
    assert ok.status_code == 200


def _auto_settings(tmp_path, mode, **kwargs):
    from resolute.config import Settings

    return Settings(
        mode=mode,
        allow_writes=True,
        db_path=tmp_path / "auto.db",
        policy_path=tmp_path / "missing.yaml",
        seerr={"webhook_shared_secret": "hook-secret"},
        **kwargs,
    )


def test_webhook_auto_profile_executes(policy, evidence_source, store, webhook_payload, tmp_path):
    settings = _auto_settings(tmp_path, AutomationMode.AUTO_PROFILE)
    engine = DecisionEngine(settings, policy, evidence_source)
    executor = Executor(settings, seerr=FakeSeerr(), sonarr=FakeSonarr())
    client = TestClient(create_app(settings, policy, engine, store, executor))

    body = client.post(
        "/api/webhooks/seerr",
        json=webhook_payload,
        headers={"X-Resolute-Token": "hook-secret"},
    ).json()
    assert body["executed_actions"] == ["set_seerr_request_profile_2160p"]
    assert executor.seerr.profile_updates == [(123, 5, [1])]
    assert executor.seerr.approvals == []  # auto_profile never approves


def test_auto_write_mode_cannot_be_configured_without_webhook_secret(tmp_path):
    from pydantic import ValidationError

    from resolute.config import Settings

    with pytest.raises(ValidationError, match="webhook_shared_secret"):
        Settings(
            mode=AutomationMode.AUTO_PROFILE,
            allow_writes=True,
            db_path=tmp_path / "x.db",
        )


def test_feedback_flow(api):
    client, _, _ = api
    decision_id = client.post(
        "/api/decisions", json={"title": "Severance", "tmdb_id": 95396}
    ).json()["decision_id"]

    ok = client.post(
        "/api/feedback",
        json={"decision_id": decision_id, "verdict": "prefer_1080p", "reason_tag": "storage"},
    )
    assert ok.status_code == 200

    assert (
        client.post(
            "/api/feedback", json={"decision_id": "missing", "verdict": "agree"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/feedback",
            json={"decision_id": decision_id, "verdict": "agree", "reason_tag": "nope"},
        ).status_code
        == 422
    )

    summary = client.get("/api/calibration/summary").json()
    assert summary["feedback"] == 1
    assert summary["override_reason_tags"] == {"storage": 1}


def test_execute_endpoint_requires_operator_token(api):
    client, _, _ = api
    decision_id = client.post(
        "/api/decisions", json={"title": "Severance", "tmdb_id": 95396}
    ).json()["decision_id"]
    # no token header
    assert (
        client.post(
            f"/api/decisions/{decision_id}/execute", json={"operator": "alex"}
        ).status_code
        == 403
    )
    # wrong token
    assert (
        client.post(
            f"/api/decisions/{decision_id}/execute",
            json={"operator": "alex"},
            headers={"X-Resolute-Operator-Token": "wrong"},
        ).status_code
        == 403
    )


def test_execute_endpoint_disabled_without_configured_token(
    settings, policy, evidence_source, store
):
    settings.execute_token = ""
    engine = DecisionEngine(settings, policy, evidence_source)
    executor = Executor(settings, seerr=FakeSeerr(), sonarr=FakeSonarr())
    client = TestClient(create_app(settings, policy, engine, store, executor))
    decision_id = client.post(
        "/api/decisions", json={"title": "Severance", "tmdb_id": 95396}
    ).json()["decision_id"]
    response = client.post(
        f"/api/decisions/{decision_id}/execute",
        json={"operator": "alex"},
        headers={"X-Resolute-Operator-Token": "anything"},
    )
    assert response.status_code == 403
    assert "disabled" in response.json()["detail"]


def test_execute_endpoint_blocked_for_held_decision(api):
    client, _, _ = api
    decision_id = client.post(
        "/api/decisions", json={"title": "The Bear", "tmdb_id": 136315}
    ).json()["decision_id"]
    response = client.post(
        f"/api/decisions/{decision_id}/execute",
        json={"operator": "alex"},
        headers={"X-Resolute-Operator-Token": OPERATOR_TOKEN},
    )
    assert response.status_code == 409


def test_reviews_pending_endpoint(settings, policy, evidence_source, store):
    class FakeSeerrList(FakeSeerr):
        def list_requests(self, filter="pending", take=50, skip=0):
            return [
                {
                    "id": 123,
                    "status": 1,
                    "media": {"mediaType": "tv", "tmdbId": 95396, "tvdbId": 371980},
                    "requestedBy": {"username": "alex"},
                    "seasons": [{"seasonNumber": 1}],
                },
                {"id": 124, "status": 1, "media": {"mediaType": "movie", "tmdbId": 1}},
            ]

    engine = DecisionEngine(settings, policy, evidence_source)
    client = TestClient(
        create_app(settings, policy, engine, store, None, seerr=FakeSeerrList())
    )
    body = client.post("/api/reviews/pending").json()
    assert body["reviewed"] == 1  # the movie was skipped
    assert body["decisions"][0]["final_resolution"] == "2160p"
    # decisions are stored and retrievable
    decision_id = body["decisions"][0]["decision_id"]
    assert client.get(f"/api/decisions/{decision_id}").status_code == 200


def test_reviews_pending_without_seerr_client(api):
    client, _, _ = api
    assert client.post("/api/reviews/pending").status_code == 503


def test_api_token_gates_decision_endpoints(settings, policy, evidence_source, store):
    settings.api_token = "api-tok"
    engine = DecisionEngine(settings, policy, evidence_source)
    client = TestClient(create_app(settings, policy, engine, store, None))

    body = {"title": "Severance", "tmdb_id": 95396}
    assert client.post("/api/decisions", json=body).status_code == 401
    assert (
        client.post(
            "/api/decisions", json=body, headers={"X-Resolute-Api-Token": "wrong"}
        ).status_code
        == 401
    )
    ok = client.post("/api/decisions", json=body, headers={"X-Resolute-Api-Token": "api-tok"})
    assert ok.status_code == 200

    # probes stay open on the main port; metrics on its dedicated listener
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200
    metrics_client = TestClient(create_metrics_app(client.app.state.metrics))
    assert metrics_client.get("/metrics").status_code == 200


def test_webhook_exempt_from_api_token(
    settings, policy, evidence_source, store, webhook_payload
):
    settings.api_token = "api-tok"
    settings.seerr.webhook_shared_secret = "hook-secret"
    engine = DecisionEngine(settings, policy, evidence_source)
    client = TestClient(create_app(settings, policy, engine, store, None))
    # webhook is governed by its own secret, not the api token
    response = client.post(
        "/api/webhooks/seerr", json=webhook_payload, headers={"X-Resolute-Token": "hook-secret"}
    )
    assert response.status_code == 200


def test_partial_execution_is_recorded_durably(
    settings, policy, evidence_source, store, webhook_payload
):
    from test_executor import FailingApproveSeerr

    settings.execute_token = OPERATOR_TOKEN
    settings.mode = AutomationMode.APPROVE
    settings.allow_writes = True
    engine = DecisionEngine(settings, policy, evidence_source)
    executor = Executor(settings, seerr=FailingApproveSeerr(), sonarr=FakeSonarr())
    client = TestClient(create_app(settings, policy, engine, store, executor))

    decision_id = client.post("/api/webhooks/seerr", json=webhook_payload).json()[
        "decision_id"
    ]
    response = client.post(
        f"/api/decisions/{decision_id}/execute",
        json={"operator": "alex"},
        headers={"X-Resolute-Operator-Token": OPERATOR_TOKEN},
    )
    assert response.status_code == 502
    assert "set_seerr_request_profile_2160p" in response.json()["detail"]
    # the successful profile update was recorded before the error surfaced
    executions = store.executions(decision_id)
    assert len(executions) == 1
    assert executions[0]["actions"] == ["set_seerr_request_profile_2160p"]
    assert executions[0]["operator"] == "alex (partial)"


def test_webhook_auto_execution_records_partial_and_reports_error(
    policy, evidence_source, store, webhook_payload, tmp_path
):
    from test_executor import FailingApproveSeerr

    settings = _auto_settings(
        tmp_path, AutomationMode.AUTO_APPROVE, auto_approve_enabled=True
    )
    engine = DecisionEngine(settings, policy, evidence_source)
    executor = Executor(settings, seerr=FailingApproveSeerr(), sonarr=FakeSonarr())
    client = TestClient(create_app(settings, policy, engine, store, executor))

    body = client.post(
        "/api/webhooks/seerr",
        json=webhook_payload,
        headers={"X-Resolute-Token": "hook-secret"},
    ).json()
    assert body["executed_actions"] == ["set_seerr_request_profile_2160p"]
    assert "seerr exploded" in body["execution_error"]
    executions = store.executions(body["decision_id"])
    assert executions[0]["operator"] == "auto (partial)"


def test_execute_endpoint_409_when_nothing_executable(api):
    """Shadow decision + valid token: no writes are permitted, no audit row written."""
    client, _, _ = api
    decision_id = client.post(
        "/api/decisions", json={"title": "Severance", "tmdb_id": 95396}
    ).json()["decision_id"]
    response = client.post(
        f"/api/decisions/{decision_id}/execute",
        json={"operator": "alex"},
        headers={"X-Resolute-Operator-Token": OPERATOR_TOKEN},
    )
    assert response.status_code == 409
    assert "nothing executable" in response.json()["detail"]


def test_sonarr_audit_endpoint(api):
    client, _, _ = api
    decision_id = client.post(
        "/api/decisions", json={"title": "Severance", "tmdb_id": 95396}
    ).json()["decision_id"]
    response = client.post("/api/sonarr/audit", json={"decision_id": decision_id})
    assert response.status_code == 200
    body = response.json()
    assert body["expected_profile"] == "Ultra-HD"
    assert body["series_found"] is False  # fixture evidence has no sonarr series

    assert client.post("/api/sonarr/audit", json={}).status_code == 422


def test_metrics_exposition(api, webhook_payload):
    client, _, _ = api
    client.post("/api/decisions", json={"title": "Severance", "tmdb_id": 95396})
    client.post("/api/webhooks/seerr", json=webhook_payload)
    metrics_client = TestClient(create_metrics_app(client.app.state.metrics))
    text = metrics_client.get("/metrics").text
    assert "resolute_decisions_total" in text
    assert "resolute_webhook_decided_total 1" in text


# -- ADR-0002: objective worth + downgrades ---------------------------------


class WorthSeerr(FakeSeerr):
    """Serves the Severance TV-details fixture (tvdbId 371980) like Seerr would."""

    def __init__(self):
        super().__init__()
        from conftest import load_fixture

        self.tv = load_fixture("seerr", "tv_details_severance.json")

    def get_tv_details(self, tmdb_id):
        if tmdb_id == self.tv["id"]:
            return self.tv
        from resolute.seerr.client import SeerrError

        raise SeerrError("not found")

    def search(self, query, page=1):
        return [
            {"mediaType": "person", "id": 1},
            {"mediaType": "tv", "id": self.tv["id"]},
        ]


class WorthSonarr:
    def get_series_by_tvdb(self, tvdb_id):
        if tvdb_id == 371980:
            return {"id": 42, "title": "Severance", "tvdbId": 371980}
        return None


def _worth_client(settings, policy, evidence_source, store):
    from resolute.api.app import create_app
    from resolute.engine.engine import DecisionEngine

    engine = DecisionEngine(settings, policy, evidence_source)
    app = create_app(
        settings, policy, engine, store, None, seerr=WorthSeerr(), sonarr=WorthSonarr()
    )
    return TestClient(app)


def test_objective_worth_via_sonarr_title_and_search(
    settings, policy, evidence_source, store
):
    client = _worth_client(settings, policy, evidence_source, store)
    body = client.get("/api/titles/371980/objective-worth").json()
    assert body["worth"] == "2160p"
    assert body["tmdb_id"] == 95396
    assert body["title"] == "Severance"
    assert body["objective_score"] > 0
    assert body["reasons"]
    # pure read: no decision recorded
    assert store.list_decisions() == []


def test_objective_worth_verifies_tmdb_hint(settings, policy, evidence_source, store):
    client = _worth_client(settings, policy, evidence_source, store)
    # correct hint resolves directly
    ok = client.get("/api/titles/371980/objective-worth?tmdb_id=95396").json()
    assert ok["worth"] == "2160p"
    # a hint whose externalIds don't match the tvdb id is rejected, and with
    # no Sonarr series either the endpoint degrades to unavailable
    bad = client.get("/api/titles/999/objective-worth?tmdb_id=95396").json()
    assert bad["worth"] == "unavailable"


def test_objective_worth_unavailable_degrades(settings, policy, evidence_source, store):
    client = _worth_client(settings, policy, evidence_source, store)
    body = client.get("/api/titles/12345/objective-worth").json()
    assert body["worth"] == "unavailable"
    assert "reason" in body


def test_downgrade_plan_endpoint_is_read_only(settings, policy, evidence_source, store):
    from test_downgrade import SERIES, FakeDowngradeSonarr
    from resolute.api.app import create_app
    from resolute.engine.engine import DecisionEngine

    engine = DecisionEngine(settings, policy, evidence_source)
    sonarr = FakeDowngradeSonarr(series=SERIES)
    client = TestClient(
        create_app(settings, policy, engine, store, None, seerr=None, sonarr=sonarr)
    )
    body = client.post(
        "/api/downgrades/plan",
        json={"costanza_decision_id": "cz-9", "tvdb_id": 404171},
    ).json()
    assert body["blockers"] == []
    assert body["estimated_gb_reclaimed"] == 30.0
    assert sonarr.profile_updates == []
    assert store.get_downgrade("cz-9") is None


def test_downgrade_execute_endpoint_gates(settings, policy, evidence_source, store):
    from test_downgrade import SERIES, FakeDowngradeSonarr
    from resolute.api.app import create_app
    from resolute.engine.engine import DecisionEngine

    settings.execute_token = OPERATOR_TOKEN
    engine = DecisionEngine(settings, policy, evidence_source)
    sonarr = FakeDowngradeSonarr(series=SERIES)
    client = TestClient(
        create_app(settings, policy, engine, store, None, seerr=None, sonarr=sonarr)
    )
    payload = {
        "operator": "alex",
        "handoff": {"costanza_decision_id": "cz-9", "tvdb_id": 404171},
    }
    # wrong operator token
    assert (
        client.post(
            "/api/downgrades/execute",
            json=payload,
            headers={"X-Resolute-Operator-Token": "wrong"},
        ).status_code
        == 403
    )
    # right token but report-only phase (gates off) -> 409, nothing written
    response = client.post(
        "/api/downgrades/execute",
        json=payload,
        headers={"X-Resolute-Operator-Token": OPERATOR_TOKEN},
    )
    assert response.status_code == 409
    assert sonarr.profile_updates == []

    # both gates open -> executes and is retrievable
    settings.allow_writes = True
    settings.downgrade.admin_confirm_enabled = True
    response = client.post(
        "/api/downgrades/execute",
        json=payload,
        headers={"X-Resolute-Operator-Token": OPERATOR_TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["executed"] is True
    assert sonarr.profile_updates == [(42, 6)]
    record = client.get("/api/downgrades/cz-9").json()
    assert record["executed"] is True
    assert record["steps"] == ["profile_set", "search_triggered"]
    # live reconciliation: profile on target, 2160p resident, nothing queued
    assert record["reconciliation"]["outcome"] == "pending"
    assert client.get("/api/downgrades/nope").status_code == 404
