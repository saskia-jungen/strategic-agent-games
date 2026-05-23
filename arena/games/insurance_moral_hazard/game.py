"""Insurance with moral hazard (hidden effort)."""

from __future__ import annotations

import random
from typing import Any

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


GAME_ID = "insurance-moral-hazard"

_INSURER = "insurer"
_INSURED = "insured"


class InsuranceMoralHazardGame(Game):
    """Insurance with moral hazard (hidden effort).

    Roles (by agent index, unless role_map overrides):
        0 = insurer
        1 = insured

    Phases: offer_contract -> accept_contract -> choose_effort
    """

    private_payload_keys: frozenset[str] = frozenset({"effort"})

    def __init__(
        self,
        *,
        base_income: float = 10,
        loss: float = 10,
        effort_cost: float = 1,
        p_good_low_effort: float = 0.6,
        p_good_high_effort: float = 0.8,
        max_rounds_offer: int = 3,
        max_rounds_accept: int = 2,
        max_rounds_effort: int = 2,
        contract: dict[str, Any] | None = None,
        role_map: dict[str, str] | None = None,
        turn_order: TurnOrder = TurnOrder.ROUND_ROBIN,
    ) -> None:
        if base_income < 0:
            raise ValueError("base_income must be >= 0")
        if loss < 0:
            raise ValueError("loss must be >= 0")
        if effort_cost < 0:
            raise ValueError("effort_cost must be >= 0")
        if not (0 <= p_good_low_effort <= 1):
            raise ValueError("p_good_low_effort must be in [0, 1]")
        if not (0 <= p_good_high_effort <= 1):
            raise ValueError("p_good_high_effort must be in [0, 1]")

        self._base_income = float(base_income)
        self._loss = float(loss)
        self._effort_cost = float(effort_cost)
        self._p_good_low_effort = float(p_good_low_effort)
        self._p_good_high_effort = float(p_good_high_effort)
        self._max_rounds_offer = max_rounds_offer
        self._max_rounds_accept = max_rounds_accept
        self._max_rounds_effort = max_rounds_effort
        self._role_map = role_map
        self._turn_order = turn_order
        self._fixed_contract = self._normalize_contract(contract) if contract is not None else None

    @classmethod
    def from_params(cls, game_params: dict, agent_ids: list[str]) -> "InsuranceMoralHazardGame":
        max_rounds = game_params.get("max_rounds")
        max_rounds_offer = game_params.get("max_rounds_offer", 3 if max_rounds is None else max_rounds)
        max_rounds_accept = game_params.get("max_rounds_accept", 2 if max_rounds is None else max_rounds)
        max_rounds_effort = game_params.get("max_rounds_effort", 2 if max_rounds is None else max_rounds)

        role_map = game_params.get("role_map")
        if isinstance(role_map, dict):
            insurer_id = role_map.get(_INSURER)
            insured_id = role_map.get(_INSURED)
            if insurer_id is not None and insurer_id not in agent_ids:
                raise ValueError("role_map.insurer must be an agent_id in the match")
            if insured_id is not None and insured_id not in agent_ids:
                raise ValueError("role_map.insured must be an agent_id in the match")
        else:
            role_map = None

        contract = game_params.get("contract")

        return cls(
            base_income=game_params.get("base_income", 10),
            loss=game_params.get("loss", 10),
            effort_cost=game_params.get("effort_cost", 1),
            p_good_low_effort=game_params.get("p_good_low_effort", 0.6),
            p_good_high_effort=game_params.get("p_good_high_effort", 0.8),
            max_rounds_offer=max_rounds_offer,
            max_rounds_accept=max_rounds_accept,
            max_rounds_effort=max_rounds_effort,
            contract=contract,
            role_map=role_map,
        )

    def get_metadata(self) -> dict:
        return {
            **super().get_metadata(),
            "base_income": self._base_income,
            "loss": self._loss,
            "effort_cost": self._effort_cost,
            "p_good_low_effort": self._p_good_low_effort,
            "p_good_high_effort": self._p_good_high_effort,
            "max_rounds_offer": self._max_rounds_offer,
            "max_rounds_accept": self._max_rounds_accept,
            "max_rounds_effort": self._max_rounds_effort,
            "contract": self._fixed_contract,
            "role_map": self._role_map,
            "turn_order": self._turn_order.value,
        }

    def spec(self) -> GameSpec:
        initial_contract = dict(self._fixed_contract) if self._fixed_contract is not None else None
        return GameSpec(
            game_id=GAME_ID,
            name="Insurance with Moral Hazard (Hidden Effort)",
            min_agents=2,
            description=(
                "Insurer offers a contract (premium, transfer_good, transfer_bad). "
                "Insured accepts or rejects. If rejected, insurer may offer again up to "
                "max_rounds_offer. If accepted, insured chooses hidden effort "
                "(low/high), then the state (good/bad) is realized stochastically. "
                "Insured utility: consumption minus effort cost. Insurer utility: premium minus transfer."
            ),
            phases=[
                Phase(
                    name="offer_contract",
                    turn_order=self._turn_order,
                    allowed_action_types=["offer", "pass", "message_only"],
                    max_rounds=self._max_rounds_offer,
                ),
                Phase(
                    name="accept_contract",
                    turn_order=self._turn_order,
                    allowed_action_types=["accept", "reject", "message_only"],
                    max_rounds=self._max_rounds_accept,
                ),
                Phase(
                    name="choose_effort",
                    turn_order=self._turn_order,
                    allowed_action_types=["choose_effort", "message_only"],
                    max_rounds=self._max_rounds_effort,
                ),
            ],
            action_types=[
                ActionTypeDef(
                    name="offer",
                    description="Offer a contract with premium and state-contingent transfers",
                    payload_schema={
                        "premium": {"type": "number"},
                        "transfer_good": {"type": "number"},
                        "transfer_bad": {"type": "number"},
                    },
                ),
                ActionTypeDef(
                    name="accept",
                    description="Accept the offered contract",
                    payload_schema={},
                ),
                ActionTypeDef(
                    name="reject",
                    description="Reject the offered contract",
                    payload_schema={},
                ),
                ActionTypeDef(
                    name="choose_effort",
                    description="Choose hidden effort level",
                    payload_schema={"effort": {"type": "string", "enum": ["low", "high"]}},
                ),
                ActionTypeDef(
                    name="pass",
                    description="Pass your turn without acting",
                    payload_schema={},
                ),
                ActionTypeDef(
                    name="message_only",
                    description="Only send messages; do not advance turn",
                    payload_schema={},
                ),
            ],
            outcome_rule=OutcomeRule.ENGINE,
            initial_game_state={
                "contract": initial_contract,
                "accepted": None,
                "effort": None,
                "state_realized": None,
                "offer_count": 0,
                "action_history": [],
                "resolved": False,
            },
        )

    # ------------------------------------------------------------------
    # Roles and phases
    # ------------------------------------------------------------------

    def _role_agent_id(self, match: Match, role: str) -> str | None:
        if self._role_map and role in self._role_map:
            return self._role_map[role]
        if role == _INSURER:
            return match.agent_ids[0] if match.agent_ids else None
        if role == _INSURED:
            return match.agent_ids[1] if len(match.agent_ids) > 1 else None
        return None

    def _role_index(self, match: Match, role: str) -> int:
        agent_id = self._role_agent_id(match, role)
        if agent_id is None:
            return 0
        try:
            return match.agent_ids.index(agent_id)
        except ValueError:
            return 0

    def _current_phase_name(self, match: Match) -> str:
        phases = match.spec.phases
        if not phases or match.current_phase_index >= len(phases):
            return ""
        return phases[match.current_phase_index].name

    def _set_phase(self, match: Match, phase_name: str) -> None:
        for i, ph in enumerate(match.spec.phases):
            if ph.name == phase_name:
                match.current_phase_index = i
                match.current_round = 0
                break
        if phase_name == "offer_contract":
            match.current_turn_index = self._role_index(match, _INSURER)
        elif phase_name in ("accept_contract", "choose_effort"):
            match.current_turn_index = self._role_index(match, _INSURED)
        else:
            match.current_turn_index = 0

    def _ensure_turn_for_phase(self, match: Match, phase_name: str) -> None:
        if phase_name == "offer_contract":
            match.current_turn_index = self._role_index(match, _INSURER)
        elif phase_name in ("accept_contract", "choose_effort"):
            match.current_turn_index = self._role_index(match, _INSURED)

    def _ensure_fixed_contract(self, match: Match) -> None:
        if self._fixed_contract is None:
            return
        if match.game_state.get("contract") is None:
            match.game_state["contract"] = dict(self._fixed_contract)

    def _sync_phase_for_state(self, match: Match) -> None:
        if match.status != MatchStatus.RUNNING:
            return
        self._ensure_fixed_contract(match)
        g = match.game_state
        if g.get("resolved"):
            return
        if g.get("accepted") is False:
            self._resolve_reject(match)
            return
        if g.get("effort") is not None and not g.get("resolved"):
            self._resolve_after_effort(match)
            return
        phase = self._current_phase_name(match)
        if g.get("contract") is not None and g.get("accepted") is None:
            if phase != "accept_contract":
                self._set_phase(match, "accept_contract")
        if g.get("accepted") is True and g.get("effort") is None:
            if phase != "choose_effort":
                self._set_phase(match, "choose_effort")
        self._ensure_turn_for_phase(match, self._current_phase_name(match))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _normalize_contract(self, contract: dict[str, Any]) -> dict[str, float]:
        if not isinstance(contract, dict):
            raise ValueError("contract must be a dict")
        required = ("premium", "transfer_good", "transfer_bad")
        out: dict[str, float] = {}
        for key in required:
            if key not in contract:
                raise ValueError(f"contract missing required field: {key}")
            try:
                out[key] = float(contract[key])
            except (TypeError, ValueError):
                raise ValueError(f"contract.{key} must be a number")
        return out

    def _parse_contract_payload(self, payload: dict[str, Any]) -> tuple[dict[str, float] | None, ActionResult | None]:
        required = ("premium", "transfer_good", "transfer_bad")
        contract: dict[str, float] = {}
        for key in required:
            if key not in payload:
                return None, action_error(ActionError.INVALID_PAYLOAD, f"{key} is required")
            try:
                contract[key] = float(payload[key])
            except (TypeError, ValueError):
                return None, action_error(ActionError.INVALID_PAYLOAD, f"{key} must be a number")
        return contract, None

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def _resolve_timeout(self, match: Match) -> None:
        match.outcome = {
            "payoffs": [{"agent_id": aid, "utility": 0.0} for aid in match.agent_ids],
            "reason": "max_rounds_exceeded",
        }
        match.game_state["resolved"] = True
        match.status = MatchStatus.FINISHED

    def _resolve_reject(self, match: Match) -> None:
        match.outcome = {
            "payoffs": [{"agent_id": aid, "utility": 0.0} for aid in match.agent_ids],
            "reason": "insurance_rejected",
        }
        match.game_state["resolved"] = True
        match.status = MatchStatus.FINISHED

    def _resolve_after_effort(self, match: Match) -> None:
        g = match.game_state
        contract = g.get("contract")
        effort = g.get("effort")
        if not contract or effort not in ("low", "high"):
            return
        p_good = self._p_good_high_effort if effort == "high" else self._p_good_low_effort
        rng = random.Random(f"{match.match_id}")
        state = "good" if rng.random() < p_good else "bad"
        g["state_realized"] = state

        transfer = contract["transfer_good"] if state == "good" else contract["transfer_bad"]
        loss_applied = self._loss if state == "bad" else 0.0
        consumption = self._base_income - loss_applied + transfer - contract["premium"]
        effort_cost = self._effort_cost if effort == "high" else 0.0
        insured_utility = consumption - effort_cost
        insurer_utility = contract["premium"] - transfer

        insured_id = self._role_agent_id(match, _INSURED)
        insurer_id = self._role_agent_id(match, _INSURER)
        payoffs = []
        if insured_id is not None:
            payoffs.append({"agent_id": insured_id, "utility": round(insured_utility, 2)})
        if insurer_id is not None:
            payoffs.append({"agent_id": insurer_id, "utility": round(insurer_utility, 2)})

        match.outcome = {
            "payoffs": payoffs,
            "reason": "insurance_resolved",
            "effort": effort,
            "state_realized": state,
            "transfer": round(transfer, 2),
            "premium": round(contract["premium"], 2),
        }
        match.game_state["resolved"] = True
        match.status = MatchStatus.FINISHED

    def _advance_round_and_check(self, match: Match) -> None:
        phase = match.spec.phases[match.current_phase_index] if match.spec.phases else None
        match.current_round += 1
        if phase and phase.max_rounds is not None and match.current_round >= phase.max_rounds:
            self._resolve_timeout(match)

    # ------------------------------------------------------------------
    # compute_turn_state
    # ------------------------------------------------------------------

    def compute_turn_state(self, match: Match, agent_id: str) -> TurnState | None:
        if match.game_id != GAME_ID:
            return None
        if match.status != MatchStatus.RUNNING:
            return self._not_running_turn_state(match, agent_id)

        self._sync_phase_for_state(match)
        if match.status != MatchStatus.RUNNING:
            return self._not_running_turn_state(match, agent_id)
        phase_name = self._current_phase_name(match)
        n = len(match.agent_ids)
        idx = match.current_turn_index
        if idx < 0 or idx >= n:
            idx = 0
        current_turn_agent_id = match.agent_ids[idx] if n > 0 else None
        is_my_turn = current_turn_agent_id == agent_id
        messages = messages_visible_to(match.messages, agent_id)
        allowed_actions = build_allowed_actions(match.spec, phase_name, is_my_turn)
        allowed_actions = self._filter_allowed_actions(allowed_actions, phase_name, match, agent_id)

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

    def _filter_allowed_actions(
        self,
        actions: list,
        phase: str,
        match: Match,
        agent_id: str,
    ) -> list:
        g = match.game_state
        insurer_id = self._role_agent_id(match, _INSURER)
        insured_id = self._role_agent_id(match, _INSURED)

        if phase == "offer_contract":
            if agent_id != insurer_id:
                return []
        if phase == "accept_contract":
            if agent_id != insured_id or g.get("contract") is None or g.get("accepted") is not None:
                return []
        if phase == "choose_effort":
            if agent_id != insured_id or g.get("accepted") is not True or g.get("effort") is not None:
                return []
        return actions

    def _visible_game_state(self, match: Match, agent_id: str) -> dict:
        g = match.game_state
        insurer_id = self._role_agent_id(match, _INSURER)
        insured_id = self._role_agent_id(match, _INSURED)
        role = "observer"
        if agent_id == insurer_id:
            role = _INSURER
        elif agent_id == insured_id:
            role = _INSURED

        state: dict[str, Any] = {
            "num_agents": len(match.agent_ids),
            "agent_ids": list(match.agent_ids),
            "insurer": insurer_id,
            "insured": insured_id,
            "my_role": role,
            "base_income": self._base_income,
            "loss": self._loss,
            "effort_cost": self._effort_cost,
            "p_good_low_effort": self._p_good_low_effort,
            "p_good_high_effort": self._p_good_high_effort,
            "contract": g.get("contract"),
            "accepted": g.get("accepted"),
            "state_realized": g.get("state_realized"),
            "offer_count": g.get("offer_count", 0),
            "action_history": g.get("action_history", []),
            "resolved": g.get("resolved", False),
        }

        if role == _INSURED:
            state["effort"] = g.get("effort")
        return state

    # ------------------------------------------------------------------
    # apply_action
    # ------------------------------------------------------------------

    def apply_action(self, match: Match, agent_id: str, action: Action) -> ActionResult:
        err = self._check_apply_preconditions(match, agent_id, GAME_ID)
        if err is not None:
            return err
        if len(match.agent_ids) < 2:
            return action_error(ActionError.MATCH_NOT_RUNNING, "Need at least 2 agents")

        self._sync_phase_for_state(match)
        if match.status != MatchStatus.RUNNING:
            return action_error(ActionError.MATCH_NOT_RUNNING, "Match is not running")
        phase_name = self._current_phase_name(match)
        current_turn_agent_id = match.agent_ids[match.current_turn_index] if match.agent_ids else None

        at = action.action_type
        if at == "message":
            at = "message_only"

        if at != "message_only" and agent_id != current_turn_agent_id:
            return action_error(ActionError.NOT_YOUR_TURN, f"It is {current_turn_agent_id}'s turn")

        if phase_name == "offer_contract":
            return self._apply_offer_phase(match, agent_id, at, action)
        if phase_name == "accept_contract":
            return self._apply_accept_phase(match, agent_id, at, action)
        if phase_name == "choose_effort":
            return self._apply_effort_phase(match, agent_id, at, action)

        return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown phase: {phase_name}")

    def _apply_offer_phase(
        self,
        match: Match,
        agent_id: str,
        action_type: str,
        action: Action,
    ) -> ActionResult:
        if agent_id != self._role_agent_id(match, _INSURER):
            return action_error(ActionError.GAME_RULE_VIOLATION, "Only insurer can offer a contract")

        if action_type == "offer":
            contract, err = self._parse_contract_payload(action.payload)
            if err is not None:
                return err
            match.game_state["contract"] = contract
            match.game_state["offer_count"] = match.game_state.get("offer_count", 0) + 1
            match.game_state.setdefault("action_history", []).append(
                {"agent_id": agent_id, "action": "offer", "phase": "offer_contract"}
            )
            self._set_phase(match, "accept_contract")
            return action_ok()

        if action_type == "pass":
            match.game_state.setdefault("action_history", []).append(
                {"agent_id": agent_id, "action": "pass", "phase": "offer_contract"}
            )
            self._advance_round_and_check(match)
            return action_ok()

        if action_type == "message_only":
            match.game_state.setdefault("action_history", []).append(
                {"agent_id": agent_id, "action": "message_only", "phase": "offer_contract"}
            )
            return action_ok()

        return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown action type: {action_type}")

    def _apply_accept_phase(
        self,
        match: Match,
        agent_id: str,
        action_type: str,
        action: Action,
    ) -> ActionResult:
        if agent_id != self._role_agent_id(match, _INSURED):
            return action_error(ActionError.GAME_RULE_VIOLATION, "Only insured can accept or reject")
        if match.game_state.get("contract") is None:
            return action_error(ActionError.GAME_RULE_VIOLATION, "No contract to accept")

        if action_type == "accept":
            match.game_state["accepted"] = True
            match.game_state.setdefault("action_history", []).append(
                {"agent_id": agent_id, "action": "accept", "phase": "accept_contract"}
            )
            self._set_phase(match, "choose_effort")
            return action_ok()

        if action_type == "reject":
            match.game_state.setdefault("action_history", []).append(
                {"agent_id": agent_id, "action": "reject", "phase": "accept_contract"}
            )
            offer_count = match.game_state.get("offer_count", 0)
            if self._fixed_contract is not None or offer_count >= self._max_rounds_offer:
                match.game_state["accepted"] = False
                self._resolve_reject(match)
            else:
                match.game_state["accepted"] = None
                match.game_state["contract"] = None
                self._set_phase(match, "offer_contract")
            return action_ok()

        if action_type == "message_only":
            match.game_state.setdefault("action_history", []).append(
                {"agent_id": agent_id, "action": "message_only", "phase": "accept_contract"}
            )
            return action_ok()

        return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown action type: {action_type}")

    def _apply_effort_phase(
        self,
        match: Match,
        agent_id: str,
        action_type: str,
        action: Action,
    ) -> ActionResult:
        if agent_id != self._role_agent_id(match, _INSURED):
            return action_error(ActionError.GAME_RULE_VIOLATION, "Only insured can choose effort")
        if match.game_state.get("accepted") is not True:
            return action_error(ActionError.GAME_RULE_VIOLATION, "Contract must be accepted first")

        if action_type == "choose_effort":
            effort = action.payload.get("effort")
            if effort not in ("low", "high"):
                return action_error(ActionError.INVALID_PAYLOAD, "effort must be 'low' or 'high'")
            match.game_state["effort"] = effort
            match.game_state.setdefault("action_history", []).append(
                {"agent_id": agent_id, "action": "choose_effort", "phase": "choose_effort"}
            )
            self._resolve_after_effort(match)
            return action_ok()

        if action_type == "message_only":
            match.game_state.setdefault("action_history", []).append(
                {"agent_id": agent_id, "action": "message_only", "phase": "choose_effort"}
            )
            return action_ok()

        return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown action type: {action_type}")

    def compute_outcome(self, match: Match) -> dict | None:
        if match.status == MatchStatus.FINISHED and match.outcome is not None:
            return match.outcome
        return None
