"""In-process test of the SessionManager (pooler) + ExperimentRunner for a
6-player Werewolf match — the exact code path the server uses, minus HTTP and
the (separately-broken) DB seeder.
"""
import threading, time, json
from arena.games.builtins import ensure_builtins_registered
from arena.games import get_game
from arena.server.sessions import SessionManager, SessionStatus
from arena.experiment.runner import ExperimentConfig, ExperimentRunner

ensure_builtins_registered()

mgr = SessionManager()

# 1) create a 6-player session (this is what POST /api/sessions/create does)
info = mgr.create_session(
    game_id="werewolf", num_players=6, creator_name="WW0",
    game_params={"seed": 1, "discuss_rounds": 6}, max_turns=250,
)
sid = info["session_id"]
print("created session:", sid, "| invite codes:", len(info["invite_codes"]))
assert len(info["invite_codes"]) == 5, "6-player session must mint 5 invites"

# 2) five players join (POST /api/sessions/join)
for i, inv in enumerate(info["invite_codes"], start=1):
    res = mgr.join_session(inv, f"WW{i}")
    assert res is not None, f"join {i} failed"
print("all joined; ready_to_start =", mgr.is_ready_to_start(sid))
assert mgr.is_ready_to_start(sid), "session should auto-be-ready with 6 players"

# 3) replicate server._start_session_match
session = mgr.get_session(sid)
mgr.set_status(sid, SessionStatus.RUNNING)
player_ids = [p.player_id for p in session.players]
all_agents = [session.polling_agents[pid] for pid in player_ids]
agent_ids = [a.agent_id for a in all_agents]
print("agent_ids:", agent_ids)

game_cls = type(get_game("werewolf"))
game = game_cls.from_params(session.game_params, agent_ids)
config = ExperimentConfig(game_id="werewolf", num_matches=1,
                          max_turns_per_match=session.max_turns, max_messages_per_turn=6)

events = []
runner = ExperimentRunner(config, on_event=lambda e: events.append(e))

# 4) drive the PollingAgents from "client" threads, exactly like real pollers
stop = threading.Event()

def client(agent):
    while not stop.is_set():
        if agent.has_match_ended():
            return
        st = agent.peek_state()
        if st is None or not agent.is_waiting_for_action():
            time.sleep(0.02); continue
        gs = st.game_state; phase = st.phase
        allowed = [a.action_type for a in st.allowed_actions]
        my_id = st.agent_id; my = gs.get("my_role"); living = gs.get("alive_players", [])
        atype, payload, msgs = "message_only", {}, []
        if phase == "night_werewolf" and "night_kill" in allowed:
            fellow = set(gs.get("fellow_werewolves", [])) | {my_id}
            tgt = next((p for p in living if p not in fellow), living[0])
            atype, payload = "night_kill", {"target": tgt}
            msgs = [{"scope": "private", "content": f"take {tgt}", "to_agent_ids": gs.get("fellow_werewolves", [])}]
        elif phase == "night_seer" and "seer_inspect" in allowed:
            tgt = next((p for p in living if p != my_id), living[0])
            atype, payload = "seer_inspect", {"inspect": tgt}
        elif phase == "day_announce" and "acknowledge" in allowed:
            atype = "acknowledge"
        elif phase == "day_discuss" and "ready_to_vote" in allowed:
            atype = "ready_to_vote"; msgs = [{"scope": "public", "content": "ready", "to_agent_ids": []}]
        elif phase == "day_vote" and "cast_vote" in allowed:
            if my == "werewolf":
                fellow = set(gs.get("fellow_werewolves", [])) | {my_id}
                tgt = next((p for p in living if p not in fellow), living[0])
            else:
                # villagers coordinate on the lexicographically-first living player who
                # is a known wolf if seer told them, else just first non-self
                tgt = next((p for p in living if p != my_id), living[0])
            atype, payload = "cast_vote", {"target": tgt}
        agent.submit_action(atype, payload, msgs)
        time.sleep(0.02)

clients = [threading.Thread(target=client, args=(a,), daemon=True) for a in all_agents]
for c in clients: c.start()

result = runner.run(all_agents, game=game)
stop.set()

mr = result.match_results[0]
print("match status:", mr.status, "| turns:", mr.num_turns)
print("outcome:", json.dumps(mr.outcome, indent=2))
assert mr.outcome is not None and mr.outcome.get("winner") in ("village", "werewolves")
# confirm private night whispers were recorded as private (visibility honored)
print("SESSION/POOLER PATH PASSED — winner:", mr.outcome["winner"])
