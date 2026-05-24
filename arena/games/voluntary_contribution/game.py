"""Voluntary contribution mechanism (public good) with negotiation phase."""

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


GAME_ID = "voluntary-contribution"


class VoluntaryContributionGame(Game):
	"""Voluntary contribution mechanism: agents decide contributions to a public good."""

	def __init__(
		self,
		*,
		endowment: float = 10,
		marginal_per_capita: float = 0.6,
		max_rounds: int = 10,
		negotiation_rounds: int = 2,
		turn_order: TurnOrder = TurnOrder.ROUND_ROBIN,
	) -> None:
		if endowment < 0:
			raise ValueError("endowment must be >= 0")
		if marginal_per_capita < 0:
			raise ValueError("marginal_per_capita must be >= 0")
		if max_rounds < 1:
			raise ValueError("max_rounds must be >= 1")
		if negotiation_rounds < 0:
			raise ValueError("negotiation_rounds must be >= 0")

		self._endowment = endowment
		self._marginal_per_capita = marginal_per_capita
		self._max_rounds = max_rounds
		self._negotiation_rounds = negotiation_rounds
		self._turn_order = turn_order

	@classmethod
	def from_params(cls, game_params: dict, agent_ids: list[str]) -> "VoluntaryContributionGame":
		endowment = game_params.get("endowment", 10)
		marginal_per_capita = game_params.get("marginal_per_capita", 0.6)
		if marginal_per_capita is None:
			marginal_per_capita = 0.6
		else:
			marginal_per_capita = float(marginal_per_capita)
		max_rounds = game_params.get("max_rounds", 10)
		negotiation_rounds = game_params.get("negotiation_rounds", 2)
		return cls(
			endowment=endowment,
			marginal_per_capita=marginal_per_capita,
			max_rounds=max_rounds,
			negotiation_rounds=negotiation_rounds,
		)

	def get_metadata(self) -> dict:
		return {
			**super().get_metadata(),
			"endowment": self._endowment,
			"marginal_per_capita": self._marginal_per_capita,
			"max_rounds": self._max_rounds,
			"negotiation_rounds": self._negotiation_rounds,
			"turn_order": self._turn_order.value,
		}

	def spec(self) -> GameSpec:
		return GameSpec(
			game_id=GAME_ID,
			name="Voluntary Contribution Mechanism (Public Good)",
			min_agents=2,
			description=(
				"Each agent is endowed with E tokens and chooses how many to contribute to a public good. "
				"Each agent receives marginal_per_capita * total_contributions. "
				"Each agent keeps un-contributed tokens. "
				"Payoff = (endowment - contrib) + marginal_per_capita * total_contributions. "
				f"Agents may negotiate for {self._negotiation_rounds} round(s) before contributing."
			),
			phases=[
				Phase(
					name="negotiation",
					turn_order=self._turn_order,
					allowed_action_types=["message_only", "pass"],
					max_rounds=self._negotiation_rounds,
				),
				Phase(
					name="contribute",
					turn_order=self._turn_order,
					allowed_action_types=["contribute", "pass", "message_only"],
					max_rounds=self._max_rounds,
				),
			],
			action_types=[
				ActionTypeDef(
					name="contribute",
					description="Contribute an amount to the public good",
					payload_schema={"amount": {"type": "number", "minimum": 0}},
				),
				ActionTypeDef(
					name="pass",
					description="Pass your turn without contributing",
					payload_schema={},
				),
				ActionTypeDef(
					name="message_only",
					description="Send messages during negotiation or contribution",
					payload_schema={},
				),
			],
			outcome_rule=OutcomeRule.ENGINE,
			initial_game_state={
				"contribs": {},
				"action_history": [],
				"resolved": False,
				"negotiation_complete": False,
			},
		)

	# ------------------------------------------------------------------
	# Phase / turn management
	# ------------------------------------------------------------------

	def _current_phase_name(self, match: Match) -> str:
		phases = match.spec.phases
		if not phases or match.current_phase_index >= len(phases):
			return "contribute"
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
			self._advance_phase(match, "contribute")

	def _advance_contribute_turn(self, match: Match) -> None:
		n = len(match.agent_ids)
		if n <= 1:
			return

		contribs = match.game_state.get("contribs", {})
		if len(contribs) >= n:
			self._resolve(match, trigger="all_contributed")
			return

		idx = match.current_turn_index
		advanced = False
		for _ in range(n):
			idx = (idx + 1) % n
			if idx == 0:
				match.current_round += 1
			next_agent_id = match.agent_ids[idx]
			if next_agent_id not in contribs:
				match.current_turn_index = idx
				advanced = True
				break
		if not advanced:
			self._resolve(match, trigger="all_contributed")
			return

		phase = match.spec.phases[match.current_phase_index]
		if phase.max_rounds is not None and match.current_round >= phase.max_rounds:
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

	def _resolve(self, match: Match, *, trigger: str = "unknown") -> None:
		contribs = match.game_state.get("contribs", {})
		total = sum(contribs.values())
		marginal_per_capita = self._marginal_per_capita
		payoffs = []
		for aid in match.agent_ids:
			contrib = contribs.get(aid, 0.0)
			utility = (self._endowment - contrib) + marginal_per_capita * total
			payoffs.append({"agent_id": aid, "utility": round(utility, 2)})

		match.outcome = {
			"payoffs": payoffs,
			"reason": "vcm_resolved",
			"trigger": trigger,
			"total_contributions": total,
			"endowment": self._endowment,
			"marginal_per_capita": marginal_per_capita,
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

		contribs = match.game_state.get("contribs", {})
		if phase_name == "negotiation":
			allowed_actions = [
				a for a in allowed_actions if a.action_type in ("message_only", "pass")
			]
		elif phase_name == "contribute":
			if agent_id in contribs:
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
		contribs = match.game_state.get("contribs", {})
		return {
			"num_agents": len(match.agent_ids),
			"agent_ids": list(match.agent_ids),
			"endowment": self._endowment,
			"marginal_per_capita": self._marginal_per_capita,
			"contribs": contribs,
			"total_contributions": sum(contribs.values()),
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

		if phase_name == "contribute":
			if at == "contribute":
				return self._do_contribute(match, agent_id, action)
			if at == "pass":
				return self._do_pass(match, agent_id)
			if at == "message_only":
				match.game_state.setdefault("action_history", []).append(
					{"agent_id": agent_id, "action": "message_only", "phase": phase_name}
				)
				return action_ok()
			return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown action type: {at}")

		return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown phase: {phase_name}")

	def _do_contribute(self, match: Match, agent_id: str, action: Action) -> ActionResult:
		contribs = match.game_state.setdefault("contribs", {})
		if agent_id in contribs:
			return action_error(ActionError.GAME_RULE_VIOLATION, "Already contributed")

		amount = action.payload.get("amount")
		if amount is None:
			return action_error(ActionError.INVALID_PAYLOAD, "amount is required")
		try:
			amount = float(amount)
		except (TypeError, ValueError):
			return action_error(ActionError.INVALID_PAYLOAD, "amount must be a number")
		if amount < 0 or amount > self._endowment:
			return action_error(ActionError.INVALID_PAYLOAD, "amount must be between 0 and endowment")

		contribs[agent_id] = amount
		match.game_state.setdefault("action_history", []).append(
			{"agent_id": agent_id, "action": "contribute", "amount": amount, "phase": "contribute"}
		)
		self._advance_contribute_turn(match)
		return action_ok()

	def _do_pass(self, match: Match, agent_id: str) -> ActionResult:
		match.game_state.setdefault("action_history", []).append(
			{"agent_id": agent_id, "action": "pass", "phase": "contribute"}
		)
		self._advance_contribute_turn(match)
		return action_ok()

	def compute_outcome(self, match: Match) -> dict | None:
		if match.status == MatchStatus.FINISHED and match.outcome is not None:
			return match.outcome
		return None
