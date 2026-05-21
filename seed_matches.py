"""Seed the arena with sample matches that include chat messages.

Usage:
    python seed_matches.py                           # seed local DB
    python seed_matches.py --db /data/arena_data.db  # seed specific DB
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arena.agents.base import Agent
from arena.experiment.runner import ExperimentConfig, ExperimentRunner
from arena.games.builtins import ensure_builtins_registered
from arena.server.store import ArenaStore
from arena.types import Action, AgentResponse, MessageIntent, MessageScope, TurnState


# ---------------------------------------------------------------------------
# Chatty agent: picks a valid action AND sends contextual messages
# ---------------------------------------------------------------------------

ULTIMATUM_MESSAGES = [
    "I think a fair split would benefit us both.",
    "Let's try to find something mutually beneficial.",
    "I'm willing to negotiate, but I need a reasonable share.",
    "How about we meet in the middle?",
    "I'd prefer a more even split if possible.",
    "I'll accept if the offer is fair enough.",
    "My reservation value is quite high, so keep that in mind.",
    "Let's not waste rounds — make me a good offer.",
    "I'm open to discussion before we commit.",
    "This seems like a reasonable proposal to me.",
    "I think we can do better than this.",
    "Deal! This works for me.",
]

TRADE_MESSAGES = [
    "I'm interested in making a deal.",
    "What price range are you thinking?",
    "I think we can find a fair price.",
    "That's a bit high for me, can you come down?",
    "Let's negotiate — I'm flexible on price.",
    "I have a strong preference for closing this deal.",
    "How about we split the difference?",
    "I need to protect my margin here.",
    "This price works for me, let's proceed.",
    "I'm willing to budge a little on my ask.",
]

AUCTION_MESSAGES = [
    "I'm feeling confident about this auction.",
    "Don't overbid — winner's curse is real!",
    "I've got a strong valuation on this item.",
    "May the best bidder win.",
    "I'm going to be strategic with my bid.",
    "Good luck everyone.",
    "I think the item is worth quite a lot.",
    "Let's keep it civil.",
    "I'm considering a moderate bid.",
    "Interesting competition we have here.",
]

PROVISION_MESSAGES = [
    "I think this public good is worth funding.",
    "We should both contribute fairly.",
    "Free-riding won't help either of us if we miss the threshold.",
    "I'm planning to commit a reasonable amount.",
    "Let's coordinate to make sure we hit the target.",
    "The threshold isn't that high — we can do this.",
    "I'll put in my fair share if you do the same.",
    "Cooperation is the best strategy here.",
    "If we both contribute enough, we both benefit.",
    "I'm going to be generous with my commitment.",
]

ALL_PAY_MESSAGES = [
    "All-pay means I'm risking my bid no matter what.",
    "I need to bid carefully, losing still costs me.",
    "Winner's curse is bad, but all-pay makes every bid costly.",
    "I'll bid aggressively if my valuation is high enough.",
    "The key here is not overbidding your valuation.",
    "Even a losing bid costs you — stay disciplined.",
    "I'm thinking about the expected loss before committing.",
    "High valuations justify high bids, but there's always risk.",
    "This is a war of attrition — choose wisely.",
    "I'll signal strength but bid strategically.",
]

HOLD_UP_MESSAGES = [
    "I'm considering investing in this project.",
    "I hope the other party will reciprocate my investment.",
    "Trust is key in this hold-up scenario.",
    "I need to be careful not to over-invest without guarantees.",
    "Let's see if we can both benefit from this.",
    "I'm worried about being held up after I invest.",
    "I'll invest more if I trust we can agree on a fair split.",
    "The more we both invest, the bigger the surplus to share.",
    "Don't underinvest — we both lose if the surplus is small.",
    "I'm signaling my investment intentions to build trust.",
]

GAME_MESSAGES = {
    "ultimatum": ULTIMATUM_MESSAGES,
    "bilateral-trade": TRADE_MESSAGES,
    "first-price-auction": AUCTION_MESSAGES,
    "provision-point": PROVISION_MESSAGES,
    "all-pay-auction": ALL_PAY_MESSAGES,
    "hold-up": HOLD_UP_MESSAGES
}


class ChattyRandomAgent(Agent):
    """Agent that picks random valid actions and sends contextual chat messages."""

    def __init__(self, agent_id: str, seed: int = 0) -> None:
        self._agent_id = agent_id
        self._rng = random.Random(seed)
        self._game_id = ""

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def on_match_start(self, match_id: str, game_id: str, agent_ids: list[str]) -> None:
        self._game_id = game_id

    def act(self, state: TurnState) -> AgentResponse:
        allowed = state.allowed_actions
        if not allowed:
            return AgentResponse(
                messages=[], action=Action(action_type="pass", payload={})
            )

        # Pick a random action
        choice = self._rng.choice(allowed)
        payload = self._build_payload(choice, state)

        # Generate 0-2 chat messages
        messages: list[MessageIntent] = []
        msg_pool = GAME_MESSAGES.get(self._game_id, ULTIMATUM_MESSAGES)
        num_msgs = self._rng.choice([0, 1, 1, 1, 2])  # bias toward 1 message
        for _ in range(num_msgs):
            messages.append(MessageIntent(
                scope=MessageScope.PUBLIC,
                content=self._rng.choice(msg_pool),
                to_agent_ids=[],
            ))

        return AgentResponse(
            messages=messages,
            action=Action(action_type=choice.action_type, payload=payload),
        )

    def _build_payload(self, action: Any, state: TurnState) -> dict[str, Any]:
        """Build a plausible payload for the given action type."""
        at = action.action_type
        gs = state.game_state

        if at in ("accept", "reject", "pass", "message_only"):
            return {}

        if at == "submit_offer":
            total = gs.get("total", 100)
            agents = gs.get("agent_ids", [state.agent_id, "other"])
            my_share = self._rng.randint(int(total * 0.3), int(total * 0.7))
            other_share = total - my_share
            if len(agents) == 2:
                return {"shares": {agents[0]: my_share, agents[1]: other_share}}
            return {"shares": {a: total // len(agents) for a in agents}}

        if at == "submit_bid":
            valuation = gs.get("my_valuation", 50)
            bid = self._rng.randint(max(1, int(valuation * 0.3)), int(valuation * 0.95))
            return {"bid": bid}

        if at == "propose_price":
            return {"price": self._rng.randint(30, 80)}

        if at == "accept_price":
            return {}

        if at in ("submit_commitment", "update_commitment"):
            rv = gs.get("my_reservation_value", 50)
            amount = self._rng.randint(max(0, rv - 20), rv + 20)
            key = "new_amount" if at == "update_commitment" else "amount"
            return {key: max(0, amount)}

        if at == "announce_project":
            return {}

        # Fallback: empty payload
        return {}


# ---------------------------------------------------------------------------
# Seed logic
# ---------------------------------------------------------------------------

AGENT_NAMES = [
    ("claude-opus", 10),
    ("claude-sonnet", 20),
    ("gpt-4o", 30),
    ("gemini-pro", 40),
    ("llama-3", 50),
    ("deepseek-r1", 60),
]

GAMES = ["ultimatum", "bilateral-trade", "first-price-auction", "provision-point", "all-pay-auction", "hold-up", "war-of-attrition", "sequential-investment"]
MATCHES_PER_PAIR = 2


def seed(db_path: str) -> None:
    ensure_builtins_registered()
    store = ArenaStore(db_path=db_path)

    existing = len(store.get_match_history(limit=9999))
    if existing > 0:
        print(f"DB already has {existing} matches. Skipping seed.")
        store.close()
        return

    agents = [ChattyRandomAgent(agent_id=name, seed=s) for name, s in AGENT_NAMES]
    total = 0

    for game_id in GAMES:
        game_factories = {
            "ultimatum": "arena.games.ultimatum:UltimatumGame",
            "bilateral-trade": "arena.games.bilateral_trade:BilateralTradeGame",
            "first-price-auction": "arena.games.first_price_auction:FirstPriceAuctionGame",
            "provision-point": "arena.games.provision_point:ProvisionPointGame",
            "all-pay-auction": "arena.games.all_pay_auction:AllPayAuctionGame",
            "hold-up": "arena.games.hold_up:HoldUpGame",
            "war-of-attrition": "arena.games.war_of_attrition:WarOfAttritionGame",
            "sequential-investment": "arena.games.sequential_investment:SequentialInvestmentGame"
        }
        mod_path, cls_name = game_factories[game_id].split(":")
        import importlib
        mod = importlib.import_module(mod_path)
        game_cls = getattr(mod, cls_name)

        pairs = [
            (agents[0], agents[1]),
            (agents[2], agents[3]),
            (agents[0], agents[4]),
            (agents[1], agents[5]),
            (agents[2], agents[5]),
            (agents[4], agents[3]),
        ]

        for a1, a2 in pairs:
            agent_ids = [a1.agent_id, a2.agent_id]
            game = game_cls.from_params({}, agent_ids)

            config = ExperimentConfig(
                game_id=game_id,
                num_matches=MATCHES_PER_PAIR,
                max_turns_per_match=20,
                max_messages_per_turn=5,
            )
            runner = ExperimentRunner(config)
            result = runner.run([a1, a2], game=game)

            for mr in result.match_results:
                log_data = mr.log.model_dump(mode="json") if mr.log else None
                store.record_match(
                    match_id=mr.match_id,
                    game_id=game_id,
                    agent_ids=mr.agent_ids,
                    outcome=mr.outcome,
                    status=mr.status,
                    num_turns=mr.num_turns,
                    duration_seconds=mr.duration_seconds,
                    log=log_data,
                )
                total += 1

        print(f"  {game_id}: seeded {len(pairs) * MATCHES_PER_PAIR} matches")

    print(f"\nTotal: {total} matches seeded into {db_path}")
    store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed arena with sample matches")
    parser.add_argument("--db", default=str(Path(__file__).resolve().parent / "arena_data.db"))
    args = parser.parse_args()
    seed(args.db)
