"""Run the Apex bot. Secrets are loaded only from the process environment or .env."""

from __future__ import annotations

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from apexbot.bot import run
from apexbot.config import Settings

if __name__ == "__main__":
    run(Settings.from_env())
