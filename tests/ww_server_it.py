"""Full integration test through the live HTTP session/pooler API.

Proves the session manager + server need NO werewolf-specific changes:
just create with num_players=6, join 5 invites, match auto-starts and runs.
"""
import threading, time, requests, sys, json

BASE = "http://127.0.0.1:8899"


def state(tok):
    return requests.get(f"{BASE}/api/sessions/state", params={"token": tok}, timeout=40).json()


def chat_sync(tok, idx):
    return requests.get(f"{BASE}/api/sessions/chat/sync", params={"token": tok, "index": idx}, timeout=20).json()


def play(tok, name, results):
    """A minimal scripted werewolf poller (no LLM): plays legal moves."""
    seen_msg_idx = 0
    while True:
        st = state(tok)
        status = st.get("status")
        if status == "waiting":
            time.sleep(0.4); continue
        if st.get("game_over") or status in ("finished", "error"):
            results[name] = st.get("outcome")
            return
        if not st.get("is_my_turn"):
            time.sleep(0.3); continue

        gs = st["game_state"]
        phase = st["phase"]
        allowed = [a["action_type"] for a in st["allowed_actions"]]
        my = gs["my_role"]
        living = gs["alive_players"]
        me = gs["agent_ids"]  # not my id directly; use agent_id field
        my_id = st["agent_id"]
        body = {"token": tok, "payload": {}}

        if phase == "night_werewolf" and "night_kill" in allowed:
            # kill first living non-wolf
            fellow = set(gs.get("fellow_werewolves", [])) | {my_id}
            target = next((p for p in living if p not in fellow), None)
            # optionally whisper to teammate
            msgs = [{"scope": "private", "content": f"let's take {target}",
                     "to_agent_ids": gs.get("fellow_werewolves", [])}]
            body.update(action_type="night_kill", payload={"target": target}, messages=msgs)
        elif phase == "night_seer" and "seer_inspect" in allowed:
            target = next((p for p in living if p != my_id), living[0])
            body.update(action_type="seer_inspect", payload={"inspect": target})
        elif phase == "day_announce" and "acknowledge" in allowed:
            body.update(action_type="acknowledge")
        elif phase == "day_discuss":
            body.update(action_type="ready_to_vote",
                        messages=[{"scope": "public", "content": "I'm ready to vote.", "to_agent_ids": []}])
        elif phase == "day_vote" and "cast_vote" in allowed:
            # villagers/seer vote for first living player that isn't them; wolves protect each other
            if my == "werewolf":
                fellow = set(gs.get("fellow_werewolves", [])) | {my_id}
                target = next((p for p in living if p not in fellow), living[0])
            else:
                target = next((p for p in living if p != my_id), living[0])
            body.update(action_type="cast_vote", payload={"target": target})
        else:
            body.update(action_type="message_only")

        r = requests.post(f"{BASE}/api/sessions/act", json=body, timeout=20)
        time.sleep(0.15)


def main():
    # 1. create session for 6 players
    r = requests.post(f"{BASE}/api/sessions/create", json={
        "game_id": "werewolf", "player_name": "WW0", "num_players": 6,
        "game_params": {"seed": 1, "discuss_rounds": 6},
    }, timeout=10).json()
    print("create:", {k: r[k] for k in ("session_id", "game_id", "status")})
    invites = r["invite_codes"]
    assert len(invites) == 5, f"expected 5 invite codes, got {len(invites)}"
    tokens = [r["token"]]
    names = ["WW0"]

    # 2. five others join
    for i, inv in enumerate(invites, start=1):
        jr = requests.post(f"{BASE}/api/sessions/join", json={
            "invite_code": inv, "player_name": f"WW{i}"}, timeout=10).json()
        tokens.append(jr["token"]); names.append(f"WW{i}")
    print("joined all 6; match should auto-start")

    # 3. play concurrently
    results = {}
    threads = [threading.Thread(target=play, args=(t, n, results), daemon=True)
               for t, n in zip(tokens, names)]
    for th in threads: th.start()
    for th in threads: th.join(timeout=60)

    outcome = next((v for v in results.values() if v), None)
    print("OUTCOME:", json.dumps(outcome, indent=2) if outcome else results)
    assert outcome and outcome.get("winner") in ("village", "werewolves"), "match did not finish cleanly"
    print("INTEGRATION TEST PASSED — winner:", outcome["winner"])


if __name__ == "__main__":
    main()
