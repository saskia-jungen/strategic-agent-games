
"""
Multi-Model Agent for local Agent Arena.
Supports any OpenRouter model.

Examples:
  python3 agent_local.py --port 5001 --name "Claude" --model "anthropic/claude-sonnet-4-5"
  python3 agent_local.py --port 5002 --name "GPT4o"  --model "openai/gpt-4o"
  python3 agent_local.py --port 5003 --name "Llama"  --model "meta-llama/llama-3.1-70b-instruct"
  python3 agent_local.py --port 5004 --name "Granite" --model "ibm-granite/granite-4.0-h-micro"

"""

import argparse, json, os, re
import requests
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
ARENA_URL = "http://localhost:8888"
MODEL = "anthropic/claude-sonnet-4-5" # default, can be overriden by --model


# ── LLM call ──────────────────────────────────────────────────────────────────

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
            "max_tokens": 400,
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


def parse_json(raw: str) -> dict:
    raw = re.sub(r"```json|```", "", raw).strip()
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON found in: {raw[:200]}")
    candidate = raw[start:end]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        fixed = re.sub(r"'([^']*)'", r'"\1"', candidate)
        return json.loads(fixed)


# ── Game-specific system prompts ──────────────────────────────────────────────

def build_system_prompt(game_id: str, agent_id: str, opponent_id: str) -> str:
    prompts = {
        "ultimatum": f"""You are playing the Ultimatum Game. Split a total amount with your opponent.
CRITICAL: Use EXACT agent IDs — yours: "{agent_id}", opponent: "{opponent_id}".

Actions:
- submit_offer: {{"action_type":"submit_offer","payload":{{"shares":{{"{agent_id}":X,"{opponent_id}":Y}}}}}}  (X+Y = total)
- accept: accept current offer
- reject: reject current offer
- pass: skip turn

Strategy: propose a split favourable to you but that opponent will accept. Reject unfair offers.""",

        "all-pay-auction": f"""You are playing an All-Pay Auction.
Rules: highest bid wins the prize, but EVERYONE pays their own bid regardless.
- Winner utility = valuation - bid. Loser utility = -bid.
- Bid strategically below your valuation.

Action: {{"action_type":"submit_bid","payload":{{"bid":45}}}}
NEVER use "shares". Only {{"bid": <number>}}.""",

        "first-price-auction": f"""You are playing a First-Price Sealed-Bid Auction.
Rules: highest bid wins and pays their bid. Losers pay nothing.
- Winner utility = valuation - bid. Loser utility = 0.
- Shade your bid below valuation to earn profit.

Action: {{"action_type":"submit_bid","payload":{{"bid":45}}}}""",

        "bilateral-trade": f"""You are playing Bilateral Trade. Negotiate a price.
Check your role (buyer/seller) in game_state.

Actions:
- propose: {{"action_type":"propose","payload":{{"price":60}}}}
- accept_price: accept current proposal
- reject_and_exit: walk away

Strategy: buyer → push price down. Seller → push price up.""",

        "provision-point": f"""You are playing Provision Point. Commit funds toward a shared public good.
If total commitments meet the threshold, everyone benefits.

Actions:
- submit_commitment: {{"action_type":"submit_commitment","payload":{{"amount":30}}}}
- update_commitment: {{"action_type":"update_commitment","payload":{{"new_amount":40}}}}
- pass: skip""",

        "hold-up": f"""You are playing the Hold-Up game. TWO phases:

PHASE 1 — invest:
- Choose how much to invest. Higher investment → bigger surplus to split later.
- You pay investment_cost per unit (deducted from final payoff).
- Payload: {{"action_type":"invest","payload":{{"amount":20}}}}

PHASE 2 — bargain:
- Split the surplus. Shares MUST sum exactly to surplus.
- make_offer: {{"action_type":"make_offer","payload":{{"split":{{"{agent_id}":X,"{opponent_id}":Y}}}}}}
- accept: accept current offer
- reject: reject current offer

Strategy:
1. If opponent rejects, offer them MORE next time.
2. Accept if your share >= 40% of surplus.
3. Deadlock = both get 0. Agreement always beats deadlock.
4. Check current_phase in game_state.

Use EXACT agent IDs: yours="{agent_id}", opponent="{opponent_id}".""",

        "war-of-attrition": f"""You are playing War of Attrition as agent "{agent_id}". TWO phases:

PHASE 1 — signal:
- You CANNOT submit a quit time yet. Only message_only is useful here.
- Send ONE creative threatening message per turn. Vary your tone each time.
- Goal: make {opponent_id} believe you will outlast them so they submit a LOW t.
- Format: {{"action": {{"action_type": "message_only", "payload": {{}}}}, "message": "your threat here"}}

PHASE 2 — choose_time:
- Submit your sealed quit time t (0-max_time). Opponent cannot see it.
- Highest t wins prize. BOTH pay cost_rate x min(t values).
- Nash equilibrium = prize/cost_rate. Do NOT blindly pick max_time.
- Example: {{"action": {{"action_type": "submit_time", "payload": {{"t": 7.3}}}}, "message": ""}}
- Submit once, then pass.

ALWAYS check current_phase in game_state. NEVER use submit_time in signal phase.""",

        "dutch-auction": """You are in a Dutch auction. The user message tells you exactly what JSON to output. Copy it exactly and output nothing else.""",

        "english-auction": """You are in an English auction. The user message tells you exactly what JSON to output. Copy it exactly and output nothing else.""",

        "sequential-investment": f"""You are playing Sequential Investment as agent "{agent_id}". TWO phases:

PHASE 1 — leader_invest (LEADER acts, follower observes):
- If my_role="leader": you move first. The follower SEES your investment before deciding.
- Use this to your advantage: signal a high investment under COMPLEMENTS to pull follower up.
- Under SUBSTITUTES: invest low — follower will top up, so you save cost.
- You MAY send message_only first to signal intent, then invest.
- Payload: {{"action_type":"invest","payload":{{"amount":X}}}}
- If my_role="follower" during this phase: you cannot invest yet. Send message_only or wait.

PHASE 2 — follower_invest (FOLLOWER acts, with full info):
- You now know the leader's exact investment (leader_invested in game_state).
- COMPLEMENTS: joint_benefit = payoff_scale × leader_inv × your_inv.
  Your marginal benefit per unit = 0.5 × payoff_scale × leader_inv.
  Invest where MB = cost: optimal_amount = investment_cost / (0.5 × payoff_scale × leader_inv) — if profitable.
  If leader invested high, match or exceed them.
- SUBSTITUTES: joint_benefit = payoff_scale × (leader_inv + your_inv).
  Your MB = 0.5 × payoff_scale (constant). Free-ride if MB < investment_cost.
- Payload: {{"action_type":"invest","payload":{{"amount":X}}}}

PAYOFF = 0.5 × joint_benefit − investment_cost × your_investment.
Think carefully. Check strategy_hint in game_state for the exact numbers.""",
    }

    base = prompts.get(game_id, f"""You are agent "{agent_id}" in a strategic game.
Pass if unsure: {{"action_type":"pass","payload":{{}}}}""")

    return f"""You are agent "{agent_id}" in a strategic game.
{base}

RESPOND WITH ONLY A VALID JSON OBJECT — no explanation, no markdown, no extra text.
Format: {{"action": {{"action_type": "...", "payload": {{}}}}, "message": "optional short message"}}"""


# ── Act endpoint ──────────────────────────────────────────────────────────────

async def act(request: Request) -> JSONResponse:
    state      = await request.json()
    agent_id   = state.get("agent_id", "agent")
    game_id    = state.get("game_id", "unknown")
    game_state = state.get("game_state", {})
    messages   = state.get("messages", [])
    allowed    = state.get("allowed_actions", [])
    game_over  = state.get("game_over", False)
    is_my_turn = state.get("is_my_turn", True)

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
    convo         = "\n".join([
        f"  {m.get('sender_id', m.get('sender','?'))}: {m.get('content','')}"
        for m in messages[-6:]
    ]) or "  (none yet)"

    extra_hint = ""

    # ── English Auction: compute decision in Python, tell model to copy ───────
    if game_id == "english-auction":
        my_val   = game_state.get("my_valuation") or 80
        min_next = game_state.get("min_next_bid") or 5

        if min_next > my_val:
            action_type = "fold"
            payload_str = "{}"
            reason      = f"min_next {min_next} exceeds my valuation {my_val}. Folding."
        else:
            action_type = "raise_bid"
            payload_str = f'{{"amount":{min_next}}}'
            reason      = f"Raising to {min_next}, below my valuation {my_val}."

        extra_hint = f"""YOUR ACTION THIS TURN — copy this JSON exactly:
{{"action":{{"action_type":"{action_type}","payload":{payload_str}}},"message":"{reason}"}}
(Reason: {reason})"""

    # ── Dutch Auction: compute decision in Python, tell model to copy ─────────
    elif game_id == "dutch-auction":
        current_price  = game_state.get("current_price", 100)
        my_val         = game_state.get("my_valuation", 80)
        profit_now     = round(my_val - current_price, 4)
        rounds_left    = game_state.get("rounds_until_zero", 0)
        decrement      = game_state.get("decrement", 5)
        optimal_price  = round(my_val * 0.5, 1)

        if profit_now < 0:
            action_type = "pass"
            reason      = f"price {current_price} > valuation {my_val}, profit negative. Wait."
        elif current_price <= optimal_price:
            action_type = "accept"
            reason      = f"price {current_price} <= Nash optimal {optimal_price}, profit={profit_now}. Accept."
        elif rounds_left <= 2:
            action_type = "accept"
            reason      = f"only {rounds_left} rounds left — accept now or both get 0."
        else:
            action_type = "pass"
            reason      = f"price {current_price} above Nash optimal {optimal_price}. Wait."

        extra_hint = f"""YOUR ACTION THIS TURN — copy this JSON exactly:
{{"action":{{"action_type":"{action_type}","payload":{{}}}},"message":"{reason}"}}
(Reason: {reason})"""

    elif game_id == "hold-up":
        prev_offers = [m for m in messages if "offer" in str(m).lower()][-3:] if messages else []
        extra_hint = f"""
NEGOTIATION CONTEXT:
- Recent offers: {json.dumps(prev_offers, indent=2) if prev_offers else "(none)"}
- If opponent rejected your offer, give them MORE in the next offer.
- Move gradually toward 50/50 if stuck.
- Accept if your share covers your investment cost."""

    elif game_id == "sequential-investment":
        my_role       = game_state.get("my_role", "unknown")
        current_phase = game_state.get("current_phase", "leader_invest")
        interaction   = game_state.get("interaction", "complements")
        payoff_scale  = game_state.get("payoff_scale", 10)
        inv_cost      = game_state.get("investment_cost", 1.0)
        leader_inv    = game_state.get("leader_invested")
        i_invested    = game_state.get("i_have_invested", False)
        action_history = game_state.get("action_history", [])
        my_msgs = sum(1 for a in action_history
                      if a.get("agent_id") == agent_id and a.get("action") == "message_only")

        is_leader_phase   = (current_phase == "leader_invest")
        is_follower_phase = (current_phase == "follower_invest")
        is_leader   = (my_role == "leader")
        is_follower = (my_role == "follower")

        # Already invested or wrong phase — pass silently (bypass LLM)
        if i_invested:
            return JSONResponse({"action": {"action_type": "pass", "payload": {}}, "messages": []})
        if (is_leader and is_follower_phase) or (is_follower and is_leader_phase):
            return JSONResponse({"action": {"action_type": "pass", "payload": {}}, "messages": []})

        # Message quota not met — force message_only (bypass LLM)
        min_msgs = 2 if is_leader else 1
        if my_msgs < min_msgs:
            if is_leader:
                msgs = [
                    f"This is a {interaction} game and I move first. I'm deciding my investment amount carefully.",
                    f"Under {interaction}, our payoffs depend on both our choices. I'll invest based on what maximises joint benefit.",
                ]
                msg = msgs[my_msgs % len(msgs)]
            else:
                msg = f"Leader invested {leader_inv}. I can see their commitment — now deciding my best response."
            return JSONResponse({
                "action":   {"action_type": "message_only", "payload": {}},
                "messages": [{"scope": "public", "content": msg, "to_agent_ids": []}],
            })

        # Message quota met — let LLM pick the investment amount
        import random as _rnd3
        _noise = round(0.7 + _rnd3.random() * 0.9, 2)  # 0.7–1.6 multiplier, different each game
        if is_leader:
            if interaction == "complements":
                base_sug   = round(2 * inv_cost / payoff_scale + 0.5, 2)
                suggestion = round(base_sug * _noise, 2)
                extra_hint = f"""LEADER — time to invest. You have signalled your intent.
interaction=complements: joint_benefit = {payoff_scale} × your_inv × follower_inv
Follower will see your investment and best-respond.
Strategically invest enough so follower's MB ({payoff_scale}×0.5×your_inv) > cost ({inv_cost}).
Threshold: your_inv > {round(2*inv_cost/payoff_scale, 2)}.
Starting point to consider: {suggestion} — but reason about the game and choose your own amount.
YOUR JSON: {{"action":{{"action_type":"invest","payload":{{"amount":{suggestion}}}}},"message":"Investing {suggestion} as leader"}}
(You may change {suggestion} to any amount you think is strategically better.)"""
            else:
                mb         = round(0.5 * payoff_scale, 2)
                base_sug   = round((mb - inv_cost) * 2, 2) if mb > inv_cost else 0.0
                suggestion = round(base_sug * _noise, 2)
                extra_hint = f"""LEADER — time to invest.
interaction=substitutes: joint_benefit = {payoff_scale} × (your_inv + follower_inv)
Your MB per unit = {mb}. Cost per unit = {inv_cost}.
{"Follower will free-ride — invest modestly." if mb > inv_cost else "MB < cost — invest 0."}
Starting point: {suggestion}.
YOUR JSON: {{"action":{{"action_type":"invest","payload":{{"amount":{suggestion}}}}},"message":"Investing {suggestion}"}}
(Adjust {suggestion} based on your strategy.)"""
        else:  # follower
            if interaction == "complements" and leader_inv is not None:
                mb         = round(0.5 * payoff_scale * leader_inv, 4)
                base_sug   = round(leader_inv * 1.5, 2) if mb > inv_cost else 0.0
                suggestion = round(base_sug * _noise, 2)
                extra_hint = f"""FOLLOWER — leader invested {leader_inv}. Time to respond.
interaction=complements: joint_benefit = {payoff_scale} × {leader_inv} × your_inv
Your MB per unit = {mb}. Cost = {inv_cost}.
{"MB > cost — invest high. Profit grows linearly with your investment." if mb > inv_cost else "MB < cost — invest 0, you would lose money."}
Starting point based on leader's choice: {suggestion}.
YOUR JSON: {{"action":{{"action_type":"invest","payload":{{"amount":{suggestion}}}}},"message":"Responding with {suggestion}"}}
(You may adjust {suggestion} — higher investment means more profit if MB>cost.)"""
            else:
                mb         = round(0.5 * payoff_scale, 2)
                base_sug   = round((mb - inv_cost) * 3, 2) if mb > inv_cost else 0.0
                suggestion = round(base_sug * _noise, 2)
                extra_hint = f"""FOLLOWER — leader invested {leader_inv}.
interaction=substitutes: your MB = {mb}, cost = {inv_cost}.
{"Free-ride opportunity — invest 0 or very small." if mb <= inv_cost else f"MB={mb} > cost — invest for profit."}
Starting point: {suggestion}.
YOUR JSON: {{"action":{{"action_type":"invest","payload":{{"amount":{suggestion}}}}},"message":"Investing {suggestion}"}}"""

    elif game_id == "war-of-attrition":
        prize         = game_state.get("prize", 10)
        cost_rate     = game_state.get("cost_rate", 1)
        max_time      = game_state.get("max_time", 50)
        submitted     = game_state.get("submitted_agents", [])
        current_phase = game_state.get("current_phase", "signal")
        equilibrium   = round(prize / cost_rate, 1)
        if current_phase == "signal":
            phase_advice = (
                "You are in SIGNAL phase. DO NOT submit_time — it will be rejected. "
                "Use message_only. Put your bluff text in the top-level 'message' field."
            )
        else:
            phase_advice = (
                f"You are in CHOOSE_TIME phase. Submit your t now. "
                f"Nash equilibrium = {equilibrium}. max_time={max_time}. Do not blindly pick max."
            )
        extra_hint = f"""
WAR OF ATTRITION:
- current_phase={current_phase}
- prize={prize}, cost_rate={cost_rate}, max_time={max_time}, equilibrium_t={equilibrium}
- {phase_advice}
- Submitted so far: {submitted}"""

    user_prompt = f"""Your agent ID: {agent_id}
Opponent ID: {opponent_id}
Allowed actions: {allowed_types}

Game state:
{json.dumps(game_state, indent=2)}
{extra_hint}

Recent conversation:
{convo}

Choose your action. Output ONLY valid JSON."""

    system = build_system_prompt(game_id, agent_id, opponent_id)

    try:
        raw    = call_model(system, user_prompt)
        print(f"[{agent_id}] Raw: {raw[:300]}", flush=True)
        parsed = parse_json(raw)
        print(f"[{agent_id}] Parsed: {json.dumps(parsed)[:200]}", flush=True)

        action      = parsed.get("action", {"action_type": "pass", "payload": {}})

        # Guard: model sometimes returns null for action
        if not action or not isinstance(action, dict):
            return fallback(agent_id, opponent_id, game_id, game_state, allowed_types, total)

        raw_payload = action.get("payload") or {}

        # Normalise payload — some small models return a string instead of {}
        if isinstance(raw_payload, str):
            payload_str = raw_payload
            action["payload"] = {}
            raw_payload = {}
            if not parsed.get("message"):
                parsed["message"] = payload_str

        msg_txt = (
            parsed.get("message")
            or (raw_payload.get("message") if isinstance(raw_payload, dict) else "")
            or ""
        )
        if isinstance(action.get("payload"), dict) and "message" in action["payload"]:
            action["payload"].pop("message")

        # War-of-attrition guard
        if game_id == "war-of-attrition" and action.get("action_type") not in (
            "submit_time", "pass", "message_only"
        ):
            print(f"[{agent_id}] Wrong action for war-of-attrition — fallback", flush=True)
            return fallback(agent_id, opponent_id, game_id, game_state, allowed_types, total)

        # Dutch auction guard
        if game_id == "dutch-auction" and action.get("action_type") not in (
            "accept", "pass", "message_only"
        ):
            print(f"[{agent_id}] Wrong action for dutch-auction — fallback", flush=True)
            return fallback(agent_id, opponent_id, game_id, game_state, allowed_types, total)

        # English auction guard
        if game_id == "english-auction" and action.get("action_type") not in (
            "raise_bid", "fold", "pass", "message_only"
        ):
            print(f"[{agent_id}] Wrong action for english-auction — fallback", flush=True)
            return fallback(agent_id, opponent_id, game_id, game_state, allowed_types, total)

        # Coerce string share values to float
        if action.get("action_type") == "submit_offer":
            shares = action.get("payload", {}).get("shares", {})
            shares = {k: float(v) for k, v in shares.items()}
            action["payload"]["shares"] = {
                (opponent_id if k not in (agent_id, opponent_id) else k): v
                for k, v in shares.items()
            }

        # Fix hold-up split keys
        if action.get("action_type") == "make_offer":
            split = action.get("payload", {}).get("split", {})
            action["payload"]["split"] = {
                (opponent_id if k not in (agent_id, opponent_id) else k): v
                for k, v in split.items()
            }

        # Validate raise_bid amount
        if action.get("action_type") == "raise_bid":
            amount = action.get("payload", {}).get("amount")
            if amount is None or not isinstance(amount, (int, float)):
                raise ValueError(f"raise_bid requires numeric amount, got: {action.get('payload')}")

        # Validate submit_bid
        if action.get("action_type") == "submit_bid":
            bid = action.get("payload", {}).get("bid")
            if bid is None or not isinstance(bid, (int, float)):
                raise ValueError(f"Invalid bid: {action.get('payload')}")

        # Validate invest
        if action.get("action_type") == "invest":
            if action.get("payload", {}).get("amount") is None:
                raise ValueError("invest requires amount")

        # Validate submit_time
        if action.get("action_type") == "submit_time":
            t = action.get("payload", {}).get("t")
            if t is None or not isinstance(t, (int, float)):
                raise ValueError(f"submit_time requires numeric t, got: {action.get('payload')}")
            max_time = game_state.get("max_time", 10)
            if t < 0 or t > max_time:
                raise ValueError(f"t={t} out of range [0, {max_time}]")

        messages_out = []
        if msg_txt:
            messages_out.append({"scope": "public", "content": str(msg_txt)[:200], "to_agent_ids": []})

        print(f"[{agent_id}] Action: {action['action_type']} {action.get('payload')}", flush=True)
        return JSONResponse({"action": action, "messages": messages_out})

    except Exception as e:
        print(f"[{agent_id}] ERROR: {e} — using fallback", flush=True)
        return fallback(agent_id, opponent_id, game_id, game_state, allowed_types, total)


# ── Rule-based fallback ───────────────────────────────────────────────────────

def fallback(agent_id, opponent_id, game_id, game_state, allowed_types, total):
    print(f"[{agent_id}] Rule-based fallback", flush=True)

    # English Auction — bid up to valuation, fold above it. No i_am_winning needed.
    if game_id == "english-auction":
        my_val   = game_state.get("my_valuation") or 80
        min_next = game_state.get("min_next_bid") or 5
        if min_next > my_val and "fold" in allowed_types:
            return JSONResponse({
                "action": {"action_type": "fold", "payload": {}},
                "messages": [{"scope": "public", "content": f"Folding — {min_next} exceeds valuation {my_val}", "to_agent_ids": []}],
            })
        if "raise_bid" in allowed_types:
            return JSONResponse({
                "action": {"action_type": "raise_bid", "payload": {"amount": min_next}},
                "messages": [{"scope": "public", "content": f"Raising to {min_next}", "to_agent_ids": []}],
            })
        return JSONResponse({"action": {"action_type": "fold", "payload": {}}, "messages": []})

    # Dutch Auction
    if game_id == "dutch-auction":
        current_price = game_state.get("current_price", 100)
        my_val        = game_state.get("my_valuation", 80)
        profit        = my_val - current_price
        rounds_left   = game_state.get("rounds_until_zero", 0)
        if profit >= 0 or rounds_left <= 1:
            return JSONResponse({
                "action": {"action_type": "accept", "payload": {}},
                "messages": [{"scope": "public", "content": f"Accepting at {current_price}", "to_agent_ids": []}],
            })
        return JSONResponse({"action": {"action_type": "pass", "payload": {}}, "messages": []})

    # War of Attrition
    if game_id == "war-of-attrition":
        import random as _rnd
        current_phase = game_state.get("current_phase", "signal")
        if current_phase == "signal":
            bluffs = [
                "I have already committed to the maximum. Walk away while it is cheap.",
                "Every round costs us both. I am comfortable with that. Are you?",
                "I have outlasted stronger opponents. This ends on my terms.",
                "You are already hesitating. I have not even started.",
                "The longer this runs, the deeper the hole you are digging.",
                "Save yourself the cost. I am not moving.",
                "You think I am bluffing? That is exactly what I want you to think.",
            ]
            return JSONResponse({
                "action": {"action_type": "message_only", "payload": {}},
                "messages": [{"scope": "public", "content": _rnd.choice(bluffs), "to_agent_ids": []}],
            })
        if "submit_time" in allowed_types and not game_state.get("i_have_submitted"):
            prize     = game_state.get("prize", 10)
            cost_rate = game_state.get("cost_rate", 1)
            max_time  = game_state.get("max_time", 50)
            import random
            t = round(min((prize / cost_rate) * (0.8 + random.random() * 0.4), max_time), 2)
            return JSONResponse({
                "action": {"action_type": "submit_time", "payload": {"t": t}},
                "messages": [],
            })
        return JSONResponse({"action": {"action_type": "pass", "payload": {}}, "messages": []})

    # Sequential Investment
    if game_id == "sequential-investment":
        my_role       = game_state.get("my_role", "unknown")
        current_phase = game_state.get("current_phase", "leader_invest")
        interaction   = game_state.get("interaction", "complements")
        payoff_scale  = game_state.get("payoff_scale", 10)
        inv_cost      = game_state.get("investment_cost", 1.0)
        leader_inv    = game_state.get("leader_invested")
        i_invested    = game_state.get("i_have_invested", False)
        action_history = game_state.get("action_history", [])
        my_msgs = sum(1 for a in action_history
                      if a.get("agent_id") == agent_id and a.get("action") == "message_only")

        if i_invested or "invest" not in allowed_types:
            return JSONResponse({"action": {"action_type": "pass", "payload": {}}, "messages": []})

        # Force messaging before investing
        min_msgs = 2 if my_role == "leader" else 1
        if my_msgs < min_msgs:
            if my_role == "leader":
                msg = f"This is a {interaction} game. I plan to invest strategically to maximise joint benefit."
            else:
                msg = f"Leader invested {leader_inv}. Calculating my best response now."
            return JSONResponse({
                "action": {"action_type": "message_only", "payload": {}},
                "messages": [{"scope": "public", "content": msg, "to_agent_ids": []}],
            })

        # Now invest — add noise so repeated games differ
        import random as _rnd2
        noise = 0.8 + _rnd2.random() * 0.8  # 0.8x to 1.6x multiplier
        if my_role == "leader":
            if interaction == "complements":
                base   = round(2 * inv_cost / payoff_scale + 0.5, 2) if payoff_scale > 0 else 1.0
                amount = round(base * noise, 2)
            else:
                mb     = 0.5 * payoff_scale
                base   = round((mb - inv_cost) * 2, 2) if mb > inv_cost else 0.0
                amount = round(base * noise, 2)
        elif my_role == "follower":
            if interaction == "complements" and leader_inv is not None:
                mb     = 0.5 * payoff_scale * leader_inv
                base   = round(leader_inv * 1.5, 2) if mb > inv_cost else 0.0
                amount = round(base * noise, 2)
            else:
                mb     = 0.5 * payoff_scale
                base   = round((mb - inv_cost) * 3, 2) if mb > inv_cost else 0.0
                amount = round(base * noise, 2)
        else:
            amount = 0.0

        amount = max(0.0, round(amount, 2))
        return JSONResponse({
            "action": {"action_type": "invest", "payload": {"amount": amount}},
            "messages": [],
        })

    # Hold-up: invest
    if "invest" in allowed_types:
        if agent_id not in game_state.get("investments", {}):
            return JSONResponse({
                "action": {"action_type": "invest", "payload": {"amount": 20}},
                "messages": [],
            })

    # Hold-up: bargain
    if "make_offer" in allowed_types:
        surplus = game_state.get("surplus", 100) or 100
        mine    = round(surplus * 0.55, 2)
        return JSONResponse({
            "action": {"action_type": "make_offer", "payload": {"split": {agent_id: mine, opponent_id: round(surplus - mine, 2)}}},
            "messages": [],
        })

    if "accept" in allowed_types and game_state.get("offer"):
        offer      = game_state["offer"]
        my_share   = offer.get(agent_id, 0)
        investment = game_state.get("investments", {}).get(agent_id, 0)
        cost       = game_state.get("investment_cost", 1.0)
        if my_share >= cost * investment:
            return JSONResponse({"action": {"action_type": "accept", "payload": {}}, "messages": []})

    # Auctions
    if game_id in ("all-pay-auction", "first-price-auction"):
        if "submit_bid" in allowed_types and not game_state.get("my_bid"):
            val = game_state.get("my_valuation", 50)
            bid = round(val * 0.65)
            return JSONResponse({
                "action": {"action_type": "submit_bid", "payload": {"bid": bid}},
                "messages": [],
            })

    # Ultimatum
    if "submit_offer" in allowed_types:
        mine = round(total * 0.55)
        return JSONResponse({
            "action": {"action_type": "submit_offer", "payload": {"shares": {agent_id: mine, opponent_id: total - mine}}},
            "messages": [],
        })

    if "accept" in allowed_types and game_state.get("current_offer"):
        my_share    = game_state["current_offer"].get(agent_id, 0)
        reservation = game_state.get("my_reservation_value", 0)
        if my_share >= reservation:
            return JSONResponse({"action": {"action_type": "accept", "payload": {}}, "messages": []})

    if "propose" in allowed_types:
        return JSONResponse({"action": {"action_type": "propose", "payload": {"price": 55}}, "messages": []})

    if "submit_commitment" in allowed_types:
        return JSONResponse({"action": {"action_type": "submit_commitment", "payload": {"amount": 30}}, "messages": []})

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
    parser = argparse.ArgumentParser(description="Multi-model local arena agent")
    parser.add_argument("--port",  type=int, default=5001)
    parser.add_argument("--name",  type=str, default="Agent")
    parser.add_argument("--model", type=str, default="anthropic/claude-sonnet-4-5")
    parser.add_argument("--no-register", action="store_true")
    args = parser.parse_args()

    MODEL = args.model

    if "paste-key" in OPENROUTER_API_KEY:
        print("Set your key: export OPENROUTER_API_KEY=sk-or-...")

    print(f"Agent : {args.name}")
    print(f"Model : {MODEL}")
    print(f"Port  : {args.port}")

    if not args.no_register:
        register(args.name, args.port)

    app = Starlette(routes=[Route("/act", act, methods=["POST"])])
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")