"""Pure validation and parsing helpers shared by commands and tests."""

from __future__ import annotations

import re
from urllib.parse import urlparse

HEX_COLOUR = re.compile(r"^#?(?:[0-9a-fA-F]{6})$")
TRIGGER = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
USER_ID = re.compile(r"^\d{15,22}$")
DURATION_PART = re.compile(r"(\d+)([smhd])", re.IGNORECASE)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|passphrase|pwd|bot\s+token|api[_ -]?key|access\s+token|refresh\s+token|"
    r"client\s+secret|secret\s+key|authorization|bearer|credential|secret|private[ _-]?key|"
    r"seed\s+phrase|wallet\s+seed|recovery\s+phrase|mnemonic|wallet\s+key)\b[\"']?\s*(?:[:=]|\bis\b)\s*(?!\[redacted\])[^\r\n]+"
)
DISCORD_TOKEN = re.compile(r"(?i)\b(?:mfa\.[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{20,})\b")
KNOWN_KEY = re.compile(r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{20,}|sk-[A-Za-z0-9_-]{20,})\b")
PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)
RESERVED_TRIGGERS = frozenset({
    "help", "warn", "warnings", "clearwarnings", "timeout", "untimeout", "kick",
    "ban", "unban", "purge", "lock", "unlock", "slowmode", "serverinfo", "botinfo",
    "userinfo", "roleinfo", "autorole", "setwelcome", "welcome-test", "nick", "role",
    "adduser", "panelbuilder", "embedbuilder", "embed", "announce", "giveaway", "welcome",
    "suggest", "suggestion", "audit", "ticket",
})


def parse_duration(value: str, *, max_seconds: int = 28 * 24 * 60 * 60) -> int:
    """Parse compact durations such as 10m or 1h30m; reject gaps and overflow."""
    source = value.strip().lower()
    if not source:
        raise ValueError("Provide a duration such as 10m, 2h, or 1d.")
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    position, seconds = 0, 0
    for match in DURATION_PART.finditer(source):
        if match.start() != position:
            raise ValueError("Duration must use only s, m, h, or d (for example 10m).")
        seconds += int(match.group(1)) * units[match.group(2).lower()]
        position = match.end()
    if position != len(source) or seconds <= 0 or seconds > max_seconds:
        raise ValueError("Duration is invalid or exceeds Discord's 28-day limit.")
    return seconds


def parse_colour(value: str) -> int:
    if not HEX_COLOUR.fullmatch(value.strip()):
        raise ValueError("Colour must be a six-digit hex value, for example #8B5CF6.")
    return int(value.strip().lstrip("#"), 16)


def validate_discord_id(value: str) -> int:
    if not USER_ID.fullmatch(value.strip()):
        raise ValueError("Discord IDs must contain 15 to 22 decimal digits.")
    numeric_id = int(value.strip())
    if not 1 <= numeric_id < 2**64:
        raise ValueError("Discord ID is outside the valid unsigned 64-bit Snowflake range.")
    return numeric_id


def validate_url(value: str, *, allow_empty: bool = True) -> str | None:
    value = value.strip()
    if not value and allow_empty:
        return None
    parsed = urlparse(value)
    if len(value) > 2048 or parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("Provide a valid http:// or https:// URL.")
    return value


def validate_button_emoji(value: str | None, guild) -> str | None:
    """Accept one Unicode emoji or a custom emoji that belongs to this guild."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        emoji = __import__("discord").PartialEmoji.from_str(value)
    except (TypeError, ValueError):
        raise ValueError("Button emoji must be one Unicode emoji or a custom emoji from this server.")
    if emoji.id is None:
        def is_base(char: str) -> bool:
            code = ord(char)
            return 0x1F000 <= code <= 0x1FAFF and not 0x1F3FB <= code <= 0x1F3FF or 0x2600 <= code <= 0x27BF or code in {0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299}

        keycap_sequence = (len(value) in {2, 3} and value[0] in "#*0123456789"
                           and value[-1] == "\u20e3" and all(char in {"\ufe0f", "\u20e3"} or char == value[0] for char in value))
        regional_pair = len(value) == 2 and all(0x1F1E6 <= ord(char) <= 0x1F1FF for char in value)
        def valid_emoji_segment(segment: str) -> bool:
            bases = [char for char in segment if is_base(char)]
            if len(bases) != 1:
                return False
            return all(is_base(char) or ord(char) in {0xFE0F} or 0x1F3FB <= ord(char) <= 0x1F3FF
                       or 0xE0020 <= ord(char) <= 0xE007F for char in segment)

        valid_zwj_sequence = "\u200d" in value and all(valid_emoji_segment(part) for part in value.split("\u200d"))
        simple_emoji = valid_emoji_segment(value)
        if not (keycap_sequence or regional_pair or valid_zwj_sequence or simple_emoji):
            raise ValueError("Button emoji must be one Unicode emoji or a custom emoji from this server.")
    elif guild is None or guild.get_emoji(emoji.id) is None:
        raise ValueError("Custom button emoji must belong to this server and still be available.")
    return value


def validate_trigger(value: str, aliases: list[str] | None = None) -> list[str]:
    names = [value.strip().lower(), *(a.strip().lower() for a in aliases or [])]
    if any(not TRIGGER.fullmatch(name) for name in names):
        raise ValueError("Triggers must start with a letter and contain only letters, digits, _ or - (max 32).")
    if len(set(names)) != len(names):
        raise ValueError("Trigger and aliases must be unique.")
    if any(name in RESERVED_TRIGGERS for name in names):
        raise ValueError("That name is reserved for a built-in command or protected trigger.")
    return names


def validate_embed(title: str, description: str, fields: list[dict], footer: str = "") -> None:
    if not isinstance(title, str) or not isinstance(description, str) or not isinstance(footer, str):
        raise ValueError("Embed title, description, and footer must be text.")
    if len(title) > 256 or len(description) > 4096 or len(footer) > 2048:
        raise ValueError("Embed title, description, or footer exceeds Discord's limit.")
    if not isinstance(fields, list):
        raise ValueError("Embed fields must be an array.")
    if len(fields) > 25:
        raise ValueError("An embed can contain at most 25 fields.")
    for field in fields:
        if not isinstance(field, dict) or "name" not in field or "value" not in field:
            raise ValueError("Each embed field must include a name and value.")
        name, value = str(field["name"]), str(field["value"])
        if not name.strip() or not value.strip():
            raise ValueError("Embed field names and values cannot be empty.")
        if len(name) > 256 or len(value) > 1024:
            raise ValueError("Embed field names are limited to 256 characters and values to 1024.")
        if "inline" in field and not isinstance(field["inline"], bool):
            raise ValueError("Embed field inline must be a boolean.")
    total = len(title) + len(description) + len(footer) + sum(
        len(str(f.get("name", ""))) + len(str(f.get("value", ""))) for f in fields
    )
    if total > 6000:
        raise ValueError("Combined embed text exceeds Discord's 6000-character limit.")
    if any(contains_secret(str(value)) for value in [title, description, footer, *(f.get("name", "") for f in fields), *(f.get("value", "") for f in fields)]):
        raise ValueError("Embed text appears to contain a password, token, private key, or recovery phrase.")


def contains_secret(value: str) -> bool:
    return bool(SECRET_ASSIGNMENT.search(value) or DISCORD_TOKEN.search(value) or
                KNOWN_KEY.search(value) or PRIVATE_KEY_BLOCK.search(value))


def redact_sensitive(value: str) -> str:
    value = PRIVATE_KEY_BLOCK.sub("[REDACTED PRIVATE KEY]", value)
    value = SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}: [REDACTED]", value)
    value = DISCORD_TOKEN.sub("[REDACTED TOKEN]", value)
    return KNOWN_KEY.sub("[REDACTED KEY]", value)


def safe_text(value: str, limit: int = 1000) -> str:
    """Bound user text and neutralize @everyone/@here for safe public output."""
    return redact_sensitive(value).replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")[:limit]
