"""
Common Agency game engine.

Multiple principals simultaneously offer wage contracts to a single agent.
The agent accepts or rejects the bundle, then chooses hidden effort (low/high).
Outcome is stochastic based on effort choice.

Payoffs:
  Agent:      sum of contracted wages for realized outcome - effort_cost (if high effort)
  Principal:  benefit_outcome - their contracted wage for realized outcome

Strategic tension:
  Principals compete to incentivize agent effort without overpaying.
  Agent weighs total wage bundle vs effort cost.
  Free-rider problem: each principal wants the other to pay for high effort.
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

GAME_ID = "common-agency"


class CommonAgencyGame(Game):
    """
    Common Agency: Multiple principals, one agent.

    Phase 1 (offer_contracts): Each principal offers {w_low, w_high}.
    Phase 2 (accept_bundle): Agent accepts or rejects the full bundle.
    Phase 3 (choose_effort): Agent chooses effort level (hidden from principals).
    Resolution: Outcome realized stochastically, payoffs computed.
    """

    def __init__(
        self,
        *,
        max_rounds: int = 15,
        num_principals: int = 2,
        benefit_low: float = 0.0,
        benefit_high: float = 10.0,
        effort_cost: float = 1.0,
        p_high_low_effort: float = 0.4,
        p_high_high_effort: float = 0.7,
        agent_role_id: str | None = None,
    ) -> None:
        self._max_rounds          = max_rounds
        self._num_principals      = num_principals
        self._benefit_low         = benefit_low
        self._benefit_high        = benefit_high
        self._effort_cost         = effort_cost
        self._p_high_low_effort   = p_high_low_effort
        self._p_high_high_effort  = p_high_high_effort
        self._agent_role_id       = agent_role_id  # set at from_params

    @classmethod
    def from_params(cls, game_params: dict, agent_ids: list[str]) -> "CommonAgencyGame":
        num_principals = game_params.get("num_principals", 2)
        # Last agent by default is the agent role; override via agent_id param
        agent_role_id = game_params.get("agent_id")
        if not agent_role_id and len(agent_ids) > num_principals:
            agent_role_id = agent_ids[-1]
        elif not agent_role_id and agent_ids:
            agent_role_id = agent_ids[-1]
        return cls(
            max_rounds         = game_params.get("max_rounds",          15),
            num_principals     = num_principals,
            benefit_low        = game_params.get("benefit_low",         0.0),
            benefit_high       = game_params.get("benefit_high",        10.0),
            effort_cost        = game_params.get("effort_cost",         1.0),
            p_high_low_effort  = game_params.get("p_high_low_effort",   0.4),
            p_high_high_effort = game_params.get("p_high_high_effort",  0.7),
            agent_role_id      = agent_role_id,
        )

    def get_metadata(self) -> dict:
        return {
            **super().get_metadata(),
            "max_rounds":          self._max_rounds,
            "num_principals":      self._num_principals,
            "benefit_low":         self._benefit_low,
            "benefit_high":        self._benefit_high,
            "effort_cost":         self._effort_cost,
            "p_high_low_effort":   self._p_high_low_effort,
            "p_high_high_effort":  self._p_high_high_effort,
        }

    def _agent_id(self, match: Match) -> str | None:
        return self._agent_role_id or match.game_state.get("agent_id")

    def _principal_ids(self, match: Match) -> list[str]:
        agent = self._agent_id(match)
        return [a for a in match.agent_ids if a != agent]

    def _role_of(self, match: Match, agent_id: str) -> str:
        return "agent" if agent_id == self._agent_id(match) else "principal"

    def _current_phase_name(self, match: Match) -> str:
        phases = match.spec.phases
        if phases and match.current_phase_index < len(phases):
            return phases[match.current_phase_index].name
        return "unknown"

    def _all_principals_offered(self, match: Match) -> bool:
        contracts   = match.game_state.get("contracts", {})
        principal_ids = self._principal_ids(match)
        return all(pid in contracts for pid in principal_ids)

    def _visible_game_state(self, match: Match, agent_id: str) -> dict:
        g             = match.game_state
        contracts     = g.get("contracts", {})
        my_role       = self._role_of(match, agent_id)
        current_phase = self._current_phase_name(match)
        agent_id_game = self._agent_id(match)
        principal_ids = self._principal_ids(match)

        # Compute expected payoffs for agent if accepted
        total_w_low  = sum(c.get("w_low",  0) for c in contracts.values())
        total_w_high = sum(c.get("w_high", 0) for c in contracts.values())
        exp_payoff_low_effort  = (
            self._p_high_low_effort  * total_w_high +
            (1 - self._p_high_low_effort)  * total_w_low
        )
        exp_payoff_high_effort = (
            self._p_high_high_effort * total_w_high +
            (1 - self._p_high_high_effort) * total_w_low
            - self._effort_cost
        )

        # Strategy
        if my_role == "principal" and current_phase == "offer_contracts":
            hint = (
                f"You are a PRINCIPAL. Offer wages {{w_low, w_high}} to the agent. "
                f"Agent earns total wages across all principals. "
                f"You earn benefit_outcome - your_w_outcome. "
                f"benefit_high={self._benefit_high}, benefit_low={self._benefit_low}. "
                f"Set w_high high enough to incentivize agent to choose high effort, "
                f"but watch out — other principals may free-ride on your contract. "
                f"Nash hint: IC constraint needs w_high - w_low >= effort_cost / (p_high - p_low) = "
                f"{round(self._effort_cost / (self._p_high_high_effort - self._p_high_low_effort), 2)}."
            )
        elif my_role == "agent" and current_phase == "accept_bundle":
            hint = (
                f"You are the AGENT. All principals have offered: {contracts}. "
                f"Total w_low={total_w_low:.2f}, total w_high={total_w_high:.2f}. "
                f"Expected payoff (low effort): {exp_payoff_low_effort:.2f}. "
                f"Expected payoff (high effort): {exp_payoff_high_effort:.2f}. "
                f"Accept if expected payoff > 0 under your preferred effort level. "
                f"Reject if the bundle is not worth it (you get 0 from rejection)."
            )
        elif my_role == "agent" and current_phase == "choose_effort":
            hint = (
                f"You are the AGENT. Bundle accepted. Now choose effort. "
                f"High effort: E[payoff]={exp_payoff_high_effort:.2f} (costs {self._effort_cost}). "
                f"Low effort: E[payoff]={exp_payoff_low_effort:.2f} (no cost). "
                f"Choose high if {exp_payoff_high_effort:.2f} > {exp_payoff_low_effort:.2f}."
            )
        else:
            hint = f"You are {my_role} in phase {current_phase}."

        return {
            "agent_ids":          list(match.agent_ids),
            "my_role":            my_role,
            "agent_id":           agent_id_game,
            "principal_ids":      principal_ids,
            "current_phase":      current_phase,
            "current_round":      match.current_round,
            "contracts":          dict(contracts),
            "accepted":           g.get("accepted"),
            "effort":             g.get("effort"),
            "outcome":            g.get("outcome"),
            "all_principals_offered": self._all_principals_offered(match),
            "total_w_low":        total_w_low,
            "total_w_high":       total_w_high,
            "exp_payoff_low_effort":  round(exp_payoff_low_effort, 4),
            "exp_payoff_high_effort": round(exp_payoff_high_effort, 4),
            "benefit_low":        self._benefit_low,
            "benefit_high":       self._benefit_high,
            "effort_cost":        self._effort_cost,
            "p_high_low_effort":  self._p_high_low_effort,
            "p_high_high_effort": self._p_high_high_effort,
            "action_history":     g.get("action_history", []),
            "resolved":           g.get("resolved", False),
            "strategy_hint":      hint,
        }

    def spec(self) -> GameSpec:
        return GameSpec(
            game_id=GAME_ID,
            name="Common Agency (Multiple Principals, One Agent)",
            min_agents=self._num_principals + 1,
            description=(
                f"{self._num_principals} principals simultaneously offer wage contracts "
                f"{{w_low, w_high}} to one agent. "
                f"Agent accepts/rejects the bundle, then chooses hidden effort (low/high). "
                f"Outcome realized stochastically: P(high|high_effort)={self._p_high_high_effort}, "
                f"P(high|low_effort)={self._p_high_low_effort}. "
                f"Agent payoff = sum(wages) - effort_cost. "
                f"Principal payoff = benefit_outcome - own_wage. "
                f"Strategic tension: free-rider problem among principals."
            ),
            phases=[
                Phase(
                    name="offer_contracts",
                    turn_order=TurnOrder.ROUND_ROBIN,
                    allowed_action_types=["offer_contract", "pass", "message_only"],
                    max_rounds=max(3, self._max_rounds // 3),
                ),
                Phase(
                    name="accept_bundle",
                    turn_order=TurnOrder.ROUND_ROBIN,
                    allowed_action_types=["accept_bundle", "reject_all", "message_only"],
                    max_rounds=3,
                ),
                Phase(
                    name="choose_effort",
                    turn_order=TurnOrder.ROUND_ROBIN,
                    allowed_action_types=["choose_effort", "message_only"],
                    max_rounds=3,
                ),
            ],
            action_types=[
                ActionTypeDef(
                    name="offer_contract",
                    description=(
                        "Principal offers a wage contract. "
                        "Payload: {\"w_low\": <number>, \"w_high\": <number>}"
                    ),
                    payload_schema={
                        "w_low":  {"type": "number", "minimum": 0},
                        "w_high": {"type": "number", "minimum": 0},
                    },
                    is_message=False,
                ),
                ActionTypeDef(
                    name="accept_bundle",
                    description="Agent accepts the full bundle of all principal contracts.",
                    payload_schema={},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="reject_all",
                    description="Agent rejects all contracts. Everyone gets 0.",
                    payload_schema={},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="choose_effort",
                    description=(
                        "Agent chooses effort level after accepting. "
                        "Payload: {\"effort\": \"low\" | \"high\"}"
                    ),
                    payload_schema={"effort": {"type": "string", "enum": ["low", "high"]}},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="pass",
                    description="Skip turn without acting.",
                    payload_schema={},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="message_only",
                    description="Send a public message without advancing the game.",
                    payload_schema={},
                    is_message=False,
                ),
            ],
            outcome_rule=OutcomeRule.ENGINE,
            initial_game_state={
                "contracts":     {},
                "accepted":      None,
                "effort":        None,
                "outcome":       None,
                "agent_id":      self._agent_role_id,
                "action_history": [],
                "resolved":      False,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _advance_turn(self, match: Match) -> None:
        n = len(match.agent_ids)
        if n == 0:
            return
        match.current_turn_index = (match.current_turn_index + 1) % n
        if match.current_turn_index == 0:
            match.current_round += 1

    def _transition_phase(self, match: Match, next_phase_idx: int) -> None:
        match.current_phase_index = next_phase_idx
        match.current_round       = 0
        # Set turn to agent for agent phases, first principal for offer phase
        if next_phase_idx in (1, 2):  # accept_bundle, choose_effort
            agent = self._agent_id(match)
            agents = list(match.agent_ids)
            match.current_turn_index = agents.index(agent) if agent in agents else 0
        else:
            match.current_turn_index = 0

    def _check_timeout(self, match: Match) -> None:
        phase = match.spec.phases[match.current_phase_index] if match.spec.phases else None
        if phase and phase.max_rounds is not None and match.current_round >= phase.max_rounds:
            match.outcome = {
                "payoffs": [{"agent_id": aid, "utility": 0.0} for aid in match.agent_ids],
                "reason":  "max_rounds_exceeded",
            }
            match.status = MatchStatus.FINISHED

    def _realize_outcome(self, match: Match) -> str:
        """Draw outcome stochastically based on effort."""
        g      = match.game_state
        effort = g.get("effort", "low")
        p      = self._p_high_high_effort if effort == "high" else self._p_high_low_effort
        seed   = hash(match.match_id) % (2**31)
        rng    = random.Random(seed)
        return "high" if rng.random() < p else "low"

    def _resolve(self, match: Match) -> None:
        g         = match.game_state
        contracts = g.get("contracts", {})
        effort    = g.get("effort", "low")
        outcome   = self._realize_outcome(match)
        g["outcome"] = outcome

        benefit = self._benefit_high if outcome == "high" else self._benefit_low
        agent_id  = self._agent_id(match)

        # Agent payoff: sum of w_outcome across all principals - effort_cost if high
        total_wage = sum(
            c.get("w_high", 0) if outcome == "high" else c.get("w_low", 0)
            for c in contracts.values()
        )
        effort_cost = self._effort_cost if effort == "high" else 0.0
        agent_utility = round(total_wage - effort_cost, 4)

        payoffs = []
        for aid in match.agent_ids:
            if aid == agent_id:
                payoffs.append({"agent_id": aid, "role": "agent", "utility": agent_utility})
            else:
                own_contract = contracts.get(aid, {})
                own_wage     = own_contract.get("w_high", 0) if outcome == "high" else own_contract.get("w_low", 0)
                utility      = round(benefit - own_wage, 4)
                payoffs.append({"agent_id": aid, "role": "principal", "utility": utility})

        g["resolved"] = True
        match.outcome = {
            "payoffs":   payoffs,
            "outcome":   outcome,
            "effort":    effort,
            "contracts": dict(contracts),
            "reason":    "common_agency_resolved",
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
        my_role         = self._role_of(match, agent_id)
        agent_game_id   = self._agent_id(match)

        # Restrict actions by role
        if phase_name == "offer_contracts" and my_role == "agent":
            allowed_actions = [a for a in allowed_actions if a.action_type == "message_only"]
        if phase_name in ("accept_bundle", "choose_effort") and my_role == "principal":
            allowed_actions = [a for a in allowed_actions if a.action_type == "message_only"]

        # Remove offer_contract if already offered
        if action_already_offered := agent_id in match.game_state.get("contracts", {}):
            allowed_actions = [a for a in allowed_actions if a.action_type != "offer_contract"]

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
            return action_error(ActionError.MATCH_NOT_RUNNING, "No agents")

        phase = match.spec.phases[match.current_phase_index] if match.spec.phases else None
        if not phase:
            return action_error(ActionError.MATCH_NOT_RUNNING, "No active phase")

        if phase.turn_order != TurnOrder.RANDOM:
            current = match.agent_ids[match.current_turn_index]
            if agent_id != current:
                return action_error(ActionError.NOT_YOUR_TURN, f"It is {current}'s turn")

        g       = match.game_state
        my_role = self._role_of(match, agent_id)

        # ── message_only ──────────────────────────────────────────────────────
        if action.action_type == "message_only":
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "message_only",
                "phase": phase.name, "round": match.current_round,
            })
            return action_ok()

        # ── pass ──────────────────────────────────────────────────────────────
        if action.action_type == "pass":
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "pass",
                "phase": phase.name, "round": match.current_round,
            })
            self._advance_turn(match)
            self._check_timeout(match)
            return action_ok()

        # ── offer_contract (principal only, offer_contracts phase) ────────────
        if action.action_type == "offer_contract":
            if my_role != "principal":
                return action_error(ActionError.GAME_RULE_VIOLATION, "Only principals can offer contracts")
            if phase.name != "offer_contracts":
                return action_error(ActionError.GAME_RULE_VIOLATION, "Not in offer_contracts phase")
            if agent_id in g.get("contracts", {}):
                return action_error(ActionError.GAME_RULE_VIOLATION, "Already offered a contract")

            w_low  = action.payload.get("w_low")
            w_high = action.payload.get("w_high")
            if w_low is None or w_high is None:
                return action_error(ActionError.INVALID_PAYLOAD, "w_low and w_high required")
            try:
                w_low, w_high = float(w_low), float(w_high)
            except (TypeError, ValueError):
                return action_error(ActionError.INVALID_PAYLOAD, "w_low and w_high must be numbers")
            if w_low < 0 or w_high < 0:
                return action_error(ActionError.INVALID_PAYLOAD, "Wages must be >= 0")

            g.setdefault("contracts", {})[agent_id] = {"w_low": w_low, "w_high": w_high}
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "offer_contract",
                "w_low": w_low, "w_high": w_high,
                "phase": phase.name, "round": match.current_round,
            })
            self._advance_turn(match)

            # Auto-transition to accept_bundle when all principals have offered
            if self._all_principals_offered(match):
                self._transition_phase(match, 1)
            else:
                self._check_timeout(match)
            return action_ok()

        # ── accept_bundle (agent only, accept_bundle phase) ───────────────────
        if action.action_type == "accept_bundle":
            if my_role != "agent":
                return action_error(ActionError.GAME_RULE_VIOLATION, "Only the agent can accept")
            if phase.name != "accept_bundle":
                return action_error(ActionError.GAME_RULE_VIOLATION, "Not in accept_bundle phase")

            g["accepted"] = True
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "accept_bundle",
                "phase": phase.name, "round": match.current_round,
            })
            self._transition_phase(match, 2)  # → choose_effort
            return action_ok()

        # ── reject_all (agent only) ───────────────────────────────────────────
        if action.action_type == "reject_all":
            if my_role != "agent":
                return action_error(ActionError.GAME_RULE_VIOLATION, "Only the agent can reject")
            if phase.name != "accept_bundle":
                return action_error(ActionError.GAME_RULE_VIOLATION, "Not in accept_bundle phase")

            g["accepted"] = False
            g["resolved"] = True
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "reject_all",
                "phase": phase.name, "round": match.current_round,
            })
            match.outcome = {
                "payoffs": [{"agent_id": aid, "utility": 0.0} for aid in match.agent_ids],
                "reason":  "common_agency_rejected",
            }
            match.status = MatchStatus.FINISHED
            return action_ok()

        # ── choose_effort (agent only, choose_effort phase) ───────────────────
        if action.action_type == "choose_effort":
            if my_role != "agent":
                return action_error(ActionError.GAME_RULE_VIOLATION, "Only the agent chooses effort")
            if phase.name != "choose_effort":
                return action_error(ActionError.GAME_RULE_VIOLATION, "Not in choose_effort phase")
            if not g.get("accepted"):
                return action_error(ActionError.GAME_RULE_VIOLATION, "Must accept bundle first")

            effort = action.payload.get("effort")
            if effort not in ("low", "high"):
                return action_error(ActionError.INVALID_PAYLOAD, "effort must be 'low' or 'high'")

            g["effort"] = effort
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "choose_effort",
                "effort": effort, "phase": phase.name, "round": match.current_round,
            })
            self._resolve(match)
            return action_ok()

        return action_error(ActionError.INVALID_ACTION_TYPE,
                            f"Unknown action: {action.action_type}")

    def compute_outcome(self, match: Match) -> dict | None:
        if match.status == MatchStatus.FINISHED and match.outcome is not None:
            return match.outcome
        return None