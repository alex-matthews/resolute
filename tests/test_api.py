import pytest
from fastapi.testclient import TestClient

from tv_decider.api.app import create_app
from tv_decider.engine.engine import DecisionEngine
from tv_decider.executor import Executor
from tv_decider.schemas import AutomationMode

from test_executor import FakeSeerr, FakeSonarr


@pytest.fixture
def api(settings, policy, evidence_source, store):
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
        "/api/webhooks/seerr", json=webhook_payload, headers={"X-TVD-Token": "s3cret"}
    )
    assert ok.status_code == 200


def test_webhook_auto_profile_executes(policy, evidence_source, store, webhook_payload, tmp_path):
    from tv_decider.config import Settings

    settings = Settings(
        mode=AutomationMode.AUTO_PROFILE,
        allow_writes=True,
        db_path=tmp_path / "auto.db",
        policy_path=tmp_path / "missing.yaml",
    )
    engine = DecisionEngine(settings, policy, evidence_source)
    executor = Executor(settings, seerr=FakeSeerr(), sonarr=FakeSonarr())
    client = TestClient(create_app(settings, policy, engine, store, executor))

    body = client.post("/api/webhooks/seerr", json=webhook_payload).json()
    assert body["executed_actions"] == ["set_seerr_request_profile_2160p"]
    assert executor.seerr.profile_updates == [(123, 5, [1])]
    assert executor.seerr.approvals == []  # auto_profile never approves


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


def test_execute_endpoint_blocked_for_held_decision(api):
    client, _, _ = api
    decision_id = client.post(
        "/api/decisions", json={"title": "The Bear", "tmdb_id": 136315}
    ).json()["decision_id"]
    response = client.post(
        f"/api/decisions/{decision_id}/execute", json={"operator": "alex"}
    )
    assert response.status_code == 409


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
    text = client.get("/metrics").text
    assert "tvdecider_decisions_total" in text
    assert "tvdecider_webhook_decided_total 1" in text
