"""
Dutch Auction game engine.

Price starts high and drops by `decrement` each round.
First agent to accept wins and pays the current price.
If price hits 0 (or below min_price) with no acceptance, everyone gets 0.

Winner utility = valuation - price_paid
Loser utility  = 0
Timeout (no acceptance) = everyone gets 0
"""

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

GAME_ID = "dutch-auction"


class DutchAuctionGame(Game):
    """
    Dutch Auction: descending-price auction.

    The auctioneer starts at start_price and drops the price by decrement
    each round. The FIRST agent to call accept wins and pays the current
    price. Waiting saves money but risks the opponent jumping in first.

    Strategic tension: accept early (pay more, guaranteed win) vs wait
    (save money, risk losing to a faster opponent).
    """

    def __init__(
        self,
        *,
        max_rounds: int = 10,
        start_price: float = 100.0,
        decrement: float = 5.0,
        min_price: float = 0.0,
        rv1: float = 80.0,   
        rv2: float = 80.0,
    ) -> None:
        self._max_rounds   = max_rounds
        self._start_price  = start_price
        self._decrement    = decrement
        self._min_price    = min_price
        self._rv1          = rv1
        self._rv2          = rv2

    @classmethod
    def from_params(cls, game_params: dict, agent_ids: list[str]) -> "DutchAuctionGame":
        return cls(
            max_rounds  = game_params.get("max_rounds",   10),
            start_price = game_params.get("start_price", 100.0),
            decrement   = game_params.get("decrement",     5.0),
            min_price   = game_params.get("min_price",     0.0),
            rv1         = game_params.get("rv1",          80.0),
            rv2         = game_params.get("rv2",          80.0),
        )

    def get_metadata(self) -> dict:
        return {
            **super().get_metadata(),
            "max_rounds":   self._max_rounds,
            "start_price":  self._start_price,
            "decrement":    self._decrement,
            "min_price":    self._min_price,
        }

    def spec(self) -> GameSpec:
        return GameSpec(
            game_id=GAME_ID,
            name="Dutch Auction (Descending Price)",
            min_agents=2,
            description=(
                f"Price starts at {self._start_price} and drops by {self._decrement} each round. "
                f"First agent to accept wins and pays the current price. "
                f"Winner utility = your_valuation - price_paid. Losers get 0. "
                f"Waiting saves money but risks losing to a faster opponent. "
                f"If price reaches {self._min_price} with no acceptance, everyone gets 0."
            ),
            phases=[
                Phase(
                    name="auction",
                    turn_order=TurnOrder.ROUND_ROBIN,
                    allowed_action_types=["accept", "pass", "message_only"],
                    max_rounds=self._max_rounds,
                ),
            ],
            action_types=[
                ActionTypeDef(
                    name="accept",
                    description=(
                        "Accept the current price and win the auction. "
                        "You pay the current price. First to accept wins."
                    ),
                    payload_schema={},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="pass",
                    description=(
                        "Pass this round. Price will drop by the decrement next round. "
                        "Risky — opponent may accept before you."
                    ),
                    payload_schema={},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="message_only",
                    description="Send a message without advancing the price clock.",
                    payload_schema={},
                    is_message=False,
                ),
            ],
            outcome_rule=OutcomeRule.ENGINE,
            initial_game_state={
                "current_price":  self._start_price,
                "winner":         None,
                "price_paid":     None,
                "action_history": [],
                "resolved":       False,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_valuation(self, match: Match, agent_id: str) -> float:
        """Return private valuation for this agent (rv1 for first, rv2 for second)."""
        idx = list(match.agent_ids).index(agent_id) if agent_id in match.agent_ids else 0
        return self._rv1 if idx == 0 else self._rv2

    def _current_price(self, match: Match) -> float:
        return match.game_state.get("current_price", self._start_price)

    def _drop_price(self, match: Match) -> None:
        """Drop price by decrement. Called after both agents have passed in a round."""
        g = match.game_state
        new_price = round(g["current_price"] - self._decrement, 4)
        g["current_price"] = max(new_price, self._min_price)

    def _visible_game_state(self, match: Match, agent_id: str) -> dict:
        g             = match.game_state
        current_price = g.get("current_price", self._start_price)
        my_valuation  = self._get_valuation(match, agent_id)
        profit_if_accept = round(my_valuation - current_price, 4)
        rounds_until_zero = int((current_price - self._min_price) / self._decrement) if self._decrement > 0 else 999

        return {
            "agent_ids":           list(match.agent_ids),
            "current_price":       current_price,
            "start_price":         self._start_price,
            "decrement":           self._decrement,
            "min_price":           self._min_price,
            "current_round":       match.current_round,
            "max_rounds":          self._max_rounds,
            "my_valuation":        my_valuation,
            "profit_if_accept_now": profit_if_accept,
            "rounds_until_zero":   rounds_until_zero,
            "winner":              g.get("winner"),
            "price_paid":          g.get("price_paid"),
            "action_history":      g.get("action_history", []),
            "resolved":            g.get("resolved", False),
            "strategy_hint": (
                f"Accept now → profit {profit_if_accept:.1f}. "
                f"Wait 1 round → price drops to {current_price - self._decrement:.1f}, "
                f"profit would be {profit_if_accept + self._decrement:.1f} — but opponent may accept first. "
                f"Price hits {self._min_price} in ~{rounds_until_zero} rounds then game ends with 0 for all."
            ),
        }

    def _advance_turn(self, match: Match) -> None:
        n = len(match.agent_ids)
        if n == 0:
            return
        prev_index = match.current_turn_index
        match.current_turn_index = (match.current_turn_index + 1) % n
        # When we complete a full round (wrap back to 0), drop the price
        if match.current_turn_index == 0:
            match.current_round += 1
            self._drop_price(match)

    def _check_timeout(self, match: Match) -> None:
        g = match.game_state
        # Timeout if max rounds exceeded
        phase = match.spec.phases[match.current_phase_index] if match.spec.phases else None
        if phase and phase.max_rounds is not None and match.current_round >= phase.max_rounds:
            self._resolve_no_winner(match, "max_rounds_exceeded")
            return
        # Timeout if price dropped to min
        if g.get("current_price", self._start_price) <= self._min_price:
            self._resolve_no_winner(match, "price_reached_minimum")

    def _resolve_no_winner(self, match: Match, reason: str) -> None:
        match.outcome = {
            "payoffs": [{"agent_id": aid, "utility": 0.0} for aid in match.agent_ids],
            "reason":  reason,
            "final_price": match.game_state.get("current_price"),
        }
        match.status = MatchStatus.FINISHED

    def _resolve_winner(self, match: Match, winner_id: str) -> None:
        g           = match.game_state
        price_paid  = g["current_price"]
        g["winner"]     = winner_id
        g["price_paid"] = price_paid
        g["resolved"]   = True

        payoffs = []
        for aid in match.agent_ids:
            if aid == winner_id:
                valuation = self._get_valuation(match, aid)
                utility   = round(valuation - price_paid, 4)
            else:
                utility = 0.0
            payoffs.append({"agent_id": aid, "utility": utility,
                            "won": aid == winner_id})

        match.outcome = {
            "payoffs":    payoffs,
            "winner":     winner_id,
            "price_paid": price_paid,
            "reason":     "dutch_auction_accepted",
        }
        match.status = MatchStatus.FINISHED

    # ── Core interface ────────────────────────────────────────────────────────

    def compute_turn_state(self, match: Match, agent_id: str) -> TurnState | None:
        if match.game_id != GAME_ID:
            return None
        if match.status != MatchStatus.RUNNING:
            return self._not_running_turn_state(match, agent_id)

        phase_name, current_turn_agent_id, is_my_turn = self._get_phase_and_turn_info(match, agent_id)
        messages        = messages_visible_to(match.messages, agent_id)
        allowed_actions = build_allowed_actions(match.spec, phase_name, is_my_turn)

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

        phase = match.spec.phases[match.current_phase_index] if match.spec.phases else None
        if not phase:
            return action_error(ActionError.MATCH_NOT_RUNNING, "No active phase")

        
        if phase.turn_order != TurnOrder.RANDOM:
            current = match.agent_ids[match.current_turn_index]
            if agent_id != current:
                return action_error(ActionError.NOT_YOUR_TURN, f"It is {current}'s turn")

        g = match.game_state

        # ── message_only ──────────────────────────────────────────────────────
        if action.action_type == "message_only":
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "message_only",
                "price": g.get("current_price"), "round": match.current_round,
            })
            # message_only does NOT advance the turn or drop the price
            return action_ok()

        # ── pass ──────────────────────────────────────────────────────────────
        if action.action_type == "pass":
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "pass",
                "price": g.get("current_price"), "round": match.current_round,
            })
            self._advance_turn(match)
            self._check_timeout(match)
            return action_ok()

        # ── accept ────────────────────────────────────────────────────────────
        if action.action_type == "accept":
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "accept",
                "price": g.get("current_price"), "round": match.current_round,
            })
            self._resolve_winner(match, agent_id)
            return action_ok()

        return action_error(ActionError.INVALID_ACTION_TYPE,
                            f"Unknown action: {action.action_type}")

    def compute_outcome(self, match: Match) -> dict | None:
        if match.status == MatchStatus.FINISHED and match.outcome is not None:
            return match.outcome
        return None