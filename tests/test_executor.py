import pytest

from tv_decider.executor import ExecutionBlocked, Executor
from tv_decider.schemas import ActionType, AutomationMode, DecisionRequest
from tv_decider.seerr.client import RequestNotPendingError


class FakeSeerr:
    """Mirrors the real client's contract: writes refuse non-pending requests."""

    def __init__(self, status: int = 1):
        self.status = status
        self.profile_updates: list[tuple] = []
        self.approvals: list[int] = []

    def resolve_profile_id(self, name, sonarr_id=None):
        return {"HD-1080p": 6, "Ultra-HD": 5}[name]

    def get_request(self, request_id):
        return {"id": request_id, "status": self.status, "seasons": [{"seasonNumber": 1}]}

    def assert_pending(self, request_id):
        request = self.get_request(request_id)
        if request["status"] != 1:
            raise RequestNotPendingError(f"request {request_id} is not pending")
        return request

    def update_request_profile(self, request_id, profile_id, seasons=None):
        self.assert_pending(request_id)
        self.profile_updates.append((request_id, profile_id, seasons))
        return {}

    def approve_request(self, request_id):
        self.approvals.append(request_id)
        return {}


class FakeSonarr:
    def __init__(self):
        self.profile_updates: list[tuple] = []

    def resolve_profile_id(self, name):
        return {"HD-1080p": 6, "Ultra-HD": 5}[name]

    def update_series_profile(self, series_id, profile_id):
        self.profile_updates.append((series_id, profile_id))
        return {}


@pytest.fixture
def seerr_decision(settings, policy, evidence_source):
    """Decision for a pending Seerr request, built by the real engine."""
    from tv_decider.engine.engine import DecisionEngine
    from tv_decider.schemas import EvidenceBundle, SeerrRequestState

    class RequestEvidenceSource:
        def collect(self, request):
            bundle: EvidenceBundle = evidence_source.collect(request)
            bundle.seerr_request = SeerrRequestState(request_id=123, status="pending")
            return bundle

    def make(mode: AutomationMode, engine_settings=settings):
        engine = DecisionEngine(engine_settings, policy, RequestEvidenceSource())
        return engine.decide(
            DecisionRequest(title="Severance", tmdb_id=95396, seasons=[1]), mode
        )

    return make


def _executor(mode, *, allow_writes, auto_approve_enabled=False, seerr=None):
    from tv_decider.config import Settings

    s = Settings(
        mode=mode, allow_writes=allow_writes, auto_approve_enabled=auto_approve_enabled
    )
    return Executor(s, seerr=seerr or FakeSeerr(), sonarr=FakeSonarr()), s


def test_shadow_mode_never_writes(seerr_decision):
    executor, _ = _executor(AutomationMode.SHADOW, allow_writes=True)
    decision = seerr_decision(AutomationMode.SHADOW)
    executed = executor.execute(decision, operator_approved=True)
    assert executed == []
    assert executor.seerr.profile_updates == []
    assert executor.seerr.approvals == []


def test_recommend_mode_never_writes(seerr_decision):
    executor, _ = _executor(AutomationMode.RECOMMEND, allow_writes=True)
    executed = executor.execute(seerr_decision(AutomationMode.RECOMMEND), operator_approved=True)
    assert executed == []


def test_allow_writes_master_switch_blocks_auto_modes(seerr_decision):
    executor, _ = _executor(AutomationMode.AUTO_PROFILE, allow_writes=False)
    executed = executor.execute(seerr_decision(AutomationMode.AUTO_PROFILE))
    assert executed == []
    assert executor.seerr.profile_updates == []


def test_approve_mode_requires_operator(seerr_decision):
    executor, _ = _executor(AutomationMode.APPROVE, allow_writes=True)
    decision = seerr_decision(AutomationMode.APPROVE)
    assert executor.execute(decision) == []  # no operator approval -> nothing runs
    executed = executor.execute(decision, operator_approved=True)
    assert ActionType.SET_SEERR_REQUEST_PROFILE_2160P in executed
    assert ActionType.APPROVE_SEERR_REQUEST in executed
    assert executor.seerr.profile_updates == [(123, 5, [1])]
    assert executor.seerr.approvals == [123]


def test_auto_profile_sets_profile_but_never_approves(seerr_decision):
    executor, _ = _executor(AutomationMode.AUTO_PROFILE, allow_writes=True)
    executed = executor.execute(seerr_decision(AutomationMode.AUTO_PROFILE))
    assert executed == [ActionType.SET_SEERR_REQUEST_PROFILE_2160P]
    assert executor.seerr.approvals == []


def test_auto_approve_requires_explicit_opt_in(seerr_decision):
    executor, _ = _executor(
        AutomationMode.AUTO_APPROVE, allow_writes=True, auto_approve_enabled=False
    )
    executed = executor.execute(seerr_decision(AutomationMode.AUTO_APPROVE))
    assert executed == [ActionType.SET_SEERR_REQUEST_PROFILE_2160P]
    assert executor.seerr.approvals == []

    executor, _ = _executor(
        AutomationMode.AUTO_APPROVE, allow_writes=True, auto_approve_enabled=True
    )
    executed = executor.execute(seerr_decision(AutomationMode.AUTO_APPROVE))
    assert ActionType.APPROVE_SEERR_REQUEST in executed
    assert executor.seerr.approvals == [123]


def test_request_no_longer_pending_is_blocked(seerr_decision):
    # A human approved the request in Seerr between decision and execution.
    executor, _ = _executor(
        AutomationMode.APPROVE, allow_writes=True, seerr=FakeSeerr(status=2)
    )
    decision = seerr_decision(AutomationMode.APPROVE)
    with pytest.raises(ExecutionBlocked, match="not pending"):
        executor.execute(decision, operator_approved=True)
    assert executor.seerr.profile_updates == []
    assert executor.seerr.approvals == []


def test_low_confidence_decision_is_blocked(seerr_decision):
    executor, _ = _executor(AutomationMode.APPROVE, allow_writes=True)
    decision = seerr_decision(AutomationMode.APPROVE)
    blocked = decision.model_copy(update={"confidence": "low"})
    with pytest.raises(ExecutionBlocked, match="low-confidence"):
        executor.execute(blocked, operator_approved=True)


def test_held_decision_is_blocked(settings, policy, evidence_source):
    from tv_decider.engine.engine import DecisionEngine

    engine = DecisionEngine(settings, policy, evidence_source)
    decision = engine.decide(
        DecisionRequest(title="The Bear", tmdb_id=136315), AutomationMode.APPROVE
    )
    assert any("hold" in a.type for a in decision.action_plan)
    executor, _ = _executor(AutomationMode.APPROVE, allow_writes=True)
    with pytest.raises(ExecutionBlocked, match="manual review"):
        executor.execute(decision, operator_approved=True)
