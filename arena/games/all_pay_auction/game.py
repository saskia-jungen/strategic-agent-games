"""All-pay auction with two phases:
  1. chat: agents talk, bluff to each other, signal (4 rounds)
  2. auction: agents submit sealed bids (6 rounds)
Highest bid wins, but ALL agents pay their own bid regardless.
Winner utility = valuation - bid; loser utility = -bid.
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


GAME_ID = "all-pay-auction"
CHAT_ROUNDS   = 4 
AUCTION_ROUNDS = 6 


class AllPayAuctionGame(Game):
    """All-pay auction with a chat phase followed by a sealed-bid auction phase."""

    def __init__(
        self,
        *,
        valuation_range: tuple[int, int] = (0, 100),
        valuations: dict[str, float] | None = None,
        turn_order: TurnOrder = TurnOrder.ROUND_ROBIN,
    ) -> None:
        self._valuation_range = valuation_range
        self._fixed_valuations = valuations
        self._turn_order = turn_order

    @classmethod
    def from_params(cls, game_params: dict, agent_ids: list[str]) -> "AllPayAuctionGame":
        return cls()

    def get_metadata(self) -> dict:
        return {
            **super().get_metadata(),
            "valuation_range": self._valuation_range,
            "chat_rounds": CHAT_ROUNDS,
            "auction_rounds": AUCTION_ROUNDS,
        }

    def spec(self) -> GameSpec:
        low, high = self._valuation_range
        return GameSpec(
            game_id=GAME_ID,
            name="All-Pay Auction",
            min_agents=2,
            description=(
                f"Each bidder has a private valuation drawn from [{low}, {high}]. "
                f"Phase 1 ({CHAT_ROUNDS} rounds): chat and signal. "
                f"Phase 2 ({AUCTION_ROUNDS} rounds): submit one sealed bid. "
                "Highest bid wins the prize, but ALL pay their own bid. "
                "Winner utility = valuation - bid; loser utility = -bid."
            ),
            phases=[
                Phase(
                    name="chat",
                    turn_order=self._turn_order,
                    allowed_action_types=["message_only", "pass"],
                    max_rounds=CHAT_ROUNDS,
                ),
                Phase(
                    name="auction",
                    turn_order=self._turn_order,
                    allowed_action_types=["submit_bid", "message_only", "pass"],
                    max_rounds=AUCTION_ROUNDS,
                ),
            ],
            action_types=[
                ActionTypeDef(
                    name="submit_bid",
                    description="Submit your sealed bid (once only, irreversible). All agents pay their bid.",
                    payload_schema={"bid": {"type": "number", "minimum": 0}},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="message_only",
                    description="Send a message without advancing the turn. Use to chat and bluff.",
                    payload_schema={},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="pass",
                    description="Pass your turn.",
                    payload_schema={},
                    is_message=False,
                ),
            ],
            outcome_rule=OutcomeRule.ENGINE,
            initial_game_state={
                "bids": {},
                "valuations": None,
                "action_history": [],
                "resolved": False,
                "phase": "chat",
                "chat_turns": 0,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _ensure_valuations(self, match: Match) -> None:
        if match.game_state.get("valuations") is not None:
            return
        if self._fixed_valuations is not None:
            if set(self._fixed_valuations.keys()) == set(match.agent_ids):
                match.game_state["valuations"] = dict(self._fixed_valuations)
            else:
                vals = list(self._fixed_valuations.values())
                match.game_state["valuations"] = dict(zip(match.agent_ids, vals))
            return
        low, high = self._valuation_range
        rng = random.Random(f"{match.match_id}")
        vals = [round(rng.uniform(low, high), 2) for _ in match.agent_ids]
        match.game_state["valuations"] = dict(zip(match.agent_ids, vals))

    def _visible_game_state(self, match: Match, agent_id: str) -> dict:
        g = match.game_state
        valuations = g.get("valuations") or {}
        bids = g.get("bids") or {}
        opponent_ids = [aid for aid in match.agent_ids if aid != agent_id]
        current_phase = g.get("phase", "chat")
        chat_turns = g.get("chat_turns", 0)
        return {
            "num_agents": len(match.agent_ids),
            "agent_ids": list(match.agent_ids),
            "current_phase": current_phase,
            "chat_turns_remaining": max(0, CHAT_ROUNDS * len(match.agent_ids) - chat_turns),
            "my_valuation": valuations.get(agent_id),
            "my_bid": bids.get(agent_id),
            "opponents_with_bid": [oid for oid in opponent_ids if oid in bids],
            "num_bids_submitted": len(bids),
            "action_history": g.get("action_history", []),
            "rule_reminder": (
                "CHAT phase: use message_only to signal/bluff before bidding. "
                if current_phase == "chat"
                else "AUCTION phase: submit your bid. Everyone pays their own bid — winner gets prize."
            ),
        }

    def _advance_turn(self, match: Match) -> None:
        n = len(match.agent_ids)
        if n == 0:
            return
        match.current_turn_index = (match.current_turn_index + 1) % n
        if match.current_turn_index == 0:
            match.current_round += 1

    def _try_phase_transition(self, match: Match) -> None:
        """Move from chat → auction phase once chat rounds are exhausted."""
        g = match.game_state
        if g.get("phase") != "chat":
            return
        chat_turns = g.get("chat_turns", 0)
        n = len(match.agent_ids)

        #Transition after CHAT_ROUNDS complete cycles
        if chat_turns >= CHAT_ROUNDS * max(n, 1):
            g["phase"] = "auction"
            match.current_phase_index = 1
            match.current_round = 0
            match.current_turn_index = 0

    def _check_auction_timeout(self, match: Match) -> None:
        """End match if auction rounds exceeded without all bids."""
        if match.game_state.get("phase") != "auction":
            return
        if match.current_round >= AUCTION_ROUNDS:
            match.outcome = {
                "payoffs": [{"agent_id": aid, "utility": 0.0} for aid in match.agent_ids],
                "reason": "max_rounds_exceeded",
            }
            match.status = MatchStatus.FINISHED

    def _resolve_auction(self, match: Match) -> None:
        bids = match.game_state["bids"]
        valuations = match.game_state["valuations"]
        max_bid = max(bids.values())
        top_bidders = [aid for aid, b in bids.items() if b == max_bid]
        if len(top_bidders) == 1:
            winner = top_bidders[0]
        else:
            rng = random.Random(f"{match.match_id}_tiebreak")
            winner = rng.choice(top_bidders)
        payoffs = []
        for aid in match.agent_ids:
            bid = bids[aid]
            utility = round(valuations[aid] - bid, 2) if aid == winner else round(-bid, 2)
            payoffs.append({"agent_id": aid, "bid": bid, "utility": utility})
        match.game_state["resolved"] = True
        match.outcome = {
            "payoffs": payoffs,
            "reason": "auction_resolved",
            "winner": winner,
            "bids": dict(bids),
        }
        match.status = MatchStatus.FINISHED

    # ── Core interface ────────────────────────────────────────────────────────

    def compute_turn_state(self, match: Match, agent_id: str) -> TurnState | None:
        if match.game_id != GAME_ID:
            return None
        if match.status != MatchStatus.RUNNING:
            return self._not_running_turn_state(match, agent_id)

        self._ensure_valuations(match)
        phase_name, current_turn_agent_id, is_my_turn = self._get_phase_and_turn_info(match, agent_id)
        messages = messages_visible_to(match.messages, agent_id)
        allowed_actions = build_allowed_actions(match.spec, phase_name, is_my_turn)

        bids = match.game_state.get("bids") or {}
        if agent_id in bids:
            allowed_actions = [a for a in allowed_actions if a.action_type != "submit_bid"]

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

    def apply_action(self, match: Match, agent_id: str, action: Action) -> ActionResult:
        err = self._check_apply_preconditions(match, agent_id, GAME_ID)
        if err is not None:
            return err

        n = len(match.agent_ids)
        if n == 0:
            return action_error(ActionError.MATCH_NOT_RUNNING, "No agents in match")

        spec = match.spec
        phase = spec.phases[match.current_phase_index] if spec.phases else None
        if not phase:
            return action_error(ActionError.MATCH_NOT_RUNNING, "No active phase")

        # Enforce turn order
        if phase.turn_order != TurnOrder.RANDOM:
            current = match.agent_ids[match.current_turn_index]
            if agent_id != current:
                return action_error(ActionError.NOT_YOUR_TURN, f"It is {current}'s turn")

        current_phase = match.game_state.get("phase", "chat")

        # ── Chat phase actions ────────────────────────────────────────────────
        if current_phase == "chat":
            if action.action_type == "submit_bid":
                return action_error(ActionError.GAME_RULE_VIOLATION, "Cannot bid during chat phase — wait for auction phase")

            if action.action_type in ("message_only", "pass"):
                match.game_state.setdefault("action_history", []).append({
                    "agent_id": agent_id,
                    "action": action.action_type,
                    "phase": "chat",
                    "round": match.current_round,
                })
                match.game_state["chat_turns"] = match.game_state.get("chat_turns", 0) + 1
                self._advance_turn(match)
                self._try_phase_transition(match)
                return action_ok()

            return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown action: {action.action_type}")

        # ── Auction phase actions ─────────────────────────────────────────────
        if current_phase == "auction":
            if action.action_type == "submit_bid":
                bids = match.game_state.get("bids") or {}
                if agent_id in bids:
                    return action_error(ActionError.GAME_RULE_VIOLATION, "You have already submitted a bid")
                bid = action.payload.get("bid")
                if bid is None:
                    return action_error(ActionError.INVALID_PAYLOAD, "bid is required")
                try:
                    bid = float(bid)
                except (TypeError, ValueError):
                    return action_error(ActionError.INVALID_PAYLOAD, "bid must be a number")
                if bid < 0:
                    return action_error(ActionError.INVALID_PAYLOAD, "bid must be >= 0")

                bids[agent_id] = bid
                match.game_state["bids"] = bids
                match.game_state.setdefault("action_history", []).append({
                    "agent_id": agent_id,
                    "action": "submit_bid",
                    "phase": "auction",
                    "round": match.current_round,
                })

                if all(aid in bids for aid in match.agent_ids):
                    self._resolve_auction(match)
                    return action_ok()

                self._advance_turn(match)
                self._check_auction_timeout(match)
                return action_ok()

            if action.action_type in ("message_only", "pass"):
                match.game_state.setdefault("action_history", []).append({
                    "agent_id": agent_id,
                    "action": action.action_type,
                    "phase": "auction",
                    "round": match.current_round,
                    "advances_turn": action.action_type == "pass",
                })
                if action.action_type == "pass":
                    self._advance_turn(match)
                    self._check_auction_timeout(match)
                return action_ok()

            return action_error(ActionError.INVALID_ACTION_TYPE, f"Unknown action: {action.action_type}")

        return action_error(ActionError.MATCH_NOT_RUNNING, f"Unknown phase: {current_phase}")

    def compute_outcome(self, match: Match) -> dict | None:
        if match.status == MatchStatus.FINISHED and match.outcome is not None:
            return match.outcome
        return None