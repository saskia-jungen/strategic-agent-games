"""Arena HTTP server: registration, matchmaking, leaderboard, dashboard."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from arena.agents.base import Agent
from arena.server.polling_agent import IdleTimeoutError
from arena.server.remote_agent import RemoteAgent
from arena.server.sessions import SessionManager, SessionStatus
from arena.server.store import ArenaStore
from arena.experiment.runner import ExperimentConfig, ExperimentRunner
from arena.games.base import Game
from arena.games.builtins import ensure_builtins_registered


# Default game params for each game type
_DEFAULT_GAME_PARAMS: dict[str, dict[str, Any]] = {
    "ultimatum": {"total": 100, "rv1": 30, "rv2": 30},
    "first-price-auction": {"rv1": 30, "rv2": 30},
    "bilateral-trade": {"buyer_rv": 80, "seller_rv": 40},
    "provision-point": {"threshold": 100, "rv1": 70, "rv2": 70},
    "public-project": {"project_cost": 100, "valuation_range": [0, 100]},
}

def build_arena_app(
    store: ArenaStore,
    games: dict[str, Game],
    builtin_agents: dict[str, Agent] | None = None,
    default_game_id: str = "ultimatum",
    matches_per_request: int = 1,
    max_turns: int = 50,
    max_messages: int = 10,
) -> Starlette:
    """Build the Starlette app for the arena server."""
    ensure_builtins_registered()
    _builtin = builtin_agents or {}
    _running_matches: dict[str, dict[str, Any]] = {}
    _match_lock = threading.Lock()
    _session_mgr = SessionManager()

    # --- API routes ---

    async def api_claim_name(request: Request) -> JSONResponse:
        """Claim a player name. First use claims it; subsequent calls require the claim_token."""
        body = await request.json()
        name = body.get("name", "").strip()
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        claim_token = body.get("claim_token")
        ok, token = store.claim_name(name, claim_token)
        if not ok:
            return JSONResponse({"error": f"Name '{name}' is already claimed by someone else"}, status_code=409)
        return JSONResponse({"ok": True, "name": name, "claim_token": token})

    async def api_register(request: Request) -> JSONResponse:
        body = await request.json()
        agent_id = body.get("agent_id")
        if not agent_id:
            return JSONResponse({"error": "agent_id required"}, status_code=400)
        endpoint = body.get("endpoint")
        agent_type = body.get("agent_type", "remote" if endpoint else "unknown")
        display_name = body.get("display_name", agent_id)
        metadata = body.get("metadata", {})

        supported_games = body.get("supported_games", [])
        agent = store.register_agent(agent_id, endpoint, agent_type, display_name, supported_games, metadata)
        return JSONResponse({
            "ok": True,
            "agent_id": agent.agent_id,
            "display_name": agent.display_name,
        })

    async def api_unregister(request: Request) -> JSONResponse:
        body = await request.json()
        agent_id = body.get("agent_id")
        if not agent_id:
            return JSONResponse({"error": "agent_id required"}, status_code=400)
        removed = store.remove_agent(agent_id)
        return JSONResponse({"ok": removed})

    async def api_leaderboard(request: Request) -> JSONResponse:
        game_id = request.query_params.get("game_id")
        return JSONResponse({"leaderboard": store.get_leaderboard(game_id=game_id), "game_id": game_id})

    async def api_agents(request: Request) -> JSONResponse:
        agents = store.list_agents()
        return JSONResponse({
            "agents": [
                {
                    "agent_id": a.agent_id,
                    "display_name": a.display_name,
                    "agent_type": a.agent_type,
                    "endpoint": a.endpoint,
                    "supported_games": a.supported_games,
                }
                for a in agents
            ]
        })

    async def api_games(request: Request) -> JSONResponse:
        game_list = []
        for gid, g in games.items():
            spec = g.spec()
            game_list.append({
                "game_id": gid,
                "description": spec.description,
                "min_agents": spec.min_agents,
            })
        return JSONResponse({"games": game_list})

    async def api_history(request: Request) -> JSONResponse:
        limit = int(request.query_params.get("limit", "50"))
        game_id = request.query_params.get("game_id")
        return JSONResponse({"matches": store.get_match_history(limit=limit, game_id=game_id)})

    async def api_match(request: Request) -> JSONResponse:
        """Start a match between specified agents."""
        body = await request.json()
        agent_ids = body.get("agent_ids", [])
        game_id = body.get("game_id", default_game_id)
        num_matches = body.get("num_matches", matches_per_request)
        req_max_turns = body.get("max_turns", max_turns)
        game_params = body.get("game_params", _DEFAULT_GAME_PARAMS.get(game_id, {}))

        if len(agent_ids) < 2:
            return JSONResponse({"error": "Need at least 2 agent_ids"}, status_code=400)
        if game_id not in games:
            return JSONResponse({"error": f"Unknown game: {game_id}. Available: {list(games.keys())}"}, status_code=400)

        # Resolve agents
        agent_objects: list[Agent] = []
        for aid in agent_ids:
            if aid in _builtin:
                agent_objects.append(_builtin[aid])
                continue
            reg = store.get_agent(aid)
            if reg is None:
                return JSONResponse({"error": f"Agent '{aid}' not registered"}, status_code=400)
            if not reg.endpoint:
                return JSONResponse({"error": f"Agent '{aid}' has no endpoint"}, status_code=400)
            agent_objects.append(RemoteAgent(aid, reg.endpoint))

        # Run in background thread
        match_session_id = uuid.uuid4().hex[:12]
        # Shared live-events list that the runner appends to via dashboard_state
        live_events: list[dict] = []
        with _match_lock:
            _running_matches[match_session_id] = {
                "status": "running", "game_id": game_id,
                "agent_ids": agent_ids, "game_params": game_params,
                "events": live_events,
            }

        def _run():
            # Create game instance with custom params via the game class's from_params
            game_cls = type(games[game_id])
            game = game_cls.from_params(game_params, agent_ids)
            config = ExperimentConfig(
                game_id=game_id,
                num_matches=num_matches,
                max_turns_per_match=req_max_turns,
                max_messages_per_turn=max_messages,
            )
            def _on_event(event: dict) -> None:
                with _match_lock:
                    live_events.append(event)

            ext_dashboard = {"games": {}}
            runner = ExperimentRunner(
                config,
                external_dashboard=ext_dashboard,
                external_dashboard_lock=_match_lock,
                on_event=_on_event,
            )

            try:
                result = runner.run(agent_objects, game=game)
                for mr in result.match_results:
                    log_data = mr.log.model_dump(mode="json") if mr.log else None
                    store.record_match(
                        match_id=mr.match_id, game_id=game_id, agent_ids=mr.agent_ids,
                        outcome=mr.outcome, status=mr.status, num_turns=mr.num_turns,
                        duration_seconds=mr.duration_seconds, log=log_data,
                        game_params=game_params,
                    )
                with _match_lock:
                    _running_matches[match_session_id] = {
                        "status": "finished",
                        "game_id": game_id,
                        "agent_ids": agent_ids,
                        "game_params": game_params,
                        "events": live_events,
                        "results": [mr.model_dump(mode="json") for mr in result.match_results],
                    }
            except Exception as e:
                import traceback
                traceback.print_exc()
                with _match_lock:
                    _running_matches[match_session_id] = {
                        "status": "error",
                        "error": str(e),
                        "game_id": game_id,
                        "agent_ids": agent_ids,
                        "events": live_events,
                    }

        threading.Thread(target=_run, daemon=True).start()
        return JSONResponse({"ok": True, "session_id": match_session_id, "status": "running"})

    async def api_match_status(request: Request) -> JSONResponse:
        session_id = request.path_params.get("session_id", "")
        with _match_lock:
            info = _running_matches.get(session_id)
        if info is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        # Support ?since=N to only return new events (for live polling)
        since = int(request.query_params.get("since", "0"))
        events = info.get("events", [])
        resp = dict(info)
        if since > 0:
            resp["events"] = events[since:] if since < len(events) else []
            resp["events_offset"] = since
        resp["events_total"] = len(events)
        return JSONResponse(resp)

    async def api_dashboard_data(request: Request) -> JSONResponse:
        game_list = list(games.keys())
        per_game_lb = {g: store.get_leaderboard(game_id=g) for g in game_list}
        return JSONResponse({
            "per_game_leaderboard": per_game_lb,
            "recent_matches": store.get_match_history(limit=20),
            "games": game_list,
            "registered_agents": [
                {"agent_id": a.agent_id, "display_name": a.display_name, "agent_type": a.agent_type, "endpoint": a.endpoint, "supported_games": a.supported_games}
                for a in store.list_agents()
            ],
        })

    # ---- Polling / Session API ----

    async def api_session_create(request: Request) -> JSONResponse:
        """Create a new game session. Returns session_id, player token, and invite codes."""
        body = await request.json()
        game_id = body.get("game_id", default_game_id)
        if game_id not in games:
            return JSONResponse({"error": f"Unknown game: {game_id}. Available: {list(games.keys())}"}, status_code=400)
        num_players = body.get("num_players", games[game_id].spec().min_agents)
        creator_name = body.get("player_name", "")
        claim_token = body.get("claim_token")
        game_params = body.get("game_params", _DEFAULT_GAME_PARAMS.get(game_id, {}))
        req_max_turns = body.get("max_turns", max_turns)

        # Validate name claim
        if creator_name:
            if not store.verify_name(creator_name, claim_token):
                return JSONResponse({"error": f"Name '{creator_name}' is already claimed by someone else"}, status_code=409)
            ok, ct = store.claim_name(creator_name, claim_token)
        else:
            ct = None

        info = _session_mgr.create_session(
            game_id=game_id,
            num_players=num_players,
            creator_name=creator_name,
            game_params=game_params,
            max_turns=req_max_turns,
        )
        if ct:
            info["claim_token"] = ct

        return JSONResponse(info)

    async def api_session_join(request: Request) -> JSONResponse:
        """Join a session via invite code."""
        body = await request.json()
        invite_code = body.get("invite_code", "")
        player_name = body.get("player_name", "")
        claim_token = body.get("claim_token")
        if not invite_code:
            return JSONResponse({"error": "invite_code required"}, status_code=400)

        # Validate name claim
        if player_name:
            if not store.verify_name(player_name, claim_token):
                return JSONResponse({"error": f"Name '{player_name}' is already claimed by someone else"}, status_code=409)
            ok, ct = store.claim_name(player_name, claim_token)
        else:
            ct = None

        result = _session_mgr.join_session(invite_code, player_name)
        if result is None:
            return JSONResponse({"error": "Invalid or expired invite code"}, status_code=400)
        if ct:
            result["claim_token"] = ct

        session_id = result["session_id"]

        # Auto-start the match if all players have joined
        if _session_mgr.is_ready_to_start(session_id):
            _start_session_match(session_id)

        return JSONResponse(result)

    def _start_session_match(session_id: str) -> None:
        """Start the match for a session in a background thread."""
        session = _session_mgr.get_session(session_id)
        if session is None:
            return
        _session_mgr.set_status(session_id, SessionStatus.RUNNING)

        game_id = session.game_id
        player_ids = [p.player_id for p in session.players]
        all_agents = [session.polling_agents[pid] for pid in player_ids]
        # Use the polling agent's agent_id (display name) for the game
        agent_ids = [a.agent_id for a in all_agents]
        game_params = session.game_params

        def _run() -> None:
            try:
                game_cls = type(games[game_id])
                game = game_cls.from_params(game_params, agent_ids)
                config = ExperimentConfig(
                    game_id=game_id,
                    num_matches=1,
                    max_turns_per_match=session.max_turns,
                    max_messages_per_turn=max_messages,
                )

                def _on_event(event: dict) -> None:
                    _session_mgr.add_game_event(session_id, event)

                runner = ExperimentRunner(
                    config,
                    on_event=_on_event,
                )
                result = runner.run(all_agents, game=game)

                for mr in result.match_results:
                    log_data = mr.log.model_dump(mode="json") if mr.log else None
                    store.record_match(
                        match_id=mr.match_id, game_id=game_id, agent_ids=mr.agent_ids,
                        outcome=mr.outcome, status=mr.status, num_turns=mr.num_turns,
                        duration_seconds=mr.duration_seconds, log=log_data,
                        game_params=game_params,
                    )
                _session_mgr.set_status(session_id, SessionStatus.FINISHED)
            except IdleTimeoutError as e:
                _session_mgr.set_status(session_id, SessionStatus.FINISHED, error=str(e))
            except Exception as e:
                import traceback
                traceback.print_exc()
                _session_mgr.set_status(session_id, SessionStatus.ERROR, error=str(e))

        t = threading.Thread(target=_run, daemon=True)
        session.match_thread = t
        t.start()

    async def api_session_state(request: Request) -> JSONResponse:
        """Get the current game state for a player. Long-polls until state is available."""
        token = request.query_params.get("token", "")
        if not token:
            return JSONResponse({"error": "token required"}, status_code=400)

        auth = _session_mgr.authenticate(token)
        if auth is None:
            return JSONResponse({"error": "Invalid token"}, status_code=401)
        session, player_id = auth

        if session.status == SessionStatus.WAITING:
            return JSONResponse({
                "status": "waiting",
                "players_joined": len(session.players),
                "slots_remaining": len(session.invite_codes),
            })

        if session.status in (SessionStatus.FINISHED, SessionStatus.ERROR):
            agent = session.polling_agents.get(player_id)
            outcome = agent.get_match_outcome() if agent else None
            return JSONResponse({
                "status": session.status.value,
                "game_over": True,
                "outcome": outcome,
                "error": session.error,
            })

        # Match is running — get the polling agent's current state
        agent = session.polling_agents.get(player_id)
        if agent is None:
            return JSONResponse({"error": "Player not found in session"}, status_code=400)

        if agent.has_match_ended():
            outcome = agent.get_match_outcome()
            return JSONResponse({
                "status": "finished",
                "game_over": True,
                "outcome": outcome,
            })

        state = agent.peek_state()
        if state is None:
            return JSONResponse({
                "status": "running",
                "is_my_turn": False,
                "waiting": True,
            })

        return JSONResponse({
            "status": "running",
            "is_my_turn": state.is_my_turn,
            "waiting": False,
            **state.model_dump(mode="json"),
        })

    async def api_session_act(request: Request) -> JSONResponse:
        """Submit an action for the current turn."""
        body = await request.json()
        token = body.get("token", "")
        if not token:
            return JSONResponse({"error": "token required"}, status_code=400)

        auth = _session_mgr.authenticate(token)
        if auth is None:
            return JSONResponse({"error": "Invalid token"}, status_code=401)
        session, player_id = auth

        if session.status != SessionStatus.RUNNING:
            return JSONResponse({"error": f"Session is {session.status.value}, not running"}, status_code=400)

        agent = session.polling_agents.get(player_id)
        if agent is None:
            return JSONResponse({"error": "Player not found"}, status_code=400)

        action_type = body.get("action_type", "")
        if not action_type:
            return JSONResponse({"error": "action_type required"}, status_code=400)

        payload = body.get("payload", {})
        messages = body.get("messages", [])

        ok = agent.submit_action(action_type, payload, messages)
        if not ok:
            return JSONResponse({"error": "Not your turn or no active state"}, status_code=400)

        return JSONResponse({"ok": True})

    async def api_session_chat_send(request: Request) -> JSONResponse:
        """Send a chat message to other players."""
        body = await request.json()
        token = body.get("token", "")
        if not token:
            return JSONResponse({"error": "token required"}, status_code=400)

        auth = _session_mgr.authenticate(token)
        if auth is None:
            return JSONResponse({"error": "Invalid token"}, status_code=401)
        session, player_id = auth

        content = body.get("content", "")
        if not content:
            return JSONResponse({"error": "content required"}, status_code=400)

        scope = body.get("scope", "public")
        to_player_ids = body.get("to_player_ids", [])

        _session_mgr.add_chat_message(session.session_id, player_id, content, scope, to_player_ids)
        return JSONResponse({"ok": True})

    async def api_session_chat_sync(request: Request) -> JSONResponse:
        """Read chat messages (with index-based pagination)."""
        token = request.query_params.get("token", "")
        if not token:
            return JSONResponse({"error": "token required"}, status_code=400)

        auth = _session_mgr.authenticate(token)
        if auth is None:
            return JSONResponse({"error": "Invalid token"}, status_code=401)
        session, player_id = auth

        since = int(request.query_params.get("index", "0"))
        messages = _session_mgr.get_chat_messages(session.session_id, player_id, since)
        return JSONResponse({"messages": messages, "total": since + len(messages)})

    async def api_session_sync(request: Request) -> JSONResponse:
        """Read game events (operator messages, actions, outcomes)."""
        token = request.query_params.get("token", "")
        if not token:
            return JSONResponse({"error": "token required"}, status_code=400)

        auth = _session_mgr.authenticate(token)
        if auth is None:
            return JSONResponse({"error": "Invalid token"}, status_code=401)
        session, _ = auth

        since = int(request.query_params.get("index", "0"))
        events = _session_mgr.get_game_events(session.session_id, since)
        return JSONResponse({"events": events, "total": since + len(events)})

    async def api_session_list(request: Request) -> JSONResponse:
        """List sessions, optionally filtered by status and game_id."""
        status = request.query_params.get("status")
        game_id = request.query_params.get("game_id")
        return JSONResponse({"sessions": _session_mgr.list_sessions(status, game_id)})

    async def api_session_events(request: Request) -> JSONResponse:
        """Public endpoint to spectate a session's game events (no auth needed)."""
        session_id = request.path_params.get("session_id", "")
        session = _session_mgr.get_session(session_id)
        if session is None:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        since = int(request.query_params.get("since", "0"))
        events = _session_mgr.get_game_events(session_id, since)
        return JSONResponse({
            "session_id": session_id,
            "status": session.status.value,
            "game_id": session.game_id,
            "players": [
                {"player_id": p.player_id, "display_name": p.display_name}
                for p in session.players
            ],
            "events": events,
            "events_total": since + len(events),
        })

    async def api_game_rules(request: Request) -> PlainTextResponse:
        """Serve per-game rules as markdown so agents can learn a specific game."""
        game_id = request.path_params.get("game_id", "")
        if game_id not in games:
            return PlainTextResponse(
                f"Unknown game: {game_id}. Available: {', '.join(games.keys())}",
                status_code=404,
            )
        g = games[game_id]
        spec = g.spec()
        lines = [
            f"# {spec.name}",
            "",
            spec.description,
            "",
            "## Phases",
            "",
        ]
        for phase in (spec.phases or []):
            lines.append(f"### {phase.name}")
            lines.append(f"- Turn order: {phase.turn_order.value}")
            if phase.max_rounds is not None:
                lines.append(f"- Max rounds: {phase.max_rounds}")
            lines.append(f"- Allowed actions: {', '.join(phase.allowed_action_types)}")
            lines.append("")

        lines.append("## Actions")
        lines.append("")
        for at in (spec.action_types or []):
            payload_desc = ""
            if at.payload_schema:
                fields = []
                for k, v in at.payload_schema.items():
                    if isinstance(v, dict):
                        fields.append(f"`{k}` ({v.get('type', 'any')})")
                    else:
                        fields.append(f"`{k}`")
                payload_desc = f" — payload: {', '.join(fields)}"
            lines.append(f"- **{at.name}**: {at.description}{payload_desc}")
        lines.append("")

        lines.append(f"## Outcome Rule")
        lines.append("")
        lines.append(f"{spec.outcome_rule.value}")
        lines.append("")

        if spec.initial_game_state:
            lines.append("## Initial Game State Keys")
            lines.append("")
            for key in spec.initial_game_state:
                lines.append(f"- `{key}`")
            lines.append("")

        return PlainTextResponse("\n".join(lines))

    async def api_skill_md(request: Request) -> PlainTextResponse:
        """Serve SKILL.md so AI agents can discover how to play."""
        skill_path = Path(__file__).resolve().parent.parent.parent / "SKILL.md"
        if not skill_path.is_file():
            return PlainTextResponse("SKILL.md not found", status_code=404)
        # Replace {{ARENA_URL}} placeholder with the actual URL
        host = request.headers.get("host", "localhost:8888")
        scheme = request.headers.get("x-forwarded-proto", "http")
        base_url = f"{scheme}://{host}"
        content = skill_path.read_text()
        content = content.replace("{{ARENA_URL}}", base_url)
        return PlainTextResponse(content)

    routes = [
        # Skill file for AI agents
        Route("/SKILL.md", api_skill_md),
        # API
        Route("/api/claim", api_claim_name, methods=["POST"]),
        Route("/api/register", api_register, methods=["POST"]),
        Route("/api/unregister", api_unregister, methods=["POST"]),
        Route("/api/agents", api_agents),
        Route("/api/games", api_games),
        Route("/api/games/{game_id}/rules", api_game_rules),
        Route("/api/leaderboard", api_leaderboard),
        Route("/api/history", api_history),
        Route("/api/match", api_match, methods=["POST"]),
        Route("/api/match/{session_id}", api_match_status),
        Route("/api/dashboard", api_dashboard_data),
        # Polling / Session API
        Route("/api/sessions", api_session_list),
        Route("/api/sessions/{session_id}/events", api_session_events),
        Route("/api/sessions/create", api_session_create, methods=["POST"]),
        Route("/api/sessions/join", api_session_join, methods=["POST"]),
        Route("/api/sessions/state", api_session_state),
        Route("/api/sessions/act", api_session_act, methods=["POST"]),
        Route("/api/sessions/chat", api_session_chat_send, methods=["POST"]),
        Route("/api/sessions/chat/sync", api_session_chat_sync),
        Route("/api/sessions/sync", api_session_sync),
    ]

    # Serve React dashboard build if available
    dist_dir = Path(__file__).resolve().parent.parent.parent / "dashboard" / "dist"
    if dist_dir.is_dir():
        async def spa_fallback(request: Request) -> FileResponse:
            return FileResponse(dist_dir / "index.html")

        routes.append(Mount("/assets", app=StaticFiles(directory=dist_dir / "assets")))
        routes.append(Route("/{path:path}", spa_fallback))
        routes.append(Route("/", spa_fallback))

    # Periodic cleanup of stale waiting sessions
    def _cleanup_loop() -> None:
        import time as _time
        while True:
            _time.sleep(30)
            _session_mgr.expire_stale_sessions(max_waiting_seconds=300)

    threading.Thread(target=_cleanup_loop, daemon=True).start()

    return Starlette(
        debug=False,
        routes=routes,
        middleware=[
            Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]),
        ],
    )
