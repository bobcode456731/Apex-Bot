"""Pure authorization and routing policy helpers, shared with offline tests."""

from __future__ import annotations

from .config import ROLE_THRESHOLD_KEYS

TICKET_MINIMUM_ROLE = {"middleman": "middleman", "support": "moderator", "rankup": "server_founder"}


def meets_position_threshold(role_positions, threshold_position: int, *, server_owner: bool = False) -> bool:
    """Use the live Discord positions supplied by the caller, never role IDs as rank."""
    return server_owner or any(int(position) >= int(threshold_position) for position in role_positions)


def has_configured_threshold(key: str) -> bool:
    return key in ROLE_THRESHOLD_KEYS

