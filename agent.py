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


def fetch_game_rules(game_id: str) -> str:
    """Fetch the current rules text for a game from the arena server."""
    try:
        res = requests.get(f"{ARENA_URL}/api/games/{game_id}/rules", timeout=5)
        res.raise_for_status()
        return res.text.strip()
    except Exception as e:
        print(f"Could not fetch rules for {game_id}: {e}", flush=True)
        return ""


def build_system_prompt(game_id: str, agent_id: str, opponent_id: str, rules: str) -> str:
    rules_text = rules[:600] if rules else "No game rules were available from the server."
    
    # Game-specific action examples
    action_examples = {
        "dictator": 'allocate_split: {"action_type":"allocate_split","payload":{"allocator_share":70,"recipient_share":30}}',
        "ultimatum": f'submit_offer: {{"action_type":"submit_offer","payload":{{"shares":{{"{agent_id}":55,"{opponent_id}":45}}}}}}',
        "bilateral-trade": 'propose: {"action_type":"propose","payload":{"price":60}}',
        "first-price-auction": 'submit_bid: {"action_type":"submit_bid","payload":{"bid":45}}',
        "all-pay-auction": 'submit_bid: {"action_type":"submit_bid","payload":{"bid":45}}',
        "public-project": 'report_value: {"action_type":"report_value","payload":{"report":75}}',
        "provision-point": 'submit_commitment: {"action_type":"submit_commitment","payload":{"amount":30}}',
    }
    example = action_examples.get(game_id, 'pass: {"action_type":"pass","payload":{}}')
    
    return f"""You are agent "{agent_id}" playing {game_id} against "{opponent_id}".

RULES:
{rules_text}

Always use exact agent IDs: yours="{agent_id}", opponent="{opponent_id}".
For {game_id}, use this action format: {example}

Respond with ONLY valid JSON:
{{"action": {{"action_type": "...", "payload": {{}}}}, "message": "optional short message"}}"""


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
    rules         = fetch_game_rules(game_id)

    user_prompt = f"""Your agent ID: {agent_id}
Opponent ID: {opponent_id}
Allowed actions: {allowed_types}

Game state:
{json.dumps(game_state, indent=2)}

Recent conversation:
{convo}

Choose your action now. Output ONLY valid JSON."""

    system = build_system_prompt(game_id, agent_id, opponent_id, rules)

    try:
        raw    = call_model(system, user_prompt)
        print(f"[{agent_id}] Raw: {raw[:300]}", flush=True)
        parsed = parse_json(raw)
        print(f"[{agent_id}] Parsed: {json.dumps(parsed)[:200]}", flush=True)

        action  = parsed.get("action", {"action_type": "pass", "payload": {}})
        msg_txt = parsed.get("message", "")

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

        # Validate allocate_split has numeric shares that sum to total
        if action.get("action_type") == "allocate_split":
            payload = action.get("payload", {})
            allocator_share = payload.get("allocator_share")
            recipient_share = payload.get("recipient_share")
            pie_total = game_state.get("pie", 100)
            if allocator_share is None or recipient_share is None:
                raise ValueError(f"Invalid allocate_split payload: {payload}")
            if not isinstance(allocator_share, (int, float)) or not isinstance(recipient_share, (int, float)):
                raise ValueError(f"allocate_split shares must be numeric: {payload}")
            if allocator_share + recipient_share != pie_total:
                raise ValueError(f"allocate_split shares must sum to {pie_total}: got {allocator_share + recipient_share}")

        messages_out = []
        if msg_txt:
            messages_out.append({"scope": "public", "content": str(msg_txt)[:2000], "to_agent_ids": []})

        print(f"[{agent_id}] Action: {action['action_type']} {action.get('payload')}", flush=True)
        return JSONResponse({"action": action, "messages": messages_out})

    except Exception as e:
        print(f"[{agent_id}] ERROR: {e} — fallback", flush=True)
        return fallback(agent_id, opponent_id, game_id, game_state, allowed_types, total)


# ── Rule-based fallback ───────────────────────────────────────────────────────

def fallback(agent_id, opponent_id, game_id, game_state, allowed_types, total):
    print(f"[{agent_id}] Using rule-based fallback", flush=True)

    # Dictator game: allocate split
    if "allocate_split" in allowed_types:
        pie_total = game_state.get("pie", 100)
        allocator_share = round(pie_total * 0.55)
        recipient_share = pie_total - allocator_share
        return JSONResponse({
            "action": {
                "action_type": "allocate_split",
                "payload": {"allocator_share": allocator_share, "recipient_share": recipient_share},
            },
            "messages": [],
        })

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