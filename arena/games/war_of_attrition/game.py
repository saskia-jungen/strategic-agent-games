"""
War of Attrition game engine — with signal phase (Option A).

Phase 1 — signal (message-only, no submissions):
  Agents bluff, posture, and try to intimidate. No t values submitted yet.

Phase 2 — choose_time (sealed bid):
  Agents submit their actual quit time t. Opponent's t stays hidden.

Each agent chooses a quit time t (0 <= t <= max_time).
The agent with the highest t wins the prize.
Both agents pay cost_rate * min(t_values).

Winner utility = prize - cost_rate * min_t
Loser utility  = -cost_rate * min_t
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

GAME_ID = "war-of-attrition"

SIGNAL_MAX_ROUNDS   = 6   # 3 full rounds each to bluff (6 turns total round-robin)
CHOOSE_MAX_ROUNDS   = 6   # 3 rounds each to submit (resolves as soon as both submit)


class WarOfAttritionGame(Game):
    """
    War of Attrition with a pre-submission signal phase.

    Phase 1 (signal): chat only — bluff, threaten, negotiate.
    Phase 2 (choose_time): sealed bid — submit t once, hidden from opponent.

    Strategic tension: what you said in phase 1 vs what you actually commit to.
    """

    def __init__(
        self,
        *,
        max_rounds: int = 10,
        prize: float = 10.0,
        cost_rate: float = 1.0,
        max_time: float = 50.0,   # deliberately >> prize/cost_rate so agents have room
    ) -> None:
        self._max_rounds = max_rounds
        self._prize      = prize
        self._cost_rate  = cost_rate
        self._max_time   = max_time

    @classmethod
    def from_params(cls, game_params: dict, agent_ids: list[str]) -> "WarOfAttritionGame":
        return cls(
            max_rounds = game_params.get("max_rounds", 10),
            prize      = game_params.get("prize",      10.0),
            cost_rate  = game_params.get("cost_rate",  1.0),
            max_time   = game_params.get("max_time",   50.0),
        )

    def get_metadata(self) -> dict:
        return {
            **super().get_metadata(),
            "max_rounds": self._max_rounds,
            "prize":      self._prize,
            "cost_rate":  self._cost_rate,
            "max_time":   self._max_time,
        }

    def spec(self) -> GameSpec:
        equilibrium = round(self._prize / self._cost_rate, 1)
        return GameSpec(
            game_id=GAME_ID,
            name="War of Attrition (Signal + Commit)",
            min_agents=2,
            description=(
                f"Phase 1 (signal): agents exchange messages to bluff and posture — no t submitted yet. "
                f"Phase 2 (choose_time): each agent submits a sealed quit time t (0–{self._max_time}). "
                f"Highest t wins prize ({self._prize}). Both pay cost_rate ({self._cost_rate}) × min(t). "
                f"Nash equilibrium: mix t around {equilibrium}. max_time={self._max_time} gives room to strategise. "
                f"Winner utility = prize - cost. Loser utility = -cost. Tie broken randomly."
            ),
            phases=[
                Phase(
                    name="signal",
                    turn_order=TurnOrder.ROUND_ROBIN,
                    allowed_action_types=["message_only", "pass"],
                    max_rounds=SIGNAL_MAX_ROUNDS,
                ),
                Phase(
                    name="choose_time",
                    turn_order=TurnOrder.ROUND_ROBIN,
                    allowed_action_types=["submit_time", "pass", "message_only"],
                    max_rounds=CHOOSE_MAX_ROUNDS,
                ),
            ],
            action_types=[
                ActionTypeDef(
                    name="message_only",
                    description=(
                        "Send a public message without advancing the turn. "
                        "Use in the signal phase to bluff, threaten, or coordinate."
                    ),
                    payload_schema={},
                    is_message=False,
                ),
                ActionTypeDef(
                    name="submit_time",
                    description=(
                        f"Submit your sealed quit time t (0 to {self._max_time}). "
                        f"Only available in choose_time phase. You can only submit once. "
                        f"Opponent cannot see your t until the game resolves."
                    ),
                    payload_schema={"t": {"type": "number", "minimum": 0}},
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
                "phase":          "signal",
                "times":          {},
                "action_history": [],
                "resolved":       False,
            },
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _current_phase_name(self, match: Match) -> str:
        phases = match.spec.phases
        if phases and match.current_phase_index < len(phases):
            return phases[match.current_phase_index].name
        return "unknown"

    def _visible_game_state(self, match: Match, agent_id: str) -> dict:
        g             = match.game_state
        times         = g.get("times", {})
        current_phase = self._current_phase_name(match)
        submitted     = set(times.keys())
        waiting_on    = [a for a in match.agent_ids if a not in submitted]
        equilibrium   = round(self._prize / self._cost_rate, 1)

        # Phase-specific hints
        if current_phase == "signal":
            phase_hint = (
                f"SIGNAL PHASE ({match.current_round + 1}/{SIGNAL_MAX_ROUNDS} rounds): "
                f"You CANNOT submit a quit time yet. Use message_only to bluff and posture. "
                f"Try to convince your opponent they will lose so they submit a low t. "
                f"After this phase ends, you will commit to a sealed t in choose_time."
            )
        else:
            phase_hint = (
                f"CHOOSE_TIME PHASE: Submit your sealed quit time t (0–{self._max_time}). "
                f"Your opponent CANNOT see your t until both have submitted. "
                f"Nash equilibrium suggests mixing around t ≈ {equilibrium}. "
                f"max_time={self._max_time} — do NOT blindly pick the max. "
                f"Think about what your opponent signalled and whether they were bluffing."
            )

        return {
            "agent_ids":          list(match.agent_ids),
            "current_phase":      current_phase,
            "current_round":      match.current_round,
            "signal_max_rounds":  SIGNAL_MAX_ROUNDS,
            "choose_max_rounds":  CHOOSE_MAX_ROUNDS,
            "prize":              self._prize,
            "cost_rate":          self._cost_rate,
            "max_time":           self._max_time,
            "equilibrium_t":      equilibrium,


            # Sealed: only reveals WHO submitted, not the actual value
            "submitted_agents":   list(submitted),
            "waiting_on":         waiting_on,
            "i_have_submitted":   agent_id in times,
            "my_time":            times.get(agent_id),  # owns value visible after submission
            "action_history":     g.get("action_history", []),
            "resolved":           g.get("resolved", False),
            "phase_hint":         phase_hint,
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
            # If signal phase timed out, transition to choose_time
            if phase.name == "signal":
                self._transition_to_choose(match)
            else:
                match.outcome = {
                    "payoffs": [{"agent_id": aid, "utility": 0.0} for aid in match.agent_ids],
                    "reason":  "max_rounds_exceeded",
                }
                match.status = MatchStatus.FINISHED

    def _transition_to_choose(self, match: Match) -> None:
        """Move from signal phase to choose_time phase."""
        match.current_phase_index = 1
        match.current_round       = 0
        match.current_turn_index  = 0
        match.game_state["phase"] = "choose_time"

    def _try_resolve(self, match: Match) -> None:
        """Resolve once all agents have submitted a quit time."""
        g     = match.game_state
        times = g.get("times", {})
        if len(times) < len(match.agent_ids):
            return

        min_t = min(times.values())
        max_t = max(times.values())
        cost  = round(self._cost_rate * min_t, 4)

        # Tiebreak: deterministic random seeded by match_id
        top_agents = [aid for aid, t in times.items() if t == max_t]
        if len(top_agents) == 1:
            winner = top_agents[0]
        else:
            rng    = random.Random(str(match.match_id) + "_tiebreak")
            winner = rng.choice(top_agents)

        payoffs = []
        for aid in match.agent_ids:
            utility = round(
                (self._prize - cost) if aid == winner else -cost, 4
            )
            payoffs.append({
                "agent_id": aid,
                "t":        times[aid],
                "utility":  utility,
                "won":      aid == winner,
            })

        g["resolved"] = True
        match.outcome = {
            "payoffs": payoffs,
            "winner":  winner,
            "times":   dict(times),
            "min_t":   min_t,
            "cost":    cost,
            "prize":   self._prize,
            "reason":  "attrition_resolved",
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

        # Remove submit_time if agent already submitted
        if agent_id in match.game_state.get("times", {}):
            allowed_actions = [a for a in allowed_actions if a.action_type != "submit_time"]

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

        g            = match.game_state
        phase_name   = phase.name

        # ── message_only ────────────────────────────────────────
        if action.action_type == "message_only":
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "message_only",
                "phase": phase_name, "round": match.current_round,
            })
            self._advance_turn(match)
            self._check_timeout(match)
            return action_ok()

        # ── pass ────────────────────────────────────────────────
        if action.action_type == "pass":
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "pass",
                "phase": phase_name, "round": match.current_round,
            })
            self._advance_turn(match)
            self._check_timeout(match)
            return action_ok()

        # ── submit_time ──────────────────────────────
        if action.action_type == "submit_time":
            if phase_name != "choose_time":
                return action_error(
                    ActionError.GAME_RULE_VIOLATION,
                    "Cannot submit a quit time during the signal phase — use message_only to bluff first.",
                )

            times = g.setdefault("times", {})
            if agent_id in times:
                return action_error(ActionError.GAME_RULE_VIOLATION,
                                    "You have already submitted a quit time")

            t = action.payload.get("t")
            if t is None:
                return action_error(ActionError.INVALID_PAYLOAD, "'t' is required in payload")
            try:
                t = float(t)
            except (TypeError, ValueError):
                return action_error(ActionError.INVALID_PAYLOAD, "'t' must be a number")
            if t < 0:
                return action_error(ActionError.INVALID_PAYLOAD, "'t' must be >= 0")
            if t > self._max_time:
                return action_error(ActionError.INVALID_PAYLOAD,
                                    f"'t' must be <= max_time ({self._max_time})")

            times[agent_id] = t
            g.setdefault("action_history", []).append({
                "agent_id": agent_id, "action": "submit_time",
                "t": t, "phase": phase_name, "round": match.current_round,
            })

            self._advance_turn(match)
            self._try_resolve(match)
            if match.status != MatchStatus.FINISHED:
                self._check_timeout(match)
            return action_ok()

        # ─ submit_time blocked in signal phase ────────────────────────
        if action.action_type == "submit_time" and phase_name == "signal":
            return action_error(
                ActionError.GAME_RULE_VIOLATION,
                "submit_time is not allowed in the signal phase.",
            )

        return action_error(ActionError.INVALID_ACTION_TYPE,
                            f"Unknown action: {action.action_type}")

    def compute_outcome(self, match: Match) -> dict | None:
        if match.status == MatchStatus.FINISHED and match.outcome is not None:
            return match.outcome
        return None