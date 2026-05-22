"""Principal-Agent game: 3-agent task delegation with oracle verification.

Roles: principal (index 0), worker (index 1), oracle (index 2).
Phases: offer → clarify → respond → execute → verify.
The oracle independently scores the worker's deliverable, removing the
conflict-of-interest where the principal evaluates their own worker.
"""

from __future__ import annotations

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


GAME_ID = "principal-agent"

_DEFAULT_OUTCOME_LEVELS = [
    {"label": "fail", "threshold": 0, "payment": 0},
    {"label": "partial", "threshold": 50, "payment": 3},
    {"label": "success", "threshold": 80, "payment": 10},
]

_PHASE_NAMES = ["offer", "clarify", "respond", "execute", "verify"]

# Role indices
_PRINCIPAL = 0
_WORKER = 1
_ORACLE = 2


class PrincipalAgentGame(Game):
    """Principal-Agent: a principal delegates a task to a worker via an
    outcome-based contract. An independent oracle scores the deliverable.

    Roles (by agent index):
        0 = principal — posts contract, answers clarifications
        1 = worker    — asks clarifications, accepts/rejects, delivers
        2 = oracle    — scores the deliverable objectively
    """

    def __init__(
        self,
        *,
        outcome_levels: list[dict] | None = None,
        max_clarify_rounds: int = 2,
    ) -> None:
        self._outcome_levels = outcome_levels or list(_DEFAULT_OUTCOME_LEVELS)
        self._max_clarify_rounds = max_clarify_rounds

    @classmethod
    def from_params(cls, game_params: dict, agent_ids: list[str]) -> "PrincipalAgentGame":
        return cls(
            max_clarify_rounds=game_params.get("max_clarify_rounds", 2),
        )

    def get_metadata(self) -> dict:
        return {
            **super().get_metadata(),
            "outcome_levels": self._outcome_levels,
            "max_clarify_rounds": self._max_clarify_rounds,
        }

    def spec(self) -> GameSpec:
        return GameSpec(
            game_id=GAME_ID,
            name="Principal-Agent (Task Delegation, Outcome-Based Payment)",
            min_agents=3,
            max_agents=3,
            description=(
                "A PRINCIPAL delegates a task to a WORKER via an outcome-based contract. "
                "An independent ORACLE scores the deliverable against the contract's success criteria. "
                "Payment is determined solely by the observable outcome score. "
                "Phases: offer → clarify → respond → execute → verify."
            ),
            phases=[
                Phase(
                    name="offer",
                    turn_order=TurnOrder.RANDOM,
                    allowed_action_types=["post_contract", "message_only"],
                    max_rounds=2,
                ),
                Phase(
                    name="clarify",
                    turn_order=TurnOrder.ROUND_ROBIN,
                    allowed_action_types=[
                        "ask_clarification",
                        "answer_clarification",
                        "skip_clarify",
                        "accept_contract",
                        "reject_contract",
                        "message_only",
                    ],
                    max_rounds=self._max_clarify_rounds * 2 + 2,
                ),
                Phase(
                    name="respond",
                    turn_order=TurnOrder.RANDOM,
                    allowed_action_types=["accept_contract", "reject_contract", "message_only"],
                    max_rounds=2,
                ),
                Phase(
                    name="execute",
                    turn_order=TurnOrder.RANDOM,
                    allowed_action_types=["submit_deliverable", "message_only"],
                    max_rounds=6,
                ),
                Phase(
                    name="verify",
                    turn_order=TurnOrder.RANDOM,
                    allowed_action_types=["record_outcome_score", "message_only"],
                    max_rounds=2,
                ),
            ],
            action_types=[
                ActionTypeDef(
                    name="post_contract",
                    description="Post a task contract with description, success criteria, and optional outcome levels",
                    payload_schema={
                        "task_description": {"type": "string"},
                        "success_criteria": {"type": "string"},
                        "outcome_levels": {"type": "array", "description": "optional override"},
                    },
                ),
                ActionTypeDef(
                    name="ask_clarification",
                    description="Worker asks a clarifying question about the contract",
                    payload_schema={"question": {"type": "string"}},
                ),
                ActionTypeDef(
                    name="answer_clarification",
                    description="Principal answers the most recent unanswered question",
                    payload_schema={"answer": {"type": "string"}},
                ),
                ActionTypeDef(
                    name="skip_clarify",
                    description="Skip remaining clarification rounds (either party)",
                    payload_schema={},
                ),
                ActionTypeDef(
                    name="accept_contract",
                    description="Worker accepts the contract and proceeds to execute",
                    payload_schema={},
                ),
                ActionTypeDef(
                    name="reject_contract",
                    description="Worker rejects the contract (game ends, payoffs 0)",
                    payload_schema={"reason": {"type": "string"}},
                ),
                ActionTypeDef(
                    name="submit_deliverable",
                    description="Worker submits the completed deliverable",
                    payload_schema={"content": {"type": "string"}},
                ),
                ActionTypeDef(
                    name="record_outcome_score",
                    description="Oracle scores the deliverable 0-100 against success criteria",
                    payload_schema={
                        "score": {"type": "integer", "minimum": 0, "maximum": 100},
                        "notes": {"type": "string"},
                    },
                ),
                ActionTypeDef(
                    name="message_only",
                    description="Send messages without advancing the turn",
                    payload_schema={},
                ),
            ],
            outcome_rule=OutcomeRule.ENGINE,
            initial_game_state={
                "contract": None,
                "clarifications": [],
                "accepted": None,
                "deliverable": None,
                "outcome_score": None,
                "outcome_label": None,
                "payment": None,
                "action_history": [],
                "resolved": False,
            },
        )

    # ------------------------------------------------------------------
    # Phase / turn management
    # ------------------------------------------------------------------

    def _current_phase_name(self, match: Match) -> str:
        phases = match.spec.phases
        if not phases or match.current_phase_index >= len(phases):
            return ""
        return phases[match.current_phase_index].name

    def _advance_phase(self, match: Match, target_phase: str) -> None:
        """Move to *target_phase* and set current_turn_index to the role that owns it."""
        for i, ph in enumerate(match.spec.phases):
            if ph.name == target_phase:
                match.current_phase_index = i
                match.current_round = 0
                break
        # Set turn to the role that acts first in the target phase
        turn_map = {
            "offer": _PRINCIPAL,
            "clarify": _WORKER,
            "respond": _WORKER,
            "execute": _WORKER,
            "verify": _ORACLE,
        }
        match.current_turn_index = turn_map.get(target_phase, 0)

    def _resolve_score(self, match: Match, score: int) -> tuple[str, int]:
        """Map a score to (label, payment) using the contract's outcome_levels."""
        contract = match.game_state.get("contract") or {}
        levels = contract.get("outcome_levels") or self._outcome_levels
        # Pick highest threshold satisfied
        best_label = levels[0]["label"]
        best_payment = levels[0]["payment"]
        for lvl in levels:
            if score >= lvl["threshold"]:
                best_label = lvl["label"]
                best_payment = lvl["payment"]
        return best_label, best_payment

    # ------------------------------------------------------------------
    # compute_turn_state
    # ------------------------------------------------------------------

    def compute_turn_state(self, match: Match, agent_id: str) -> TurnState | None:
        if match.game_id != GAME_ID:
            return None
        if match.status != MatchStatus.RUNNING:
            return self._not_running_turn_state(match, agent_id)

        phase_name = self._current_phase_name(match)
        n = len(match.agent_ids)
        idx = match.current_turn_index
        if idx < 0 or idx >= n:
            idx = 0
        current_turn_agent_id = match.agent_ids[idx]
        is_my_turn = current_turn_agent_id == agent_id
        messages = messages_visible_to(match.messages, agent_id)
        allowed_actions = build_allowed_actions(match.spec, phase_name, is_my_turn)

        # Restrict actions based on role
        agent_index = match.agent_ids.index(agent_id) if agent_id in match.agent_ids else -1
        allowed_actions = self._filter_actions_for_role(
            allowed_actions, agent_index, phase_name, match
        )

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

    def _filter_actions_for_role(
        self, actions: list, agent_index: int, phase: str, match: Match
    ) -> list:
        """Remove actions that the agent's role cannot perform in this phase."""
        principal_actions = {"post_contract", "answer_clarification", "skip_clarify", "message_only"}
        worker_actions = {
            "ask_clarification",
            "skip_clarify",
            "accept_contract",
            "reject_contract",
            "submit_deliverable",
            "message_only",
        }
        oracle_actions = {"record_outcome_score", "message_only"}

        if agent_index == _PRINCIPAL:
            allowed_names = principal_actions
        elif agent_index == _WORKER:
            allowed_names = worker_actions
        elif agent_index == _ORACLE:
            allowed_names = oracle_actions
        else:
            allowed_names = {"message_only"}

        return [a for a in actions if a.action_type in allowed_names]

    def _visible_game_state(self, match: Match, agent_id: str) -> dict:
        """All agents see the full game state (oracle needs contract + deliverable to score)."""
        g = match.game_state
        agent_index = match.agent_ids.index(agent_id) if agent_id in match.agent_ids else -1
        role_map = {_PRINCIPAL: "principal", _WORKER: "worker", _ORACLE: "oracle"}
        return {
            "num_agents": len(match.agent_ids),
            "agent_ids": list(match.agent_ids),
            "principal": match.agent_ids[_PRINCIPAL] if len(match.agent_ids) > _PRINCIPAL else None,
            "worker": match.agent_ids[_WORKER] if len(match.agent_ids) > _WORKER else None,
            "oracle": match.agent_ids[_ORACLE] if len(match.agent_ids) > _ORACLE else None,
            "my_role": role_map.get(agent_index, "unknown"),
            "contract": g.get("contract"),
            "clarifications": g.get("clarifications", []),
            "accepted": g.get("accepted"),
            "deliverable": g.get("deliverable"),
            "outcome_score": g.get("outcome_score"),
            "outcome_label": g.get("outcome_label"),
            "payment": g.get("payment"),
            "action_history": g.get("action_history", []),
        }

    # ------------------------------------------------------------------
    # apply_action
    # ------------------------------------------------------------------

    def apply_action(self, match: Match, agent_id: str, action: Action) -> ActionResult:
        err = self._check_apply_preconditions(match, agent_id, GAME_ID)
        if err is not None:
            return err

        n = len(match.agent_ids)
        if n < 3:
            return action_error(ActionError.MATCH_NOT_RUNNING, "Need at least 3 agents")

        phase_name = self._current_phase_name(match)
        current_turn_agent_id = match.agent_ids[match.current_turn_index]
        agent_index = match.agent_ids.index(agent_id) if agent_id in match.agent_ids else -1

        # Turn check (message_only never requires turn ownership)
        if action.action_type != "message_only" and agent_id != current_turn_agent_id:
            return action_error(ActionError.NOT_YOUR_TURN, f"It is {current_turn_agent_id}'s turn")

        at = action.action_type

        if at == "post_contract":
            return self._do_post_contract(match, agent_id, agent_index, phase_name, action)
        if at == "ask_clarification":
            return self._do_ask_clarification(match, agent_id, agent_index, phase_name, action)
        if at == "answer_clarification":
            return self._do_answer_clarification(match, agent_id, agent_index, phase_name, action)
        if at == "skip_clarify":
            return self._do_skip_clarify(match, agent_id, agent_index, phase_name)
        if at == "accept_contract":
            return self._do_accept_contract(match, agent_id, agent_index, phase_name)
        if at == "reject_contract":
            return self._do_reject_contract(match, agent_id, agent_index, phase_name, action)
        if at == "submit_deliverable":
            return self._do_submit_deliverable(match, agent_id, agent_index, phase_name, action)
        if at == "record_outcome_score":
            return self._do_record_outcome_score(match, agent_id, agent_index, phase_name, action)
        if at == "message_only":
            match.game_state.setdefault("action_history", []).append(
                {"agent_id": agent_id, "action": "message_only", "phase": phase_name}
            )
            return action_ok()

        return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown action type: {at}")

    # --- Individual action handlers ---

    def _do_post_contract(
        self, match: Match, agent_id: str, agent_index: int, phase: str, action: Action
    ) -> ActionResult:
        if phase != "offer":
            return action_error(ActionError.GAME_RULE_VIOLATION, "post_contract only allowed in offer phase")
        if agent_index != _PRINCIPAL:
            return action_error(ActionError.GAME_RULE_VIOLATION, "Only the principal can post a contract")
        if match.game_state.get("contract") is not None:
            return action_error(ActionError.GAME_RULE_VIOLATION, "Contract already posted")

        task_desc = action.payload.get("task_description")
        criteria = action.payload.get("success_criteria")
        if not task_desc or not criteria:
            return action_error(ActionError.INVALID_PAYLOAD, "task_description and success_criteria are required")

        levels = action.payload.get("outcome_levels") or self._outcome_levels
        match.game_state["contract"] = {
            "task_description": task_desc,
            "success_criteria": criteria,
            "outcome_levels": levels,
        }
        match.game_state.setdefault("action_history", []).append(
            {"agent_id": agent_id, "action": "post_contract", "phase": phase}
        )
        self._advance_phase(match, "clarify")
        return action_ok()

    def _do_ask_clarification(
        self, match: Match, agent_id: str, agent_index: int, phase: str, action: Action
    ) -> ActionResult:
        if phase != "clarify":
            return action_error(ActionError.GAME_RULE_VIOLATION, "ask_clarification only in clarify phase")
        if agent_index != _WORKER:
            return action_error(ActionError.GAME_RULE_VIOLATION, "Only the worker can ask clarifications")

        clarifications = match.game_state.get("clarifications", [])
        if len(clarifications) >= self._max_clarify_rounds:
            return action_error(
                ActionError.GAME_RULE_VIOLATION,
                f"Max clarification rounds ({self._max_clarify_rounds}) reached",
            )

        question = action.payload.get("question") or action.payload.get("text")
        if not question:
            return action_error(ActionError.INVALID_PAYLOAD, "question is required")

        clarifications.append({"question": question, "answer": None})
        match.game_state["clarifications"] = clarifications
        match.game_state.setdefault("action_history", []).append(
            {"agent_id": agent_id, "action": "ask_clarification", "phase": phase}
        )
        # Switch turn to principal to answer
        match.current_turn_index = _PRINCIPAL
        return action_ok()

    def _do_answer_clarification(
        self, match: Match, agent_id: str, agent_index: int, phase: str, action: Action
    ) -> ActionResult:
        if phase != "clarify":
            return action_error(ActionError.GAME_RULE_VIOLATION, "answer_clarification only in clarify phase")
        if agent_index != _PRINCIPAL:
            return action_error(ActionError.GAME_RULE_VIOLATION, "Only the principal can answer clarifications")

        clarifications = match.game_state.get("clarifications", [])
        if not clarifications or clarifications[-1].get("answer") is not None:
            return action_error(ActionError.GAME_RULE_VIOLATION, "No unanswered question to respond to")

        answer = action.payload.get("answer") or action.payload.get("text")
        if not answer:
            return action_error(ActionError.INVALID_PAYLOAD, "answer is required")

        clarifications[-1]["answer"] = answer
        match.game_state["clarifications"] = clarifications
        match.game_state.setdefault("action_history", []).append(
            {"agent_id": agent_id, "action": "answer_clarification", "phase": phase}
        )
        # Switch turn back to worker
        match.current_turn_index = _WORKER
        return action_ok()

    def _do_skip_clarify(
        self, match: Match, agent_id: str, agent_index: int, phase: str
    ) -> ActionResult:
        if phase != "clarify":
            return action_error(ActionError.GAME_RULE_VIOLATION, "skip_clarify only in clarify phase")
        if agent_index not in (_PRINCIPAL, _WORKER):
            return action_error(ActionError.GAME_RULE_VIOLATION, "Only principal or worker can skip clarify")

        match.game_state.setdefault("action_history", []).append(
            {"agent_id": agent_id, "action": "skip_clarify", "phase": phase}
        )
        self._advance_phase(match, "respond")
        return action_ok()

    def _do_accept_contract(
        self, match: Match, agent_id: str, agent_index: int, phase: str
    ) -> ActionResult:
        if phase not in ("clarify", "respond"):
            return action_error(ActionError.GAME_RULE_VIOLATION, "accept_contract only in clarify or respond phase")
        if agent_index != _WORKER:
            return action_error(ActionError.GAME_RULE_VIOLATION, "Only the worker can accept the contract")
        if match.game_state.get("contract") is None:
            return action_error(ActionError.GAME_RULE_VIOLATION, "No contract to accept")

        match.game_state["accepted"] = True
        match.game_state.setdefault("action_history", []).append(
            {"agent_id": agent_id, "action": "accept_contract", "phase": phase}
        )
        self._advance_phase(match, "execute")
        return action_ok()

    def _do_reject_contract(
        self, match: Match, agent_id: str, agent_index: int, phase: str, action: Action
    ) -> ActionResult:
        if phase not in ("clarify", "respond"):
            return action_error(ActionError.GAME_RULE_VIOLATION, "reject_contract only in clarify or respond phase")
        if agent_index != _WORKER:
            return action_error(ActionError.GAME_RULE_VIOLATION, "Only the worker can reject the contract")

        reason = action.payload.get("reason", "")
        match.game_state["accepted"] = False
        match.game_state["resolved"] = True
        match.game_state.setdefault("action_history", []).append(
            {"agent_id": agent_id, "action": "reject_contract", "reason": reason, "phase": phase}
        )
        match.outcome = {
            "payoffs": [{"agent_id": aid, "utility": 0.0} for aid in match.agent_ids],
            "reason": "contract_rejected",
        }
        match.status = MatchStatus.FINISHED
        return action_ok()

    def _do_submit_deliverable(
        self, match: Match, agent_id: str, agent_index: int, phase: str, action: Action
    ) -> ActionResult:
        if phase != "execute":
            return action_error(ActionError.GAME_RULE_VIOLATION, "submit_deliverable only in execute phase")
        if agent_index != _WORKER:
            return action_error(ActionError.GAME_RULE_VIOLATION, "Only the worker can submit a deliverable")
        if match.game_state.get("accepted") is not True:
            return action_error(ActionError.GAME_RULE_VIOLATION, "Contract must be accepted first")
        if match.game_state.get("deliverable") is not None:
            return action_error(ActionError.GAME_RULE_VIOLATION, "Deliverable already submitted")

        content = (
            action.payload.get("content")
            or action.payload.get("text")
            or action.payload.get("deliverable")
        )
        if not content:
            return action_error(ActionError.INVALID_PAYLOAD, "content is required")

        match.game_state["deliverable"] = content
        match.game_state.setdefault("action_history", []).append(
            {"agent_id": agent_id, "action": "submit_deliverable", "phase": phase}
        )
        self._advance_phase(match, "verify")
        return action_ok()

    def _do_record_outcome_score(
        self, match: Match, agent_id: str, agent_index: int, phase: str, action: Action
    ) -> ActionResult:
        if phase != "verify":
            return action_error(ActionError.GAME_RULE_VIOLATION, "record_outcome_score only in verify phase")
        if agent_index != _ORACLE:
            return action_error(ActionError.GAME_RULE_VIOLATION, "Only the oracle can record an outcome score")
        if match.game_state.get("deliverable") is None:
            return action_error(ActionError.GAME_RULE_VIOLATION, "No deliverable to score")
        if match.game_state.get("outcome_score") is not None:
            return action_error(ActionError.GAME_RULE_VIOLATION, "Outcome score already recorded")

        score = action.payload.get("score") or action.payload.get("rating")
        if score is None:
            return action_error(ActionError.INVALID_PAYLOAD, "score is required")
        try:
            score = int(score)
        except (TypeError, ValueError):
            return action_error(ActionError.INVALID_PAYLOAD, "score must be an integer")
        if score < 0 or score > 100:
            return action_error(ActionError.INVALID_PAYLOAD, "score must be between 0 and 100")

        notes = action.payload.get("notes", "")
        label, payment = self._resolve_score(match, score)

        match.game_state["outcome_score"] = score
        match.game_state["outcome_label"] = label
        match.game_state["payment"] = payment
        match.game_state["resolved"] = True
        match.game_state.setdefault("action_history", []).append(
            {
                "agent_id": agent_id,
                "action": "record_outcome_score",
                "score": score,
                "notes": notes,
                "phase": phase,
            }
        )

        match.outcome = {
            "payoffs": [
                {"agent_id": match.agent_ids[_PRINCIPAL], "utility": -payment},
                {"agent_id": match.agent_ids[_WORKER], "utility": payment},
                {"agent_id": match.agent_ids[_ORACLE], "utility": 0.0},
            ],
            "reason": f"task_resolved_{label}",
            "outcome_score": score,
            "outcome_label": label,
            "payment": payment,
        }
        match.status = MatchStatus.FINISHED
        return action_ok()

    def compute_outcome(self, match: Match) -> dict | None:
        if match.status == MatchStatus.FINISHED and match.outcome is not None:
            return match.outcome
        return None
