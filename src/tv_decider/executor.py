"""Mode-gated executor: the only code path that writes to Seerr or Sonarr.

Write matrix (all rows also require settings.allow_writes=True):

  mode          set Seerr profile          approve Seerr request      Sonarr fallback
  shadow        never                      never                      never
  recommend     never                      never                      never
  approve       operator_approved only     operator_approved only     operator_approved only
  auto_profile  automatic                  never                      never
  auto_approve  automatic                  automatic (needs           never
                                           auto_approve_enabled)

Holds, audits, and insufficient-metadata actions are never "executed" — they
are signals for humans or the audit loop.
"""

from __future__ import annotations

import logging

from .config import Settings
from .schemas import Action, ActionType, AutomationMode, Confidence, Decision
from .seerr.client import RequestNotPendingError, SeerrClient
from .sonarr.client import SonarrClient

logger = logging.getLogger(__name__)

_SEERR_PROFILE_ACTIONS = {
    ActionType.SET_SEERR_REQUEST_PROFILE_1080P,
    ActionType.SET_SEERR_REQUEST_PROFILE_2160P,
}
_SONARR_FALLBACK_ACTIONS = {
    ActionType.FALLBACK_SET_SONARR_PROFILE_1080P,
    ActionType.FALLBACK_SET_SONARR_PROFILE_2160P,
}


class ExecutionBlocked(Exception):
    pass


class Executor:
    def __init__(
        self,
        settings: Settings,
        seerr: SeerrClient | None = None,
        sonarr: SonarrClient | None = None,
    ) -> None:
        self.settings = settings
        self.seerr = seerr
        self.sonarr = sonarr

    def _may_execute(self, action: Action, mode: AutomationMode, operator_approved: bool) -> bool:
        if not action.is_write:
            return False
        if not self.settings.allow_writes:
            return False
        if mode in (AutomationMode.SHADOW, AutomationMode.RECOMMEND):
            return False
        if mode is AutomationMode.APPROVE:
            return operator_approved
        # auto modes
        if action.type in _SEERR_PROFILE_ACTIONS:
            return True
        if action.type is ActionType.APPROVE_SEERR_REQUEST:
            return (
                mode is AutomationMode.AUTO_APPROVE and self.settings.auto_approve_enabled
            )
        if action.type in _SONARR_FALLBACK_ACTIONS:
            return operator_approved  # fallback is never automatic in v1
        return False

    def execute(self, decision: Decision, *, operator_approved: bool = False) -> list[ActionType]:
        """Execute the eligible write actions of a decision. Returns what ran."""
        mode = decision.mode
        if any(
            a.type
            in (
                ActionType.HOLD_FOR_MANUAL_REVIEW,
                ActionType.HOLD_SEERR_REQUEST_FOR_MANUAL_REVIEW,
                ActionType.INSUFFICIENT_METADATA,
            )
            for a in decision.action_plan
        ):
            raise ExecutionBlocked("decision is held for manual review; nothing to execute")
        if decision.confidence == Confidence.LOW:
            raise ExecutionBlocked("refusing to execute a low-confidence decision")

        executed: list[ActionType] = []
        for action in decision.action_plan:
            if not self._may_execute(action, mode, operator_approved):
                continue
            try:
                self._run(action, decision)
            except RequestNotPendingError as exc:
                # The request state changed between decision and execution
                # (e.g. a human approved it in Seerr). Stop; audit will catch drift.
                raise ExecutionBlocked(str(exc)) from exc
            executed.append(action.type)
        return executed

    def _run(self, action: Action, decision: Decision) -> None:
        if action.type in _SEERR_PROFILE_ACTIONS:
            if self.seerr is None:
                raise ExecutionBlocked("no Seerr client configured")
            request_id = int(action.params["seerr_request_id"])  # type: ignore[arg-type]
            profile_name = str(action.params["profile_name"])
            profile_id = self.seerr.resolve_profile_id(profile_name)
            seasons = decision.seasons or None
            self.seerr.update_request_profile(request_id, profile_id, seasons)
            logger.info(
                "seerr request %s profile set to '%s' (id=%s)",
                request_id,
                profile_name,
                profile_id,
            )
        elif action.type is ActionType.APPROVE_SEERR_REQUEST:
            if self.seerr is None:
                raise ExecutionBlocked("no Seerr client configured")
            request_id = int(action.params["seerr_request_id"])  # type: ignore[arg-type]
            self.seerr.assert_pending(request_id)
            self.seerr.approve_request(request_id)
            logger.info("seerr request %s approved", request_id)
        elif action.type in _SONARR_FALLBACK_ACTIONS:
            if self.sonarr is None:
                raise ExecutionBlocked("no Sonarr client configured")
            series_id = int(action.params["sonarr_series_id"])  # type: ignore[arg-type]
            profile_name = str(action.params["profile_name"])
            profile_id = self.sonarr.resolve_profile_id(profile_name)
            self.sonarr.update_series_profile(series_id, profile_id)
            logger.info(
                "sonarr series %s profile corrected to '%s' (id=%s)",
                series_id,
                profile_name,
                profile_id,
            )
        else:  # pragma: no cover - _may_execute filters everything else
            raise ExecutionBlocked(f"action {action.type} is not executable")
