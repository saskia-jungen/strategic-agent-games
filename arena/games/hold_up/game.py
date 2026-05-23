""" Both agents first choose relationship-specific investment amounts (invest phase).
  Surplus is computed as: surplus_base + surplus_multiplier * sum(investments).
  Agents then alternate offers to split the realized surplus (bargain phase).
  Underinvestment can arise because surplus gains are shared but investment costs are private.
  Game ends on agreement or timeout.
  NOTE: state.surplus is computed from investments at the start of the bargain phase,
  before any offer is made.

  TIMEOUT PROTECTION (engine-level):
  - On the LAST round of invest phase: any agent who hasn't invested is force-invested at 0.
  - On the LAST round of bargain phase:
      * pass / reject are blocked — agent must make_offer or accept instead.
      * If an offer is on the table and agent tries to reject/pass → force accept.
      * If no offer on the table and agent tries to pass → force 50/50 offer.
  - This guarantees both agents never silently time out to mutual 0 due to stalling.
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


GAME_ID = "hold-up"

INVEST_MAX_ROUNDS  = 5
BARGAIN_MAX_ROUNDS = 5


class HoldUpGame(Game):
    """Hold Up Game: Two agents decide whether to cooperate or defect.
        In the invest phase, both agents simultaneously choose how much to invest in a joint project.
        The total surplus generated depends on the sum of investments.
        In the bargain phase, agents alternate making offers to split the surplus.
        The game ends when an offer is accepted or if max rounds are exceeded.
        Utilities are determined by the final agreement and investments.
        This game captures the tension between individual incentives to underinvest and collective gains from cooperation.
        Strategic communication can help agents coordinate on better outcomes.
        Key challenge: balancing trust-building in the invest phase with competitive bargaining in the bargain phase.
    """

    def __init__(
        self,
        *,
        max_rounds: int = 10,
        investment_cost: float = 1.0,
        surplus_base: float = 0.0,
        surplus_multiplier: float = 0.3,
        turn_order: TurnOrder = TurnOrder.ROUND_ROBIN,
    ) -> None:
        self._max_rounds = max_rounds
        self._investment_cost = investment_cost
        self._surplus_base = surplus_base
        self._surplus_multiplier = surplus_multiplier
        self._turn_order = turn_order

    @classmethod
    def from_params(cls, game_params: dict, agent_ids: list[str]) -> "HoldUpGame":
        return cls(
            max_rounds=game_params.get("max_rounds", 10),
            investment_cost=game_params.get("investment_cost", 1.0),
            surplus_base=game_params.get("surplus_base", 0.0),
            surplus_multiplier=game_params.get("surplus_multiplier", 3.0),
        )

    def get_metadata(self):
        return {
            **super().get_metadata(),
            "max_rounds":        self._max_rounds,
            "investment_cost":   self._investment_cost,
            "surplus_base":      self._surplus_base,
            "surplus_multiplier": self._surplus_multiplier,
            "turn_order":        self._turn_order.value,
        }

    def spec(self) -> GameSpec:
        return GameSpec(
            game_id=GAME_ID,
            name="Hold-Up (Investment then Bargaining)",
            min_agents=2,
            max_agents=2,
            description=(
                f"Both agents first choose investment amounts (invest phase, up to {INVEST_MAX_ROUNDS} rounds). "
                f"Surplus = {self._surplus_base} + {self._surplus_multiplier} * sum(investments). "
                f"Agents then alternate offers to split the surplus (bargain phase, up to {BARGAIN_MAX_ROUNDS} rounds). "
                f"Underinvestment can arise because surplus gains are shared but investment costs are private. "
                f"Payoff = share_received - investment_cost * amount_invested. "
                f"Game ends on agreement or timeout (everyone gets 0). "
                f"Engine enforces last-round emergency actions to prevent silent timeout."
            ),
            phases=[
                Phase(
                    name="invest",
                    turn_order=self._turn_order,
                    allowed_action_types=["invest", "pass", "message_only"],
                    max_rounds=INVEST_MAX_ROUNDS,
                ),
                Phase(
                    name="bargain",
                    turn_order=TurnOrder.ROUND_ROBIN,
                    allowed_action_types=["make_offer", "accept", "reject", "message_only", "pass"],
                    max_rounds=BARGAIN_MAX_ROUNDS,
                ),
            ],
            action_types=[
                ActionTypeDef(
                    name="invest",
                    description="Choose your investment amount. Cost = investment_cost * amount. Can only invest once.",
                    payload_schema={"amount": {"type": "number", "minimum": 0}},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="make_offer",
                    description="Propose how to split the surplus. Payload must include shares for all agents summing to surplus.",
                    payload_schema={"split": {"type": "object"}},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="accept",
                    description="Accept the current offer on the table.",
                    payload_schema={},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="reject",
                    description="Reject the current offer on the table.",
                    payload_schema={},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="pass",
                    description="Pass your turn.",
                    payload_schema={},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="message_only",
                    description="Send a message without advancing the turn.",
                    payload_schema={},
                    is_message=False,
                ),
            ],
            outcome_rule=OutcomeRule.ENGINE,
            initial_game_state={
                "investments":    {},
                "surplus":        None,
                "offer":          None,
                "last_offer_by":  None,
                "action_history": [],
                "resolved":       False,
                "phase":          "invest",
            },
        )

    # ── Helper functions ──────────────────────────────────────────────────────

    def _compute_surplus(self, investments: dict) -> float:
        """surplus = surplus_base + surplus_multiplier * sum(investments)"""
        return round(
            self._surplus_base + self._surplus_multiplier * sum(investments.values()), 2
        )

    def _is_last_round(self, match: Match) -> bool:
        """True when we are on the final allowable round of the current phase."""
        phase = match.spec.phases[match.current_phase_index] if match.spec.phases else None
        if phase and phase.max_rounds is not None:
            return match.current_round >= phase.max_rounds - 1
        return False

    def _force_invest_uninvested(self, match: Match) -> None:
        """
        Last-round invest safety net: any agent who hasn't invested yet is
        automatically invested at 0 so the game can transition to bargain.
        Called at the START of the last invest round before normal action processing.
        """
        g = match.game_state
        investments = g.setdefault("investments", {})
        for aid in match.agent_ids:
            if aid not in investments:
                investments[aid] = 0.0
                g.setdefault("action_history", []).append({
                    "agent_id": aid,
                    "action":   "invest_forced",
                    "amount":   0.0,
                    "round":    match.current_round,
                    "reason":   "last_round_safety",
                })
        self._try_transition_to_bargain(match)

    def _make_50_50_split(self, match: Match, proposer_id: str) -> dict:
        """Return a valid 50/50 split dict for the current surplus."""
        g       = match.game_state
        surplus = g.get("surplus", 0.0) or 0.0
        agents  = list(match.agent_ids)
        half    = round(surplus / 2, 2)
        split   = {agents[0]: half, agents[1]: round(surplus - half, 2)}
        return split

    def _visible_game_state(self, match: Match, agent_id: str) -> dict:
        g = match.game_state
        investments   = g.get("investments", {})
        current_phase = g.get("phase", "invest")
        on_last_round = self._is_last_round(match)
        return {
            "num_agents":         len(match.agent_ids),
            "agent_ids":          list(match.agent_ids),
            "current_phase":      current_phase,
            "current_round":      match.current_round,
            "max_rounds_phase":   INVEST_MAX_ROUNDS if current_phase == "invest" else BARGAIN_MAX_ROUNDS,
            "is_last_round":      on_last_round,
            "investments":        investments,
            "my_investment":      investments.get(agent_id),
            "investment_cost":    self._investment_cost,
            "surplus_multiplier": self._surplus_multiplier,
            "surplus":            g.get("surplus"),
            "current_offer":      g.get("offer"),
            "last_offer_by":      g.get("last_offer_by"),
            "resolved":           g.get("resolved", False),
            "action_history":     g.get("action_history", []),
            "phase_hint": (
                (
                    f" Last round  of invest phase! Uninvested agents will be force-invested at 0. "
                    f"Invest now or lose the chance to grow the surplus."
                    if on_last_round else
                    f"INVEST phase: choose how much to invest. "
                    f"Each unit costs {self._investment_cost} but multiplies surplus by {self._surplus_multiplier}. "
                    f"Higher investments = bigger pie to split."
                )
                if current_phase == "invest"
                else
                (
                    f"Last Round of bargain phase! pass and reject are BLOCKED. "
                    f"You MUST make_offer or accept — timeout means both get 0. "
                    f"surplus={g.get('surplus')}."
                    if on_last_round else
                    f"BARGAIN phase: surplus={g.get('surplus')}. "
                    f"Make offers to split it. Remember your investment cost is deducted from your payoff."
                )
            ),
        }

    def _advance_turn(self, match: Match) -> None:
        n = len(match.agent_ids)
        if n == 0:
            return
        match.current_turn_index = (match.current_turn_index + 1) % n
        if match.current_turn_index == 0:
            match.current_round += 1

    def _try_transition_to_bargain(self, match: Match) -> None:
        """Switch from invest → bargain once all agents have invested."""
        g = match.game_state
        if g.get("phase") != "invest":
            return
        if len(g.get("investments", {})) < len(match.agent_ids):
            return
        surplus = self._compute_surplus(g["investments"])
        g["surplus"] = surplus
        g["phase"]   = "bargain"
        match.current_phase_index = 1
        match.current_round       = 0
        match.current_turn_index  = 0

    def _check_timeout(self, match: Match) -> None:
        """End match if current phase exceeds max rounds."""
        phase = match.spec.phases[match.current_phase_index] if match.spec.phases else None
        if phase and phase.max_rounds is not None and match.current_round >= phase.max_rounds:
            match.outcome = {
                "payoffs": [{"agent_id": aid, "utility": 0.0} for aid in match.agent_ids],
                "reason":  "max_rounds_exceeded",
            }
            match.status = MatchStatus.FINISHED

    def _resolve(self, match: Match) -> None:
        """Finalize the game when an offer is accepted."""
        g           = match.game_state
        offer       = g.get("offer", {})
        investments = g.get("investments", {})
        payoffs = []
        for aid in match.agent_ids:
            share      = offer.get(aid, 0)
            investment = investments.get(aid, 0)
            utility    = round(share - self._investment_cost * investment, 2)
            payoffs.append({"agent_id": aid, "share": share,
                            "investment": investment, "utility": utility})
        g["resolved"] = True
        match.outcome = {
            "payoffs":     payoffs,
            "reason":      "hold_up_agreement",
            "investments": dict(investments),
            "surplus":     g.get("surplus"),
            "final_offer": dict(offer),
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

        # Remove invest if agent already invested
        investments = match.game_state.get("investments", {})
        if agent_id in investments:
            allowed_actions = [a for a in allowed_actions if a.action_type != "invest"]

        # Remove accept/reject if no offer on table
        offer = match.game_state.get("offer")
        if not offer:
            allowed_actions = [a for a in allowed_actions
                               if a.action_type not in ("accept", "reject")]

        # Remove accept if I made the offer
        if offer and match.game_state.get("last_offer_by") == agent_id:
            allowed_actions = [a for a in allowed_actions if a.action_type != "accept"]

        # ── Last-round bargain: strip pass and reject from allowed list ────────
        # Agents see only make_offer / accept (+ message_only) so they know
        # they cannot stall. Engine enforces this in apply_action too.
        if phase_name == "bargain" and self._is_last_round(match) and is_my_turn:
            allowed_actions = [a for a in allowed_actions
                               if a.action_type not in ("pass", "reject")]

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

        # Enforce turn order
        if phase.turn_order != TurnOrder.RANDOM:
            current = match.agent_ids[match.current_turn_index]
            if agent_id != current:
                return action_error(ActionError.NOT_YOUR_TURN, f"It is {current}'s turn")

        g             = match.game_state
        current_phase = g.get("phase", "invest")
        last_round    = self._is_last_round(match)

        # ── INVEST PHASE: last-round safety — force-invest anyone still out ───
        if current_phase == "invest" and last_round:
            self._force_invest_uninvested(match)
            # If transition already happened (both invested), game may now be in bargain
            if g.get("phase") == "bargain":
                # Re-read phase after transition
                current_phase = "bargain"
                last_round    = self._is_last_round(match)

        # ── message_only (both phases) ────────────────────────────────────────
        if action.action_type == "message_only":
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "message_only",
                "phase": current_phase, "round": match.current_round,
            })
            return action_ok()

        # ── pass ──────────────────────────────────────────────────────────────
        if action.action_type == "pass":
            # BARGAIN last round: pass → redirect to emergency offer or accept
            if current_phase == "bargain" and last_round:
                offer = g.get("offer")
                if offer and g.get("last_offer_by") != agent_id:
                    # Accept whatever is on the table — beats mutual 0
                    g.setdefault("action_history", []).append({
                        "agent_id": agent_id, "action": "accept_forced",
                        "reason": "pass_on_last_round", "round": match.current_round,
                    })
                    self._resolve(match)
                    return action_ok()
                else:
                    # No offer or we made it — force a 50/50
                    split = self._make_50_50_split(match, agent_id)
                    g["offer"]        = split
                    g["last_offer_by"] = agent_id
                    g.setdefault("action_history", []).append({
                        "agent_id": agent_id, "action": "make_offer_forced",
                        "split": split, "reason": "pass_on_last_round",
                        "round": match.current_round,
                    })
                    self._advance_turn(match)
                    self._check_timeout(match)
                    return action_ok()

            # Normal pass
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "pass",
                "phase": current_phase, "round": match.current_round,
            })
            self._advance_turn(match)
            self._check_timeout(match)
            return action_ok()

        # ── INVEST PHASE ──────────────────────────────────────────────────────
        if current_phase == "invest":
            if action.action_type == "invest":
                investments = g.get("investments", {})
                if agent_id in investments:
                    return action_error(ActionError.GAME_RULE_VIOLATION,
                                        "You have already invested")
                amount = action.payload.get("amount")
                if amount is None:
                    return action_error(ActionError.INVALID_PAYLOAD, "amount is required")
                try:
                    amount = float(amount)
                except (TypeError, ValueError):
                    return action_error(ActionError.INVALID_PAYLOAD, "amount must be a number")
                if amount < 0:
                    return action_error(ActionError.INVALID_PAYLOAD, "amount must be >= 0")

                investments[agent_id] = amount
                g["investments"] = investments
                g.setdefault("action_history", []).append({
                    "agent_id": agent_id, "action": "invest",
                    "amount": amount, "round": match.current_round,
                })
                self._advance_turn(match)
                self._try_transition_to_bargain(match)
                if match.status != MatchStatus.FINISHED:
                    self._check_timeout(match)
                return action_ok()

            if action.action_type in ("make_offer", "accept", "reject"):
                return action_error(ActionError.GAME_RULE_VIOLATION,
                                    "Cannot bargain during invest phase")

            return action_error(ActionError.INVALID_ACTION_TYPE,
                                f"Unknown action: {action.action_type}")

        # ── BARGAIN PHASE ─────────────────────────────────────────────────────
        if current_phase == "bargain":
            surplus = g.get("surplus")
            if surplus is None:
                return action_error(ActionError.MATCH_NOT_RUNNING, "Surplus not yet computed")

            if action.action_type == "make_offer":
                split = action.payload.get("split")
                if not split or not isinstance(split, dict):
                    return action_error(ActionError.INVALID_PAYLOAD,
                                        "split must be a dict of {agent_id: share}")
                total_split = sum(split.values())
                if abs(total_split - surplus) > 0.01:
                    return action_error(ActionError.INVALID_PAYLOAD,
                                        f"Shares must sum to surplus ({surplus}), got {total_split}")
                for aid in match.agent_ids:
                    if aid not in split:
                        return action_error(ActionError.INVALID_PAYLOAD,
                                            f"split must include agent {aid}")

                g["offer"]         = split
                g["last_offer_by"] = agent_id
                g.setdefault("action_history", []).append({
                    "agent_id": agent_id, "action": "make_offer",
                    "split": split, "round": match.current_round,
                })
                self._advance_turn(match)
                self._check_timeout(match)
                return action_ok()

            if action.action_type == "accept":
                if not g.get("offer"):
                    return action_error(ActionError.GAME_RULE_VIOLATION, "No offer to accept")
                if g.get("last_offer_by") == agent_id:
                    return action_error(ActionError.GAME_RULE_VIOLATION,
                                        "Cannot accept your own offer")
                g.setdefault("action_history", []).append({
                    "agent_id": agent_id, "action": "accept",
                    "round": match.current_round,
                })
                self._resolve(match)
                return action_ok()

            if action.action_type == "reject":
                # LAST ROUND: reject → force accept if offer exists, else force 50/50
                if last_round:
                    offer = g.get("offer")
                    if offer and g.get("last_offer_by") != agent_id:
                        g.setdefault("action_history", []).append({
                            "agent_id": agent_id, "action": "accept_forced",
                            "reason": "reject_on_last_round", "round": match.current_round,
                        })
                        self._resolve(match)
                        return action_ok()
                    else:
                        split = self._make_50_50_split(match, agent_id)
                        g["offer"]         = split
                        g["last_offer_by"] = agent_id
                        g.setdefault("action_history", []).append({
                            "agent_id": agent_id, "action": "make_offer_forced",
                            "split": split, "reason": "reject_on_last_round",
                            "round": match.current_round,
                        })
                        self._advance_turn(match)
                        self._check_timeout(match)
                        return action_ok()

                # Normal reject
                if not g.get("offer"):
                    return action_error(ActionError.GAME_RULE_VIOLATION, "No offer to reject")
                g["offer"]         = None
                g["last_offer_by"] = None
                g.setdefault("action_history", []).append({
                    "agent_id": agent_id, "action": "reject",
                    "round": match.current_round,
                })
                self._advance_turn(match)
                self._check_timeout(match)
                return action_ok()

            if action.action_type == "invest":
                return action_error(ActionError.GAME_RULE_VIOLATION,
                                    "Cannot invest during bargain phase")

            return action_error(ActionError.INVALID_ACTION_TYPE,
                                f"Unknown action: {action.action_type}")

        return action_error(ActionError.MATCH_NOT_RUNNING, f"Unknown phase: {current_phase}")

    def compute_outcome(self, match: Match) -> dict | None:
        if match.status == MatchStatus.FINISHED and match.outcome is not None:
            return match.outcome
        return None