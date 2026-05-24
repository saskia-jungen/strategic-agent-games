"""Centipede game: alternating take-or-push with doubling piles."""

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


GAME_ID = "centipede"


class CentipedeGame(Game):
	"""Centipede: two players alternate between take and push.

	Two piles start at (small, large). On each turn, the player can:
	  - take: keep the larger pile and give the smaller to the other player (game ends)
	  - push: pass both piles to the other player; both piles double

	The game ends on take or after max_pushes pushes (push is then disallowed).
	"""

	def __init__(
		self,
		*,
		small_pile: int = 1,
		large_pile: int = 4,
		max_pushes: int = 10,
		turn_order: TurnOrder = TurnOrder.ROUND_ROBIN,
	) -> None:
		if small_pile < 0 or large_pile < 0:
			raise ValueError("pile sizes must be >= 0")
		if max_pushes < 0:
			raise ValueError("max_pushes must be >= 0")
		if large_pile < small_pile:
			small_pile, large_pile = large_pile, small_pile

		self._small_pile = int(small_pile)
		self._large_pile = int(large_pile)
		self._max_pushes = int(max_pushes)
		self._turn_order = turn_order

	@classmethod
	def from_params(cls, game_params: dict, agent_ids: list[str]) -> "CentipedeGame":
		_ = agent_ids
		max_pushes = game_params.get("max_pushes", game_params.get("max_rounds", 10))
		small_pile = game_params.get("small_pile", 1)
		large_pile = game_params.get("large_pile", 4)
		return cls(small_pile=small_pile, large_pile=large_pile, max_pushes=max_pushes)

	def get_metadata(self) -> dict:
		return {
			**super().get_metadata(),
			"small_pile": self._small_pile,
			"large_pile": self._large_pile,
			"max_pushes": self._max_pushes,
			"turn_order": self._turn_order.value,
		}

	def spec(self) -> GameSpec:
		return GameSpec(
			game_id=GAME_ID,
			name="Centipede",
			min_agents=2,
			description=(
				"Two players alternate between take and push. "
				f"Initial piles are {self._small_pile} and {self._large_pile}. "
				"On take, the current player keeps the larger pile and gives the smaller pile to the other. "
				"On push, the piles pass to the other player and both piles double. "
				f"Push is allowed at most {self._max_pushes} times."
			),
			phases=[
				Phase(
					name="play",
					turn_order=self._turn_order,
					allowed_action_types=["take", "push", "message_only"],
					max_rounds=None,
				),
			],
			action_types=[
				ActionTypeDef(
					name="take",
					description="Take the larger pile and give the smaller to the other player",
					payload_schema={},
				),
				ActionTypeDef(
					name="push",
					description="Push both piles across the table; both piles double",
					payload_schema={},
				),
				ActionTypeDef(
					name="message_only",
					description="Send messages without advancing the turn",
					payload_schema={},
				),
			],
			outcome_rule=OutcomeRule.ENGINE,
			initial_game_state={
				"small_pile": self._small_pile,
				"large_pile": self._large_pile,
				"push_count": 0,
				"max_pushes": self._max_pushes,
				"action_history": [],
				"resolved": False,
			},
		)

	def _visible_game_state(self, match: Match, agent_id: str) -> dict:
		_ = agent_id
		g = match.game_state
		small = g.get("small_pile", self._small_pile)
		large = g.get("large_pile", self._large_pile)
		push_count = g.get("push_count", 0)
		max_pushes = g.get("max_pushes", self._max_pushes)
		return {
			"num_agents": len(match.agent_ids),
			"agent_ids": list(match.agent_ids),
			"small_pile": small,
			"large_pile": large,
			"push_count": push_count,
			"max_pushes": max_pushes,
			"can_push": push_count < max_pushes,
			"action_history": g.get("action_history", []),
			"resolved": g.get("resolved", False),
		}

	def compute_turn_state(self, match: Match, agent_id: str) -> TurnState | None:
		if match.game_id != GAME_ID:
			return None
		if match.status != MatchStatus.RUNNING:
			return self._not_running_turn_state(match, agent_id)

		phase_name, current_turn_agent_id, is_my_turn = self._get_phase_and_turn_info(match, agent_id)
		messages = messages_visible_to(match.messages, agent_id)
		allowed_actions = build_allowed_actions(match.spec, phase_name, is_my_turn)

		push_count = match.game_state.get("push_count", 0)
		max_pushes = match.game_state.get("max_pushes", self._max_pushes)
		if push_count >= max_pushes:
			allowed_actions = [a for a in allowed_actions if a.action_type != "push"]

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

	def _advance_turn_and_round(self, match: Match) -> None:
		n = len(match.agent_ids)
		if n == 0:
			return
		match.current_turn_index = (match.current_turn_index + 1) % n
		if match.current_turn_index == 0:
			match.current_round += 1

	def _other_agent_id(self, match: Match, agent_id: str) -> str | None:
		for aid in match.agent_ids:
			if aid != agent_id:
				return aid
		return None

	def _resolve_take(self, match: Match, taker_id: str) -> None:
		small = match.game_state.get("small_pile", self._small_pile)
		large = match.game_state.get("large_pile", self._large_pile)
		if large < small:
			small, large = large, small

		other_id = self._other_agent_id(match, taker_id)
		payoffs = []
		payoffs.append({"agent_id": taker_id, "utility": float(large), "taken": large})
		if other_id is not None:
			payoffs.append({"agent_id": other_id, "utility": float(small), "taken": small})

		match.outcome = {
			"payoffs": payoffs,
			"reason": "take",
			"taker": taker_id,
			"small_pile": small,
			"large_pile": large,
			"push_count": match.game_state.get("push_count", 0),
		}
		match.game_state["resolved"] = True
		match.status = MatchStatus.FINISHED

	def apply_action(self, match: Match, agent_id: str, action: Action) -> ActionResult:
		err = self._check_apply_preconditions(match, agent_id, GAME_ID)
		if err is not None:
			return err

		phase = match.spec.phases[match.current_phase_index] if match.spec.phases else None
		if not phase or phase.name != "play":
			return action_error(ActionError.MATCH_NOT_RUNNING, "Not in play phase")

		if len(match.agent_ids) < 2:
			return action_error(ActionError.MATCH_NOT_RUNNING, "Need at least 2 agents")

		if phase.turn_order != TurnOrder.RANDOM:
			current_turn_agent_id = match.agent_ids[match.current_turn_index]
			if agent_id != current_turn_agent_id:
				return action_error(ActionError.NOT_YOUR_TURN, f"It is {current_turn_agent_id}'s turn")

		at = action.action_type
		if at == "take":
			match.game_state.setdefault("action_history", []).append(
				{
					"agent_id": agent_id,
					"action": "take",
					"round": match.current_round,
					"push_count": match.game_state.get("push_count", 0),
				}
			)
			self._resolve_take(match, agent_id)
			return action_ok()

		if at == "push":
			push_count = match.game_state.get("push_count", 0)
			max_pushes = match.game_state.get("max_pushes", self._max_pushes)
			if push_count >= max_pushes:
				return action_error(ActionError.GAME_RULE_VIOLATION, "Maximum pushes reached; must take")
			small = match.game_state.get("small_pile", self._small_pile)
			large = match.game_state.get("large_pile", self._large_pile)
			match.game_state["small_pile"] = small * 2
			match.game_state["large_pile"] = large * 2
			match.game_state["push_count"] = push_count + 1
			match.game_state.setdefault("action_history", []).append(
				{
					"agent_id": agent_id,
					"action": "push",
					"round": match.current_round,
					"push_count": match.game_state["push_count"],
				}
			)
			self._advance_turn_and_round(match)
			return action_ok()

		if at == "message_only":
			match.game_state.setdefault("action_history", []).append(
				{
					"agent_id": agent_id,
					"action": "message_only",
					"round": match.current_round,
					"advances_turn": False,
				}
			)
			return action_ok()

		return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown action type: {at}")

	def compute_outcome(self, match: Match) -> dict | None:
		if match.status == MatchStatus.FINISHED and match.outcome is not None:
			return match.outcome
		return None
