"""Trust game: trustor sends, trustee returns, with negotiation first."""

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


GAME_ID = "trust"

_TRUSTOR = "trustor"
_TRUSTEE = "trustee"


class TrustGame(Game):
	"""Trust: trustor sends x, trustee returns r, with a negotiation phase first."""

	def __init__(
		self,
		*,
		endowment: float = 10,
		multiplier: float = 3,
		max_rounds_send: int = 3,
		max_rounds_return: int = 3,
		negotiation_rounds: int = 2,
		role_map: dict[str, str] | None = None,
		turn_order: TurnOrder = TurnOrder.ROUND_ROBIN,
	) -> None:
		if endowment < 0:
			raise ValueError("endowment must be >= 0")
		if multiplier <= 0:
			raise ValueError("multiplier must be > 0")
		if max_rounds_send < 1 or max_rounds_return < 1:
			raise ValueError("max_rounds_send and max_rounds_return must be >= 1")
		if negotiation_rounds < 0:
			raise ValueError("negotiation_rounds must be >= 0")

		self._endowment = endowment
		self._multiplier = multiplier
		self._max_rounds_send = max_rounds_send
		self._max_rounds_return = max_rounds_return
		self._negotiation_rounds = negotiation_rounds
		self._role_map = role_map
		self._turn_order = turn_order

	@classmethod
	def from_params(cls, game_params: dict, agent_ids: list[str]) -> "TrustGame":
		endowment = game_params.get("endowment", 10)
		multiplier = game_params.get("multiplier", 3)
		negotiation_rounds = game_params.get("negotiation_rounds", 2)
		max_rounds = game_params.get("max_rounds")
		max_rounds_send = game_params.get("max_rounds_send", max_rounds if max_rounds is not None else 3)
		max_rounds_return = game_params.get("max_rounds_return", max_rounds if max_rounds is not None else 3)
		role_map = game_params.get("role_map")
		if isinstance(role_map, dict):
			trustor_id = role_map.get(_TRUSTOR)
			trustee_id = role_map.get(_TRUSTEE)
			if trustor_id is not None and trustor_id not in agent_ids:
				raise ValueError("role_map.trustor must be an agent_id in the match")
			if trustee_id is not None and trustee_id not in agent_ids:
				raise ValueError("role_map.trustee must be an agent_id in the match")
		else:
			role_map = None
		return cls(
			endowment=endowment,
			multiplier=multiplier,
			max_rounds_send=max_rounds_send,
			max_rounds_return=max_rounds_return,
			negotiation_rounds=negotiation_rounds,
			role_map=role_map,
		)

	def get_metadata(self) -> dict:
		return {
			**super().get_metadata(),
			"endowment": self._endowment,
			"multiplier": self._multiplier,
			"max_rounds_send": self._max_rounds_send,
			"max_rounds_return": self._max_rounds_return,
			"negotiation_rounds": self._negotiation_rounds,
			"role_map": self._role_map,
			"turn_order": self._turn_order.value,
		}

	def spec(self) -> GameSpec:
		return GameSpec(
			game_id=GAME_ID,
			name="Trust (Investment then Return)",
			min_agents=2,
			max_agents=2,
			description=(
				"Trustor sends an amount x (0 <= x <= endowment) to trustee. "
				"The sent amount is multiplied by m. Trustee then chooses how much r to return "
				"(0 <= r <= m*x). Payoffs: trustor = (endowment - x) + r; trustee = (m*x) - r. "
				f"Agents first negotiate for {self._negotiation_rounds} round(s)."
			),
			phases=[
				Phase(
					name="negotiation",
					turn_order=self._turn_order,
					allowed_action_types=["message_only"],
					max_rounds=self._negotiation_rounds,
				),
				Phase(
					name="send",
					turn_order=self._turn_order,
					allowed_action_types=["send", "pass"],
					max_rounds=self._max_rounds_send,
				),
				Phase(
					name="return",
					turn_order=self._turn_order,
					allowed_action_types=["return_amount", "pass"],
					max_rounds=self._max_rounds_return,
				),
			],
			action_types=[
				ActionTypeDef(
					name="send",
					description="Trustor sends an amount to the trustee",
					payload_schema={"amount": {"type": "number", "minimum": 0}},
				),
				ActionTypeDef(
					name="return_amount",
					description="Trustee returns an amount to the trustor",
					payload_schema={"amount": {"type": "number", "minimum": 0}},
				),
				ActionTypeDef(
					name="pass",
					description="Pass your turn without acting",
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
				"sent": None,
				"returned": None,
				"action_history": [],
				"resolved": False,
				"negotiation_complete": False,
			},
		)

	# ------------------------------------------------------------------
	# Role helpers
	# ------------------------------------------------------------------

	def _role_agent_id(self, match: Match, role: str) -> str | None:
		if self._role_map and role in self._role_map:
			return self._role_map[role]
		if role == _TRUSTOR:
			return match.agent_ids[0] if match.agent_ids else None
		if role == _TRUSTEE:
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

	# ------------------------------------------------------------------
	# Phase / turn management
	# ------------------------------------------------------------------

	def _current_phase_name(self, match: Match) -> str:
		phases = match.spec.phases
		if not phases or match.current_phase_index >= len(phases):
			return "send"
		return phases[match.current_phase_index].name

	def _advance_phase(self, match: Match, target_phase: str) -> None:
		for i, ph in enumerate(match.spec.phases):
			if ph.name == target_phase:
				match.current_phase_index = i
				match.current_round = 0
				break
		if target_phase == "send":
			match.current_turn_index = self._role_index(match, _TRUSTOR)
		elif target_phase == "return":
			match.current_turn_index = self._role_index(match, _TRUSTEE)
		else:
			match.current_turn_index = 0

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
			self._advance_phase(match, "send")

	def _advance_role_round(self, match: Match, max_rounds: int) -> None:
		match.current_round += 1
		if match.current_round >= max_rounds:
			self._resolve_timeout(match)

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

	def _resolve(self, match: Match) -> None:
		sent = match.game_state.get("sent") or 0.0
		returned = match.game_state.get("returned") or 0.0
		trustor_id = self._role_agent_id(match, _TRUSTOR)
		trustee_id = self._role_agent_id(match, _TRUSTEE)
		payoffs = []
		if trustor_id is not None:
			payoffs.append(
				{
					"agent_id": trustor_id,
					"utility": round((self._endowment - sent) + returned, 2),
				}
			)
		if trustee_id is not None:
			payoffs.append(
				{
					"agent_id": trustee_id,
					"utility": round((self._multiplier * sent) - returned, 2),
				}
			)
		match.outcome = {
			"payoffs": payoffs,
			"reason": "trust_resolved",
			"sent": sent,
			"returned": returned,
			"endowment": self._endowment,
			"multiplier": self._multiplier,
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

		phase_name = self._current_phase_name(match)
		n = len(match.agent_ids)
		idx = match.current_turn_index
		if idx < 0 or idx >= n:
			idx = 0
		current_turn_agent_id = match.agent_ids[idx] if n > 0 else None
		is_my_turn = current_turn_agent_id == agent_id
		messages = messages_visible_to(match.messages, agent_id)
		allowed_actions = build_allowed_actions(match.spec, phase_name, is_my_turn)

		trustor_id = self._role_agent_id(match, _TRUSTOR)
		trustee_id = self._role_agent_id(match, _TRUSTEE)
		sent = match.game_state.get("sent")
		returned = match.game_state.get("returned")

		if phase_name == "negotiation":
			allowed_actions = [a for a in allowed_actions if a.action_type == "message_only"]
		elif phase_name == "send":
			if agent_id != trustor_id or sent is not None:
				allowed_actions = []
		elif phase_name == "return":
			if agent_id != trustee_id or sent is None or returned is not None:
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
		trustor_id = self._role_agent_id(match, _TRUSTOR)
		trustee_id = self._role_agent_id(match, _TRUSTEE)
		role = "observer"
		if agent_id == trustor_id:
			role = _TRUSTOR
		elif agent_id == trustee_id:
			role = _TRUSTEE
		return {
			"num_agents": len(match.agent_ids),
			"agent_ids": list(match.agent_ids),
			"trustor": trustor_id,
			"trustee": trustee_id,
			"my_role": role,
			"endowment": self._endowment,
			"multiplier": self._multiplier,
			"sent": match.game_state.get("sent"),
			"returned": match.game_state.get("returned"),
			"action_history": match.game_state.get("action_history", []),
			"resolved": match.game_state.get("resolved", False),
		}

	# ------------------------------------------------------------------
	# apply_action
	# ------------------------------------------------------------------

	def apply_action(self, match: Match, agent_id: str, action: Action) -> ActionResult:
		err = self._check_apply_preconditions(match, agent_id, GAME_ID)
		if err is not None:
			return err

		phase_name = self._current_phase_name(match)
		current_turn_agent_id = match.agent_ids[match.current_turn_index] if match.agent_ids else None
		at = action.action_type

		if at == "message":
			at = "message_only"

		if at != "message_only" and agent_id != current_turn_agent_id:
			return action_error(ActionError.NOT_YOUR_TURN, f"It is {current_turn_agent_id}'s turn")

		if phase_name == "negotiation":
			if at != "message_only":
				return action_error(
					ActionError.GAME_RULE_VIOLATION,
					f"Only message_only is allowed in negotiation phase, got {at}",
				)
			match.game_state.setdefault("action_history", []).append(
				{"agent_id": agent_id, "action": "message_only", "phase": phase_name}
			)
			self._advance_negotiation_turn(match)
			return action_ok()

		trustor_id = self._role_agent_id(match, _TRUSTOR)
		trustee_id = self._role_agent_id(match, _TRUSTEE)

		if phase_name == "send":
			if agent_id != trustor_id:
				return action_error(ActionError.GAME_RULE_VIOLATION, "Only trustor can send")
			if at == "send":
				return self._do_send(match, agent_id, action)
			if at == "pass":
				return self._do_send_pass(match, agent_id)
			if at == "message_only":
				return action_error(
					ActionError.GAME_RULE_VIOLATION, "message_only is not allowed in send phase"
				)
			return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown action type: {at}")

		if phase_name == "return":
			if agent_id != trustee_id:
				return action_error(ActionError.GAME_RULE_VIOLATION, "Only trustee can return")
			if at == "return_amount":
				return self._do_return(match, agent_id, action)
			if at == "pass":
				return self._do_return_pass(match, agent_id)
			if at == "message_only":
				return action_error(
					ActionError.GAME_RULE_VIOLATION, "message_only is not allowed in return phase"
				)
			return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown action type: {at}")

		return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown phase: {phase_name}")

	def _do_send(self, match: Match, agent_id: str, action: Action) -> ActionResult:
		if match.game_state.get("sent") is not None:
			return action_error(ActionError.GAME_RULE_VIOLATION, "Already sent")
		amount = action.payload.get("amount")
		if amount is None:
			return action_error(ActionError.INVALID_PAYLOAD, "amount is required")
		try:
			amount = float(amount)
		except (TypeError, ValueError):
			return action_error(ActionError.INVALID_PAYLOAD, "amount must be a number")
		if amount < 0 or amount > self._endowment:
			return action_error(ActionError.INVALID_PAYLOAD, "amount must be between 0 and endowment")

		match.game_state["sent"] = amount
		match.game_state.setdefault("action_history", []).append(
			{"agent_id": agent_id, "action": "send", "amount": amount, "phase": "send"}
		)
		self._advance_phase(match, "return")
		return action_ok()

	def _do_send_pass(self, match: Match, agent_id: str) -> ActionResult:
		match.game_state.setdefault("action_history", []).append(
			{"agent_id": agent_id, "action": "pass", "phase": "send"}
		)
		self._advance_role_round(match, self._max_rounds_send)
		return action_ok()

	def _do_return(self, match: Match, agent_id: str, action: Action) -> ActionResult:
		if match.game_state.get("returned") is not None:
			return action_error(ActionError.GAME_RULE_VIOLATION, "Already returned")
		sent = match.game_state.get("sent")
		if sent is None:
			return action_error(ActionError.GAME_RULE_VIOLATION, "Cannot return before send")
		amount = action.payload.get("amount")
		if amount is None:
			return action_error(ActionError.INVALID_PAYLOAD, "amount is required")
		try:
			amount = float(amount)
		except (TypeError, ValueError):
			return action_error(ActionError.INVALID_PAYLOAD, "amount must be a number")
		if amount < 0 or amount > self._multiplier * sent:
			return action_error(
				ActionError.INVALID_PAYLOAD, "amount must be between 0 and multiplier * sent"
			)

		match.game_state["returned"] = amount
		match.game_state.setdefault("action_history", []).append(
			{"agent_id": agent_id, "action": "return_amount", "amount": amount, "phase": "return"}
		)
		self._resolve(match)
		return action_ok()

	def _do_return_pass(self, match: Match, agent_id: str) -> ActionResult:
		match.game_state.setdefault("action_history", []).append(
			{"agent_id": agent_id, "action": "pass", "phase": "return"}
		)
		self._advance_role_round(match, self._max_rounds_return)
		return action_ok()

	def compute_outcome(self, match: Match) -> dict | None:
		if match.status == MatchStatus.FINISHED and match.outcome is not None:
			return match.outcome
		return None
