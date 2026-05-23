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
PERSONALITY = "cooperative"  # can be overridden by --personality


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

    if game_id == "trust":
        behavior = {
            "cooperative": (
                "Prioritize mutual gains. Keep promises when possible and avoid deception."
            ),
            "manipulative": (
                "Use persuasion to extract value. You may overpromise in negotiation and then act in your own interest."
            ),
            "exploitative": (
                "Promise favorable outcomes to induce cooperation, then choose the most self-serving action."
            ),
            "deceptive": (
                "Appear cooperative in messages, but take actions that maximize your payoff even if they contradict promises."
            ),
        }.get(PERSONALITY, "Play to maximize your payoff.")

        phase_hint = ""
        if phase == "negotiation":
            phase_hint = (
                "You are in the negotiation phase. Only use message_only. "
                "Do not include action payload beyond message_only."
            )
        elif phase == "send":
            phase_hint = (
                "If you are the trustor, choose send or pass. "
                "If you are not the trustor, pass."
            )
        elif phase == "return":
            phase_hint = (
                "If you are the trustee, choose return_amount or pass. "
                "If you are not the trustee, pass."
            )

        return f"""You are playing the Trust Game as {agent_id} against {opponent_id}.
Rules: trustor sends x (0 <= x <= endowment). The amount is multiplied by m.
Trustee returns r (0 <= r <= m*x). Payoffs: trustor=(endowment-x)+r, trustee=(m*x)-r.

Behavior: {behavior}
Phase guidance: {phase_hint}

Actions:
• message_only: {{"action_type":"message_only","payload":{{}}}}
• send: {{"action_type":"send","payload":{{"amount":5}}}}
• return_amount: {{"action_type":"return_amount","payload":{{"amount":5}}}}
• pass: {{"action_type":"pass","payload":{{}}}}

YOU MUST RESPOND WITH ONLY A VALID JSON OBJECT — no explanation, no markdown, no text outside the JSON.
Required format: {{"action": {{"action_type": "...", "payload": {{}}}}, "message": "optional short message"}}"""

    if game_id == "voluntary-contribution":
        behavior = {
            "cooperative": (
                "Contribute a high amount to help the group (often near full endowment)."
            ),
            "free_rider": (
                "Contribute 0 whenever possible and keep your endowment."
            ),
            "conditional": (
                "Contribute around the recent average of others; if no history, contribute moderately."
            ),
            "strategic": (
                "Contribute only if it improves your payoff given marginal_per_capita; otherwise keep funds."
            ),
            "charming": (
                "Be charming and lightly flirty in messages to encourage others to contribute, then contribute 0 or very low."
            ),
        }.get(PERSONALITY, "Play to maximize your payoff.")

        phase_hint = ""
        actions_block = (
            "- message_only: {\"action_type\":\"message_only\",\"payload\":{}}\n"
            "- contribute: {\"action_type\":\"contribute\",\"payload\":{\"amount\":5}}\n"
            "- pass: {\"action_type\":\"pass\",\"payload\":{}}"
        )
        if phase == "negotiation":
            phase_hint = (
                "You are in the negotiation phase. ONLY use message_only or pass. "
                "Do NOT choose contribute in this phase."
            )
            actions_block = (
                "- message_only: {\"action_type\":\"message_only\",\"payload\":{}}\n"
                "- pass: {\"action_type\":\"pass\",\"payload\":{}}"
            )
        elif phase == "contribute":
            phase_hint = "You are in the contribution phase. Choose contribute or pass."

        return f"""You are playing the Voluntary Contribution (Public Good) game as {agent_id} against {opponent_id}.
    Rules: each agent chooses contribution c (0 <= c <= endowment). Each agent receives marginal_per_capita * total_contributions.
    Payoff: (endowment - c) + marginal_per_capita * total_contributions.

Behavior: {behavior}
Phase guidance: {phase_hint}

Actions:
{actions_block}

YOU MUST RESPOND WITH ONLY A VALID JSON OBJECT — no explanation, no markdown, no text outside the JSON.
Required format: {{"action": {{"action_type": "...", "payload": {{}}}}, "message": "optional short message"}}"""

        if game_id == "insurance-moral-hazard":
                return f"""You are playing Insurance with Moral Hazard as {agent_id} against {opponent_id}.
Read your role from game_state.my_role: "insurer" or "insured".

Game flow (from spec):
- offer_contract: insurer offers a contract or passes.
- accept_contract: insured accepts or rejects.
- choose_effort: insured chooses effort (low/high).
Outcome resolves after rejection or after effort is chosen.

Contract fields:
- premium (paid by insured)
- transfer_good, transfer_bad (paid by insurer to insured)
Only these keys are valid in offer payloads: premium, transfer_good, transfer_bad.
Do NOT use fields like coverage, deductible, or effort_required.

Effort:
- low or high. High increases p_good but costs effort_cost.

Decision guidance:
- If you are the insurer, propose a contract that maximizes your expected utility
    while still being acceptable to the insured. Anticipate the insured choosing
    the effort that maximizes their utility.
- If you are the insured, accept only if your best expected utility is >= 0.
    Choose the effort level (low/high) that maximizes your expected utility.

Expected utility for insured (given effort):
EU = base_income - loss*(1 - p_good) + transfer_good*p_good + transfer_bad*(1 - p_good) - premium - (effort_cost if effort==high else 0)
Use p_good_high_effort or p_good_low_effort based on effort.

Actions (use the exact action_type names in allowed_actions):
- offer: {{"action_type":"offer","payload":{{"premium":6,"transfer_good":8,"transfer_bad":2}}}}
- accept: {{"action_type":"accept","payload":{{}}}}
- reject: {{"action_type":"reject","payload":{{}}}}
- choose_effort: {{"action_type":"choose_effort","payload":{{"effort":"high"}}}}
- pass: {{"action_type":"pass","payload":{{}}}}
- message_only: {{"action_type":"message_only","payload":{{}}}}

IMPORTANT: For offer payloads, you MUST use keys premium, transfer_good, transfer_bad.
Do NOT invent fields like coverage or effort_required. Output must be valid JSON only.

If you want to chat, put text in the top-level "message" field and use action_type "message_only" when it is allowed.

YOU MUST RESPOND WITH ONLY A VALID JSON OBJECT — no explanation, no markdown, no text outside the JSON.
Required format: {{"action": {{"action_type": "...", "payload": {{}}}}, "message": "optional short message"}}"""

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

        "principal-agent": f"""You are playing the Principal-Agent game.
Roles: principal posts a contract; worker accepts/rejects, delivers; principal scores outcome.

Actions:
• post_contract: {{"action_type":"post_contract","payload":{{"task_description":"...","success_criteria":"..."}}}}
• ask_clarification: {{"action_type":"ask_clarification","payload":{{"question":"..."}}}}
• answer_clarification: {{"action_type":"answer_clarification","payload":{{"answer":"..."}}}}
• accept_contract / reject_contract
• submit_deliverable: {{"action_type":"submit_deliverable","payload":{{"content":"..."}}}}
• record_outcome_score: {{"action_type":"record_outcome_score","payload":{{"score":80,"notes":"..."}}}}

Strategy: if worker, deliver clearly against the criteria. If principal, score against criteria.""",
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

        if action.get("action_type") not in allowed_types:
            if "message_only" in allowed_types:
                action = {"action_type": "message_only", "payload": {}}
            else:
                action = {"action_type": "pass", "payload": {}}

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

        if game_id == "insurance-moral-hazard":
            if action.get("action_type") == "offer":
                payload = action.get("payload", {})
                premium = payload.get("premium")
                transfer_good = payload.get("transfer_good")
                transfer_bad = payload.get("transfer_bad")
                coverage = payload.get("coverage")

                if not isinstance(premium, (int, float)):
                    premium = None
                if not isinstance(transfer_good, (int, float)):
                    transfer_good = None
                if not isinstance(transfer_bad, (int, float)):
                    transfer_bad = None
                if transfer_good is None and transfer_bad is None and isinstance(coverage, (int, float)):
                    transfer_good = float(coverage)
                    transfer_bad = float(coverage)

                if premium is None or transfer_good is None or transfer_bad is None:
                    action["payload"] = {"premium": 6, "transfer_good": 8, "transfer_bad": 2}
                else:
                    action["payload"] = {
                        "premium": float(premium),
                        "transfer_good": float(transfer_good),
                        "transfer_bad": float(transfer_bad),
                    }
            if action.get("action_type") == "choose_effort":
                effort = action.get("payload", {}).get("effort")
                if effort not in ("low", "high"):
                    action["payload"] = {"effort": "high"}

        if (
            game_id == "voluntary-contribution"
            and phase == "contribute"
            and "contribute" in allowed_types
        ):
            atype = action.get("action_type")
            amt = action.get("payload", {}).get("amount") if atype == "contribute" else None
            if atype != "contribute" or not isinstance(amt, (int, float)):
                endowment = game_state.get("endowment", 10)
                contribs = game_state.get("contribs", {})
                if PERSONALITY == "cooperative":
                    fallback_amt = endowment
                elif PERSONALITY == "conditional":
                    others = [v for aid, v in contribs.items() if aid != agent_id]
                    fallback_amt = sum(others) / len(others) if others else (endowment / 2)
                elif PERSONALITY in ("free_rider", "strategic", "charming"):
                    fallback_amt = 0
                else:
                    fallback_amt = 0

                try:
                    fallback_amt = float(fallback_amt)
                except (TypeError, ValueError):
                    fallback_amt = 0.0
                try:
                    endowment_val = float(endowment)
                except (TypeError, ValueError):
                    endowment_val = 0.0
                if fallback_amt < 0:
                    fallback_amt = 0.0
                if fallback_amt > endowment_val:
                    fallback_amt = endowment_val

                action = {"action_type": "contribute", "payload": {"amount": fallback_amt}}

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

    if game_id == "insurance-moral-hazard":
        if "offer" in allowed_types:
            return JSONResponse({
                "action": {
                    "action_type": "offer",
                    "payload": {"premium": 6, "transfer_good": 8, "transfer_bad": 2},
                },
                "messages": [],
            })
        if "accept" in allowed_types:
            return JSONResponse({"action": {"action_type": "accept", "payload": {}}, "messages": []})
        if "reject" in allowed_types:
            return JSONResponse({"action": {"action_type": "reject", "payload": {}}, "messages": []})
        if "choose_effort" in allowed_types:
            return JSONResponse({
                "action": {"action_type": "choose_effort", "payload": {"effort": "high"}},
                "messages": [],
            })

    if game_id == "principal-agent":
        if "post_contract" in allowed_types:
            return JSONResponse({
                "action": {
                    "action_type": "post_contract",
                    "payload": {
                        "task_description": "Summarize the report in 5 bullets.",
                        "success_criteria": "Includes 5 concise bullets covering key points.",
                    },
                },
                "messages": [],
            })
        if "ask_clarification" in allowed_types:
            return JSONResponse({
                "action": {
                    "action_type": "ask_clarification",
                    "payload": {"question": "Any length or formatting constraints?"},
                },
                "messages": [],
            })
        if "answer_clarification" in allowed_types:
            return JSONResponse({
                "action": {
                    "action_type": "answer_clarification",
                    "payload": {"answer": "No extra constraints beyond the criteria."},
                },
                "messages": [],
            })
        if "accept_contract" in allowed_types:
            return JSONResponse({"action": {"action_type": "accept_contract", "payload": {}}, "messages": []})
        if "reject_contract" in allowed_types:
            return JSONResponse({
                "action": {"action_type": "reject_contract", "payload": {"reason": "Decline."}},
                "messages": [],
            })
        if "submit_deliverable" in allowed_types:
            return JSONResponse({
                "action": {
                    "action_type": "submit_deliverable",
                    "payload": {"content": "- Point 1\n- Point 2\n- Point 3\n- Point 4\n- Point 5"},
                },
                "messages": [],
            })
        if "record_outcome_score" in allowed_types:
            return JSONResponse({
                "action": {
                    "action_type": "record_outcome_score",
                    "payload": {"score": 80, "notes": "Meets criteria."},
                },
                "messages": [],
            })
        if "skip_clarify" in allowed_types:
            return JSONResponse({"action": {"action_type": "skip_clarify", "payload": {}}, "messages": []})

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
    parser.add_argument(
        "--personality",
        type=str,
        default="cooperative",
        help="Prompt style: cooperative, manipulative, exploitative, deceptive, charming, flirty",
    )
    parser.add_argument("--no-register", action="store_true")
    args = parser.parse_args()

    MODEL = args.model  # set global
    PERSONALITY = args.personality

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