"""Dictator game: allocator (dictator) chooses how to split a fixed pie between self and recipient. Recipient has no action. Engine resolves immediately after allocator submits the split.  
"""
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


GAME_ID = "dictator"

# Role indices
_ALLOCATOR = 0
_RECIPIENT = 1


class DictatorGame(Game):
    """Dictator: Allocator (dictator) chooses how to split a fixed pie between self and recipient. Recipient has no action. Engine resolves immediately after allocator submits the split.

    Roles (by agent index):
        0 = allocator — splits the pie
        1 = recipient — no action, receives the allocation

    Timeout (or pass without allocating) yields 0 for both.
    """

    def __init__(
        self,
        *,
        pie: int = 100,
        max_rounds: int = 3,
        negotiation_rounds: int = 2,
        reservation_max: float | None = None,
        reservation_values: dict[str, float] | None = None,
    ) -> None:
        self._pie = pie
        self._max_rounds = max_rounds
        self._negotiation_rounds = negotiation_rounds
        self._reservation_max = reservation_max
        self._fixed_reservation_values = reservation_values

    @classmethod
    def from_params(cls, game_params: dict, agent_ids: list[str]) -> "DictatorGame":
        pie = game_params.get("pie", 100)
        max_rounds = game_params.get("max_rounds", 3)
        negotiation_rounds = game_params.get("negotiation_rounds", 2)
        reservation_max = game_params.get("reservation_max")
        reservation_values = None
        if len(agent_ids) >= 2 and ("rv_allocator" in game_params or "rv_recipient" in game_params):
            reservation_values = {
                agent_ids[_ALLOCATOR]: float(game_params.get("rv_allocator", 0)),
                agent_ids[_RECIPIENT]: float(game_params.get("rv_recipient", 0)),
            }
        return cls(
            pie=pie,
            max_rounds=max_rounds,
            negotiation_rounds=negotiation_rounds,
            reservation_max=reservation_max,
            reservation_values=reservation_values,
        )

    def get_metadata(self) -> dict:
        return {
            **super().get_metadata(),
            "pie": self._pie,
            "max_rounds": self._max_rounds,
            "negotiation_rounds": self._negotiation_rounds,
            "reservation_max": self._reservation_max,
            "reservation_values": self._fixed_reservation_values,
        }

    def spec(self) -> GameSpec:
        return GameSpec(
            game_id=GAME_ID,
            name="Dictator",
            min_agents=2,
            description=(
                f"Allocator (dictator) chooses how to split a fixed pie (value {self._pie}) "
                "between self and recipient. Recipient has no action. "
                f"Each agent has a private reservation value v drawn uniformly from [0, {self._reservation_max if self._reservation_max is not None else self._pie}]. "
                f"First, both agents have {self._negotiation_rounds} round(s) to negotiate via messages. "
                f"Then allocator submits the split. "
                "Payoff at game end is u = share - v for each agent. "
                f"Timeout (or pass without allocating) yields share=0 for both. Max {self._max_rounds} allocation rounds."
            ),
            phases=[
                Phase(
                    name="negotiation",
                    turn_order=TurnOrder.ROUND_ROBIN,
                    allowed_action_types=["message_only"],
                    max_rounds=self._negotiation_rounds,
                ),
                Phase(
                    name="allocate",
                    turn_order=TurnOrder.ROUND_ROBIN,
                    allowed_action_types=["allocate_split", "pass", "message_only"],
                    max_rounds=self._max_rounds,
                ),
            ],
            action_types=[
                ActionTypeDef(
                    name="allocate_split",
                    description="Allocator splits the pie by specifying their share and recipient's share",
                    payload_schema={
                        "allocator_share": {"type": "number", "minimum": 0},
                        "recipient_share": {"type": "number", "minimum": 0},
                    },
                ),
                ActionTypeDef(
                    name="pass",
                    description="Skip this turn without allocating",
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
                "allocation": None,
                "action_history": [],
                "resolved": False,
                "reservation_values": None,
            },
        )

    def _ensure_reservation_values(self, match: Match) -> None:
        if match.game_state.get("reservation_values") is not None:
            return
        if self._fixed_reservation_values is not None:
            match.game_state["reservation_values"] = dict(self._fixed_reservation_values)
            return
        v_max = self._pie if self._reservation_max is None else float(self._reservation_max)
        rng = random.Random(f"{match.match_id}")
        vals = [rng.uniform(0, v_max) for _ in match.agent_ids]
        match.game_state["reservation_values"] = dict(zip(match.agent_ids, vals))

    def _visible_game_state(self, match: Match, agent_id: str) -> dict:
        """Return the visible portions of game state for the agent."""
        g = match.game_state
        agent_index = match.agent_ids.index(agent_id) if agent_id in match.agent_ids else -1
        role_map = {_ALLOCATOR: "allocator", _RECIPIENT: "recipient"}
        rv = g.get("reservation_values")
        my_reservation_value = rv.get(agent_id) if isinstance(rv, dict) else None
        return {
            "num_agents": len(match.agent_ids),
            "agent_ids": list(match.agent_ids),
            "allocator": match.agent_ids[_ALLOCATOR] if len(match.agent_ids) > _ALLOCATOR else None,
            "recipient": match.agent_ids[_RECIPIENT] if len(match.agent_ids) > _RECIPIENT else None,
            "my_role": role_map.get(agent_index, "unknown"),
            "pie": self._pie,
            "my_reservation_value": my_reservation_value,
            "allocation": g.get("allocation"),
            "resolved": g.get("resolved", False),
            "action_history": g.get("action_history", []),
        }

    def _get_current_phase(self, match: Match) -> str:
        """Determine which phase we're in based on game state."""
        if match.game_state.get("resolved", False):
            return "allocate"  # Game already resolved
        if match.game_state.get("negotiation_complete", False):
            return "allocate"
        return "negotiation"

    def _filter_actions_for_role(
        self, actions: list, agent_index: int, phase: str, match: Match
    ) -> list:
        """Remove actions that the agent's role cannot perform in this phase."""
        if phase == "negotiation":
            # Both agents can only send messages in negotiation phase
            return [a for a in actions if a.action_type == "message_only"]
        
        # In allocate phase:
        allocator_actions = {"allocate_split", "pass", "message_only"}
        recipient_actions = {"message_only"}

        if agent_index == _ALLOCATOR:
            allowed_names = allocator_actions
        elif agent_index == _RECIPIENT:
            allowed_names = recipient_actions
        else:
            allowed_names = {"message_only"}

        return [a for a in actions if a.action_type in allowed_names]

    def compute_turn_state(self, match: Match, agent_id: str) -> TurnState | None:
        if match.game_id != GAME_ID:
            return None
        if match.status != MatchStatus.RUNNING:
            return self._not_running_turn_state(match, agent_id)

        self._ensure_reservation_values(match)

        # Determine current phase based on game state
        current_phase = self._get_current_phase(match)
        
        # Get phase info from spec
        phase_spec = next(
            (p for p in match.spec.phases if p.name == current_phase),
            None
        )
        if phase_spec is None:
            current_phase = "allocate"  # fallback
            phase_spec = next((p for p in match.spec.phases if p.name == "allocate"), None)
        
        n = len(match.agent_ids)
        idx = match.current_turn_index
        if idx < 0 or idx >= n:
            idx = 0
        current_turn_agent_id = match.agent_ids[idx]
        is_my_turn = current_turn_agent_id == agent_id
        messages = messages_visible_to(match.messages, agent_id)
        allowed_actions = build_allowed_actions(match.spec, current_phase, is_my_turn)

        # Restrict actions based on role
        agent_index = match.agent_ids.index(agent_id) if agent_id in match.agent_ids else -1
        allowed_actions = self._filter_actions_for_role(
            allowed_actions, agent_index, current_phase, match
        )

        return TurnState(
            match_id=match.match_id,
            game_id=match.game_id,
            agent_id=agent_id,
            phase=current_phase,
            is_my_turn=is_my_turn,
            current_turn_agent_id=current_turn_agent_id,
            game_state=self._visible_game_state(match, agent_id),
            messages=messages,
            allowed_actions=allowed_actions,
            game_over=(match.status == MatchStatus.FINISHED),
            outcome=match.outcome,
        )

    def apply_action(self, match: Match, agent_id: str, action: Action) -> ActionResult:
        err = self._check_apply_preconditions(match, agent_id, GAME_ID)
        if err is not None:
            return err

        self._ensure_reservation_values(match)

        n = len(match.agent_ids)
        if n < 2:
            return action_error(ActionError.MATCH_NOT_RUNNING, "Need at least 2 agents")

        current_turn_agent_id = match.agent_ids[match.current_turn_index]
        agent_index = match.agent_ids.index(agent_id) if agent_id in match.agent_ids else -1
        current_phase = self._get_current_phase(match)

        # Turn check (message_only never requires turn ownership)
        if action.action_type != "message_only" and agent_id != current_turn_agent_id:
            return action_error(ActionError.NOT_YOUR_TURN, f"It is {current_turn_agent_id}'s turn")

        # Handle negotiation phase
        if current_phase == "negotiation":
            if action.action_type == "message_only":
                match.game_state.setdefault("action_history", []).append(
                    {"agent_id": agent_id, "action": "message_only", "round": match.current_round, "phase": "negotiation"}
                )
                # Advance turn and check if negotiation phase is complete
                self._advance_turn_and_check_phase(match)
                return action_ok()
            else:
                return action_error(
                    ActionError.GAME_RULE_VIOLATION, 
                    f"Only message_only is allowed in negotiation phase, got {action.action_type}"
                )

        # Handle allocate phase
        # Only allocator can perform non-message actions in allocate phase
        if action.action_type != "message_only" and agent_index != _ALLOCATOR:
            return action_error(
                ActionError.GAME_RULE_VIOLATION, f"Only the allocator can perform {action.action_type}"
            )

        if action.action_type == "allocate_split":
            return self._do_allocate_split(match, agent_id, action)

        if action.action_type == "pass":
            return self._do_pass(match, agent_id)

        if action.action_type == "message_only":
            match.game_state.setdefault("action_history", []).append(
                {"agent_id": agent_id, "action": "message_only", "round": match.current_round, "phase": "allocate"}
            )
            return action_ok()

        return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown action type: {action.action_type}")

    def _do_allocate_split(self, match: Match, agent_id: str, action: Action) -> ActionResult:
        """Process allocate_split action."""
        payload = action.payload
        try:
            allocator_share = float(payload.get("allocator_share", 0))
            recipient_share = float(payload.get("recipient_share", 0))
        except (TypeError, ValueError):
            return action_error(ActionError.INVALID_PAYLOAD, "Shares must be numeric")

        # Validate non-negative
        if allocator_share < 0 or recipient_share < 0:
            return action_error(ActionError.INVALID_PAYLOAD, "Shares must be >= 0")

        # Validate sum equals pie
        share_sum = allocator_share + recipient_share
        if abs(share_sum - self._pie) > 0.01:
            return action_error(
                ActionError.INVALID_PAYLOAD, f"Shares must sum to {self._pie}, got {share_sum}"
            )

        # Record allocation and mark resolved
        match.game_state["allocation"] = {
            "allocator_share": allocator_share,
            "recipient_share": recipient_share,
        }
        match.game_state["resolved"] = True
        match.game_state.setdefault("action_history", []).append(
            {
                "agent_id": agent_id,
                "action": "allocate_split",
                "allocator_share": allocator_share,
                "recipient_share": recipient_share,
                "round": match.current_round,
            }
        )

        # Compute outcome immediately
        allocation = match.game_state["allocation"]
        rv = match.game_state.get("reservation_values") or {}
        payoffs = [
            {
                "agent_id": match.agent_ids[_ALLOCATOR],
                "share": allocation["allocator_share"],
                "reservation_value": rv.get(match.agent_ids[_ALLOCATOR], 0.0),
                "utility": round(allocation["allocator_share"] - rv.get(match.agent_ids[_ALLOCATOR], 0.0), 2),
            },
            {
                "agent_id": match.agent_ids[_RECIPIENT],
                "share": allocation["recipient_share"],
                "reservation_value": rv.get(match.agent_ids[_RECIPIENT], 0.0),
                "utility": round(allocation["recipient_share"] - rv.get(match.agent_ids[_RECIPIENT], 0.0), 2),
            },
        ]
        match.outcome = {
            "payoffs": payoffs,
            "reason": "dictator_allocated",
            "allocation": allocation,
        }
        match.status = MatchStatus.FINISHED

        return action_ok()

    def _do_pass(self, match: Match, agent_id: str) -> ActionResult:
        """Process pass action."""
        match.game_state.setdefault("action_history", []).append(
            {"agent_id": agent_id, "action": "pass", "round": match.current_round}
        )
        self._advance_turn_and_check_rounds(match)
        return action_ok()

    def _advance_turn_and_check_phase(self, match: Match) -> None:
        """Advance turn index and check if negotiation phase is complete."""
        n = len(match.agent_ids)
        if n == 0:
            return
        match.current_turn_index = (match.current_turn_index + 1) % n
        if match.current_turn_index == 0:
            match.current_round += 1
        # When we've completed enough rounds in negotiation phase, mark it complete
        if match.current_round >= self._negotiation_rounds:
            match.game_state["negotiation_complete"] = True
            # Reset turn and round for allocate phase
            match.current_turn_index = 0
            match.current_round = 0

    def _advance_turn_and_check_rounds(self, match: Match) -> None:
        """Advance turn index and check if max rounds exceeded."""
        n = len(match.agent_ids)
        if n == 0:
            return
        match.current_turn_index = (match.current_turn_index + 1) % n
        if match.current_turn_index == 0:
            match.current_round += 1
        if match.current_round >= self._max_rounds:
            rv = match.game_state.get("reservation_values") or {}
            match.outcome = {
                "payoffs": [
                    {
                        "agent_id": aid,
                        "share": 0.0,
                        "reservation_value": rv.get(aid, 0.0),
                        "utility": round(0.0 - rv.get(aid, 0.0), 2),
                    }
                    for aid in match.agent_ids
                ],
                "reason": "max_rounds_exceeded",
            }
            match.status = MatchStatus.FINISHED

    def compute_outcome(self, match: Match) -> dict | None:
        """Return outcome if game is finished."""
        if match.status == MatchStatus.FINISHED and match.outcome is not None:
            return match.outcome
        return None

