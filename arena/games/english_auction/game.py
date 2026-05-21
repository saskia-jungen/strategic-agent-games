"""
English Auction game engine — ascending open-bid auction.

Price starts at start_price and rises each time an agent raises.
Agents openly raise bids or fold (drop out permanently).
Last agent standing wins and pays their final bid.
If only one agent remains (opponent folds), that agent wins.
If nobody raises and only one bid exists, highest bidder wins.

Winner utility = valuation - final_bid
Loser utility  = 0  (English auction: losers pay nothing)
Timeout with no bids = everyone gets 0.
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

GAME_ID = "english-auction"


class EnglishAuctionGame(Game):
    """
    English (ascending) Auction.

    Price starts low and rises as agents outbid each other.
    An agent can raise_bid (must exceed current highest bid by at least min_increment)
    or fold (permanently exit). Last agent with an active bid wins.

    Strategic tension: keep bidding to win vs fold to avoid overpaying.
    Dominant strategy: bid up to your valuation, fold above it.
    With asymmetric valuations the higher-valuation agent wins but pays
    just above the lower agent's valuation.
    """

    def __init__(
        self,
        *,
        max_rounds: int = 20,
        start_price: float = 0.0,
        min_increment: float = 5.0,
        rv1: float = 80.0,
        rv2: float = 65.0,
    ) -> None:
        self._max_rounds    = max_rounds
        self._start_price   = start_price
        self._min_increment = min_increment
        self._rv1           = rv1
        self._rv2           = rv2

    @classmethod
    def from_params(cls, game_params: dict, agent_ids: list[str]) -> "EnglishAuctionGame":
        return cls(
            max_rounds    = game_params.get("max_rounds",    20),
            start_price   = game_params.get("start_price",   0.0),
            min_increment = game_params.get("min_increment", 5.0),
            rv1           = game_params.get("rv1",          80.0),
            rv2           = game_params.get("rv2",          65.0),
        )

    def get_metadata(self) -> dict:
        return {
            **super().get_metadata(),
            "max_rounds":    self._max_rounds,
            "start_price":   self._start_price,
            "min_increment": self._min_increment,
        }

    def spec(self) -> GameSpec:
        return GameSpec(
            game_id=GAME_ID,
            name="English Auction (Ascending Open Bid)",
            min_agents=2,
            description=(
                f"Agents openly raise bids starting from {self._start_price}. "
                f"Each raise must exceed the current highest bid by at least {self._min_increment}. "
                f"An agent can fold to permanently exit. Last agent standing wins and pays their bid. "
                f"Winner utility = valuation - bid_paid. Loser utility = 0. "
                f"Optimal strategy: keep bidding while price < your valuation, fold above it."
            ),
            phases=[
                Phase(
                    name="auction",
                    turn_order=TurnOrder.ROUND_ROBIN,
                    allowed_action_types=["raise_bid", "fold", "pass", "message_only"],
                    max_rounds=self._max_rounds,
                ),
            ],
            action_types=[
                ActionTypeDef(
                    name="raise_bid",
                    description=(
                        f"Raise the current bid. Your bid must exceed the current highest bid "
                        f"by at least {self._min_increment}. "
                        f"Payload: {{\"amount\": <number>}}"
                    ),
                    payload_schema={"amount": {"type": "number", "minimum": 0}},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="fold",
                    description=(
                        "Permanently exit the auction. "
                        "If all other agents have folded, the last remaining bidder wins."
                    ),
                    payload_schema={},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="message_only",
                    description="Send a public message without advancing the auction.",
                    payload_schema={},
                    is_message=False,
                ),
            ],
            outcome_rule=OutcomeRule.ENGINE,
            initial_game_state={
                "current_bid":    self._start_price,
                "highest_bidder": None,
                "bids":           {},    # {agent_id: latest_bid}
                "folded":         [],    # agent_ids who have folded
                "action_history": [],
                "resolved":       False,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_valuation(self, match: Match, agent_id: str) -> float:
        idx = list(match.agent_ids).index(agent_id) if agent_id in match.agent_ids else 0
        return self._rv1 if idx == 0 else self._rv2

    def _active_agents(self, match: Match) -> list[str]:
        folded = match.game_state.get("folded", [])
        return [a for a in match.agent_ids if a not in folded]

    def _min_next_bid(self, match: Match) -> float:
        current = match.game_state.get("current_bid", self._start_price)
        return round(current + self._min_increment, 4)

    def _visible_game_state(self, match: Match, agent_id: str) -> dict:
        g               = match.game_state
        current_bid     = g.get("current_bid", self._start_price)
        highest_bidder  = g.get("highest_bidder")
        my_valuation    = self._get_valuation(match, agent_id)
        min_next        = self._min_next_bid(match)
        active          = self._active_agents(match)
        profit_if_win   = round(my_valuation - current_bid, 4)
        i_am_winning    = highest_bidder == agent_id

        return {
            "agent_ids":        list(match.agent_ids),
            "current_bid":      current_bid,
            "highest_bidder":   highest_bidder,
            "i_am_winning":     i_am_winning,
            "bids":             dict(g.get("bids", {})),
            "folded":           list(g.get("folded", [])),
            "active_agents":    active,
            "current_round":    match.current_round,
            "max_rounds":       self._max_rounds,
            "my_valuation":     my_valuation,
            "min_next_bid":     min_next,
            "profit_if_win_now": profit_if_win,
            "action_history":   g.get("action_history", []),
            "resolved":         g.get("resolved", False),
            "strategy_hint": (
                f"{'You are currently winning at {current_bid}. Hold or outbid if challenged.' if i_am_winning else 'You are NOT winning.'} "
                f"Raise to at least {min_next} to take the lead. "
                f"Fold if price exceeds your valuation ({my_valuation}). "
                f"Optimal: keep bidding while price < valuation, fold above it. "
                f"If opponent folds, you win at current price."
            ),
        }

    def _advance_turn(self, match: Match) -> None:
        n = len(match.agent_ids)
        if n == 0:
            return
        # Skip folded agents
        for _ in range(n):
            match.current_turn_index = (match.current_turn_index + 1) % n
            if match.current_turn_index == 0:
                match.current_round += 1
            if match.agent_ids[match.current_turn_index] not in match.game_state.get("folded", []):
                break

    def _check_timeout(self, match: Match) -> None:
        phase = match.spec.phases[match.current_phase_index] if match.spec.phases else None
        g = match.game_state
        # Hard stop: use action count as turn proxy (more reliable than round counter)
        action_count = len(g.get("action_history", []))
        max_actions  = (phase.max_rounds * len(match.agent_ids)) if phase and phase.max_rounds else 60
        timed_out = (
            (phase and phase.max_rounds is not None and match.current_round >= phase.max_rounds)
            or action_count >= max_actions
        )
        if timed_out:
            if g.get("highest_bidder"):
                self._resolve_winner(match, g["highest_bidder"], reason="timeout_highest_bidder")
            else:
                match.outcome = {
                    "payoffs": [{"agent_id": aid, "utility": 0.0} for aid in match.agent_ids],
                    "reason":  "timeout_no_bids",
                }
                match.status = MatchStatus.FINISHED

    def _resolve_winner(self, match: Match, winner_id: str, reason: str = "last_standing") -> None:
        g          = match.game_state
        bid_paid   = g.get("bids", {}).get(winner_id, g.get("current_bid", 0))
        valuation  = self._get_valuation(match, winner_id)
        g["resolved"] = True

        payoffs = []
        for aid in match.agent_ids:
            if aid == winner_id:
                utility = round(valuation - bid_paid, 4)
            else:
                utility = 0.0
            payoffs.append({"agent_id": aid, "utility": utility, "won": aid == winner_id})

        match.outcome = {
            "payoffs":   payoffs,
            "winner":    winner_id,
            "bid_paid":  bid_paid,
            "reason":    reason,
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

        # Folded agents can't act
        if agent_id in match.game_state.get("folded", []):
            allowed_actions = []

        # Winning agent doesn't need to raise (but can message)
        # Remove fold if agent hasn't bid yet (nothing to lose by waiting)

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

        # Enforce turn order (skip folded agents)
        if phase.turn_order != TurnOrder.RANDOM:
            current = match.agent_ids[match.current_turn_index]
            if agent_id != current:
                return action_error(ActionError.NOT_YOUR_TURN, f"It is {current}'s turn")

        # Folded agents cannot act
        if agent_id in match.game_state.get("folded", []):
            return action_error(ActionError.GAME_RULE_VIOLATION, "You have folded and cannot act")

        g = match.game_state

        # ── message_only ──────────────────────────────────────────────────────
        if action.action_type == "message_only":
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "message_only",
                "round": match.current_round,
            })
            return action_ok()

        # ── pass — winning agent uses this to yield turn to opponent ──────────
        if action.action_type == "pass":
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "pass",
                "round": match.current_round,
            })
            self._advance_turn(match)
            self._check_timeout(match)
            return action_ok()

        # ── fold ──────────────────────────────────────────────────────────────
        if action.action_type == "fold":
            g.setdefault("folded", []).append(agent_id)
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "fold",
                "round": match.current_round,
            })
            self._advance_turn(match)

            # Check if only one active agent remains
            active = self._active_agents(match)
            if len(active) == 1:
                self._resolve_winner(match, active[0], reason="last_standing")
            elif len(active) == 0:
                # Everyone folded — no winner
                match.outcome = {
                    "payoffs": [{"agent_id": aid, "utility": 0.0} for aid in match.agent_ids],
                    "reason":  "all_folded",
                }
                match.status = MatchStatus.FINISHED
            else:
                self._check_timeout(match)
            return action_ok()

        # ── raise_bid ─────────────────────────────────────────────────────────
        if action.action_type == "raise_bid":
            amount = action.payload.get("amount")
            if amount is None:
                return action_error(ActionError.INVALID_PAYLOAD, "'amount' is required")
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                return action_error(ActionError.INVALID_PAYLOAD, "'amount' must be a number")

            min_next = self._min_next_bid(match)
            if amount < min_next:
                return action_error(
                    ActionError.INVALID_PAYLOAD,
                    f"Bid {amount} too low. Must be at least {min_next} "
                    f"(current {g['current_bid']} + increment {self._min_increment})"
                )

            my_valuation = self._get_valuation(match, agent_id)
            if amount > my_valuation * 1.5:
                # Soft warning in action_history but allow it — agents can overbid
                pass

            g["current_bid"]    = amount
            g["highest_bidder"] = agent_id
            g.setdefault("bids", {})[agent_id] = amount
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "raise_bid",
                "amount": amount, "round": match.current_round,
            })

            self._advance_turn(match)
            self._check_timeout(match)
            return action_ok()

        return action_error(ActionError.INVALID_ACTION_TYPE,
                            f"Unknown action: {action.action_type}")

    def compute_outcome(self, match: Match) -> dict | None:
        if match.status == MatchStatus.FINISHED and match.outcome is not None:
            return match.outcome
        return None