"""API hardening: webhook body guard, well-formed metrics exposition."""

from collections import Counter

from fastapi.testclient import TestClient

from resolute.api.app import create_app, create_metrics_app
from resolute.engine.engine import DecisionEngine


def _client(settings, policy, evidence_source, store) -> TestClient:
    engine = DecisionEngine(settings, policy, evidence_source)
    return TestClient(create_app(settings, policy, engine, store, None))


def test_webhook_invalid_json_is_stored_and_400(settings, policy, evidence_source, store):
    client = _client(settings, policy, evidence_source, store)
    response = client.post(
        "/api/webhooks/seerr",
        content=b"this is not json{{",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    # the garbage payload still lands in the webhook_events fixture farm
    row = store._conn.execute(
        "SELECT outcome, payload FROM webhook_events"
    ).fetchone()
    assert row[0] == "invalid: not json"
    assert "this is not json" in row[1]


def test_webhook_non_object_json_is_422(settings, policy, evidence_source, store):
    client = _client(settings, policy, evidence_source, store)
    response = client.post("/api/webhooks/seerr", json=["a", "list"])
    assert response.status_code == 422


def test_metrics_exposition_type_line_per_family():
    metrics: Counter[str] = Counter()
    metrics['decisions_total{resolution="2160p"}'] += 1
    metrics['decisions_total{resolution="1080p"}'] += 2
    metrics["webhook_decided_total"] += 1
    text = TestClient(create_metrics_app(metrics)).get("/metrics").text
    # one TYPE line per family, named after the family (not a catch-all)
    assert text.count("# TYPE resolute_decisions_total counter") == 1
    assert "# TYPE resolute_webhook_decided_total counter" in text
    assert 'resolute_decisions_total{resolution="1080p"} 2' in text
    assert "resolute_events" not in text
