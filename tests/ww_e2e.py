"""Drive a full 6-player Werewolf match through the engine to validate it."""
import sys
from arena.games.builtins import ensure_builtins_registered
from arena.games import get_game
from arena.core.runner import create_match
from arena.core.match import MatchStatus
from arena.types import Action

ensure_builtins_registered()
game = get_game("werewolf")
spec = game.spec()
assert spec.min_agents == 6 and spec.max_agents == 6, "must be fixed 6-player"

AGENTS = [f"P{i}" for i in range(6)]
# Deterministic role assignment via seed so the test is reproducible.
g = game.__class__(seed=42)
match = create_match("m1", "werewolf", g.spec(), AGENTS)
assert match.status == MatchStatus.RUNNING

# Force role table by initializing once.
g._ensure_initialized(match)
roles = match.game_state["roles"]
wolves = [a for a in AGENTS if roles[a] == "werewolf"]
seer = [a for a in AGENTS if roles[a] == "seer"][0]
villagers = [a for a in AGENTS if roles[a] == "villager"]
print("roles:", roles)
assert len(wolves) == 2 and len(villagers) == 3

# --- privacy checks ---
ts_v = g.compute_turn_state(match, villagers[0])
assert ts_v.game_state["my_role"] == "villager"
assert "fellow_werewolves" not in ts_v.game_state, "villager must not see wolf info"
ts_w = g.compute_turn_state(match, wolves[0])
assert ts_w.game_state["fellow_werewolves"] == [wolves[1]], "wolf sees partner"
ts_s = g.compute_turn_state(match, seer)
assert ts_s.game_state["my_role"] == "seer"
print("privacy OK")


def current(match):
    return match.agent_ids[match.current_turn_index % 6]


def act(match, aid, atype, payload=None):
    res = g.apply_action(match, aid, Action(action_type=atype, payload=payload or {}))
    assert res.ok, f"{aid} {atype} failed: {res.error_detail}"
    out = g.compute_outcome(match)
    if out:
        match.outcome = out
        match.status = MatchStatus.FINISHED


turns = 0
MAX = 200
while match.status == MatchStatus.RUNNING and turns < MAX:
    turns += 1
    phase = g._current_phase_name(match)
    actor = current(match)
    living = g._living_ids(match)

    if phase == "night_werewolf":
        # Both wolves agree to kill a villager (whoever's alive).
        victim = next(v for v in villagers if v in living)
        act(match, actor, "night_kill", {"target": victim})
    elif phase == "night_seer":
        # Seer inspects a wolf the first time, then anyone.
        tgt = next((w for w in wolves if w in living), living[0])
        act(match, actor, "seer_inspect", {"inspect": tgt})
    elif phase == "day_announce":
        act(match, actor, "acknowledge")
    elif phase == "day_discuss":
        # Everyone immediately signals ready -> jump to vote quickly.
        act(match, actor, "ready_to_vote")
    elif phase == "day_vote":
        # Villagers + seer all vote for a living wolf; wolves vote a villager.
        living_wolves = [w for w in wolves if w in living]
        if roles[actor] == "werewolf":
            tgt = next((v for v in villagers if v in living), living_wolves[0])
        else:
            tgt = living_wolves[0] if living_wolves else living[0]
        act(match, actor, "cast_vote", {"target": tgt})
    else:
        print("unexpected phase", phase); break

print(f"finished after {turns} engine turns; status={match.status.value}")
print("outcome:", match.outcome and match.outcome.get("winner"), match.outcome and match.outcome.get("reason"))
print("event_log:")
for e in match.game_state["event_log"]:
    print("  ", e)

assert match.status == MatchStatus.FINISHED, "game must end"
assert match.outcome["winner"] in ("village", "werewolves")
# With the whole village correctly voting wolves out, village should win.
assert match.outcome["winner"] == "village", "coordinated village should win this script"
payoffs = {p["agent_id"]: p["utility"] for p in match.outcome["payoffs"]}
for a in AGENTS:
    expected = 1.0 if roles[a] != "werewolf" else -1.0
    assert payoffs[a] == expected, f"payoff mismatch for {a}: {payoffs[a]}"
print("ALL ENGINE CHECKS PASSED")
