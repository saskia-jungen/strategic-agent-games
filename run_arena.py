"""Start the Strategic Agent Games arena server.

Usage:
    python run_arena.py                    # starts the arena
    python run_arena.py --port 9000        # custom port
    python run_arena.py --no-browser       # don't auto-open browser
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path
from threading import Thread

# Ensure project root is on the path so arena is importable without pip install
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Strategic Agent Games Arena")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8888")))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--games", nargs="+",
        default=["ultimatum", "bilateral-trade", "first-price-auction", "provision-point", "dictator", "public-project"],
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--max-turns", type=int, default=10)
    parser.add_argument("--max-messages", type=int, default=10)
    parser.add_argument("--db", default=None, help="Path to SQLite database file (default: arena_data.db)")
    args = parser.parse_args()

    from arena.server import build_arena_app
    from arena.server.store import ArenaStore
    from arena.games.builtins import ensure_builtins_registered

    ensure_builtins_registered()

    # Build game instances
    games = {}
    game_factories = {
        "ultimatum": "arena.games.ultimatum:UltimatumGame",
        "bilateral-trade": "arena.games.bilateral_trade:BilateralTradeGame",
        "first-price-auction": "arena.games.first_price_auction:FirstPriceAuctionGame",
        "provision-point": "arena.games.provision_point:ProvisionPointGame",
        "dictator": "arena.games.dictator:DictatorGame",
        "public-project": "arena.games.public_project:PublicProjectGame"
    }
    for gid in args.games:
        if gid in game_factories:
            mod_path, cls_name = game_factories[gid].split(":")
            import importlib
            mod = importlib.import_module(mod_path)
            cls = getattr(mod, cls_name)
            games[gid] = cls()
        else:
            print(f"  Warning: unknown game '{gid}', skipping")

    db_path = args.db or os.environ.get("ARENA_DB_PATH", str(Path(__file__).resolve().parent / "arena_data.db"))
    store = ArenaStore(db_path=db_path)

    # Auto-seed if DB is empty
    if not store.get_match_history(limit=1):
        print("  Database is empty — seeding with sample matches...")
        from seed_matches import seed
        seed(db_path)
        # Reload store so in-memory state reflects seeded data
        store.close()
        store = ArenaStore(db_path=db_path)

    app = build_arena_app(
        store=store,
        games=games,
        builtin_agents={},
        default_game_id=args.games[0] if args.games else "ultimatum",
        max_turns=args.max_turns,
        max_messages=args.max_messages,
    )

    port = args.port
    print(f"\n  Strategic Agent Games")
    print(f"  Dashboard:  http://127.0.0.1:{port}")
    print(f"  API:        http://127.0.0.1:{port}/api/")
    print(f"  Games:      {list(games.keys())}")
    print(f"  Database:   {db_path}")
    print()

    if not args.no_browser:
        Thread(target=lambda: webbrowser.open(f"http://127.0.0.1:{port}"), daemon=True).start()

    uvicorn.run(app, host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
