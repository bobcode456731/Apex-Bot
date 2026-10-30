"""Environment-backed settings and centralized Discord object IDs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


CHANNEL_IDS = {
    "middleman_panel": 1539783649208049704,
    "support_panel": 1539785643398533120,
    "rankup_panel": 1552317076549533827,
    "ticket_logs": 1552316695744610365,
    "moderation_logs": 1552317097277657129,
    "role_logs": 1552317055217176616,
    "middleman_terms": 1552292603947520042,
    "support_terms": 1552305124691415171,
    "giveaways": 1539328333807100085,
    "suggestions": 1565009040818573412,
    "welcome": 1539833608544714822,
    "audit_logs": 1552317322394468393,
}

ROLE_IDS = {
    "owner": 1539256548684202034,
    "head_owner": 1552668910665597089,
    "server_founder": 1552341106694299699,
    "president": 1552341768492814366,
    "director_ops": 1552341937271611443,
    "operations_lead": 1552342597484285952,
    "head_security": 1552342129542696991,
    "bots": 1552341466179964978,
    "community_manager": 1552343258892472432,
    "administrator": 1552343707074699386,
    "moderator": 1552344063003467797,
    "lead_designer": 1552343120551616702,
    "head_middleman": 1552345162083078287,
    "expert_middleman": 1552344749816414298,
    "middleman": 1552358685336928356,
    "vip": 1543618690547843104,
    "trusted_trader": 1552471219368894474,
    "level_2_trader": 1552345662841159710,
    "level_1_trader": 1552345934203977728,
}

# Command checks resolve these role IDs in the live guild and compare Discord's
# actual role positions. No rank order is inferred from role IDs or role names.
ROLE_THRESHOLD_KEYS = frozenset({
    "moderator", "lead_designer", "administrator", "director_ops", "operations_lead",
    "middleman", "head_owner", "server_founder",
})

PRIVILEGED_ROLE_KEYS = {
    "owner", "head_owner", "server_founder", "president", "director_ops",
    "operations_lead", "head_security", "administrator", "moderator",
    "lead_designer", "head_middleman", "expert_middleman", "middleman",
    "bots",
}


@dataclass(frozen=True)
class Settings:
    token: str
    database_path: Path
    prefix: str = "!"
    log_retention_days: int = 180
    default_welcome_channel_id: int = CHANNEL_IDS["welcome"]
    command_log_channel_id: int = CHANNEL_IDS["audit_logs"]

    @classmethod
    def from_env(cls) -> "Settings":
        """Load process environment (an optional dotenv file is loaded by the entrypoint)."""
        prefix = os.getenv("COMMAND_PREFIX", "!")
        if prefix != "!":
            raise ValueError("This bot only supports the required ! prefix.")
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if token.upper() in {"YOUR_DISCORD_BOT_TOKEN_HERE", "YOUR_TOKEN_HERE", "CHANGEME"}:
            token = ""
        database_value = os.getenv("DATABASE_PATH", "data/apexbot.sqlite3").strip()
        if not database_value or not Path(database_value).name:
            raise ValueError("DATABASE_PATH must name a persistent SQLite database file.")
        return cls(
            token=token,
            database_path=Path(database_value),
            prefix=prefix,
            log_retention_days=max(1, int(os.getenv("LOG_RETENTION_DAYS", "180"))),
        )
