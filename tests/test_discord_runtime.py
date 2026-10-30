"""Runtime smoke tests run when requirements.txt dependencies are installed."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

try:
    import discord  # noqa: F401
except ImportError:
    discord = None


@unittest.skipIf(discord is None, "Install requirements.txt to run the Discord runtime smoke test")
class DiscordRuntimeSmokeTests(unittest.TestCase):
    def test_cog_definitions_prefix_commands_and_persistent_views(self):
        from apexbot.core import CoreCog
        from apexbot.moderation import ModerationCog
        from apexbot.server import ServerCog
        from apexbot.builders import BuilderCog
        from apexbot.community import CommunityCog
        from apexbot.tickets import TicketCog
        from apexbot.tickets import TicketControls, TicketPanelView
        from apexbot.community import GiveawayEntryView, SuggestionView

        cog_classes = (CoreCog, ModerationCog, ServerCog, TicketCog, CommunityCog, BuilderCog)
        command_names = {command.name for cog in cog_classes for command in cog.__cog_commands__}
        self.assertTrue({"help", "warn", "panelbuilder", "embedbuilder", "giveaway", "suggestion", "audit"}.issubset(command_names))
        # There are no application command definitions in any cog module.
        self.assertFalse(any("app_commands" in command.callback.__module__ for cog in cog_classes for command in cog.__cog_commands__))
        cog = SimpleNamespace(bot=None)
        views = [TicketControls(cog, "ticket1"),
                 TicketPanelView(cog, "panel1", "Open ticket", None),
                 GiveawayEntryView(cog, "giveaway1"),
                 SuggestionView(cog, 1)]
        self.assertTrue(all(view.is_persistent() for view in views))


if __name__ == "__main__":
    unittest.main()
