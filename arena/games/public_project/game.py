from __future__ import annotations

import random

from arena.core.match import Match, MatchStatus
from arena.spec import ActionTypeDef, GameSpec, OutcomeRule, Phase, TurnOrder
from arena.types import (
    Action,
    ActionError,
    ActionResult,
    TurnState,
    action_error,
    action_ok,
)

from arena.games.base import Game
from arena.games.utils import messages_visible_to, build_allowed_actions


GAME_ID = "public-project"


class PublicProjectGame(Game):
    """Public Project Game:
    Each agent has a private valuation for a public project of cost C.
    Agents report valuations (reports need not be truthful).
    If sum of reports >= C, the project is built and cost is shared equally.
    Payoffs use TRUE valuations: if built, agent gets (true_valuation - cost/n); else 0.
    """

    def __init__(
        self,
        *,
        project_cost: float = 100,
        valuation_range: tuple[float, float] = (0, 100),
        valuation_mode: str = "random",
        valuations: dict[str, float] | None = None,
        cost_sharing: str = "equal",
        turn_order: TurnOrder = TurnOrder.ROUND_ROBIN,
        max_rounds: int = 10,
        negotiation_rounds: int = 2,
    ) -> None:
        if valuation_mode not in ("random", "fixed"):
            raise ValueError(f"valuation_mode must be 'random' or 'fixed', got {valuation_mode!r}")
        if valuation_mode == "fixed" and not valuations:
            raise ValueError("valuations dict required when valuation_mode='fixed'")
        if cost_sharing != "equal":
            raise ValueError("public-project currently supports only cost_sharing='equal'")
        if negotiation_rounds < 0:
            raise ValueError("negotiation_rounds must be >= 0")

        self._project_cost = project_cost
        self._valuation_range = valuation_range
        self._valuation_mode = valuation_mode
        self._fixed_valuations = valuations
        self._cost_sharing = cost_sharing
        self._turn_order = turn_order
        self._max_rounds = max_rounds
        self._negotiation_rounds = negotiation_rounds

    @classmethod
    def from_params(cls, game_params: dict, agent_ids: list[str]) -> "PublicProjectGame":
        project_cost = game_params.get("project_cost", 100)
        valuation_range = game_params.get("valuation_range", (0, 100))
        if isinstance(valuation_range, list):
            valuation_range = tuple(valuation_range)
        valuations = game_params.get("valuations")
        cost_sharing = game_params.get("cost_sharing", "equal")
        negotiation_rounds = game_params.get("negotiation_rounds", 2)
        valuation_mode = "fixed" if valuations else "random"
        return cls(
            project_cost=project_cost,
            valuation_range=valuation_range,
            valuation_mode=valuation_mode,
            valuations=valuations,
            cost_sharing=cost_sharing,
            negotiation_rounds=negotiation_rounds,
        )

    def get_metadata(self) -> dict:
        return {
            **super().get_metadata(),
            "project_cost": self._project_cost,
            "valuation_range": list(self._valuation_range),
            "valuation_mode": self._valuation_mode,
            "valuations": self._fixed_valuations,
            "cost_sharing": self._cost_sharing,
            "turn_order": self._turn_order.value,
            "max_rounds": self._max_rounds,
            "negotiation_rounds": self._negotiation_rounds,
        }

    def spec(self) -> GameSpec:
        return GameSpec(
            game_id=GAME_ID,
            name="Public Project",
            min_agents=2,
            description=(
                "Each agent has a private valuation for a public project. "
                f"Agents may negotiate for {self._negotiation_rounds} round(s) before reporting values. "
                "Agents report valuations (reports need not be truthful). "
                f"If sum of reports >= {self._project_cost}, the project is built "
                "and cost is shared equally. "
                "Payoffs use true valuations: if built, agent gets (true_valuation - cost/n); else 0."
            ),
            phases=[
                Phase(
                    name="negotiation",
                    turn_order=self._turn_order,
                    allowed_action_types=["message_only", "pass"],
                    max_rounds=self._negotiation_rounds,
                ),
                Phase(
                    name="report",
                    turn_order=self._turn_order,
                    allowed_action_types=["report_value", "pass"],
                    max_rounds=self._max_rounds,
                ),
            ],
            action_types=[
                ActionTypeDef(
                    name="report_value",
                    description="Report your valuation for the public project",
                    payload_schema={"report": {"type": "number", "minimum": 0}},
                ),
                ActionTypeDef(
                    name="pass",
                    description="Pass your turn without reporting",
                    payload_schema={},
                ),
                ActionTypeDef(
                    name="message_only",
                    description="Send messages during negotiation",
                    payload_schema={},
                ),
            ],
            outcome_rule=OutcomeRule.ENGINE,
            initial_game_state={
                "reports": {},
                "passes": {},
                "valuations": {},
                "built": None,
                "action_history": [],
                "resolved": False,
                "negotiation_complete": False,
            },
        )

    # ------------------------------------------------------------------
    # Phase / turn management
    # ------------------------------------------------------------------

    def _current_phase_name(self, match: Match) -> str:
        """Get the name of the current phase."""
        phases = match.spec.phases
        if not phases or match.current_phase_index >= len(phases):
            return "report"
        return phases[match.current_phase_index].name

    def _advance_phase(self, match: Match, target_phase: str) -> None:
        for i, ph in enumerate(match.spec.phases):
            if ph.name == target_phase:
                match.current_phase_index = i
                match.current_round = 0
                match.current_turn_index = 0
                break

    def _advance_negotiation_turn(self, match: Match) -> None:
        n = len(match.agent_ids)
        if n <= 1:
            return
        match.current_turn_index = (match.current_turn_index + 1) % n
        if match.current_turn_index == 0:
            match.current_round += 1
        phase = match.spec.phases[match.current_phase_index]
        if phase.max_rounds is not None and match.current_round >= phase.max_rounds:
            match.game_state["negotiation_complete"] = True
            self._advance_phase(match, "report")

    def _ensure_valuations(self, match: Match) -> None:
        """Ensure that valuations are initialized in the game state."""
        if match.game_state.get("valuations") is not None and match.game_state["valuations"]:
            return
        if self._valuation_mode == "fixed":
            match.game_state["valuations"] = dict(self._fixed_valuations)  # type: ignore[arg-type]
        else:  # random
            low, high = self._valuation_range
            rng = random.Random(f"{match.match_id}")
            vals = {}
            for aid in match.agent_ids:
                vals[aid] = round(rng.uniform(low, high), 2)
            match.game_state["valuations"] = vals

    # ------------------------------------------------------------------
    # Turn management
    # ------------------------------------------------------------------

    def _agent_has_responded(self, match: Match, agent_id: str) -> bool:
        reports = match.game_state.get("reports", {})
        passes = match.game_state.get("passes", {})
        return agent_id in reports or agent_id in passes

    def _advance_turn(self, match: Match) -> None:
        """Advance turn to next agent, check for resolution."""
        n = len(match.agent_ids)
        if n <= 1:
            return

        reports = match.game_state.get("reports", {})
        passes = match.game_state.get("passes", {})
        
        # Check if all agents have responded (report or pass) → resolve
        if len(reports) + len(passes) >= n:
            self._resolve(match, trigger="all_responded")
            return

        # Advance turn to next agent who has not responded yet
        idx = match.current_turn_index
        advanced = False
        for _ in range(n):
            idx = (idx + 1) % n
            if idx == 0:
                match.current_round += 1
            next_agent_id = match.agent_ids[idx]
            if not self._agent_has_responded(match, next_agent_id):
                match.current_turn_index = idx
                advanced = True
                break
        if not advanced:
            self._resolve(match, trigger="all_responded")
            return
        
        # Check round limit → resolve
        phase = match.spec.phases[match.current_phase_index]
        if phase.max_rounds is not None and match.current_round >= phase.max_rounds:
            self._resolve_timeout(match)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def _resolve_timeout(self, match: Match) -> None:
        payoffs = [{"agent_id": aid, "utility": 0.0} for aid in match.agent_ids]
        match.outcome = {
            "payoffs": payoffs,
            "reason": "max_rounds_exceeded",
            "total_reported": sum(match.game_state.get("reports", {}).values()),
            "project_cost": self._project_cost,
            "built": False,
        }
        match.game_state["built"] = False
        match.game_state["resolved"] = True
        match.status = MatchStatus.FINISHED

    def _resolve(self, match: Match, *, trigger: str = "unknown") -> None:
        self._ensure_valuations(match)
        reports = match.game_state.get("reports", {})
        total_reports = sum(reports.values())
        built = total_reports >= self._project_cost
        match.game_state["built"] = built
        valuations = match.game_state.get("valuations", {})
        
        n = len(match.agent_ids)
        cost_per_agent = self._project_cost / n if n > 0 else 0
        
        payoffs = []
        for aid in match.agent_ids:
            if built:
                utility = valuations.get(aid, 0) - cost_per_agent
            else:
                utility = 0.0
            payoffs.append({"agent_id": aid, "utility": round(utility, 2)})

        match.outcome = {
            "payoffs": payoffs,
            "reason": "public_project_built" if built else "public_project_not_built",
            "trigger": trigger,
            "total_reported": total_reports,
            "project_cost": self._project_cost,
            "built": built,
        }
        match.game_state["resolved"] = True
        match.status = MatchStatus.FINISHED

    # ------------------------------------------------------------------
    # compute_turn_state
    # ------------------------------------------------------------------

    def compute_turn_state(self, match: Match, agent_id: str) -> TurnState | None:
        if match.game_id != GAME_ID:
            return None
        if match.status != MatchStatus.RUNNING:
            return self._not_running_turn_state(match, agent_id)

        self._ensure_valuations(match)
        phase_name = self._current_phase_name(match)
        n = len(match.agent_ids)
        idx = match.current_turn_index
        if idx < 0 or idx >= n:
            idx = 0
        current_turn_agent_id = match.agent_ids[idx]
        is_my_turn = current_turn_agent_id == agent_id
        messages = messages_visible_to(match.messages, agent_id)
        allowed_actions = build_allowed_actions(match.spec, phase_name, is_my_turn)

        # Filter actions: if agent has already reported or passed, no further actions in report phase
        if phase_name == "report":
            reports = match.game_state.get("reports", {})
            passes = match.game_state.get("passes", {})
            if agent_id in reports or agent_id in passes:
                allowed_actions = []

        return TurnState(
            match_id=match.match_id,
            game_id=match.game_id,
            agent_id=agent_id,
            phase=phase_name,
            is_my_turn=is_my_turn,
            current_turn_agent_id=current_turn_agent_id,
            game_state=self._visible_game_state(match, agent_id),
            messages=messages,
            allowed_actions=allowed_actions,
            game_over=(match.status == MatchStatus.FINISHED),
            outcome=match.outcome,
        )

    def _visible_game_state(self, match: Match, agent_id: str) -> dict:
        g = match.game_state
        state: dict = {
            "num_agents": len(match.agent_ids),
            "agent_ids": list(match.agent_ids),
            "reports": g.get("reports", {}),
            "total_reported": sum(g.get("reports", {}).values()),
            "project_cost": self._project_cost,
            "built": g.get("built"),
            "action_history": g.get("action_history", []),
        }
        valuations = g.get("valuations", {})
        if agent_id in valuations:
            state["my_valuation"] = valuations[agent_id]
        return state

    # ------------------------------------------------------------------
    # apply_action
    # ------------------------------------------------------------------

    def apply_action(self, match: Match, agent_id: str, action: Action) -> ActionResult:
        err = self._check_apply_preconditions(match, agent_id, GAME_ID)
        if err is not None:
            return err

        phase_name = self._current_phase_name(match)
        current_turn_agent_id = match.agent_ids[match.current_turn_index]
        at = action.action_type

        if at == "message":
            at = "message_only"

        if at != "message_only" and agent_id != current_turn_agent_id:
            return action_error(ActionError.NOT_YOUR_TURN, f"It is {current_turn_agent_id}'s turn")

        if phase_name == "negotiation":
            if at not in ("message_only", "pass"):
                return action_error(
                    ActionError.GAME_RULE_VIOLATION,
                    f"Only message_only or pass is allowed in negotiation phase, got {at}",
                )
            match.game_state.setdefault("action_history", []).append(
                {"agent_id": agent_id, "action": at, "phase": phase_name}
            )
            self._advance_negotiation_turn(match)
            return action_ok()

        if at == "report_value":
            return self._do_report(match, agent_id, phase_name, action)
        if at == "pass":
            return self._do_pass(match, agent_id, phase_name)
        if at == "message_only":
            return action_error(
                ActionError.GAME_RULE_VIOLATION, "message_only is not allowed in report phase"
            )
        return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown action type: {at}")

    def _do_report(
        self, match: Match, agent_id: str, phase: str, action: Action
    ) -> ActionResult:
        if phase != "report":
            return action_error(ActionError.GAME_RULE_VIOLATION, "report_value only allowed in report phase")
        
        # Prevent agent from reporting a new value if they have already reported
        if self._agent_has_responded(match, agent_id):
            return action_error(ActionError.GAME_RULE_VIOLATION, "Already responded")

        report = action.payload.get("report")
        if report is None:
            return action_error(ActionError.INVALID_PAYLOAD, "report is required")
        try:
            report = float(report)
        except (TypeError, ValueError):
            return action_error(ActionError.INVALID_PAYLOAD, "report must be a number")
        if report < 0:
            return action_error(ActionError.INVALID_PAYLOAD, "report must be >= 0")

        match.game_state.setdefault("reports", {})[agent_id] = report
        match.game_state.setdefault("action_history", []).append(
            {"agent_id": agent_id, "action": "report_value", "report": report, "phase": phase}
        )
        self._advance_turn(match)
        return action_ok()

    def _do_pass(self, match: Match, agent_id: str, phase: str) -> ActionResult:
        if phase != "report":
            return action_error(ActionError.GAME_RULE_VIOLATION, "pass only allowed in report phase")
        if self._agent_has_responded(match, agent_id):
            return action_error(ActionError.GAME_RULE_VIOLATION, "Already responded")
        match.game_state.setdefault("passes", {})[agent_id] = True
        match.game_state.setdefault("action_history", []).append(
            {"agent_id": agent_id, "action": "pass", "phase": phase}
        )
        self._advance_turn(match)
        return action_ok()

    def compute_outcome(self, match: Match) -> dict | None:
        if match.status == MatchStatus.FINISHED and match.outcome is not None:
            return match.outcome
        return None