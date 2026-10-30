"""Run with installed dependencies to exercise the real discord.py setup hook."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if (ROOT / ".test-deps").is_dir():
    sys.path.insert(0, str(ROOT / ".test-deps"))

from apexbot.bot import ApexBot
from apexbot.config import Settings


async def main() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT) as folder:
        bot = ApexBot(Settings(token="test-token-never-used", database_path=Path(folder) / "setup.sqlite3"))
        try:
            await asyncio.wait_for(bot.setup_hook(), timeout=10)
            required = {"help", "warn", "panelbuilder", "embedbuilder", "giveaway", "suggestion", "audit"}
            missing = sorted(required - set(bot.all_commands))
            if missing:
                raise AssertionError(f"Required prefix commands missing: {missing}")
            if bot.tree.get_commands():
                raise AssertionError("Unexpected application/slash commands were registered")
            print(f"setup_hook passed; {len(bot.all_commands)} prefix roots registered; application commands: 0")
        finally:
            community = bot.get_cog("CommunityCog")
            if community is not None:
                community.draw_loop.cancel()
            await asyncio.wait_for(bot.close(), timeout=5)
            bot.db.close()


if __name__ == "__main__":
    asyncio.run(main())
