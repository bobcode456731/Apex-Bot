from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from apexbot.builders import BuilderCog, EmbedWizard, PanelDeliveryView, PanelWizard
from apexbot.bot import ApexBot
from apexbot.common import SafeView, can_bot_manage_member, has_minimum_role, is_private_staff_channel
from apexbot.community import CommunityCog, GiveawayEntryView, SuggestionView
from apexbot.config import CHANNEL_IDS, ROLE_IDS, Settings
from apexbot.database import Database, utcnow
from apexbot.moderation import ModerationCog, purge_message_filter
from apexbot.server import ServerCog
from apexbot.tickets import TICKET_FIELDS, TicketCog, TicketControls, TicketPanelView
from apexbot.validation import validate_button_emoji


class FeatureTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.db_path = Path(self.temp.name) / "feature.sqlite3"
        self.db = Database(self.db_path)
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def add_ticket(self, ticket_id="t1", *, category="support", opener_id="21", channel_id="300"):
        self.db.execute(
            "INSERT INTO tickets(id,guild_id,category,opener_id,channel_id,status,created_at) VALUES(?,?,?,?,?,'open',?)",
            (ticket_id, "100", category, opener_id, channel_id, utcnow()),
        )


class TestSpecificationConfiguration(FeatureTestCase):
    def test_all_confirmed_channel_and_role_ids_match_the_spec(self):
        expected_channels = {
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
        expected_roles = {
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
        self.assertEqual(CHANNEL_IDS, expected_channels)
        self.assertEqual(ROLE_IDS, expected_roles)
        spec = (Path(__file__).resolve().parents[1] / "APEX_BOT_CODEX_SPEC.md").read_text(encoding="utf-8")
        for snowflake in [*expected_channels.values(), *expected_roles.values()]:
            self.assertIn(str(snowflake), spec)

    def test_environment_configuration_rejects_wrong_prefix_and_keeps_placeholder_only(self):
        with patch.dict(os.environ, {"DISCORD_TOKEN": "placeholder", "COMMAND_PREFIX": "?", "DATABASE_PATH": "x.db"}, clear=True):
            with self.assertRaisesRegex(ValueError, "only supports"):
                Settings.from_env()
        with patch.dict(os.environ, {"DISCORD_TOKEN": "placeholder", "COMMAND_PREFIX": "!", "LOG_RETENTION_DAYS": "bad"}, clear=True):
            with self.assertRaises(ValueError):
                Settings.from_env()
        with patch.dict(os.environ, {"DISCORD_TOKEN": "YOUR_DISCORD_BOT_TOKEN_HERE", "DATABASE_PATH": "  "}, clear=True):
            with self.assertRaisesRegex(ValueError, "DATABASE_PATH"):
                Settings.from_env()
        with patch.dict(os.environ, {"DISCORD_TOKEN": "YOUR_DISCORD_BOT_TOKEN_HERE", "DATABASE_PATH": "bot.sqlite3"}, clear=True):
            self.assertEqual(Settings.from_env().token, "")
        example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
        self.assertRegex(example, r"(?m)^DISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN_HERE$")
        self.assertNotRegex(example, r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{20,}")

    def test_ticket_forms_match_all_three_approved_workflows(self):
        self.assertEqual([label for label, _ in TICKET_FIELDS["middleman"]], [
            "Trading partner's Discord username", "What is being traded?", "Estimated value", "Who gives first?"])
        self.assertEqual([label for label, _ in TICKET_FIELDS["support"]], [
            "What help is needed?", "Explanation", "Relevant user IDs/evidence"])
        self.assertEqual([label for label, _ in TICKET_FIELDS["rankup"]], [
            "Current staff role", "Requested role/rank", "Reason", "Approval/poll/arrangement"])

    def test_database_transaction_rolls_back_and_schema_covers_required_entities(self):
        with self.assertRaises(RuntimeError):
            with self.db.transaction() as connection:
                connection.execute("INSERT INTO settings(key,value,updated_at) VALUES('rollback','x',?)", (utcnow(),))
                raise RuntimeError("force rollback")
        self.assertIsNone(self.db.setting("rollback"))
        tables = {row["name"] for row in self.db.all("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"settings", "warnings", "moderation_cases", "locked_channels", "tickets", "ticket_events",
                         "panel_templates", "custom_embeds", "giveaways", "giveaway_entries", "suggestions",
                         "suggestion_votes", "suggestion_history", "audit_events"}.issubset(tables))


class TestServerAutomation(FeatureTestCase):
    def setUp(self):
        super().setUp()
        self.bot = SimpleNamespace(db=self.db, logger=MagicMock())
        self.cog = ServerCog(self.bot)

    def test_autorole_refuses_privileged_role_without_saving_it(self):
        role = SimpleNamespace(id=777, permissions=SimpleNamespace(administrator=True), managed=False,
                               is_default=lambda: False)
        guild = SimpleNamespace(id=100, me=SimpleNamespace(top_role=SimpleNamespace(position=100)))
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=50), send=AsyncMock())
        with patch("apexbot.server.send_error", new=AsyncMock()) as error:
            asyncio_run(ServerCog.autorole_set.callback(self.cog, ctx, role))
        error.assert_awaited_once()
        self.assertIsNone(self.db.setting("autorole:100"))

    def test_setwelcome_does_not_persist_channel_without_view_permission(self):
        bot_member = MagicMock(spec=discord.Member)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 999
        channel.permissions_for.return_value = SimpleNamespace(view_channel=False, send_messages=True, embed_links=True)
        ctx = SimpleNamespace(guild=SimpleNamespace(id=100, me=bot_member), author=SimpleNamespace(id=50), send=AsyncMock())
        with patch("apexbot.server.send_error", new=AsyncMock()):
            asyncio_run(ServerCog.setwelcome.callback(self.cog, ctx, channel))
        self.assertIsNone(self.db.setting("welcome:100"))

    def test_welcome_test_uses_only_the_saved_destination(self):
        self.db.set_setting("welcome:100", "999")
        bot_member = MagicMock(spec=discord.Member)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 999
        channel.mention = "<#999>"
        channel.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=True, embed_links=True)
        guild = SimpleNamespace(id=100, me=bot_member, get_channel=MagicMock(return_value=channel))
        author = MagicMock(spec=discord.Member)
        author.id = 50
        author.mention = "<@50>"
        ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())
        with patch("apexbot.server.audit", new=AsyncMock(return_value=True)):
            asyncio_run(ServerCog.welcome_test.callback(self.cog, ctx))
        guild.get_channel.assert_called_once_with(999)
        channel.send.assert_awaited_once()
        self.assertEqual(channel.send.await_args.kwargs["allowed_mentions"].users, [author])

    def test_malformed_persisted_welcome_configuration_does_not_send_to_default(self):
        self.db.set_setting("welcome:100", "not-a-channel-id")
        guild = MagicMock(spec=discord.Guild)
        guild.id = 100
        member = MagicMock(spec=discord.Member)
        member.id = 25
        member.guild = guild
        with patch("apexbot.server.audit", new=AsyncMock(return_value=True)):
            asyncio_run(self.cog.on_member_join(member))
        guild.get_channel.assert_not_called()

class TestPermissionAndHierarchy(FeatureTestCase):
    def test_command_minimum_roles_and_native_permission_decorators(self):
        expected_roles = {
            "warn": "moderator", "warnings": "lead_designer", "clearwarnings": "director_ops",
            "timeout": "lead_designer", "untimeout": "lead_designer", "kick": "moderator",
            "ban": "administrator", "unban": "server_founder", "purge": "administrator",
            "lock": "head_owner", "unlock": "head_owner", "slowmode": "head_owner",
        }
        expected_native = {
            "timeout": "moderate_members", "untimeout": "moderate_members", "kick": "kick_members",
            "ban": "ban_members", "unban": "ban_members", "purge": "manage_messages",
            "lock": "manage_channels", "unlock": "manage_channels", "slowmode": "manage_channels",
        }
        all_commands = {command.name: command for command in ModerationCog.__cog_commands__}
        all_commands.update({command.name: command for command in ServerCog.__cog_commands__})
        for name, role_key in expected_roles.items():
            command = all_commands[name]
            closures = [dict(zip(check.__code__.co_freevars, [cell.cell_contents for cell in (check.__closure__ or ())]))
                        for check in command.checks]
            self.assertIn(role_key, [closure.get("key") for closure in closures], name)
        for name, permission in expected_native.items():
            command = all_commands[name]
            closures = [dict(zip(check.__code__.co_freevars, [cell.cell_contents for cell in (check.__closure__ or ())]))
                        for check in command.checks]
            self.assertIn(permission, [closure.get("permission") for closure in closures], name)

    def test_role_check_rejects_member_without_required_role(self):
        command = next(command for command in ModerationCog.__cog_commands__ if command.name == "warn")
        role_predicate = next(check for check in command.checks if "key" in check.__code__.co_freevars)
        ctx = SimpleNamespace(author=MagicMock(spec=discord.Member))
        with patch("apexbot.common.has_minimum_role", return_value=False):
            with self.assertRaises(commands.CheckFailure):
                asyncio_run(role_predicate(ctx))

    def test_native_permission_check_rejects_missing_discord_permission(self):
        command = next(command for command in ModerationCog.__cog_commands__ if command.name == "ban")
        predicate = next(check for check in command.checks if "permission" in check.__code__.co_freevars)
        ctx = SimpleNamespace(author=SimpleNamespace(guild_permissions=SimpleNamespace(ban_members=False)))
        with self.assertRaises(commands.CheckFailure):
            asyncio_run(predicate(ctx))

    def test_live_role_positions_and_actor_bot_hierarchy_are_enforced(self):
        role = SimpleNamespace(id=ROLE_IDS["moderator"], position=5)
        guild = SimpleNamespace(owner_id=1, get_role=lambda role_id: role if role_id == role.id else None)
        member = SimpleNamespace(id=2, guild=guild, roles=[SimpleNamespace(position=4)])
        self.assertFalse(has_minimum_role(member, "moderator"))
        member.roles.append(SimpleNamespace(position=5))
        self.assertTrue(has_minimum_role(member, "moderator"))

        class PositionRole:
            def __init__(self, position):
                self.position = position

            def __ge__(self, other):
                return self.position >= other.position

        server = SimpleNamespace(owner_id=1, me=SimpleNamespace(top_role=PositionRole(8)))
        actor = SimpleNamespace(id=2, top_role=PositionRole(6))
        target = SimpleNamespace(id=3, top_role=PositionRole(5))
        self.assertEqual(can_bot_manage_member(server, actor, target), (True, ""))
        target.top_role = PositionRole(6)
        self.assertFalse(can_bot_manage_member(server, actor, target)[0])
        target.top_role = PositionRole(8)
        self.assertFalse(can_bot_manage_member(server, actor, target)[0])
        target.id = actor.id
        target.top_role = PositionRole(3)
        self.assertFalse(can_bot_manage_member(server, actor, target)[0])

    def test_purge_filter_excludes_controls_and_never_exceeds_requested_count(self):
        check = purge_message_filter(2, {10, 11})
        messages = [SimpleNamespace(id=value) for value in (10, 11, 12, 13, 14)]
        self.assertEqual([check(message) for message in messages], [False, False, True, True, False])

    def test_audit_channel_privacy_rejects_general_roles_and_member_overrides(self):
        everyone = SimpleNamespace(id=0, position=0, is_default=lambda: True)
        moderator = SimpleNamespace(id=ROLE_IDS["moderator"], position=10, is_default=lambda: False)
        trader = SimpleNamespace(id=ROLE_IDS["level_1_trader"], position=2, is_default=lambda: False)
        guild = SimpleNamespace(default_role=everyone, roles=[everyone, moderator, trader],
                                 get_role=lambda value: moderator if value == ROLE_IDS["moderator"] else None)
        channel = SimpleNamespace(overwrites={}, permissions_for=lambda target: SimpleNamespace(
            view_channel=False if target is everyone else target is moderator or target is trader))
        self.assertFalse(is_private_staff_channel(channel, guild))
        channel.permissions_for = lambda target: SimpleNamespace(view_channel=False if target is everyone else target is moderator)
        guild.roles.remove(trader)
        self.assertTrue(is_private_staff_channel(channel, guild))
        member = MagicMock(spec=discord.Member)
        member.id = 99
        channel.overwrites = {member: SimpleNamespace(view_channel=True)}
        self.assertFalse(is_private_staff_channel(channel, guild))

    def test_server_and_community_permission_requirements_are_present(self):
        commands_by_name = {command.qualified_name: command for command in ServerCog.__cog_commands__}
        for name, role, native in (
            ("autorole set", "head_owner", "manage_roles"), ("autorole off", "head_owner", "manage_roles"),
            ("setwelcome", "head_owner", "manage_guild"), ("welcome off", "head_owner", "manage_guild"),
            ("welcome-test", "head_owner", "manage_guild"), ("nick", "lead_designer", "manage_nicknames"),
            ("role add", "operations_lead", "manage_roles"), ("role remove", "operations_lead", "manage_roles"),
        ):
            command = commands_by_name[name]
            closures = [dict(zip(check.__code__.co_freevars, [cell.cell_contents for cell in (check.__closure__ or ())]))
                        for check in command.checks]
            self.assertIn(role, [closure.get("key") for closure in closures], name)
            self.assertIn(native, [closure.get("permission") for closure in closures], name)

    def test_clearwarnings_does_not_archive_without_confirmation(self):
        self.db.execute("INSERT INTO warnings(guild_id,user_id,actor_id,reason,created_at) VALUES('100','30','40','reason',?)", (utcnow(),))
        bot = SimpleNamespace(db=self.db, logger=MagicMock())
        cog = ModerationCog(bot)
        member = SimpleNamespace(id=30, mention="<@30>")
        ctx = SimpleNamespace(guild=SimpleNamespace(id=100), author=SimpleNamespace(id=40), send=AsyncMock())
        with patch.object(cog, "require_target", new=AsyncMock(return_value=True)), \
             patch("apexbot.moderation.confirm", new=AsyncMock(return_value=False)):
            asyncio_run(ModerationCog.clearwarnings.callback(cog, ctx, member, reason="review"))
        self.assertIsNone(self.db.one("SELECT archived_at FROM warnings WHERE user_id='30'")[0])

    def test_ban_api_failure_is_not_reported_as_success(self):
        bot = SimpleNamespace(db=self.db, logger=MagicMock())
        cog = ModerationCog(bot)
        member = MagicMock(spec=discord.Member)
        member.id = 30
        member.mention = "<@30>"
        guild = SimpleNamespace(id=100, me=SimpleNamespace(guild_permissions=SimpleNamespace(ban_members=True)),
                                ban=AsyncMock(side_effect=discord.Forbidden(MagicMock(), "denied")))
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=40), send=AsyncMock())
        with patch.object(cog, "require_target", new=AsyncMock(return_value=True)), \
             patch.object(cog, "log", new=AsyncMock()), \
             patch("apexbot.moderation.confirm", new=AsyncMock(return_value=True)), \
             patch("apexbot.moderation.send_error", new=AsyncMock()):
            asyncio_run(ModerationCog.ban.callback(cog, ctx, member, reason="test"))
        guild.ban.assert_awaited_once()
        ctx.send.assert_not_awaited()


def asyncio_run(coro):
    import asyncio
    return asyncio.run(asyncio.wait_for(coro, timeout=5))


def make_panel_guild(*, category="middleman", public_rankup=False, role_exists=True):
    channel_id = CHANNEL_IDS["rankup_panel"] if category == "rankup" else CHANNEL_IDS["middleman_panel"]
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    default_role = MagicMock(spec=discord.Role)
    bot_member = MagicMock(spec=discord.Member)
    bot_member.guild_permissions.mention_everyone = False
    channel.permissions_for.side_effect = lambda who: SimpleNamespace(
        view_channel=(True if who is bot_member else (True if public_rankup else False)),
        send_messages=True, embed_links=True)
    role = MagicMock(spec=discord.Role)
    role.id = 1552344063003467797
    role.mentionable = True
    role.is_default.return_value = False
    guild = MagicMock(spec=discord.Guild)
    guild.id = 100
    guild.me = bot_member
    guild.default_role = default_role
    guild.get_channel.side_effect = lambda found: channel if int(found) == channel_id else None
    guild.get_role.side_effect = lambda found: role if int(found) == role.id and role_exists else None
    return guild, channel, role


class TestBuildersAndTicketPolicies(FeatureTestCase):
    def setUp(self):
        super().setUp()
        self.bot = SimpleNamespace(db=self.db, logger=MagicMock(), add_view=MagicMock(), get_cog=MagicMock())
        self.builder = BuilderCog(self.bot)

    def panel_wizard(self, guild, category="middleman", fields=None, **extra):
        channel_id = CHANNEL_IDS["rankup_panel"] if category == "rankup" else CHANNEL_IDS["middleman_panel"]
        data = {"name": "Audit draft", "title": "Request", "description": "Private request", "fields": fields or [],
                "channel_id": str(channel_id), "support_role_id": "1552344063003467797", "colour": "#8B5CF6",
                "image_url": "", "footer": "Apex", "button_label": "Open ticket", "button_emoji": "🎫"}
        data.update(extra)
        return PanelWizard(self.builder, 100, 50, category, data)

    def test_panel_draft_persists_category_destination_and_notification_role(self):
        guild, _, role = make_panel_guild()
        wizard = self.panel_wizard(guild, fields=[{"name": "Trade", "value": "Details", "inline": False}])
        panel_id = wizard.save(guild)
        row = self.db.one("SELECT * FROM panel_templates WHERE id=?", (panel_id,))
        self.assertEqual(row["ticket_type"], "middleman")
        self.assertEqual(row["channel_id"], str(CHANNEL_IDS["middleman_panel"]))
        self.assertEqual(row["support_role_id"], str(role.id))
        self.assertEqual(row["state"], "draft")
        self.assertEqual(json.loads(row["fields_json"])[0]["name"], "Trade")

    def test_panel_builder_uses_real_channel_and_role_selects(self):
        guild, _, _ = make_panel_guild()
        view = PanelDeliveryView(self.panel_wizard(guild))
        channel_select = next(child for child in view.children if isinstance(child, discord.ui.ChannelSelect))
        role_select = next(child for child in view.children if isinstance(child, discord.ui.RoleSelect))
        self.assertIsInstance(channel_select, discord.ui.ChannelSelect)
        self.assertIsInstance(role_select, discord.ui.RoleSelect)
        self.assertEqual(channel_select.channel_types, [discord.ChannelType.text])

    def test_panel_publish_persists_one_panel_and_uses_strict_allowed_mentions(self):
        guild, channel, _ = make_panel_guild()
        wizard = self.panel_wizard(guild, fields=[{"name": "Details", "value": "Trade details", "inline": False}])
        panel_id = wizard.save(guild)
        message = SimpleNamespace(id=555, channel=channel)
        channel.send = AsyncMock(return_value=message)
        self.bot.get_cog.return_value = SimpleNamespace(bot=self.bot)
        author = SimpleNamespace(id=50)
        ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())
        with patch.object(self.builder, "_permission", new=AsyncMock(return_value=True)), \
             patch("apexbot.builders.audit", new=AsyncMock(return_value=True)):
            asyncio_run(BuilderCog.panel_publish.callback(self.builder, ctx, panel_id))
        row = self.db.one("SELECT state,published_channel_id,published_message_id,published_config_json FROM panel_templates WHERE id=?", (panel_id,))
        self.assertEqual((row["state"], row["published_channel_id"], row["published_message_id"]),
                         ("published", str(channel.id), "555"))
        snapshot = json.loads(row["published_config_json"])
        self.assertEqual(snapshot["support_role_id"], "1552344063003467797")
        self.assertEqual(channel.send.await_args.kwargs["allowed_mentions"].to_dict(),
                         discord.AllowedMentions.none().to_dict())
        self.assertEqual(channel.send.await_args.kwargs["view"].children[0].custom_id, f"ticket:open:{panel_id}")
        self.bot.add_view.assert_called_once()

    def test_published_panel_edit_requires_confirmation_before_discord_update(self):
        guild, channel, _ = make_panel_guild()
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO panel_templates(id,guild_id,name,ticket_type,title,description,fields_json,colour,footer,button_label,channel_id,support_role_id,creator_id,created_at,updated_at,state,published_message_id,published_channel_id,published_config_json) VALUES('p1','100','panel','middleman','','','[]',9133558,'','Request',?,?, '50',?,?, 'published','888',?,'{}')",
                               (str(channel.id), "1552344063003467797", utcnow(), utcnow(), str(channel.id)))
        self.bot.get_cog.return_value = SimpleNamespace(bot=self.bot)
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=50), send=AsyncMock())
        with patch.object(self.builder, "_permission", new=AsyncMock(return_value=True)), \
             patch("apexbot.builders.confirm", new=AsyncMock(return_value=False)):
            asyncio_run(BuilderCog.panel_update.callback(self.builder, ctx, "p1"))
        channel.fetch_message.assert_not_awaited()
        channel.send.assert_not_awaited()
        self.assertEqual(self.db.one("SELECT published_message_id FROM panel_templates WHERE id='p1'")[0], "888")

    def test_confirmed_panel_delete_disables_published_button_before_removing_record(self):
        guild, channel, _ = make_panel_guild()
        panel_id = self.panel_wizard(guild).save(guild)
        with self.db.transaction() as connection:
            connection.execute("UPDATE panel_templates SET state='published',published_channel_id=?,published_message_id='888' WHERE id=?",
                               (str(channel.id), panel_id))
        message = SimpleNamespace(edit=AsyncMock())
        channel.fetch_message = AsyncMock(return_value=message)
        self.bot.get_cog.return_value = SimpleNamespace(bot=self.bot)
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=50), send=AsyncMock())
        with patch.object(self.builder, "_permission", new=AsyncMock(return_value=True)), \
             patch("apexbot.builders.confirm", new=AsyncMock(return_value=True)), \
             patch("apexbot.builders.audit", new=AsyncMock(return_value=True)):
            asyncio_run(BuilderCog.panel_delete.callback(self.builder, ctx, panel_id))
        message.edit.assert_awaited_once()
        self.assertIsNone(message.edit.await_args.kwargs["view"])
        self.assertIsNone(self.db.one("SELECT id FROM panel_templates WHERE id=?", (panel_id,)))

    def test_custom_duplicate_trims_trigger_and_keeps_it_unique(self):
        guild = SimpleNamespace(id=100)
        source = EmbedWizard(self.builder, 100, 50, {"trigger": "base", "aliases": [], "title": "Title",
                              "description": "Description", "fields": [], "colour": "#8B5CF6",
                              "image_url": "", "footer": "Apex"})
        embed_id = source.save(guild)
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=50), send=AsyncMock())
        with patch.object(self.builder, "_permission", new=AsyncMock(return_value=True)), \
             patch("apexbot.builders.audit", new=AsyncMock(return_value=True)):
            asyncio_run(BuilderCog.custom_duplicate.callback(self.builder, ctx, embed_id, "  copytrade  "))
        self.assertEqual(self.db.one("SELECT trigger FROM custom_embeds WHERE trigger='copytrade'")[0], "copytrade")

    def test_panel_validation_blocks_public_rankup_missing_role_bad_emoji_and_accepts_25_fields(self):
        guild, _, _ = make_panel_guild(category="rankup", public_rankup=True)
        with self.assertRaisesRegex(ValueError, "deny public and non-staff"):
            self.panel_wizard(guild, category="rankup").validate(guild)
        guild, _, _ = make_panel_guild(role_exists=False)
        with self.assertRaisesRegex(ValueError, "existing server role"):
            self.panel_wizard(guild).validate(guild)
        guild, _, _ = make_panel_guild()
        with self.assertRaisesRegex(ValueError, "Button emoji"):
            self.panel_wizard(guild, button_emoji="notemoji").validate(guild)
        fields = [{"name": f"Field {i}", "value": "x", "inline": False} for i in range(25)]
        wizard = self.panel_wizard(guild, fields=fields)
        wizard.validate(guild)
        self.assertEqual(len(wizard.current_embed().fields), 25)

    def test_custom_trigger_builder_saves_normalized_names_and_blocks_collisions(self):
        guild = SimpleNamespace(id=100)
        wizard = EmbedWizard(self.builder, 100, 50, {"trigger": "  TradeInfo ", "aliases": [" Details "],
                    "title": "Trade info", "description": "", "fields": [], "colour": "#8B5CF6",
                    "image_url": "", "footer": ""})
        embed_id = wizard.save(guild)
        row = self.db.one("SELECT trigger,aliases_json FROM custom_embeds WHERE id=?", (embed_id,))
        self.assertEqual(row["trigger"], "tradeinfo")
        self.assertEqual(json.loads(row["aliases_json"]), ["details"])
        collision = EmbedWizard(self.builder, 100, 50, {"trigger": "another", "aliases": ["details"],
                    "title": "", "description": "", "fields": [], "colour": "#8B5CF6",
                    "image_url": "", "footer": ""})
        with self.assertRaisesRegex(ValueError, "already used"):
            collision.save(guild)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM custom_embeds")[0], 1)

    def test_builder_interaction_rechecks_founder_and_native_permission(self):
        guild, _, _ = make_panel_guild()
        wizard = self.panel_wizard(guild)
        from apexbot.builders import PanelStartView
        view = PanelStartView(wizard)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.id = 50
        interaction.response.send_message = AsyncMock()
        with patch("apexbot.builders.has_minimum_role", return_value=False):
            self.assertFalse(asyncio_run(view.interaction_check(interaction)))
        interaction.response.send_message.assert_awaited_once()

    def test_builder_interaction_rejects_missing_native_manage_server(self):
        guild, _, _ = make_panel_guild()
        wizard = self.panel_wizard(guild)
        from apexbot.builders import PanelStartView
        view = PanelStartView(wizard)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.id = 50
        interaction.user.guild_permissions.manage_guild = False
        interaction.guild_id = 100
        interaction.response.send_message = AsyncMock()
        with patch("apexbot.builders.has_minimum_role", return_value=True):
            self.assertFalse(asyncio_run(view.interaction_check(interaction)))
        self.assertIn("Manage Server", interaction.response.send_message.await_args.args[0])

    def test_rankup_panel_button_denies_ordinary_member(self):
        snapshot = {"ticket_type": "rankup"}
        row = {"id": "p1", "ticket_type": "rankup", "published_config_json": json.dumps(snapshot),
               "published_message_id": "888", "published_channel_id": str(CHANNEL_IDS["rankup_panel"])}
        self.bot.db = SimpleNamespace(one=MagicMock(return_value=row))
        cog = SimpleNamespace(bot=self.bot)
        view = TicketPanelView(cog, "p1", "Open purchase ticket", "💎")
        interaction = MagicMock(spec=discord.Interaction)
        interaction.data = {"custom_id": "ticket:open:p1"}
        interaction.guild_id = 100
        interaction.guild = MagicMock(spec=discord.Guild)
        interaction.message.id = 888
        interaction.message.channel_id = CHANNEL_IDS["rankup_panel"]
        interaction.user = MagicMock(spec=discord.Member)
        interaction.response.send_message = AsyncMock()
        interaction.response.send_modal = AsyncMock()
        with patch("apexbot.tickets.has_minimum_role", return_value=False):
            asyncio_run(view.open(interaction))
        interaction.response.send_message.assert_awaited_once()
        self.assertIn("staff-only", interaction.response.send_message.await_args.args[0])
        interaction.response.send_modal.assert_not_awaited()

    def test_button_emoji_checks_unicode_and_server_custom_emoji(self):
        guild = SimpleNamespace(get_emoji=lambda emoji_id: SimpleNamespace(id=emoji_id) if emoji_id == 123456789012345678 else None)
        self.assertEqual(validate_button_emoji("🎫", guild), "🎫")
        self.assertEqual(validate_button_emoji("<:ticket:123456789012345678>", guild), "<:ticket:123456789012345678>")
        with self.assertRaises(ValueError):
            validate_button_emoji("<:ticket:123456789012345679>", guild)


class TestTicketLifecycleAndPersistence(FeatureTestCase):
    def setUp(self):
        super().setUp()
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = 300
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.get_channel.return_value = self.channel
        self.guild.get_role.return_value = None
        self.bot = SimpleNamespace(db=self.db, logger=MagicMock(), get_guild=MagicMock(return_value=self.guild),
                                   add_view=MagicMock())
        self.cog = TicketCog(self.bot)

    def test_claim_and_unclaim_record_correct_actor_assignment_and_timestamp(self):
        self.add_ticket()
        self.assertTrue(self.cog.update_claim("t1", 41, "claim", actor_id=41))
        self.assertFalse(self.cog.update_claim("t1", 43, "claim", actor_id=43))
        self.assertTrue(self.cog.update_claim("t1", None, "unclaim", actor_id=42))
        rows = self.db.all("SELECT kind,actor_id,target_id,old_value,new_value,created_at FROM ticket_events ORDER BY id")
        self.assertEqual(rows[1]["actor_id"], "42")
        self.assertEqual(rows[1]["target_id"], "41")
        self.assertEqual(rows[1]["old_value"], "41")
        self.assertIsNone(rows[1]["new_value"])
        self.assertTrue(rows[1]["created_at"])

    def test_ticket_open_creates_private_category_ticket_and_pings_only_saved_role(self):
        default_role = MagicMock(spec=discord.Role)
        default_role.is_default.return_value = True
        staff_role = MagicMock(spec=discord.Role)
        staff_role.id = ROLE_IDS["moderator"]
        staff_role.position = 10
        staff_role.is_default.return_value = False
        ping_role = staff_role
        bot_member = MagicMock(spec=discord.Member)
        bot_member.guild_permissions.manage_channels = True
        user = MagicMock(spec=discord.Member)
        user.id = 21
        user.name = "ticket user"
        user.mention = "<@21>"
        user.bot = False
        guild = MagicMock(spec=discord.Guild)
        guild.id = 100
        guild.default_role = default_role
        guild.roles = [default_role, staff_role]
        guild.me = bot_member
        guild.get_role.side_effect = lambda value: ping_role if value == ROLE_IDS["moderator"] else None
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 300
        channel.mention = "<#300>"
        guild.create_text_channel = AsyncMock(return_value=channel)
        self.bot.get_guild.return_value = guild
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild = guild
        interaction.user = user
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        panel = {"id": "panel1", "ticket_type": "support", "support_role_id": str(ping_role.id)}
        with patch.object(self.cog, "log_ticket", new=AsyncMock(return_value=True)), \
             patch("apexbot.tickets.audit", new=AsyncMock(return_value=True)):
            asyncio_run(self.cog.open_ticket(interaction, panel, {"Explanation": "Trade issue"}))
            asyncio_run(self.cog.open_ticket(interaction, panel, {"Explanation": "Duplicate"}))
        row = self.db.one("SELECT * FROM tickets WHERE opener_id='21' AND category='support'")
        self.assertEqual((row["status"], row["channel_id"], row["support_role_id"]), ("open", "300", str(ping_role.id)))
        guild.create_text_channel.assert_awaited_once()
        sent_mentions = channel.send.await_args.kwargs["allowed_mentions"]
        self.assertEqual(sent_mentions.roles, [ping_role])
        self.assertEqual(sent_mentions.users, [user])
        self.assertEqual(interaction.followup.send.await_count, 2)
        self.assertIn("already have an open support ticket", interaction.followup.send.await_args.args[0])

    def test_ticket_open_rejects_corrupt_saved_support_role_configuration(self):
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild = self.guild
        interaction.user = MagicMock(spec=discord.Member)
        interaction.user.id = 21
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        with patch("apexbot.tickets.audit", new=AsyncMock(return_value=True)) as audit_call:
            asyncio_run(self.cog.open_ticket(interaction, {"id": "panel1", "ticket_type": "support",
                                                           "support_role_id": "invalid"}, {}))
        self.assertIn("configuration is invalid", interaction.followup.send.await_args.args[0])
        self.guild.create_text_channel.assert_not_awaited()
        audit_call.assert_awaited_once()

    def test_transcript_upload_failure_keeps_ticket_open_and_alerts_staff(self):
        self.add_ticket()
        closer = SimpleNamespace(id=77)
        self.cog._save_transcript = AsyncMock(side_effect=RuntimeError("upload denied"))
        self.channel.send = AsyncMock()
        with patch("apexbot.tickets.audit", new=AsyncMock(return_value=True)):
            success, result = asyncio_run(self.cog.close_ticket("t1", closer, "Trade Complete"))
        self.assertFalse(success)
        self.assertIn("remains open", result)
        self.assertEqual(self.db.one("SELECT status FROM tickets WHERE id='t1'")[0], "open")
        self.assertIsNone(self.db.one("SELECT closed_at FROM tickets WHERE id='t1'")[0])
        self.channel.delete.assert_not_awaited()
        self.channel.send.assert_awaited()
        self.assertIsNotNone(self.db.one("SELECT 1 FROM ticket_events WHERE ticket_id='t1' AND kind='transcript_failure'"))

    def test_ticket_channel_is_deleted_only_after_transcript_and_database_close(self):
        self.add_ticket()
        order = []
        async def save(*args):
            order.append("transcript")
            return SimpleNamespace(id=555)
        async def delete(*args, **kwargs):
            self.assertEqual(self.db.one("SELECT status,transcript_saved FROM tickets WHERE id='t1'")[:], ("closed", 1))
            order.append("delete")
        self.cog._save_transcript = AsyncMock(side_effect=save)
        self.channel.delete = AsyncMock(side_effect=delete)
        with patch("apexbot.tickets.audit", new=AsyncMock(return_value=True)):
            success, result = asyncio_run(self.cog.close_ticket("t1", SimpleNamespace(id=77), "Reason"))
        self.assertTrue(success)
        self.assertEqual(order, ["transcript", "delete"])
        self.assertIn("deleted", result)
        close_requested = self.db.one("SELECT actor_id,detail FROM ticket_events WHERE kind='close_requested'")
        self.assertEqual(close_requested["actor_id"], "77")
        self.assertEqual(close_requested["detail"], "Reason")

    def test_transcript_contains_ticket_timeline_messages_and_attachment_metadata(self):
        self.add_ticket(opener_id="21")
        self.cog.event("t1", "claim", 41, target_id=41, old_value="unclaimed", new_value="41")
        self.cog.event("t1", "close_requested", 99, detail="Trade Complete")
        self.guild.default_role = SimpleNamespace(id=0, is_default=lambda: True)
        self.guild.roles = [self.guild.default_role]
        self.guild.get_role.return_value = None
        self.guild.me = SimpleNamespace()
        log_channel = MagicMock(spec=discord.TextChannel)
        log_channel.overwrites = {}
        log_channel.permissions_for.side_effect = lambda target: SimpleNamespace(
            view_channel=False if target is self.guild.default_role else True,
            send_messages=True, attach_files=True)
        transcript_message = SimpleNamespace(id=555)
        log_channel.send = AsyncMock(return_value=transcript_message)
        self.guild.get_channel.return_value = log_channel
        self.channel.guild = self.guild
        message = SimpleNamespace(content="Hello @everyone password: do-not-store", created_at=datetime.now(timezone.utc),
                                  author=SimpleNamespace(id=21, __str__=lambda self: "member"),
                                  attachments=[SimpleNamespace(filename="evidence.png", url="https://cdn.example/evidence.png")],
                                  stickers=[])
        async def history(**kwargs):
            yield message
        self.channel.history = MagicMock(side_effect=history)
        row = self.db.one("SELECT * FROM tickets WHERE id='t1'")
        asyncio_run(self.cog._save_transcript(row, self.channel, SimpleNamespace(id=99), "Trade Complete"))
        upload = log_channel.send.await_args.kwargs["file"]
        data = upload.fp.getvalue().decode("utf-8")
        self.assertIn("ID t1", data)
        self.assertIn("Category: support", data)
        self.assertIn("Opener ID: 21", data)
        self.assertIn("close actor=99", data)
        self.assertIn("evidence.png • https://cdn.example/evidence.png", data)
        self.assertIn("@\u200beveryone", data)
        self.assertNotIn("do-not-store", data)

    def test_open_ticket_panel_only_accepts_its_current_published_message(self):
        row = {"id": "p1", "ticket_type": "middleman", "published_config_json": "{}", "published_message_id": "888"}
        self.bot.db.one = MagicMock(return_value=row)
        view = TicketPanelView(SimpleNamespace(bot=self.bot), "p1", "Request", None)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.data = {"custom_id": "ticket:open:p1"}
        interaction.guild_id = 100
        interaction.guild = MagicMock(spec=discord.Guild)
        interaction.user = MagicMock(spec=discord.Member)
        interaction.message.id = 999
        interaction.response.send_message = AsyncMock()
        interaction.response.send_modal = AsyncMock()
        asyncio_run(view.open(interaction))
        interaction.response.send_message.assert_awaited_once_with("This panel is unavailable.", ephemeral=True)
        interaction.response.send_modal.assert_not_awaited()

    def test_adduser_denies_opener_even_when_they_have_staff_role(self):
        self.add_ticket(opener_id="20")
        ctx = self._ticket_context(actor_id=20)
        target = SimpleNamespace(id=30, mention="<@30>")
        with patch("apexbot.tickets.staff_for_category", return_value=True), \
             patch("apexbot.tickets.has_minimum_role", return_value=True), \
             patch("apexbot.tickets.send_error", new=AsyncMock()):
            asyncio_run(TicketCog.adduser.callback(self.cog, ctx, target))
        ctx.channel.set_permissions.assert_not_awaited()
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM ticket_events")[0], 0)

    def test_adduser_denies_staff_without_native_manage_channels(self):
        self.add_ticket(opener_id="20")
        ctx = self._ticket_context(actor_id=42)
        ctx.author.guild_permissions.manage_channels = False
        target = SimpleNamespace(id=30, mention="<@30>")
        with patch("apexbot.tickets.staff_for_category", return_value=True), \
             patch("apexbot.tickets.has_minimum_role", return_value=False), \
             patch("apexbot.tickets.send_error", new=AsyncMock()):
            asyncio_run(TicketCog.adduser.callback(self.cog, ctx, target))
        ctx.channel.set_permissions.assert_not_awaited()
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM ticket_events")[0], 0)

    def test_adduser_grants_only_selected_user_and_logs_actor_target_and_ticket(self):
        self.add_ticket(opener_id="20")
        ctx = self._ticket_context(actor_id=42)
        target = SimpleNamespace(id=30, mention="<@30>")
        with patch("apexbot.tickets.staff_for_category", return_value=True), \
             patch("apexbot.tickets.has_minimum_role", return_value=False), \
             patch.object(self.cog, "log_ticket", new=AsyncMock(return_value=True)):
            asyncio_run(TicketCog.adduser.callback(self.cog, ctx, target))
        ctx.channel.set_permissions.assert_awaited_once()
        event = self.db.one("SELECT ticket_id,actor_id,target_id,detail FROM ticket_events WHERE kind='add_user'")
        self.assertEqual((event["ticket_id"], event["actor_id"], event["target_id"]), ("t1", "42", "30"))
        self.assertIn("this ticket only", event["detail"])

    def _ticket_context(self, actor_id):
        actor = SimpleNamespace(id=actor_id, mention=f"<@{actor_id}>", guild_permissions=SimpleNamespace(manage_channels=True))
        bot_member = SimpleNamespace(guild_permissions=SimpleNamespace(manage_channels=True))
        self.guild.me = bot_member
        self.channel.permissions_for.return_value = SimpleNamespace(manage_channels=True)
        self.channel.set_permissions = AsyncMock()
        ctx = SimpleNamespace(author=actor, guild=self.guild, channel=self.channel, send=AsyncMock())
        return ctx

    def test_views_reregister_after_database_reopen(self):
        self.add_ticket()
        self.db.execute("INSERT INTO panel_templates(id,guild_id,name,ticket_type,title,description,fields_json,colour,footer,button_label,channel_id,support_role_id,creator_id,created_at,updated_at,state,published_message_id) VALUES('p1','100','panel','middleman','','','[]',9133558,'','Request','200','201','20',?,?, 'published','888')", (utcnow(), utcnow()))
        end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.db.execute("INSERT INTO giveaways(id,guild_id,channel_id,host_id,prize,winner_count,created_at,ends_at,status,message_id) VALUES('g1','100','200','20','Prize',1,?,?, 'active','777')", (utcnow(), end))
        cursor = self.db.execute("INSERT INTO suggestions(guild_id,channel_id,message_id,submitter_id,content,created_at,updated_at) VALUES('100','200','666','20','idea',?,?)", (utcnow(), utcnow()))
        suggestion_id = cursor.lastrowid
        self.db.close()
        reopened = Database(self.db_path)
        reopened.initialize()
        bot = SimpleNamespace(db=reopened, add_view=MagicMock(), logger=MagicMock())
        ticket_cog = TicketCog(bot)
        ticket_cog.register_persistent_views()
        community_cog = CommunityCog.__new__(CommunityCog)
        community_cog.bot = bot
        community_cog.register_persistent_views()
        ids = {child.custom_id for view in [call.args[0] for call in bot.add_view.call_args_list] for child in view.children}
        self.assertTrue({"ticket:open:p1", "ticket:claim:t1", "giveaway:enter:g1",
                         f"suggest:1:{suggestion_id}", f"suggest:-1:{suggestion_id}"}.issubset(ids))
        reopened.close()
        self.db.connection = None


class TestGiveawaySuggestionAndErrors(FeatureTestCase):
    def setUp(self):
        super().setUp()
        self.bot = SimpleNamespace(db=self.db, logger=MagicMock(), get_guild=MagicMock(return_value=MagicMock()),
                                   get_channel=MagicMock(return_value=None))
        self.cog = CommunityCog.__new__(CommunityCog)
        self.cog.bot = self.bot

    def add_giveaway(self, giveaway_id="g1"):
        end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.db.execute("INSERT INTO giveaways(id,guild_id,channel_id,host_id,prize,winner_count,created_at,ends_at,status,message_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (giveaway_id, "100", "200", "20", "Prize", 1, utcnow(), end, "active", "777"))

    def test_giveaway_button_records_one_entry_and_rejects_duplicate_or_forged_message(self):
        self.add_giveaway()
        view = GiveawayEntryView(self.cog, "g1")
        interaction = MagicMock(spec=discord.Interaction)
        interaction.data = {"custom_id": "giveaway:enter:g1"}
        interaction.guild_id = 100
        interaction.message.id = 777
        interaction.user = MagicMock(spec=discord.User)
        interaction.user.id = 33
        interaction.user.bot = False
        interaction.response.send_message = AsyncMock()
        with patch.object(self.cog, "audit_giveaway", new=AsyncMock()):
            asyncio_run(view.enter(interaction))
            asyncio_run(view.enter(interaction))
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id='g1'")[0], 1)
        self.assertIn("already entered", interaction.response.send_message.await_args.args[0])
        interaction.message.id = 778
        with patch.object(self.cog, "audit_giveaway", new=AsyncMock()):
            asyncio_run(view.enter(interaction))
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id='g1'")[0], 1)

    def test_giveaway_end_and_reroll_persist_outcome_and_do_not_repeat_winners(self):
        self.add_giveaway()
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO giveaway_entries(giveaway_id,user_id,entered_at) VALUES('g1','31',?)", (utcnow(),))
            connection.execute("INSERT INTO giveaway_entries(giveaway_id,user_id,entered_at) VALUES('g1','32',?)", (utcnow(),))
        with patch.object(self.cog, "audit_giveaway", new=AsyncMock()):
            ok, result = asyncio_run(self.cog.end_giveaway("g1", actor_id=40, announce=False))
        self.assertTrue(ok)
        row = self.db.one("SELECT status,winners_json,outcome FROM giveaways WHERE id='g1'")
        self.assertEqual(row["status"], "ended")
        first = json.loads(row["winners_json"])
        self.assertEqual(len(first), 1)
        self.assertEqual(row["outcome"], "winner(s) selected")
        self.assertIn(first[0], result)
        # The DB winner list is the exclusion set used by reroll.
        remaining = {r["user_id"] for r in self.db.all("SELECT user_id FROM giveaway_entries WHERE giveaway_id='g1'")} - set(first)
        self.assertEqual(len(remaining), 1)

    def test_giveaway_end_with_no_entrants_has_no_winner(self):
        self.add_giveaway()
        with patch.object(self.cog, "audit_giveaway", new=AsyncMock()):
            ok, result = asyncio_run(self.cog.end_giveaway("g1", actor_id=None, announce=False))
        row = self.db.one("SELECT status,winners_json,outcome FROM giveaways WHERE id='g1'")
        self.assertTrue(ok)
        self.assertIn("no eligible entrants", result)
        self.assertEqual(json.loads(row["winners_json"]), [])
        self.assertEqual(row["status"], "ended")

    def test_giveaway_cancel_has_no_winner_and_reroll_uses_an_unselected_entrant(self):
        self.add_giveaway()
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO giveaway_entries(giveaway_id,user_id,entered_at) VALUES('g1','31',?)", (utcnow(),))
            connection.execute("INSERT INTO giveaway_entries(giveaway_id,user_id,entered_at) VALUES('g1','32',?)", (utcnow(),))
        self.db.execute("UPDATE giveaways SET status='ended',winners_json='[\"31\"]',outcome='winner(s) selected' WHERE id='g1'")
        channel = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(id=100, get_channel=lambda value: channel)
        author = SimpleNamespace(id=40, guild_permissions=SimpleNamespace(manage_guild=True))
        ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())
        with patch("apexbot.community.has_minimum_role", return_value=True), \
             patch("apexbot.community.audit", new=AsyncMock(return_value=True)):
            asyncio_run(CommunityCog.giveaway_reroll.callback(self.cog, ctx, "g1"))
        winners = json.loads(self.db.one("SELECT winners_json FROM giveaways WHERE id='g1'")[0])
        self.assertEqual(set(winners), {"31", "32"})
        self.assertTrue(channel.send.await_args.args[0].find("32") >= 0)

        self.add_giveaway("g2")
        with self.db.transaction() as connection:
            connection.execute("UPDATE giveaways SET message_id='778' WHERE id='g2'")
        message = SimpleNamespace(edit=AsyncMock())
        channel.fetch_message = AsyncMock(return_value=message)
        with patch("apexbot.community.has_minimum_role", return_value=True), \
             patch("apexbot.community.audit", new=AsyncMock(return_value=True)):
            asyncio_run(CommunityCog.giveaway_cancel.callback(self.cog, ctx, "g2"))
        cancelled = self.db.one("SELECT status,winners_json,outcome FROM giveaways WHERE id='g2'")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(json.loads(cancelled["winners_json"]), [])
        self.assertIn("no winner", cancelled["outcome"])
        message.edit.assert_awaited_once_with(view=None)

    def test_suggestion_vote_is_unique_private_and_rejects_invalid_vote(self):
        cursor = self.db.execute("INSERT INTO suggestions(guild_id,channel_id,message_id,submitter_id,content,created_at,updated_at) VALUES('100','200','901','20','idea',?,?)", (utcnow(), utcnow()))
        suggestion_id = cursor.lastrowid
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild_id = 100
        interaction.guild = self.bot.get_guild.return_value
        interaction.message.id = 901
        interaction.user.id = 44
        interaction.response.send_message = AsyncMock()
        self.cog.refresh_suggestion = AsyncMock()
        with patch("apexbot.community.audit", new=AsyncMock(return_value=True)):
            asyncio_run(self.cog.record_vote(interaction, suggestion_id, 1))
            asyncio_run(self.cog.record_vote(interaction, suggestion_id, 1))
            asyncio_run(self.cog.record_vote(interaction, suggestion_id, 2))
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM suggestion_votes WHERE suggestion_id=?", (suggestion_id,))[0], 1)
        self.assertIn("already voted", interaction.response.send_message.await_args_list[1].args[0])
        self.assertIn("invalid", interaction.response.send_message.await_args_list[2].args[0])
        self.cog.refresh_suggestion.assert_awaited_once_with(suggestion_id)

    def test_suggestion_vote_database_failure_is_reported_without_success(self):
        db = SimpleNamespace(one=MagicMock(return_value={"id": 1, "guild_id": "100", "message_id": "901"}),
                             execute=MagicMock(side_effect=sqlite3.OperationalError("offline")))
        self.cog.bot.db = db
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild_id = 100
        interaction.guild = self.bot.get_guild.return_value
        interaction.message.id = 901
        interaction.user.id = 44
        interaction.response.send_message = AsyncMock()
        with patch("apexbot.community.audit", new=AsyncMock(return_value=True)):
            asyncio_run(self.cog.record_vote(interaction, 1, -1))
        self.assertIn("database is unavailable", interaction.response.send_message.await_args.args[0])

    def test_suggestion_command_deletes_identity_source_before_public_post(self):
        events = []
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = CHANNEL_IDS["suggestions"]
        channel.permissions_for.return_value = SimpleNamespace(view_channel=True, send_messages=True, embed_links=True)
        message = MagicMock()
        async def delete_source():
            events.append("source_deleted")
        message.delete = AsyncMock(side_effect=delete_source)
        public_message = MagicMock()
        public_message.id = 902
        async def post_public(*args, **kwargs):
            events.append("public_post")
            return public_message
        channel.send = AsyncMock(side_effect=post_public)
        guild = MagicMock(spec=discord.Guild)
        guild.id = 100
        guild.me = MagicMock(spec=discord.Member)
        guild.get_channel.return_value = channel
        author = MagicMock(spec=discord.Member)
        author.id = 44
        author.name = "Private Submitter"
        author.send = AsyncMock()
        source_channel = MagicMock()
        source_channel.id = 303
        source_channel.permissions_for.return_value = SimpleNamespace(manage_messages=True)
        ctx = SimpleNamespace(guild=guild, channel=source_channel, message=message, author=author, send=AsyncMock())
        with patch("apexbot.community.audit", new=AsyncMock(return_value=True)):
            asyncio_run(CommunityCog.suggest.callback(self.cog, ctx, idea="A good idea @everyone"))
        self.assertEqual(events, ["source_deleted", "public_post"])
        sent_embed = channel.send.await_args.kwargs["embed"]
        self.assertNotIn(author.name, str(sent_embed.to_dict()))
        self.assertNotIn("@everyone", sent_embed.description)
        self.assertEqual(self.db.one("SELECT submitter_id FROM suggestions")[0], str(author.id))

    def test_giveaway_management_requires_director_and_native_manage_server(self):
        ctx = SimpleNamespace(author=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True)), send=AsyncMock())
        with patch("apexbot.community.has_minimum_role", return_value=False), \
             patch("apexbot.community.send_error", new=AsyncMock()) as error:
            self.assertFalse(asyncio_run(self.cog._management_ok(ctx)))
        error.assert_awaited_once()
        ctx.author.guild_permissions.manage_guild = False
        with patch("apexbot.community.has_minimum_role", return_value=True), \
             patch("apexbot.community.send_error", new=AsyncMock()) as error:
            self.assertFalse(asyncio_run(self.cog._management_ok(ctx)))
        error.assert_awaited_once()

    def test_suggestion_staff_decision_denied_without_lead_designer_and_status_stays_pending(self):
        cursor = self.db.execute("INSERT INTO suggestions(guild_id,channel_id,message_id,submitter_id,content,created_at,updated_at) VALUES('100','200','901','20','idea',?,?)", (utcnow(), utcnow()))
        suggestion_id = cursor.lastrowid
        ctx = SimpleNamespace(guild=SimpleNamespace(id=100), author=SimpleNamespace(id=44), send=AsyncMock())
        with patch("apexbot.community.has_minimum_role", return_value=False), \
             patch("apexbot.community.send_error", new=AsyncMock()):
            asyncio_run(self.cog.decide_suggestion(ctx, suggestion_id, "Approved"))
        self.assertEqual(self.db.one("SELECT status FROM suggestions WHERE id=?", (suggestion_id,))[0], "Pending")

    def test_suggestion_status_transitions_persist_actor_reason_and_history(self):
        cursor = self.db.execute("INSERT INTO suggestions(guild_id,channel_id,message_id,submitter_id,content,created_at,updated_at) VALUES('100','200','901','20','idea',?,?)", (utcnow(), utcnow()))
        suggestion_id = cursor.lastrowid
        ctx = SimpleNamespace(guild=SimpleNamespace(id=100), author=SimpleNamespace(id=44), send=AsyncMock())
        self.cog.refresh_suggestion = AsyncMock(return_value=True)
        with patch("apexbot.community.has_minimum_role", return_value=True), \
             patch("apexbot.community.audit", new=AsyncMock(return_value=True)):
            asyncio_run(self.cog.decide_suggestion(ctx, suggestion_id, "Approved"))
            asyncio_run(self.cog.decide_suggestion(ctx, suggestion_id, "Implemented"))
        row = self.db.one("SELECT status,decision_actor_id FROM suggestions WHERE id=?", (suggestion_id,))
        history = self.db.all("SELECT old_status,new_status,actor_id FROM suggestion_history WHERE suggestion_id=? ORDER BY id", (suggestion_id,))
        self.assertEqual((row["status"], row["decision_actor_id"]), ("Implemented", "44"))
        self.assertEqual([(item["old_status"], item["new_status"]) for item in history],
                         [("Pending", "Approved"), ("Approved", "Implemented")])

    def test_custom_trigger_execution_is_staff_gated_and_suppresses_mentions(self):
        row = {"id": "custom123", "trigger": "tradeinfo", "aliases_json": "[]", "title": "Trade Info",
               "description": "No @everyone pings", "fields_json": "[]", "colour": 0x8B5CF6,
               "image_url": None, "footer": "Apex"}
        channel = SimpleNamespace(send=AsyncMock())
        author = MagicMock(spec=discord.Member)
        author.bot = False
        author.id = 41
        message = SimpleNamespace(author=author, guild=SimpleNamespace(id=100), content="!tradeinfo", channel=channel)
        bot = SimpleNamespace(process_commands=AsyncMock(), get_command=MagicMock(return_value=None),
                               db=SimpleNamespace(one=MagicMock(return_value=row), all=MagicMock(return_value=[row])),
                               logger=MagicMock())
        with patch("apexbot.common.has_minimum_role", return_value=True), \
             patch("apexbot.common.audit", new=AsyncMock(return_value=True)):
            asyncio_run(ApexBot.on_message(bot, message))
        channel.send.assert_awaited_once()
        self.assertEqual(channel.send.await_args.kwargs["allowed_mentions"].to_dict(),
                         discord.AllowedMentions.none().to_dict())
        self.assertNotIn("@everyone", channel.send.await_args.kwargs["embed"].description)
        channel.send.reset_mock()
        with patch("apexbot.common.has_minimum_role", return_value=False):
            asyncio_run(ApexBot.on_message(bot, message))
        channel.send.assert_not_awaited()

    def test_bare_prefix_message_does_not_crash_or_dispatch_custom_trigger(self):
        author = MagicMock(spec=discord.Member)
        author.bot = False
        message = SimpleNamespace(author=author, guild=SimpleNamespace(id=100), content="!", channel=SimpleNamespace(send=AsyncMock()))
        bot = SimpleNamespace(process_commands=AsyncMock(), get_command=MagicMock(return_value=None),
                               db=SimpleNamespace(one=MagicMock(), all=MagicMock()), logger=MagicMock())
        asyncio_run(ApexBot.on_message(bot, message))
        bot.process_commands.assert_awaited_once_with(message)
        bot.db.one.assert_not_called()

    def test_safe_component_error_handler_returns_private_generic_message(self):
        bot = SimpleNamespace(logger=MagicMock())
        view = SafeView(bot=bot)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.guild = None
        interaction.user.id = 44
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()
        item = SimpleNamespace(custom_id="safe:test")
        with patch("apexbot.common.audit", new=AsyncMock()):
            asyncio_run(view.on_error(interaction, RuntimeError("password: never expose this"), item))
        response = interaction.response.send_message.await_args.args[0]
        self.assertNotIn("never expose", response)
        self.assertIn("audit log", response)
        self.assertTrue(bot.logger.error.called)


if __name__ == "__main__":
    unittest.main()
