from resolute.schemas import DecisionRequest, FeedbackIn, FeedbackVerdict


def _decision(engine, title="Severance", tmdb_id=95396):
    return engine.decide(DecisionRequest(title=title, tmdb_id=tmdb_id))


def test_decision_roundtrip(engine, store):
    decision = _decision(engine)
    store.save_decision(decision)
    loaded = store.get_decision(decision.decision_id)
    assert loaded is not None
    assert loaded == decision


def test_last_and_list(engine, store):
    first = _decision(engine)
    second = _decision(engine, "Friends", 1668)
    store.save_decision(first)
    store.save_decision(second)
    assert store.last_decision().decision_id == second.decision_id
    assert [d.decision_id for d in store.list_decisions()] == [
        second.decision_id,
        first.decision_id,
    ]


def test_feedback_and_calibration_summary(engine, store):
    decision = _decision(engine)
    store.save_decision(decision)
    store.save_feedback(
        FeedbackIn(decision_id=decision.decision_id, verdict=FeedbackVerdict.AGREE)
    )
    store.save_feedback(
        FeedbackIn(
            decision_id=decision.decision_id,
            verdict=FeedbackVerdict.PREFER_1080P,
            reason_tag="storage",
            comment="pool is filling up",
        )
    )
    summary = store.calibration_summary()
    assert summary["decisions"] == 1
    assert summary["feedback"] == 2
    assert summary["agreement_rate"] == 0.5
    assert summary["override_reason_tags"] == {"storage": 1}

    overrides = store.overrides()
    assert len(overrides) == 1
    assert overrides[0]["verdict"] == "prefer_1080p"
    assert overrides[0]["title"] == "Severance"


def test_webhook_event_and_execution_records(engine, store):
    decision = _decision(engine)
    store.save_decision(decision)
    event_id = store.save_webhook_event(
        {"notification_type": "MEDIA_PENDING"}, "decided", decision.decision_id
    )
    assert event_id
    store.mark_executed(decision.decision_id, ["approve_seerr_request"], operator="alex")


def test_export_jsonl(engine, store, tmp_path):
    store.save_decision(_decision(engine))
    store.save_decision(_decision(engine, "Friends", 1668))
    out = tmp_path / "out" / "decisions.jsonl"
    assert store.export_jsonl(out) == 2
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 2
