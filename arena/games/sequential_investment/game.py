"""
Sequential Investment game engine.

Leader invests first (observable by follower).
Follower then invests knowing the leader's choice.

Joint benefit depends on interaction type:
  complements: payoff_scale * leader_inv * follower_inv
  substitutes: payoff_scale * (leader_inv + follower_inv)

Each agent payoff = 0.5 * joint_benefit - investment_cost * own_investment

Strategic tension:
  Complements — follower mirrors leader; leader signals high to pull follower up.
  Substitutes — follower free-rides; leader under-invests knowing follower will top up.
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

GAME_ID = "sequential-investment"


class SequentialInvestmentGame(Game):
    """
    Sequential Investment: Leader-Follower investment game.

    Phase 1 (leader_invest): Only the leader acts.
    Phase 2 (follower_invest): Only the follower acts, with full knowledge of leader's choice.

    Under complements the follower wants to match the leader's investment;
    under substitutes the follower wants to free-ride on the leader.
    The leader anticipates this and adjusts accordingly.
    """

    def __init__(
        self,
        *,
        max_rounds: int = 6,
        investment_cost: float = 1.0,
        interaction: str = "complements",   # "complements" | "substitutes"
        payoff_scale: float = 10.0,
        role_map: dict[str, str] | None = None,  # {"leader": agent_id, "follower": agent_id}
    ) -> None:
        self._max_rounds      = max_rounds
        self._investment_cost = investment_cost
        self._interaction     = interaction
        self._payoff_scale    = payoff_scale
        self._role_map        = role_map  

    @classmethod
    def from_params(cls, game_params: dict, agent_ids: list[str]) -> "SequentialInvestmentGame":
        role_map = game_params.get("role_map")
        
        if not role_map and len(agent_ids) >= 2:
            shuffled = list(agent_ids)
            random.shuffle(shuffled)
            role_map = {"leader": shuffled[0], "follower": shuffled[1]}
        return cls(
            max_rounds      = game_params.get("max_rounds",      6),
            investment_cost = game_params.get("investment_cost", 1.0),
            interaction     = game_params.get("interaction",     "complements"),
            payoff_scale    = game_params.get("payoff_scale",    10.0),
            role_map        = role_map,
        )

    def get_metadata(self) -> dict:
        return {
            **super().get_metadata(),
            "max_rounds":      self._max_rounds,
            "investment_cost": self._investment_cost,
            "interaction":     self._interaction,
            "payoff_scale":    self._payoff_scale,
            "role_map":        self._role_map,
        }

    def _leader_id(self, match: Match) -> str | None:
        if self._role_map:
            return self._role_map.get("leader")
        return match.game_state.get("leader_id")

    def _follower_id(self, match: Match) -> str | None:
        if self._role_map:
            return self._role_map.get("follower")
        return match.game_state.get("follower_id")

    def _role_of(self, match: Match, agent_id: str) -> str:
        if agent_id == self._leader_id(match):
            return "leader"
        if agent_id == self._follower_id(match):
            return "follower"
        return "unknown"

    def spec(self) -> GameSpec:
        if self._interaction == "complements":
            benefit_formula = f"{self._payoff_scale} × leader_inv × follower_inv"
            strategy_note   = (
                "COMPLEMENTS: if either invests 0, joint benefit = 0 for both. "
                "Follower should mirror leader. Leader signals high to pull follower up."
            )
        else:
            benefit_formula = f"{self._payoff_scale} × (leader_inv + follower_inv)"
            strategy_note   = (
                "SUBSTITUTES: investments are additive. "
                "Follower may free-ride on leader. Leader under-invests anticipating this."
            )

        return GameSpec(
            game_id=GAME_ID,
            name="Sequential Investment (Leader then Follower)",
            min_agents=2,
            max_agents=2,
            description=(
                f"Leader invests first (observable by follower). "
                f"Follower invests knowing leader's choice. "
                f"Joint benefit = {benefit_formula}. "
                f"Each agent payoff = 0.5 × joint_benefit − investment_cost × own_investment. "
                f"{strategy_note}"
            ),
            phases=[
                Phase(
                    name="leader_invest",
                    turn_order=TurnOrder.ROUND_ROBIN,
                    allowed_action_types=["invest", "pass", "message_only"],
                    max_rounds=max(3, self._max_rounds // 2),
                ),
                Phase(
                    name="follower_invest",
                    turn_order=TurnOrder.ROUND_ROBIN,
                    allowed_action_types=["invest", "pass", "message_only"],
                    max_rounds=max(3, self._max_rounds // 2),
                ),
            ],
            action_types=[
                ActionTypeDef(
                    name="invest",
                    description=(
                        "Submit your investment amount (>= 0). "
                        "Cost = investment_cost × amount. Can only invest once per game."
                    ),
                    payload_schema={"amount": {"type": "number", "minimum": 0}},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="pass",
                    description="Pass your turn without investing.",
                    payload_schema={},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="message_only",
                    description="Send a public message without advancing the turn.",
                    payload_schema={},
                    is_message=False,
                ),
            ],
            outcome_rule=OutcomeRule.ENGINE,
            initial_game_state={
                "investments":    {},
                "leader_id":      self._role_map.get("leader")   if self._role_map else None,
                "follower_id":    self._role_map.get("follower") if self._role_map else None,
                "action_history": [],
                "resolved":       False,
                "interaction":    self._interaction,
                "payoff_scale":   self._payoff_scale,
                "investment_cost": self._investment_cost,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _compute_joint_benefit(self, leader_inv: float, follower_inv: float) -> float:
        if self._interaction == "complements":
            return self._payoff_scale * leader_inv * follower_inv
        else:  
            return self._payoff_scale * (leader_inv + follower_inv)

    def _current_phase_name(self, match: Match) -> str:
        phases = match.spec.phases
        if phases and match.current_phase_index < len(phases):
            return phases[match.current_phase_index].name
        return "unknown"

    def _is_leader_turn(self, match: Match) -> bool:
        return self._current_phase_name(match) == "leader_invest"

    def _active_agent_for_phase(self, match: Match) -> str | None:
        """Return the agent whose turn it is for the current phase."""
        if self._is_leader_turn(match):
            return self._leader_id(match)
        return self._follower_id(match)

    def _visible_game_state(self, match: Match, agent_id: str) -> dict:
        g             = match.game_state
        investments   = g.get("investments", {})
        leader_id     = self._leader_id(match)
        follower_id   = self._follower_id(match)
        my_role       = self._role_of(match, agent_id)
        current_phase = self._current_phase_name(match)

        leader_inv   = investments.get(leader_id)
        follower_inv = investments.get(follower_id)

        
        hint = ""
        if leader_inv is not None and follower_inv is None and my_role == "follower":
            if self._interaction == "complements":
                optimal = (self._investment_cost * 2) / self._payoff_scale if leader_inv > 0 else 0
                hint = (
                    f"Leader invested {leader_inv}. "
                    f"COMPLEMENTS: your marginal benefit per unit = "
                    f"{0.5 * self._payoff_scale * leader_inv:.2f}, "
                    f"your cost per unit = {self._investment_cost}. "
                    f"Invest if 0.5 × {self._payoff_scale} × {leader_inv} > {self._investment_cost}. "
                    f"Nash: invest up to where MB = MC."
                )
            else:
                mb = 0.5 * self._payoff_scale
                hint = (
                    f"Leader invested {leader_inv}. "
                    f"SUBSTITUTES: your marginal benefit per unit = {mb:.1f}, "
                    f"cost per unit = {self._investment_cost}. "
                    f"{'Invest freely — MB > cost.' if mb > self._investment_cost else 'Free-ride — MB < cost, invest 0.'}"
                )
        elif my_role == "leader" and current_phase == "leader_invest":
            if self._interaction == "complements":
                hint = (
                    f"COMPLEMENTS: follower will mirror you. Higher investment → bigger joint benefit. "
                    f"Optimal: invest where 0.5 × {self._payoff_scale} × follower_response = {self._investment_cost}."
                )
            else:
                hint = (
                    f"SUBSTITUTES: follower will free-ride. "
                    f"Invest only if 0.5 × {self._payoff_scale} > {self._investment_cost} "
                    f"(= {0.5 * self._payoff_scale > self._investment_cost})."
                )

        return {
            "agent_ids":       list(match.agent_ids),
            "my_role":         my_role,
            "leader_id":       leader_id,
            "follower_id":     follower_id,
            "current_phase":   current_phase,
            "current_round":   match.current_round,
            "interaction":     self._interaction,
            "payoff_scale":    self._payoff_scale,
            "investment_cost": self._investment_cost,
            "investments":     {k: v for k, v in investments.items()}, 
            "leader_invested": leader_inv,     
            "my_investment":   investments.get(agent_id),
            "i_have_invested": agent_id in investments,
            "action_history":  g.get("action_history", []),
            "resolved":        g.get("resolved", False),
            "strategy_hint":   hint,
        }

    def _advance_turn(self, match: Match) -> None:
        n = len(match.agent_ids)
        if n == 0:
            return
        match.current_turn_index = (match.current_turn_index + 1) % n
        if match.current_turn_index == 0:
            match.current_round += 1

    def _check_timeout(self, match: Match) -> None:
        phase = match.spec.phases[match.current_phase_index] if match.spec.phases else None
        if phase and phase.max_rounds is not None and match.current_round >= phase.max_rounds:
            match.outcome = {
                "payoffs": [{"agent_id": aid, "utility": 0.0} for aid in match.agent_ids],
                "reason":  "max_rounds_exceeded",
            }
            match.status = MatchStatus.FINISHED

    def _try_transition_to_follower(self, match: Match) -> None:
        """Switch from leader_invest → follower_invest once leader has invested."""
        g = match.game_state
        if self._current_phase_name(match) != "leader_invest":
            return
        leader_id = self._leader_id(match)
        if leader_id not in g.get("investments", {}):
            return
        match.current_phase_index = 1
        match.current_round       = 0
        follower_id = self._follower_id(match)
        agents = list(match.agent_ids)
        match.current_turn_index = agents.index(follower_id) if follower_id in agents else 0
        g["current_phase"] = "follower_invest"

    def _try_resolve(self, match: Match) -> None:
        """Resolve once both agents have invested."""
        g           = match.game_state
        investments = g.get("investments", {})
        if len(investments) < len(match.agent_ids):
            return

        leader_id   = self._leader_id(match)
        follower_id = self._follower_id(match)
        leader_inv   = investments.get(leader_id, 0.0)
        follower_inv = investments.get(follower_id, 0.0)

        joint_benefit = self._compute_joint_benefit(leader_inv, follower_inv)

        payoffs = []
        for aid in match.agent_ids:
            own_inv = investments.get(aid, 0.0)
            utility = round(0.5 * joint_benefit - self._investment_cost * own_inv, 4)
            payoffs.append({
                "agent_id":  aid,
                "role":      self._role_of(match, aid),
                "investment": own_inv,
                "utility":   utility,
            })

        g["resolved"] = True
        match.outcome = {
            "payoffs":       payoffs,
            "leader_id":     leader_id,
            "follower_id":   follower_id,
            "leader_inv":    leader_inv,
            "follower_inv":  follower_inv,
            "joint_benefit": round(joint_benefit, 4),
            "interaction":   self._interaction,
            "reason":        "sequential_investment_resolved",
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

        active_agent = self._active_agent_for_phase(match)
        if agent_id != active_agent:
            allowed_actions = []

        if agent_id in match.game_state.get("investments", {}):
            allowed_actions = [a for a in allowed_actions if a.action_type != "invest"]

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

        # Enforce role — only the active role can act in each phase
        active_agent = self._active_agent_for_phase(match)
        if agent_id != active_agent and action.action_type != "message_only":
            return action_error(
                ActionError.GAME_RULE_VIOLATION,
                f"Only {active_agent} can act during {phase.name}."
            )

        g            = match.game_state
        phase_name   = phase.name

        if action.action_type == "message_only":
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "message_only",
                "phase": phase_name, "round": match.current_round,
            })
            return action_ok()

        # ── pass ──────────────────────────────────────────────────────────────
        if action.action_type == "pass":
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "pass",
                "phase": phase_name, "round": match.current_round,
            })
            self._advance_turn(match)
            self._check_timeout(match)
            return action_ok()

        # ── invest ────────────────────────────────────────────────────────────
        if action.action_type == "invest":
            investments = g.setdefault("investments", {})
            if agent_id in investments:
                return action_error(ActionError.GAME_RULE_VIOLATION,
                                    "You have already invested.")

            amount = action.payload.get("amount")
            if amount is None:
                return action_error(ActionError.INVALID_PAYLOAD, "'amount' is required")
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                return action_error(ActionError.INVALID_PAYLOAD, "'amount' must be a number")
            if amount < 0:
                return action_error(ActionError.INVALID_PAYLOAD, "'amount' must be >= 0")

            investments[agent_id] = amount
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "invest",
                "amount": amount, "phase": phase_name, "round": match.current_round,
            })

            self._advance_turn(match)

            if phase_name == "leader_invest":
                self._try_transition_to_follower(match)

            self._try_resolve(match)

            if match.status != MatchStatus.FINISHED:
                self._check_timeout(match)

            return action_ok()

        return action_error(ActionError.INVALID_ACTION_TYPE,
                            f"Unknown action: {action.action_type}")

    def compute_outcome(self, match: Match) -> dict | None:
        if match.status == MatchStatus.FINISHED and match.outcome is not None:
            return match.outcome
        return None