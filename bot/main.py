"""Entrypoint: run the Silent bot against a live Slay the Spire 2 + STS2MCP session.

Usage:
    python -m bot.main [--max-actions N]
"""
from __future__ import annotations

import argparse

from .loop import BotLoop


def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous Silent bot for Slay the Spire 2")
    parser.add_argument("--max-actions", type=int, default=None, help="Stop after N actions (default: run forever)")
    args = parser.parse_args()

    loop = BotLoop(max_actions=args.max_actions)
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\n[bot] stopped by user")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
