import argparse, json, os, re, time
import requests
from dotenv import load_dotenv

"""
Multi-model agent for https://strategic-agent-games-production.up.railway.app/
Supports OpenRouter (Claude, GPT-4o, Llama etc) AND IBM WatsonX

OpenRouter example:
  python3 agent.py --game ultimatum --name "Claude" --model "anthropic/claude-sonnet-4-5" --num-players 3
  python3 agent.py --game ultimatum --name "GPT4o"  --model "openai/gpt-4o"
  python3 agent.py --game ultimatum --name "Llama"  --model "meta-llama/llama-3.3-70b-instruct"

WatsonX example:
  python3 agent.py --game ultimatum --name "Granite" --provider watsonx \
    --model "ibm/granite-4-h-small" \
    --watsonx-key YOUR_IAM_API_KEY \
    --watsonx-project project_id

Requires: pip install requests
"""

load_dotenv()


ARENA    = "https://strategic-agent-games-production.up.railway.app"
API_KEY  =  os.environ.get("OPENROUTER_API_KEY")
MODEL    = "anthropic/claude-sonnet-4-5"
PROVIDER = "openrouter"
POLL     = 2

# WatsonX globals (set at startup)
WX_API_KEY   = os.environ.get("WX_API_KEY")
WX_PROJECT   = os.environ.get("WX_PROJECT")
WX_REGION    = "eu-de"
WX_TOKEN     = None
WX_TOKEN_EXP = 0

G = "\033[92m"; C = "\033[96m"; Y = "\033[93m"
R = "\033[91m"; P = "\033[95m"; D = "\033[90m"; X = "\033[0m"

def log(name, msg, col=G): print(f"{col}[{name}] {msg}{X}", flush=True)
def dim(name, msg):        print(f"{D}[{name}] {msg}{X}", flush=True)
def warn(name, msg):       print(f"{Y}[{name}] {msg}{X}", flush=True)
def err(name, msg):        print(f"{R}[{name}] {msg}{X}", flush=True)
def win(name, msg):        print(f"{P}[{name}] {msg}{X}", flush=True)


# ── WatsonX ───────────────────────────────────────────────────────────────────

def get_watsonx_token() -> str:
    global WX_TOKEN, WX_TOKEN_EXP
    now = time.time()
    if WX_TOKEN and now < WX_TOKEN_EXP - 60:
        return WX_TOKEN
    print("  Refreshing WatsonX IAM token...", flush=True)
    res = requests.post(
        "https://iam.cloud.ibm.com/identity/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"grant_type=urn:ibm:params:oauth:grant-type:apikey&apikey={WX_API_KEY}",
        timeout=15,
    )
    data = res.json()
    if "access_token" not in data:
        raise ValueError(f"WatsonX token error: {data}")
    WX_TOKEN     = data["access_token"]
    WX_TOKEN_EXP = now + data.get("expires_in", 3600)
    return WX_TOKEN


def call_watsonx(system: str, user: str) -> str:
    token = get_watsonx_token()
    res = requests.post(
        f"https://{WX_REGION}.ml.cloud.ibm.com/ml/v1/text/chat?version=2023-05-29",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        },
        json={
            "model_id":   MODEL,
            "project_id": WX_PROJECT,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "parameters": {"max_new_tokens": 400},
        },
        timeout=30,
    )
    data = res.json()
    if "choices" not in data:
        raise ValueError(f"WatsonX error: {data}")
    return data["choices"][0]["message"]["content"]


# ── OpenRouter ────────────────────────────────────────────────────────────────
def call_openrouter(system: str, user: str) -> str:
    res = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  "http://localhost",
        },
        json={
            "model":      MODEL,
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        },
        timeout=20,
    )
    data = res.json()
    if "choices" not in data:
        raise ValueError(f"API error: {data.get('error', data)}")
    return data["choices"][0]["message"]["content"]


def call_model(system: str, user: str) -> str:
    if PROVIDER == "watsonx":
        return call_watsonx(system, user)
    return call_openrouter(system, user)


def parse_json(raw: str) -> dict:
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


# ── Arena API (gets the sessions) ─────────────────────────────────────────────────────────────────
def get_sessions(game_id):
    r = requests.get(f"{ARENA}/api/sessions?status=waiting&game_id={game_id}", timeout=10)
    return r.json().get("sessions", [])  

def create_session(game_id, name, num_players=None):
    payload = {"game_id": game_id, "player_name": name}
    if num_players is not None:
        payload["num_players"] = num_players
    r = requests.post(f"{ARENA}/api/sessions/create",
        json=payload, timeout=10)
    return r.json()

def join_session(invite_code, name):
    r = requests.post(f"{ARENA}/api/sessions/join",
        json={"invite_code": invite_code, "player_name": name}, timeout=10)
    return r.json()

def get_state(token):
    r = requests.get(f"{ARENA}/api/sessions/state?token={token}", timeout=30)
    return r.json()

def get_rules(game_id):
    r = requests.get(f"{ARENA}/api/games/{game_id}/rules", timeout=10)
    return r.text

def act(token, action_type, payload, messages):
    r = requests.post(f"{ARENA}/api/sessions/act",
        json={"token": token, "action_type": action_type,
              "payload": payload, "messages": messages}, timeout=10)
    return r.json()


# ── Prompts (builds the system prompt, quick fix ) ───────────────────────────────────────────────────────────────────
def build_prompt(game_id, agent_id, opponent_id, rules):
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
        "dictator": f"""Allocate a pie split between allocator and recipient.
- allocate_split: {{"allocator_share": X, "recipient_share": Y}} where X+Y equals pie
- pass: skip""",
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
    }
    return f"""You are agent "{agent_id}" playing {game_id} against "{opponent_id}".

RULES: {rules[:600]}

{hints.get(game_id, "")}

Respond with ONLY valid JSON:
{{"action_type": "...", "payload": {{}}, "message": "optional short message"}}

Always use exact agent IDs: yours="{agent_id}", opponent="{opponent_id}"."""


# ── Decision ──────────────────────────────────────────────────────────────────
def decide(name, game_id, state, rules):
    gs          = state.get("game_state", {})
    allowed     = [a["action_type"] if isinstance(a, dict) else a
                   for a in state.get("allowed_actions", [])]
    agent_id    = state.get("player_id", name.lower())
    all_agents  = gs.get("agent_ids", [agent_id, "opponent"])
    opponent_id = next((a for a in all_agents if a != agent_id), "opponent")
    convo       = "\n".join([f"  {m.get('sender','?')}: {m.get('content','')}"
                             for m in state.get("messages", [])[-6:]]) or "  (none)"

    system = build_prompt(game_id, agent_id, opponent_id, rules)
    user   = f"""Game state: {json.dumps(gs, indent=2)}
Allowed actions: {allowed}
Recent messages:
{convo}
Decide now."""

    try:
        raw    = call_model(system, user)
        dim(name, f"Model: {raw[:200]}")
        parsed = parse_json(raw)

        action_type = parsed.get("action_type", "pass")
        payload     = parsed.get("payload", {})
        message     = parsed.get("message", "")

        # Fix "opponent" key in shares
        if action_type == "submit_offer":
            shares = payload.get("shares", {})
            payload["shares"] = {
                opponent_id if k not in (agent_id, opponent_id) else k: v
                for k, v in shares.items()
            }

        if action_type not in allowed:
            warn(name, f"{action_type} not allowed — passing")
            return "pass", {}, ""

        return action_type, payload, message

    except Exception as e:
        err(name, f"Model error: {e} — fallback")
        return fallback(game_id, allowed, gs, agent_id, opponent_id)


def fallback(game_id, allowed, gs, agent_id, opponent_id):
    total = gs.get("total", 100)
    if "message_only" in allowed:
        return "message_only", {}, "Ready to play."
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
    if "allocate_split" in allowed:
        pie = gs.get("pie", 100)
        my_share = round(pie * 0.55)
        return "allocate_split", {"allocator_share": my_share, "recipient_share": pie - my_share}, "Allocating."
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


# ── Main loop(plays the game) ─────────────────────────────────────────────────────────────────
def play(game_id, name, num_players=None):
    log(name, f"Starting | game={game_id} | model={MODEL} | provider={PROVIDER}")
    if num_players is not None:
        log(name, f"Target players: {num_players}")

    rules = get_rules(game_id)
    dim(name, f"Rules fetched ({len(rules)} chars)")

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
        log(name, f"Joining existing session")
        result = join_session(invite, name)
    else:
        log(name, "No open sessions — creating one")
        result = create_session(game_id, name, num_players=num_players)
        log(name, "Waiting for opponent...")

    if "token" not in result:
        err(name, f"Failed: {result}")
        return

    token = result["token"]
    log(name, f"Token: {token[:20]}...")
    #Get real player_id from server immediately
    initial_state = get_state(token)
    real_player_id = initial_state.get("player_id", name.lower())
    log(name, f"Real player_id: {real_player_id}")

    while True:
        state = get_state(token)
        if state.get("status") == "running":
            break
        if state.get("status") == "finished":
            win(name, "Already finished")
            return
        dim(name, "Waiting for opponent...")
        time.sleep(POLL)

    log(name, "Match started!")

    while True:
        state  = get_state(token)
        status = state.get("status", "")

        if status == "finished":
            outcome = state.get("outcome") or {}
            payoffs = outcome.get("payoffs", [])
            my_pay  = next((p for p in payoffs if p.get("agent_id") == state.get("player_id")), {})
            win(name, f"GAME OVER — {outcome.get('reason')} | utility: {my_pay.get('utility', '?')}")
            break

        if not state.get("is_my_turn"):
            dim(name, "Waiting for opponent...")
            time.sleep(POLL)
            continue

        log(name, f"My turn | phase={state.get('phase')}")
        action_type, payload, message = decide(name, game_id, state, rules)
        log(name, f"Acting: {action_type} {payload}")

        msgs   = [{"scope": "public", "content": message}] if message else []
        result = act(token, action_type, payload, msgs)

        if result.get("ok") is False or "error" in result:
            err(name, f"Action failed: {result}")
        else:
            log(name, "Action accepted ✓")

        time.sleep(0.5)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--game",     default="ultimatum",
                        choices=["ultimatum","bilateral-trade","first-price-auction","provision-point","dictator","trust","public-project","voluntary-contribution"])
    parser.add_argument("--name",     default="Agent")
    parser.add_argument("--model",    default="anthropic/claude-sonnet-4-5")
    parser.add_argument("--provider", default="openrouter", choices=["openrouter","watsonx"])
    parser.add_argument("--num-players", type=int, default=2,
                        help="Create/join sessions with this total player count")
    parser.add_argument("--watsonx-key",     default=os.environ.get("WATSONX_API_KEY", ""))
    parser.add_argument("--watsonx-project", default=os.environ.get("WATSONX_PROJECT_ID", ""))
    parser.add_argument("--watsonx-region",  default="eu-de")
    args = parser.parse_args()

    MODEL    = args.model
    PROVIDER = args.provider

    if PROVIDER == "watsonx":
        if args.watsonx_key:
            WX_API_KEY = args.watsonx_key
    if args.watsonx_project:
        WX_PROJECT = args.watsonx_project
    WX_REGION = args.watsonx_region
    if not WX_API_KEY or not WX_PROJECT:
        print("WatsonX needs --watsonx-key and --watsonx-project")
        exit(1)

    print(f"Agent   : {args.name}")
    print(f"Model   : {MODEL}")
    print(f"Provider: {PROVIDER}")

    play(args.game, args.name, num_players=args.num_players)