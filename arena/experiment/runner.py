"""ExperimentRunner: run N matches programmatically with pluggable agents."""
from __future__ import annotations

import random as _random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from arena.agents.base import Agent
from arena.core.match import MatchStatus
from arena.core.runner import apply_action, apply_message, create_match, get_turn_state
from arena.games import get_game, get_game_spec, register_game
from arena.spec import TurnOrder
from arena.games.base import Game
from arena.games.builtins import ensure_builtins_registered
from arena.logging.match_logger import MatchLog, MatchLogger
from arena.types import Action, AllowedAction


def _sanitize_payload(game_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Strip private fields from an action payload for logging/dashboard.

    If the game declares ``private_payload_keys``, those keys are replaced with
    ``"[private]"`` and a ``_redacted`` list is added so the dashboard can show
    a privacy indicator.
    """
    game = get_game(game_id)
    if game is None:
        return payload
    private_keys = game.private_payload_keys
    if not private_keys:
        return payload
    found = [k for k in private_keys if k in payload]
    if not found:
        return payload
    sanitized = {k: ("[private]" if k in private_keys else v) for k, v in payload.items()}
    sanitized["_redacted"] = found
    return sanitized


class ExperimentConfig(BaseModel):
    """Configuration for an experiment."""

    game_id: str = Field(..., description="Game to play")
    num_matches: int = Field(default=1, description="Number of matches to run")
    max_turns_per_match: int = Field(
        default=20,
        description=(
            "Maximum number of turns allowed in a single match. "
            "If this many turns occur without a resolution, the match will be aborted. "
            "A turn typically consists of a single agent taking an action."
        )
    )
    max_messages_per_turn: int = Field(
        default=10,
        description=(
            "Maximum number of messages an agent can send during a single turn. "
            "Prevents agents from flooding the environment with excessive communication in one turn."
        )
    )
    max_message_pings: int = Field(
        default=5,
        description="When an agent uses message_only, max reply rounds from the other agent(s) before returning to the current agent.",
    )
    max_stale_turns: int = Field(
        default=10,
        description=(
            "Maximum consecutive turns where no real action is taken (only message_only or pass). "
            "Once reached, message_only and pass are removed from allowed actions, forcing the agent to act."
        ),
    )
    log_directory: Path | None = Field(default=None, description="Directory to save match logs")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary metadata")
    max_workers: int = Field(default=1, description="Max concurrent matches. 1 = sequential (default).")


class MatchResult(BaseModel):
    """Result of a single match."""

    match_id: str
    game_id: str
    agent_ids: list[str]
    outcome: dict[str, Any] | None = None
    status: str = ""
    num_turns: int = 0
    num_messages: int = 0
    duration_seconds: float = 0.0
    log: MatchLog | None = None
    error: str | None = None


class ExperimentResult(BaseModel):
    """Result of running an experiment (N matches)."""

    game_id: str
    num_matches: int
    match_results: list[MatchResult] = Field(default_factory=list)
    total_duration_seconds: float = 0.0

    _RESOLVED_REASONS = {"agreement", "auction_resolved", "task_resolved_fail", "task_resolved_partial", "task_resolved_success", "trade_completed", "delivery_disputed_split", "delivery_disputed_no_payment", "negotiation_failed", "initiator_exited", "all_providers_exited", "project_funded", "threshold_met"}

    def _resolved_results(self) -> list["MatchResult"]:
        """Matches that ended with a resolution (agreement or auction resolved)."""
        return [
            mr for mr in self.match_results
            if mr.outcome and (mr.outcome.get("reason") or mr.outcome.get("trigger")) in self._RESOLVED_REASONS
        ]

    def _agreement_results(self) -> list["MatchResult"]:
        """Matches that ended with an agreement (exclude no-deal). Alias for backward compat."""
        return self._resolved_results()

    @property
    def no_deal_count(self) -> int:
        """Number of matches that ended without a resolution."""
        return len(self.match_results) - len(self._resolved_results())

    @property
    def payoff_matrix(self) -> dict[str, list[float]]:
        """agent_id -> list of payoffs across resolved matches only."""
        matrix: dict[str, list[float]] = {}
        for mr in self._resolved_results():
            for p in mr.outcome["payoffs"]:  # type: ignore[index]
                aid = p["agent_id"]
                matrix.setdefault(aid, []).append(float(p.get("utility", p.get("value", 0))))
        return matrix

    @property
    def mean_payoffs(self) -> dict[str, float]:
        """agent_id -> mean payoff across resolved matches only."""
        return {
            aid: sum(vals) / len(vals) if vals else 0.0
            for aid, vals in self.payoff_matrix.items()
        }

    @property
    def mean_shares(self) -> dict[str, float]:
        """agent_id -> mean share (deal amount) across agreement matches only."""
        shares: dict[str, list[float]] = {}
        for mr in self._resolved_results():
            for p in mr.outcome["payoffs"]:  # type: ignore[index]
                aid = p["agent_id"]
                if "share" in p:
                    shares.setdefault(aid, []).append(float(p["share"]))
        return {aid: sum(v) / len(v) if v else 0.0 for aid, v in shares.items()}

    @property
    def mean_bids(self) -> dict[str, float]:
        """agent_id -> mean bid across resolved auction matches only."""
        bids: dict[str, list[float]] = {}
        for mr in self._resolved_results():
            for p in mr.outcome["payoffs"]:  # type: ignore[index]
                aid = p["agent_id"]
                if "bid" in p:
                    bids.setdefault(aid, []).append(float(p["bid"]))
        return {aid: sum(v) / len(v) if v else 0.0 for aid, v in bids.items()}

    @property
    def is_auction(self) -> bool:
        return self.game_id == "first-price-auction"

    @property
    def completion_rate(self) -> float:
        """Fraction of matches that finished (FINISHED status)."""
        if not self.match_results:
            return 0.0
        finished = sum(1 for mr in self.match_results if mr.status == "finished")
        return finished / len(self.match_results)


class ExperimentRunner:
    """Runs N matches with pluggable agents and collects results."""

    def __init__(
        self,
        config: ExperimentConfig,
        external_dashboard: dict[str, Any] | None = None,
        external_dashboard_lock: threading.Lock | None = None,
        on_event: Any | None = None,
    ) -> None:
        self._config = config
        self._ext_dashboard = external_dashboard
        self._ext_dashboard_lock = external_dashboard_lock
        self._on_event = on_event

    def run(self, agents: list[Agent], game: Game | None = None) -> ExperimentResult:
        """Run the experiment: N matches with the given agents.

        Args:
            agents: List of agents to play.
            game: Optional Game instance with custom settings.
                  If None, the default registered game for game_id is used.
        """
        ensure_builtins_registered()

        if game is not None:
            register_game(game)
            spec = game.spec()
        else:
            spec = get_game_spec(self._config.game_id)
        if spec is None:
            raise ValueError(f"Unknown game: {self._config.game_id}")

        min_agents = getattr(spec, "min_agents", 1)
        if len(agents) < min_agents:
            raise ValueError(
                f"Game '{self._config.game_id}' requires at least {min_agents} agents, got {len(agents)}"
            )

        _game_obj_for_dashboard = get_game(self._config.game_id)
        _game_dashboard_meta = _game_obj_for_dashboard.get_metadata() if _game_obj_for_dashboard is not None else {}
        _agents_dashboard_meta = {a.agent_id: {"model": a.get_metadata().get("model", ""), "provider": a.get_metadata().get("provider", "")} for a in agents}

        start_time = time.monotonic()
        match_results: list[MatchResult] = []
        dashboard_state: dict[str, Any] | None = None
        dashboard_lock: threading.Lock | None = None

        if self._ext_dashboard is not None:
            # Multi-game mode: use a sub-state inside the shared dashboard
            dashboard_lock = self._ext_dashboard_lock
            game_sub: dict[str, Any] = {
                "config": self._config,
                "game_metadata": _game_dashboard_meta,
                "agents_metadata": _agents_dashboard_meta,
                "match_results": [],
                "active_matches": {},
                "status": "running",
                "game_id": self._config.game_id,
                "num_matches": self._config.num_matches,
                "total_duration_seconds": 0.0,
            }
            if dashboard_lock:
                with dashboard_lock:
                    self._ext_dashboard["games"][self._config.game_id] = game_sub
            else:
                self._ext_dashboard["games"][self._config.game_id] = game_sub
            dashboard_state = game_sub

        if self._config.max_workers <= 1:
            for _ in range(self._config.num_matches):
                mr = self._run_single_match(agents, spec, dashboard_state, dashboard_lock)
                match_results.append(mr)
                if dashboard_state is not None:
                    dashboard_state["match_results"].append(mr.model_dump(mode="json"))
        else:
            with ThreadPoolExecutor(max_workers=self._config.max_workers) as pool:
                futures = [
                    pool.submit(self._run_single_match, agents, spec, dashboard_state, dashboard_lock)
                    for _ in range(self._config.num_matches)
                ]
                for fut in futures:
                    mr = fut.result()
                    match_results.append(mr)
                    if dashboard_state is not None:
                        with dashboard_lock:  # type: ignore[union-attr]
                            dashboard_state["match_results"].append(mr.model_dump(mode="json"))

        total_duration = time.monotonic() - start_time
        result = ExperimentResult(
            game_id=self._config.game_id,
            num_matches=self._config.num_matches,
            match_results=match_results,
            total_duration_seconds=total_duration,
        )

        if dashboard_state is not None:
            dashboard_state["status"] = "finished"
            dashboard_state["total_duration_seconds"] = total_duration

        return result

    def _push_live_event(
        self,
        dashboard_state: dict[str, Any] | None,
        dashboard_lock: threading.Lock | None,
        match_id: str,
        event_type: str,
        agent_id: str | None = None,
        **data: Any,
    ) -> None:
        event = {
            "timestamp_ns": time.time_ns(),
            "event_type": event_type,
            "agent_id": agent_id,
            "data": data,
        }
        if self._on_event is not None:
            self._on_event(event)
        if not dashboard_state:
            return
        if dashboard_lock:
            with dashboard_lock:
                active = dashboard_state.get("active_matches", {})
                current = active.get(match_id)
                if current and "events" in current:
                    current["events"].append(event)
        else:
            active = dashboard_state.get("active_matches", {})
            current = active.get(match_id)
            if current and "events" in current:
                current["events"].append(event)

    def _send_messages(
        self,
        match: Any,
        sender_id: str,
        messages: list,
        agent_ids: list[str],
        logger: MatchLogger,
        dashboard_state: dict[str, Any] | None,
        dashboard_lock: threading.Lock | None,
        match_id: str,
    ) -> int:
        """Send messages from an agent, log them, and push dashboard events. Returns count sent."""
        count = 0
        for msg in messages[: self._config.max_messages_per_turn]:
            msg_result = apply_message(
                match, sender_id, msg.scope.value, msg.content, msg.to_agent_ids or None
            )
            if msg_result.ok:
                count += 1
            logger.log_messages(sender_id, [msg])
            visible_to = (
                list(agent_ids)
                if msg.scope.value == "public"
                else sorted(set([sender_id] + (msg.to_agent_ids or [])))
            )
            self._push_live_event(
                dashboard_state, dashboard_lock, match_id, "message",
                agent_id=sender_id, scope=msg.scope.value, content=msg.content,
                to_agent_ids=msg.to_agent_ids or [], visible_to=visible_to,
            )
        return count

    def _log_action_event(
        self,
        game_id: str,
        agent_id: str,
        action: Action,
        result: Any,
        agent_ids: list[str],
        logger: MatchLogger,
        dashboard_state: dict[str, Any] | None,
        dashboard_lock: threading.Lock | None,
        match_id: str,
    ) -> None:
        """Log an action and push it to the dashboard."""
        safe_payload = _sanitize_payload(game_id, action.payload)
        logger.log_action(agent_id, action.action_type, safe_payload, result)
        self._push_live_event(
            dashboard_state, dashboard_lock, match_id, "action",
            agent_id=agent_id, action_type=action.action_type,
            payload=safe_payload, ok=result.ok, error=result.error,
            error_detail=result.error_detail, visible_to=list(agent_ids),
        )

    def _run_single_match(
        self,
        agents: list[Agent],
        spec: Any,
        dashboard_state: dict[str, Any] | None = None,
        dashboard_lock: threading.Lock | None = None,
    ) -> MatchResult:
        """Run a single match to completion or max_turns."""
        match_id = uuid.uuid4().hex
        agent_ids = [a.agent_id for a in agents]
        agent_map = {a.agent_id: a for a in agents}
        match = create_match(match_id, self._config.game_id, spec, agent_ids)

        if dashboard_state is not None:
            entry = {
                "match_id": match_id,
                "agent_ids": agent_ids,
                "events": [],
            }
            if dashboard_lock:
                with dashboard_lock:
                    dashboard_state["active_matches"][match_id] = entry
            else:
                dashboard_state.setdefault("active_matches", {})[match_id] = entry

        logger = MatchLogger(match_id, self._config.game_id, agent_ids)
        logger.set_metadata(**self._config.metadata)

        # Auto-populate metadata from game and agent config
        game_obj = get_game(self._config.game_id)
        if game_obj is not None:
            logger.set_metadata(game=game_obj.get_metadata())
        logger.set_metadata(
            agents={a.agent_id: a.get_metadata() for a in agents},
            max_turns_per_match=self._config.max_turns_per_match,
            max_messages_per_turn=self._config.max_messages_per_turn,
            num_agents=len(agents),
        )

        logger.log_event("match_start")
        self._push_live_event(dashboard_state, dashboard_lock, match_id, "match_start")

        for agent in agents:
            agent.on_match_start(match_id, self._config.game_id, agent_ids)

        start_time = time.monotonic()
        turn_count = 0
        message_count = 0
        stale_turns = 0
        error_str: str | None = None

        try:
            while match.status == MatchStatus.RUNNING and turn_count < self._config.max_turns_per_match:
                # Detect whether the current phase uses RANDOM turn order
                _phase = (
                    match.spec.phases[match.current_phase_index]
                    if match.spec.phases and match.current_phase_index < len(match.spec.phases)
                    else None
                )
                _is_random_phase = _phase is not None and _phase.turn_order == TurnOrder.RANDOM
                if _is_random_phase:
                    # Only pick among agents that can actually act in this phase
                    _active = [
                        aid for aid in agent_ids
                        if (ts := get_turn_state(match, aid)) is not None and ts.is_my_turn
                    ]
                    current_agent_id = _random.choice(_active) if _active else agent_ids[match.current_turn_index % len(agent_ids)]
                else:
                    current_agent_id = agent_ids[match.current_turn_index % len(agent_ids)]
                agent = agent_map[current_agent_id]

                state = get_turn_state(match, current_agent_id)
                if state is None:
                    break

                # If too many consecutive stale turns, remove message_only/pass
                if stale_turns >= self._config.max_stale_turns:
                    real_actions = [
                        a for a in state.allowed_actions
                        if a.action_type not in ("message_only", "pass")
                    ]
                    if real_actions:
                        state = state.model_copy(update={"allowed_actions": real_actions})

                # Push turn_state event so dashboard can show per-agent game view
                self._push_live_event(
                    dashboard_state,
                    dashboard_lock,
                    match_id,
                    "turn_state",
                    agent_id=current_agent_id,
                    game_state=state.game_state,
                    phase=state.phase,
                    allowed_actions=[a.action_type for a in state.allowed_actions],
                )
                logger.log_event(
                    "turn_state",
                    agent_id=current_agent_id,
                    game_state=state.game_state,
                    phase=state.phase,
                    allowed_actions=[a.action_type for a in state.allowed_actions],
                )

                # Inject round progress so agents know how many rounds remain
                _gs = dict(state.game_state)
                _gs["current_round"] = turn_count + 1
                _gs["max_rounds"] = self._config.max_turns_per_match
                state = state.model_copy(update={"game_state": _gs})

                response = agent.act(state)
                message_count += self._send_messages(
                    match, current_agent_id, response.messages,
                    agent_ids, logger, dashboard_state, dashboard_lock, match_id,
                )

                action = response.action
                result = apply_action(match, current_agent_id, action)
                if action.action_type != "message_only":
                    self._log_action_event(
                        self._config.game_id, current_agent_id, action, result,
                        agent_ids, logger, dashboard_state, dashboard_lock, match_id,
                    )

                # If the action failed, advance the turn so the game doesn't get
                # stuck with the same agent retrying the same invalid move.
                if not result.ok and action.action_type not in ("pass", "message_only"):
                    n = len(agent_ids)
                    match.current_turn_index = (match.current_turn_index + 1) % n

                # Message ping-pong: when agent uses message_only, let others reply
                message_only_action = AllowedAction(
                    action_type="message_only",
                    description="Only send messages in response to new chat",
                    payload_schema={},
                )
                ping_count = 0
                other_ids = [a for a in agent_ids if a != current_agent_id]
                while (
                    match.status == MatchStatus.RUNNING
                    and action.action_type == "message_only"
                    and ping_count < self._config.max_message_pings
                ):
                    for other_id in other_ids:
                        if match.status != MatchStatus.RUNNING:
                            break
                        state_other = get_turn_state(match, other_id)
                        if state_other is None:
                            continue
                        state_other = state_other.model_copy(
                            update={"allowed_actions": [message_only_action]}
                        )
                        resp = agent_map[other_id].act(state_other)
                        message_count += self._send_messages(
                            match, other_id, resp.messages,
                            agent_ids, logger, dashboard_state, dashboard_lock, match_id,
                        )
                        act_other = resp.action
                        if act_other.action_type != "message_only":
                            act_other = Action(action_type="message_only", payload={})
                        apply_action(match, other_id, act_other)
                    ping_count += 1
                    state = get_turn_state(match, current_agent_id)
                    if state is None:
                        break
                    response = agent.act(state)
                    message_count += self._send_messages(
                        match, current_agent_id, response.messages,
                        agent_ids, logger, dashboard_state, dashboard_lock, match_id,
                    )
                    action = response.action
                    result = apply_action(match, current_agent_id, action)
                    if action.action_type != "message_only":
                        self._log_action_event(
                            self._config.game_id, current_agent_id, action, result,
                            agent_ids, logger, dashboard_state, dashboard_lock, match_id,
                        )
                        break

                if action.action_type not in ("pass", "message_only"):
                    turn_count += 1
                    stale_turns = 0
                else:
                    stale_turns += 1
        except Exception as e:
            error_str = str(e)

        # If runner hit max_turns but game never resolved, force-finish via the game engine
        if match.status == MatchStatus.RUNNING:
            game_obj = get_game(self._config.game_id)
            if game_obj is not None:
                forced = game_obj.compute_outcome(match)
                if forced is not None:
                    match.outcome = forced
            if match.outcome is None:
                match.outcome = {
                    "payoffs": [{"agent_id": aid, "utility": 0.0} for aid in agent_ids],
                    "reason": "max_turns_exceeded",
                }
            match.status = MatchStatus.FINISHED

        duration = time.monotonic() - start_time
        logger.set_outcome(match.outcome)
        _end_trigger = (match.outcome or {}).get("trigger") or (match.outcome or {}).get("reason") or match.status.value
        logger.log_event("match_end", status=match.status.value, trigger=_end_trigger)
        self._push_live_event(dashboard_state, dashboard_lock, match_id, "match_end", status=match.status.value, trigger=_end_trigger)
        if dashboard_state is not None:
            if dashboard_lock:
                with dashboard_lock:
                    dashboard_state["active_matches"].pop(match_id, None)
            else:
                dashboard_state.get("active_matches", {}).pop(match_id, None)

        for agent in agents:
            agent.on_match_end(match_id, match.outcome)

        log = logger.to_log()
        if self._config.log_directory:
            logger.save(self._config.log_directory)

        return MatchResult(
            match_id=match_id,
            game_id=self._config.game_id,
            agent_ids=agent_ids,
            outcome=match.outcome,
            status=match.status.value,
            num_turns=turn_count,
            num_messages=message_count,
            duration_seconds=duration,
            log=log,
            error=error_str,
        )
