"""
Multi-Model Agent for local Agent Arena
Supports any OpenRouter model.

Examples:
  python agent.py --port 5001 --name "Claude" --model "anthropic/claude-sonnet-4-5"
  python agent.py --port 5002 --name "GPT4o"  --model "openai/gpt-4o"
  python agent.py --port 5003 --name "Llama"  --model "meta-llama/llama-3.1-70b-instruct"
  python agent.py --port 5004 --name "Gemini" --model "google/gemini-2.5-pro"
  python agent.py --port 5005 --name "DSR1"   --model "deepseek/deepseek-r1"

Requires: pip install starlette uvicorn requests
"""

import argparse, json, os, re
import requests
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from dotenv import load_dotenv

load_dotenv()  # Load from .env file

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not found in environment or .env file")

ARENA_URL = "http://localhost:8888"
MODEL = "anthropic/claude-sonnet-4-5"  # can be overridden by --model


# ── API call ──────────────────────────────────────────────────────────────────
# - Send a request to OpenRouter API with two prompts
# - Return the model's response text
# - Handle API errors
def call_model(system_prompt: str, user_prompt: str) -> str:
    res = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
        },
        json={
            "model": MODEL,
            "max_tokens": 2000,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
        },
        timeout=20,
    )
    data = res.json()
    if "choices" not in data:
        raise ValueError(f"API error: {data.get('error', data)}")
    return data["choices"][0]["message"]["content"]

# Extract JSON from model output
def parse_json(raw: str) -> dict:
    """Robustly extract JSON from model output."""
    raw = raw.strip()
    # Remove markdown fences, if present
    raw = re.sub(r"⁠ json| ⁠", "", raw).strip()
    # Find the outermost { ... }
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON found in: {raw[:200]}")
    candidate = raw[start:end]
    # Replace single quotes with double quotes as last resort
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Try fixing single quotes
        fixed = re.sub(r"'([^']*)'", r'"\1"', candidate)
        return json.loads(fixed)


# ── Game-specific prompts ─────────────────────────────────────────────────────
# Create specific custom instructions for each game type
def build_system_prompt(game_id: str, agent_id: str, opponent_id: str, phase: str = "default") -> str:
    # Special handling for dictator game negotiation phase
    if game_id == "dictator" and phase == "negotiation":
        return f"""You are in the NEGOTIATION PHASE of the Dictator Game.
You and your opponent ({opponent_id}) are discussing the split BEFORE the allocator decides.

Important private information:
- Your own reservation value is shown in game_state as my_reservation_value.
- This value is private. Do NOT reveal your value in messages.
- You do NOT know your opponent's reservation value.

Your role: Advocate for a favorable outcome. Persuade the allocator to give you a better share.
- If you ARE the allocator: listen to your opponent's arguments and consider them
- If you are the RECIPIENT: make your case for why a fair split benefits everyone

Actions available:
• Send messages without making any decisions yet. Use the message_only action type.

Example response:
{{"action": {{"action_type": "message_only", "payload": {{}}}}, "message": "I think a fair split would be X for me and Y for you because..."}}

You MUST respond with only a valid JSON object — no explanation, no markdown, no text outside the JSON."""

    base = {
        "ultimatum": f"""You are playing the Ultimatum Game. Split a total amount with your opponent.
CRITICAL: Use the EXACT agent IDs in shares — yours is "{agent_id}", opponent is "{opponent_id}".

Actions:
•⁠  ⁠submit_offer: {{"action_type":"submit_offer","payload":{{"shares":{{"{agent_id}":X,"{opponent_id}":Y}}}}}}  X+Y must equal total
•⁠  ⁠accept: accept current offer
•⁠  ⁠reject: reject current offer  
•⁠  ⁠pass: skip turn

Strategy: propose a split favorable to you but that opponent will accept. Reject unfair offers.""",

        "all-pay-auction": f"""You are playing an All-Pay Auction.
Rules: highest bid wins the prize, but EVERYONE pays their own bid regardless of outcome.
•⁠  ⁠Winner utility = my_valuation - bid
•⁠  ⁠Loser utility = -bid (you LOSE your bid amount)
•⁠  ⁠Optimal: bid strategically below your valuation

Action: submit_bid with a plain number: {{"action_type":"submit_bid","payload":{{"bid":45}}}}
NEVER use "shares". Only {{"bid": <number>}}.""",

        "first-price-auction": f"""You are playing a First-Price Sealed-Bid Auction.
Rules: highest bid wins and pays their bid. Losers pay nothing.
•⁠  ⁠Winner utility = my_valuation - bid. Loser utility = 0.
•⁠  ⁠Bid below your valuation to make profit. Shade your bid strategically.

Action: {{"action_type":"submit_bid","payload":{{"bid":45}}}}""",

        "bilateral-trade": f"""You are playing Bilateral Trade. You are negotiating a price.
Check your role (buyer/seller) in game_state.

Actions:
•⁠  ⁠propose: {{"action_type":"propose","payload":{{"price":60}}}}
•⁠  ⁠accept_price: accept current proposal
•⁠  ⁠reject_and_exit: walk away

Strategy: if buyer, negotiate price down. If seller, negotiate price up.""",

        "provision-point": f"""You are playing Provision Point. Commit funds to a shared public good.
If total commitments meet the threshold, everyone benefits.

Actions:
•⁠  ⁠submit_commitment: {{"action_type":"submit_commitment","payload":{{"amount":30}}}}
•⁠  ⁠update_commitment: {{"action_type":"update_commitment","payload":{{"new_amount":40}}}}
•⁠  ⁠pass: skip""",

        "public-project": f"""You are playing the Public Project Game.
Each agent has a PRIVATE TRUE VALUATION for a public project (shown as my_valuation).
You will REPORT a valuation (which may be truthful or strategic).
If sum of all reports >= project_cost, the project is BUILT.

Payoffs:
•⁠  ⁠If built: you get (my_valuation - cost_per_agent)
•⁠  ⁠If not built: you get 0

Actions:
•⁠  ⁠report_value: {{"action_type":"report_value","payload":{{"report":75}}}}  (your reported valuation)
•⁠  ⁠pass: {{"action_type":"pass","payload":{{}}}}
•⁠  ⁠message: {{"action_type":"message","payload":{{"text":"short message"}}}}

Strategy: consider reporting truthfully or strategically to influence the outcome.""",

        "dictator": f"""You are playing the Dictator Game. You (the allocator) decide how to split a pie.
Your opponent is the recipient and has no choice in the split — they must accept whatever you allocate.

    Important private information:
    - Your own reservation value is in game_state.my_reservation_value.
    - Reservation values are private: do NOT reveal your own value.
    - You do NOT know your opponent's reservation value.

    Utility objective:
    - Final utility is (your_share - your_reservation_value).
    - Try to end with utility > 0.

CRITICAL: Ensure your allocator_share + recipient_share equals the pie total.
•⁠  ⁠allocator_share is what YOU get
•⁠  ⁠recipient_share is what {opponent_id} gets

Actions:
•⁠  ⁠allocate_split: {{"action_type":"allocate_split","payload":{{"allocator_share":<your_amount>,"recipient_share":<opponent_amount>}}}}
•⁠  ⁠pass: skip this turn
•⁠  ⁠message_only: send a message without making a decision

Strategy: decide how much you want to keep vs. give to your opponent. The game ends immediately once you allocate.""",
    }.get(game_id, f"""You are playing a negotiation game as {agent_id}.
Pass if unsure: {{"action_type":"pass","payload":{{}}}}""")

    return f"""You are agent "{agent_id}" in a strategic game.
{base}

YOU MUST RESPOND WITH ONLY A VALID JSON OBJECT — no explanation, no markdown, no text outside the JSON.
Required format: {{"action": {{"action_type": "...", "payload": {{}}}}, "message": "optional short message"}}"""


# ── Act endpoint ──────────────────────────────────────────────────────────────
# asynchronous function (it can pause and wait)
async def act(request: Request) -> JSONResponse:
    state      = await request.json()
    agent_id   = state.get("agent_id", "agent")
    game_id    = state.get("game_id", "unknown")
    phase      = state.get("phase", "default")
    game_state = state.get("game_state", {})
    messages   = state.get("messages", [])
    allowed    = state.get("allowed_actions", [])
    game_over  = state.get("game_over", False)
    is_my_turn = state.get("is_my_turn", True)

    # Get opponent ID reliably from game_state.agent_ids
    all_agents  = game_state.get("agent_ids", [])
    opponent_id = next((a for a in all_agents if a != agent_id), "opponent")

    print(f"\n[{agent_id}] game={game_id} turn={is_my_turn} opponent={opponent_id}", flush=True)

    if game_over:
        outcome = state.get("outcome", {})
        print(f"[{agent_id}] GAME OVER: {outcome}", flush=True)
        return JSONResponse({"action": {"action_type": "pass", "payload": {}}, "messages": []})

    if not is_my_turn:
        return JSONResponse({"action": {"action_type": "pass", "payload": {}}, "messages": []})

    allowed_types = [a["action_type"] for a in allowed]
    total         = game_state.get("total", 100)
    convo         = "\n".join([f"  {m['sender_id']}: {m['content']}" for m in messages[-6:]]) or "  (none yet)"

    user_prompt = f"""Your agent ID: {agent_id}
Opponent ID: {opponent_id}
Allowed actions: {allowed_types}

Game state:
{json.dumps(game_state, indent=2)}

Recent conversation:
{convo}

Choose your action now. Output ONLY valid JSON."""

    system = build_system_prompt(game_id, agent_id, opponent_id, phase)

    try:
        raw    = call_model(system, user_prompt)
        print(f"[{agent_id}] Raw: {raw[:300]}", flush=True)
        parsed = parse_json(raw)
        print(f"[{agent_id}] Parsed: {json.dumps(parsed)[:200]}", flush=True)

        action  = parsed.get("action", {"action_type": "pass", "payload": {}})
        msg_txt = parsed.get("message", "")

        if game_id == "public-project":
            # Keep old prompts/results compatible with the newer public-project action schema.
            if action.get("action_type") == "message_only":
                action["action_type"] = "message"
            if action.get("action_type") == "message":
                payload = action.setdefault("payload", {})
                text = payload.get("text")
                if not isinstance(text, str) or not text.strip():
                    if msg_txt:
                        payload["text"] = str(msg_txt)[:2000]
                        msg_txt = ""
                    else:
                        payload["text"] = "message"

        # Fix any remaining "opponent" keys in shares
        if action.get("action_type") == "submit_offer":
            shares = action.get("payload", {}).get("shares", {})
            fixed  = {}
            for k, v in shares.items():
                fixed[opponent_id if k not in (agent_id, opponent_id) else k] = v
            action["payload"]["shares"] = fixed

        # Validate submit_bid has numeric bid
        if action.get("action_type") == "submit_bid":
            bid = action.get("payload", {}).get("bid")
            if bid is None or not isinstance(bid, (int, float)):
                raise ValueError(f"Invalid bid payload: {action.get('payload')}")

        messages_out = []
        if msg_txt and not (game_id == "public-project" and action.get("action_type") == "message"):
            messages_out.append({"scope": "public", "content": str(msg_txt)[:2000], "to_agent_ids": []})

        print(f"[{agent_id}] Action: {action['action_type']} {action.get('payload')}", flush=True)
        return JSONResponse({"action": action, "messages": messages_out})

    except Exception as e:
        print(f"[{agent_id}] ERROR: {e} — fallback", flush=True)
        return fallback(agent_id, opponent_id, game_id, game_state, allowed_types, total)


# ── Rule-based fallback ───────────────────────────────────────────────────────

def fallback(agent_id, opponent_id, game_id, game_state, allowed_types, total):
    print(f"[{agent_id}] Using rule-based fallback", flush=True)

    if game_id in ("all-pay-auction", "first-price-auction"):
        if "submit_bid" in allowed_types and not game_state.get("my_bid"):
            val = game_state.get("my_valuation", 50)
            bid = round(val * 0.65)
            return JSONResponse({
                "action": {"action_type": "submit_bid", "payload": {"bid": bid}},
                "messages": [],
            })

    if "submit_offer" in allowed_types:
        mine = round(total * 0.55)
        return JSONResponse({
            "action": {
                "action_type": "submit_offer",
                "payload": {"shares": {agent_id: mine, opponent_id: total - mine}},
            },
            "messages": [],
        })

    if "accept" in allowed_types and game_state.get("current_offer"):
        my_share = game_state["current_offer"].get(agent_id, 0)
        reservation = game_state.get("my_reservation_value", 0)
        if my_share >= reservation:
            return JSONResponse({"action": {"action_type": "accept", "payload": {}}, "messages": []})

    if "propose" in allowed_types:
        return JSONResponse({
            "action": {"action_type": "propose", "payload": {"price": 55}},
            "messages": [],
        })

    if "submit_commitment" in allowed_types:
        return JSONResponse({
            "action": {"action_type": "submit_commitment", "payload": {"amount": 30}},
            "messages": [],
        })

    if "report_value" in allowed_types:
        my_valuation = game_state.get("my_valuation", 50)
        return JSONResponse({
            "action": {"action_type": "report_value", "payload": {"report": my_valuation}},
            "messages": [],
        })

    return JSONResponse({"action": {"action_type": "pass", "payload": {}}, "messages": []})


# ── Register + start ──────────────────────────────────────────────────────────

def register(name: str, port: int):
    try:
        res = requests.post(f"{ARENA_URL}/api/register", json={
            "agent_id":     name.lower().replace(" ", "-"),
            "endpoint":     f"http://localhost:{port}",
            "display_name": f"{name} [{MODEL.split('/')[-1]}]",
        }, timeout=5)
        print(f"Registered: {res.json()}")
    except Exception as e:
        print(f"Could not register (is arena running?): {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-model arena agent")
    parser.add_argument("--port",  type=int, default=5001, help="Port to run on")
    parser.add_argument("--name",  type=str, default="Agent", help="Display name")
    parser.add_argument("--model", type=str, default="anthropic/claude-sonnet-4-5",
                        help="OpenRouter model string e.g. openai/gpt-4o")
    parser.add_argument("--no-register", action="store_true")
    args = parser.parse_args()

    MODEL = args.model  # set global

    #Warning if the API key is not set yet
    if "YOUR_KEY" in OPENROUTER_API_KEY:
        print("  Set your key: export OPENROUTER_API_KEY=sk-or-...")

    print(f"Agent : {args.name}")
    print(f"Model : {MODEL}")
    print(f"Port  : {args.port}")

    if not args.no_register:
        register(args.name, args.port)

    app = Starlette(routes=[Route("/act", act, methods=["POST"])])
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")