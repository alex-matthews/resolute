import json

import pytest
from typer.testing import CliRunner

from resolute.cli import app

runner = CliRunner()


@pytest.fixture
def env(tmp_path, fixtures_dir):
    """Environment that keeps CLI runs offline and inside tmp_path."""
    return {
        "RESOLUTE_DB_PATH": str(tmp_path / "cli.db"),
        "RESOLUTE_POLICY_PATH": str(fixtures_dir.parent / "config" / "policy.yaml"),
    }


def _decide(env, fixtures_dir, *extra):
    return runner.invoke(
        app,
        [
            "decide",
            "Severance",
            "--year",
            "2022",
            "--tmdb-id",
            "95396",
            "--fixtures",
            str(fixtures_dir / "evidence"),
            *extra,
        ],
        env=env,
    )


def test_decide_offline(env, fixtures_dir):
    result = _decide(env, fixtures_dir)
    assert result.exit_code == 0, result.output
    assert "2160p" in result.output
    assert "mode=shadow" in result.output


def test_decide_json_output(env, fixtures_dir):
    result = _decide(env, fixtures_dir, "--json")
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["final_resolution"] == "2160p"
    assert body["confidence"] == "high"


def test_feedback_last_and_calibrate_and_overrides(env, fixtures_dir):
    assert _decide(env, fixtures_dir).exit_code == 0

    fb = runner.invoke(
        app,
        [
            "feedback",
            "last",
            "prefer_1080p",
            "--reason-tag",
            "storage",
            "--fixtures",
            str(fixtures_dir / "evidence"),
        ],
        env=env,
    )
    assert fb.exit_code == 0, fb.output

    cal = runner.invoke(
        app, ["calibrate", "--fixtures", str(fixtures_dir / "evidence")], env=env
    )
    assert cal.exit_code == 0
    summary = json.loads(cal.output)
    assert summary["feedback"] == 1

    ov = runner.invoke(
        app, ["review-overrides", "--fixtures", str(fixtures_dir / "evidence")], env=env
    )
    assert ov.exit_code == 0
    assert "prefer_1080p" in ov.output


def test_feedback_rejects_unknown_reason_tag(env, fixtures_dir):
    assert _decide(env, fixtures_dir).exit_code == 0
    fb = runner.invoke(
        app,
        [
            "feedback",
            "last",
            "prefer_1080p",
            "--reason-tag",
            "not_a_tag",
            "--fixtures",
            str(fixtures_dir / "evidence"),
        ],
        env=env,
    )
    assert fb.exit_code == 1


def test_fixtures_test_golden_suite(env, fixtures_dir):
    result = runner.invoke(
        app,
        [
            "fixtures-test",
            "--fixtures",
            str(fixtures_dir / "evidence"),
            "--golden",
            str(fixtures_dir / "golden" / "expectations.json"),
        ],
        env=env,
    )
    assert result.exit_code == 0, result.output
    assert "FAIL" not in result.output


def test_execute_command_exists_and_is_mode_gated(env, fixtures_dir):
    assert _decide(env, fixtures_dir).exit_code == 0
    # shadow decision with no write actions: executes nothing, exits cleanly
    result = runner.invoke(
        app, ["execute", "last", "--operator", "alex", "--yes"], env=env
    )
    assert result.exit_code == 0, result.output
    assert "nothing executed" in result.output


def test_execute_command_unknown_decision(env):
    result = runner.invoke(
        app, ["execute", "NOPE", "--operator", "alex", "--yes"], env=env
    )
    assert result.exit_code == 1


def test_export_jsonl(env, fixtures_dir, tmp_path):
    assert _decide(env, fixtures_dir).exit_code == 0
    out = tmp_path / "decisions.jsonl"
    result = runner.invoke(
        app,
        ["export-jsonl", "--out", str(out), "--fixtures", str(fixtures_dir / "evidence")],
        env=env,
    )
    assert result.exit_code == 0
    assert out.read_text().count("\n") == 1


class _FakeResponse:
    def __init__(self, status_code=200, text='{"reviewed": []}'):
        self.status_code = status_code
        self.text = text


def test_review_pending_remote_posts_to_api(env, monkeypatch):
    """--remote goes through the API (never the local store) with the token header."""
    import httpx

    calls = {}

    def fake_post(url, *, params, headers, timeout):
        calls.update(url=url, params=params, headers=headers, timeout=timeout)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = runner.invoke(
        app,
        ["review-pending", "--remote", "http://resolute.default.svc/", "--limit", "7"],
        env={**env, "RESOLUTE_API_TOKEN": "sekrit"},
    )
    assert result.exit_code == 0, result.output
    assert calls["url"] == "http://resolute.default.svc/api/reviews/pending"
    assert calls["params"] == {"limit": 7}
    assert calls["headers"] == {"X-Resolute-Api-Token": "sekrit"}
    assert "reviewed" in result.output


def test_review_pending_remote_no_token_sends_no_header(env, monkeypatch):
    import httpx

    seen = {}

    def fake_post(url, *, params, headers, timeout):
        seen["headers"] = headers
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    env_no_token = {k: v for k, v in env.items() if k != "RESOLUTE_API_TOKEN"}
    result = runner.invoke(
        app, ["review-pending", "--remote", "http://r"], env=env_no_token
    )
    assert result.exit_code == 0, result.output
    assert seen["headers"] == {}


def test_review_pending_remote_http_error_exits_nonzero(env, monkeypatch):
    import httpx

    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResponse(status_code=503, text="down")
    )
    result = runner.invoke(app, ["review-pending", "--remote", "http://r"], env=env)
    assert result.exit_code == 1
    assert "HTTP 503" in result.output


def test_review_pending_remote_transport_error_is_sanitized(env, monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("connection refused to http://r?apikey=nope")

    monkeypatch.setattr(httpx, "post", boom)
    result = runner.invoke(app, ["review-pending", "--remote", "http://r"], env=env)
    assert result.exit_code == 1
    assert "ConnectError" in result.output
    assert "apikey" not in result.output
