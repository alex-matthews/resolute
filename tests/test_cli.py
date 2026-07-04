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
