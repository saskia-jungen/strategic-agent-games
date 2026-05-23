"""
Local debug agent for testing games against a local arena server.
Useful for reproducing issues in the deployment environment locally.

Focus: Test the dictator game and other games locally to debug issues.

Usage:
  python agent_debug.py --game dictator --name "DebugAgent"
  python agent_debug.py --game ultimatum --name "TestAgent" --model "anthropic/claude-sonnet-4-5"
  python agent_debug.py --game dictator --name "DebugAgent" --arena http://localhost:8888
    python agent_debug.py --game voluntary-contribution --name "VCMTester" --num-players 3

Requires: pip install requests
"""

import argparse, json, os, re, time
import requests
from dotenv import load_dotenv

load_dotenv()

# Configuration
ARENA = "http://localhost:8888"  # Local arena (change with --arena)
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not found in environment or .env file")

MODEL    = "anthropic/claude-sonnet-4-5"
PROVIDER = "openrouter"
POLL     = 1  # Poll interval in seconds

# Color codes for logging
G = "\033[92m"; C = "\033[96m"; Y = "\033[93m"
R = "\033[91m"; P = "\033[95m"; D = "\033[90m"; X = "\033[0m"

def log(name, msg, col=G):
    print(f"{col}[{name}] {msg}{X}", flush=True)

def dim(name, msg):
    print(f"{D}[{name}] {msg}{X}", flush=True)

def warn(name, msg):
    print(f"{Y}[{name}] {msg}{X}", flush=True)

def err(name, msg):
    print(f"{R}[{name}] {msg}{X}", flush=True)

def win(name, msg):
    print(f"{P}[{name}] {msg}{X}", flush=True)

def debug_state(name, state):
    """Dump full game state for debugging"""
    dim(name, "=== FULL STATE ===")
    print(json.dumps(state, indent=2), flush=True)
    dim(name, "=== END STATE ===")


# ── OpenRouter API ────────────────────────────────────────────────────────────

def call_model(system: str, user: str) -> str:
    """Call OpenRouter API with system and user prompts"""
    res = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
        },
        json={
            "model": MODEL,
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=20,
    )
    data = res.json()
    if "choices" not in data:
        raise ValueError(f"API error: {data.get('error', data)}")
    return data["choices"][0]["message"]["content"]


def parse_json(raw: str) -> dict:
    """Robustly extract JSON from model output"""
    raw = re.sub(r"```json|```", "", raw).strip()
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON in: {raw[:200]}")
    candidate = raw[start:end]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        fixed = re.sub(r"'([^']*)'", r'"\1"', candidate)
        return json.loads(fixed)


# ── Arena API ─────────────────────────────────────────────────────────────────

def get_sessions(game_id):
    """Get waiting sessions for a game"""
    try:
        r = requests.get(f"{ARENA}/api/sessions?status=waiting&game_id={game_id}", timeout=10)
        return r.json().get("sessions", [])
    except Exception as e:
        raise ValueError(f"Failed to get sessions: {e}")


def create_session(game_id, name, num_players=None):
    """Create a new game session"""
    try:
        payload = {"game_id": game_id, "player_name": name}
        if num_players is not None:
            payload["num_players"] = num_players
        r = requests.post(f"{ARENA}/api/sessions/create",
            json=payload, timeout=10)
        return r.json()
    except Exception as e:
        raise ValueError(f"Failed to create session: {e}")


def join_session(invite_code, name):
    """Join an existing game session"""
    try:
        r = requests.post(f"{ARENA}/api/sessions/join",
            json={"invite_code": invite_code, "player_name": name}, timeout=10)
        return r.json()
    except Exception as e:
        raise ValueError(f"Failed to join session: {e}")


def get_state(token):
    """Get current game state"""
    try:
        r = requests.get(f"{ARENA}/api/sessions/state?token={token}", timeout=30)
        return r.json()
    except Exception as e:
        raise ValueError(f"Failed to get state: {e}")


def get_rules(game_id):
    """Get game rules"""
    try:
        r = requests.get(f"{ARENA}/api/games/{game_id}/rules", timeout=10)
        return r.text
    except Exception as e:
        warn("DEBUG", f"Failed to get rules: {e}")
        return ""


def act(token, action_type, payload, messages):
    """Submit an action to the arena"""
    try:
        r = requests.post(f"{ARENA}/api/sessions/act",
            json={"token": token, "action_type": action_type,
                  "payload": payload, "messages": messages}, timeout=10)
        return r.json()
    except Exception as e:
        raise ValueError(f"Failed to act: {e}")


# ── Prompts ───────────────────────────────────────────────────────────────────

def build_prompt(game_id, agent_id, opponent_id, rules):
    """Build system prompt for the model"""
    hints = {
        "ultimatum": f"""Split a total with your opponent.
- submit_offer: {{"shares": {{"{agent_id}": X, "{opponent_id}": Y}}}} where X+Y=total
- accept: accept current offer
- reject: reject current offer
- pass: skip""",
        "bilateral-trade": f"""Negotiate a price as buyer or seller.
- propose: {{"price": <number>}}
- accept_price: accept current proposal
- reject_and_exit: walk away""",
        "first-price-auction": f"""Sealed bid auction. Highest bid wins, pays their bid.
- submit_bid: {{"bid": <number>}} — bid below your valuation""",
        "provision-point": f"""Commit funds to a public good.
- submit_commitment: {{"amount": <number>}}
- update_commitment: {{"new_amount": <number>}}""",
        "dictator": f"""You are the ALLOCATOR (dictator).
Allocate a pie split between yourself (allocator) and the recipient.
- allocate_split: {{"allocator_share": X, "recipient_share": Y}} where X+Y equals pie
- pass: skip (results in 0 for both)""",
        "trust": f"""Trustor sends amount; trustee receives multiplied and returns some.
- send: {{"amount": <number>}} (trustor only, <= endowment)
- return_amount: {{"amount": <number>}} (trustee only, <= multiplier * sent)
- pass: skip""",
        "public-project": f"""Report valuation for public project; built if total >= cost.
- report_value: {{"report": <number>}} (submit your valuation)
- pass: skip (counts as final response)""",
        "voluntary-contribution": f"""Choose a contribution to a public good.
    - contribute: {{"amount": <number>}} (0 <= amount <= endowment)
    - pass: skip""",
        "insurance-moral-hazard": f"""Insurance with moral hazard.
    Insurer offers contract {{premium, transfer_good, transfer_bad}}. Insured accepts/rejects; if accepted, insured chooses effort.
    Output ONLY JSON. No analysis, no markdown.
    - offer: {{"premium": <number>, "transfer_good": <number>, "transfer_bad": <number>}}
    - accept: {{}}
    - reject: {{}}
    - choose_effort: {{"effort": "low" | "high"}}""",
        "principal-agent": f"""Principal-Agent (task delegation).
    - post_contract: {{"task_description": "...", "success_criteria": "..."}}
    - ask_clarification: {{"question": "..."}}
    - answer_clarification: {{"answer": "..."}}
    - accept_contract / reject_contract
    - submit_deliverable: {{"content": "..."}}
    - record_outcome_score: {{"score": <0-100>, "notes": "..."}}""",
    }
    
    return f"""You are agent "{agent_id}" playing {game_id} against "{opponent_id}".

RULES: {rules[:600]}

{hints.get(game_id, "")}

Respond with ONLY valid JSON:
{{"action_type": "...", "payload": {{}}, "message": "optional short message"}}

Always use exact agent IDs: yours="{agent_id}", opponent="{opponent_id}"."""


# ── Decision Logic ────────────────────────────────────────────────────────────

def decide(name, game_id, state, rules):
    """Use model to decide on next action"""
    gs = state.get("game_state", {})
    allowed = [a["action_type"] if isinstance(a, dict) else a
               for a in state.get("allowed_actions", [])]
    agent_id = state.get("player_id", name.lower())
    all_agents = gs.get("agent_ids", [agent_id, "opponent"])
    opponent_id = next((a for a in all_agents if a != agent_id), "opponent")
    convo = "\n".join([f"  {m.get('sender','?')}: {m.get('content','')}"
                       for m in state.get("messages", [])[-6:]]) or "  (none)"

    system = build_prompt(game_id, agent_id, opponent_id, rules)
    user = f"""Game state: {json.dumps(gs, indent=2)}
Allowed actions: {allowed}
Recent messages:
{convo}
Decide now."""

    try:
        raw = call_model(system, user)
        dim(name, f"Model raw response (first 200 chars): {raw[:200]}")
        parsed = parse_json(raw)
        dim(name, f"Parsed: {json.dumps(parsed)}")

        action_type = parsed.get("action_type", "pass")
        payload = parsed.get("payload", {})
        message = parsed.get("message", "")

        # Fix "opponent" key in shares
        if action_type == "submit_offer":
            shares = payload.get("shares", {})
            payload["shares"] = {
                opponent_id if k not in (agent_id, opponent_id) else k: v
                for k, v in shares.items()
            }

        if game_id == "insurance-moral-hazard" and action_type == "offer":
            if not all(k in payload for k in ("premium", "transfer_good", "transfer_bad")):
                warn(name, f"Invalid offer payload: {payload} — passing")
                return "pass", {}, ""
        if game_id == "insurance-moral-hazard" and action_type == "choose_effort":
            effort = payload.get("effort")
            if effort not in ("low", "high"):
                warn(name, f"Invalid effort payload: {payload} — passing")
                return "pass", {}, ""

        if action_type not in allowed:
            warn(name, f"{action_type} not allowed (allowed: {allowed}) — passing")
            return "pass", {}, ""

        return action_type, payload, message

    except Exception as e:
        err(name, f"Model error: {e}")
        return fallback(game_id, allowed, gs, agent_id, opponent_id)


def fallback(game_id, allowed, gs, agent_id, opponent_id):
    """Rule-based fallback when model fails"""
    total = gs.get("total", 100)

    if game_id == "insurance-moral-hazard":
        if "offer" in allowed:
            return "offer", {"premium": 6, "transfer_good": 8, "transfer_bad": 2}, "Proposing contract."
        if "accept" in allowed:
            return "accept", {}, "Accepting contract."
        if "reject" in allowed:
            return "reject", {}, "Rejecting contract."
        if "choose_effort" in allowed:
            return "choose_effort", {"effort": "high"}, "Choosing high effort."

    if game_id == "principal-agent":
        if "post_contract" in allowed:
            return "post_contract", {
                "task_description": "Summarize the report in 5 bullets.",
                "success_criteria": "Includes 5 concise bullets covering key points.",
            }, "Posting contract."
        if "ask_clarification" in allowed:
            return "ask_clarification", {"question": "Any length or formatting constraints?"}, "Clarifying."
        if "answer_clarification" in allowed:
            return "answer_clarification", {"answer": "No extra constraints beyond the criteria."}, "Answering."
        if "accept_contract" in allowed:
            return "accept_contract", {}, "Accepting contract."
        if "reject_contract" in allowed:
            return "reject_contract", {"reason": "Decline."}, "Rejecting."
        if "submit_deliverable" in allowed:
            return "submit_deliverable", {
                "content": "- Point 1\n- Point 2\n- Point 3\n- Point 4\n- Point 5",
            }, "Submitting deliverable."
        if "record_outcome_score" in allowed:
            return "record_outcome_score", {"score": 80, "notes": "Meets criteria."}, "Scoring."
        if "skip_clarify" in allowed:
            return "skip_clarify", {}, "Skipping clarification."

    if "message_only" in allowed:
        return "message_only", {}, "Ready to play."
    
    if "allocate_split" in allowed:
        pie = gs.get("pie", 100)
        my_share = round(pie * 0.55)
        return "allocate_split", {"allocator_share": my_share, "recipient_share": pie - my_share}, "Allocating."
    
    if "submit_offer" in allowed:
        mine = round(total * 0.55)
        return "submit_offer", {"shares": {agent_id: mine, opponent_id: total - mine}}, "Making an offer."
    
    if "accept" in allowed and gs.get("current_offer"):
        share = gs["current_offer"].get(agent_id, 0)
        if share >= gs.get("my_reservation_value", 0):
            return "accept", {}, "Accepting."
    
    if "submit_bid" in allowed and not gs.get("my_bid"):
        val = gs.get("my_valuation", 50)
        return "submit_bid", {"bid": round(val * 0.65)}, "Placing bid."
    
    if "propose" in allowed:
        return "propose", {"price": 55}, "Proposing."
    
    if "submit_commitment" in allowed:
        return "submit_commitment", {"amount": 30}, "Committing."

    if "contribute" in allowed:
        endowment = gs.get("endowment", 10)
        amount = round(endowment * 0.4, 2)
        return "contribute", {"amount": amount}, "Contributing."
    
    if "send" in allowed:
        endowment = gs.get("endowment", 10)
        return "send", {"amount": round(endowment * 0.5)}, "Sending."
    
    if "return_amount" in allowed:
        received = gs.get("received_amount", 0)
        return "return_amount", {"amount": round(received * 0.5)}, "Returning."
    
    if "report_value" in allowed:
        valuation = gs.get("my_valuation", 50)
        return "report_value", {"report": valuation}, "Reporting valuation."
    
    return "pass", {}, ""


# ── Main Game Loop ────────────────────────────────────────────────────────────

def play(game_id, name, num_players=None):
    """Play a game from start to finish"""
    log(name, f"Starting debug session | game={game_id} | model={MODEL}")
    log(name, f"Arena: {ARENA}")
    if num_players is not None:
        log(name, f"Target players: {num_players}")

    rules = get_rules(game_id)
    dim(name, f"Rules fetched ({len(rules)} chars)")

    # Find or create a session
    sessions = get_sessions(game_id)
    session = None
    if sessions:
        if num_players is None:
            session = sessions[0]
        else:
            for s in sessions:
                total_players = s.get("num_players", 0) + s.get("slots_remaining", 0)
                if total_players == num_players:
                    session = s
                    break
    if session:
        invite = session["invite_codes"][0]
        log(name, f"Found waiting session, joining...")
        result = join_session(invite, name)
    else:
        log(name, "No waiting sessions — creating new one")
        result = create_session(game_id, name, num_players=num_players)
        log(name, "Waiting for opponent...")

    if "token" not in result:
        err(name, f"Failed to create/join session: {result}")
        return

    token = result["token"]
    log(name, f"Session token: {token[:30]}...")
    
    # Get real player_id from server
    try:
        initial_state = get_state(token)
        real_player_id = initial_state.get("player_id", name.lower())
        log(name, f"Assigned player_id: {real_player_id}")
    except Exception as e:
        err(name, f"Could not get initial state: {e}")
        return

    # Wait for game to start
    while True:
        try:
            state = get_state(token)
            status = state.get("status")
            if status == "running":
                break
            if status == "finished":
                win(name, "Game already finished")
                return
            dim(name, f"Waiting for opponent... (status: {status})")
            time.sleep(POLL)
        except Exception as e:
            err(name, f"Error waiting for game: {e}")
            time.sleep(POLL)

    log(name, "✓ Match started!")

    # Main game loop
    turn_count = 0
    while True:
        try:
            state = get_state(token)
            status = state.get("status", "")

            if status == "finished":
                outcome = state.get("outcome") or {}
                payoffs = outcome.get("payoffs", [])
                my_pay = next((p for p in payoffs if p.get("agent_id") == state.get("player_id")), {})
                win(name, f"✓ GAME OVER")
                log(name, f"Reason: {outcome.get('reason')}")
                log(name, f"My utility: {my_pay.get('utility', '?')}")
                log(name, f"Full outcome: {json.dumps(outcome, indent=2)}")
                break

            if not state.get("is_my_turn"):
                dim(name, "Waiting for opponent...")
                time.sleep(POLL)
                continue

            turn_count += 1
            log(name, f"[Turn {turn_count}] My turn | phase={state.get('phase')}")
            
            # Debug: dump game state for dictator game
            if game_id == "dictator":
                dim(name, f"Dictator game state:")
                gs = state.get("game_state", {})
                dim(name, f"  pie: {gs.get('pie')}")
                dim(name, f"  agent_ids: {gs.get('agent_ids')}")
                dim(name, f"  my_id: {state.get('player_id')}")
            
            # Decide on action
            action_type, payload, message = decide(name, game_id, state, rules)
            log(name, f"Action: {action_type} with payload: {json.dumps(payload)}")

            if message:
                log(name, f"Message: {message}")

            # Submit action
            msgs = [{"scope": "public", "content": message}] if message else []
            try:
                result = act(token, action_type, payload, msgs)
                if result.get("ok") is False or "error" in result:
                    err(name, f"Action rejected: {result}")
                else:
                    log(name, "✓ Action accepted")
            except Exception as e:
                err(name, f"Failed to submit action: {e}")

            time.sleep(0.5)

        except Exception as e:
            err(name, f"Game loop error: {e}")
            time.sleep(POLL)


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local debug agent for testing games")
    parser.add_argument("--game", type=str, default="dictator",
                        choices=["ultimatum", "bilateral-trade", "first-price-auction",
                                 "provision-point", "dictator", "trust", "public-project",
                                 "voluntary-contribution", "insurance-moral-hazard", "principal-agent"],
                        help="Game to play (default: dictator for debugging)")
    parser.add_argument("--name", type=str, default="DebugAgent",
                        help="Agent name")
    parser.add_argument("--model", type=str, default="anthropic/claude-sonnet-4-5",
                        help="OpenRouter model ID")
    parser.add_argument("--arena", type=str, default="http://localhost:8888",
                        help="Arena URL (default: http://localhost:8888)")
    parser.add_argument("--num-players", type=int, default=2,
                        help="Create/join sessions with this total player count")
    
    args = parser.parse_args()

    MODEL = args.model
    ARENA = args.arena

    print(f"Agent   : {args.name}")
    print(f"Game    : {args.game}")
    print(f"Model   : {MODEL}")
    print(f"Arena   : {ARENA}")
    print()

    try:
        play(args.game, args.name, num_players=args.num_players)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user", flush=True)
    except Exception as e:
        err("MAIN", f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
