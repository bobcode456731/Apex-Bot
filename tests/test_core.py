from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from apexbot.config import CHANNEL_IDS, ROLE_IDS
from apexbot.database import Database
from apexbot.policy import TICKET_MINIMUM_ROLE, meets_position_threshold
from apexbot.validation import (
    RESERVED_TRIGGERS,
    parse_colour,
    parse_duration,
    safe_text,
    validate_discord_id,
    validate_embed,
    validate_trigger,
    validate_url,
)
from apexbot.builders import parse_fields


class ValidationTests(unittest.TestCase):
    def test_prefix_duration_units_and_limit(self):
        self.assertEqual(parse_duration("10m"), 600)
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("28d"), 28 * 86400)
        for invalid in ("", "0s", "-5m", "5 minutes", "2mgarbage", "29d"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_duration(invalid)

    def test_colour_url_and_discord_ids(self):
        self.assertEqual(parse_colour("#8B5CF6"), 0x8B5CF6)
        with self.assertRaises(ValueError):
            parse_colour("purple")
        self.assertEqual(validate_url("https://example.com/image.png"), "https://example.com/image.png")
        with self.assertRaises(ValueError):
            validate_url("javascript:alert(1)")
        self.assertGreater(validate_discord_id("123456789012345"), 0)
        for invalid in ("123", "x" * 18, "1" * 23):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_discord_id(invalid)
        for invalid in ("0" * 18, "9" * 22):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_discord_id(invalid)

    def test_embed_limits_and_user_mention_sanitizing(self):
        validate_embed("A title", "Description", [{"name": "One", "value": "Text", "inline": True}])
        with self.assertRaises(ValueError):
            validate_embed("x" * 257, "", [])
        with self.assertRaises(ValueError):
            validate_embed("", "", [{"name": "n", "value": "v"}] * 26)
        self.assertEqual(safe_text("@everyone @here"), "@\u200beveryone @\u200bhere")
        self.assertIn("[REDACTED]", safe_text("password: do-not-log-this"))
        with self.assertRaises(ValueError):
            validate_embed("", "api_key: do-not-store-this", [])
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            validate_embed("", "", [{"name": " ", "value": "empty field"}])
        with self.assertRaisesRegex(ValueError, "Each embed field"):
            validate_embed("", "", [None])
        self.assertEqual(parse_fields('[{"name":"a","value":"b","inline":"false"}]')[0]["inline"], False)
        with self.assertRaisesRegex(ValueError, "inline"):
            parse_fields('[{"name":"a","value":"b","inline":"sometimes"}]')

    def test_trigger_syntax_reserved_builtins_and_user_owned_terms(self):
        self.assertEqual(validate_trigger("mm", ["rules"]), ["mm", "rules"])
        with self.assertRaises(ValueError):
            validate_trigger("help")
        with self.assertRaises(ValueError):
            validate_trigger("trade", ["trade"])
        self.assertIn("help", RESERVED_TRIGGERS)
        for name in ("mm", "rules", "tos", "vouch", "scam", "fees", "support"):
            self.assertNotIn(name, RESERVED_TRIGGERS)


class PolicyTests(unittest.TestCase):
    def test_role_threshold_uses_live_positions(self):
        self.assertFalse(meets_position_threshold([2], 3))
        self.assertTrue(meets_position_threshold([3], 3))
        self.assertTrue(meets_position_threshold([8], 3))
        self.assertTrue(meets_position_threshold([], 3, server_owner=True))

    def test_category_access_thresholds_are_configured_by_named_role(self):
        self.assertEqual(TICKET_MINIMUM_ROLE, {
            "middleman": "middleman", "support": "moderator", "rankup": "server_founder"
        })
        self.assertEqual(CHANNEL_IDS["rankup_panel"], 1552317076549533827)
        self.assertNotEqual(ROLE_IDS["middleman"], ROLE_IDS["moderator"])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.path = Path(self.temp.name) / "apex.sqlite3"
        self.db = Database(self.path)
        self.db.initialize()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_migration_and_process_restart_persistence(self):
        self.assertEqual(self.db.one("PRAGMA user_version")[0], 1)
        self.db.set_setting("autorole:10", "20")
        self.db.execute(
            "INSERT INTO warnings(guild_id,user_id,actor_id,reason,created_at) VALUES(?,?,?,?,?)",
            ("10", "30", "40", "test reason", "2026-01-01T00:00:00+00:00"),
        )
        self.db.close()
        reopened = Database(self.path)
        reopened.initialize()
        self.assertEqual(reopened.setting("autorole:10"), "20")
        self.assertEqual(reopened.one("SELECT reason FROM warnings WHERE guild_id='10'")["reason"], "test reason")
        reopened.close()
        self.db.connection = None

    def test_one_open_ticket_per_user_and_category(self):
        self.db.execute("INSERT INTO tickets(id,guild_id,category,opener_id,status,created_at) VALUES(?,?,?,?,?,?)",
                        ("a1", "10", "middleman", "20", "open", "2026-01-01T00:00:00+00:00"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO tickets(id,guild_id,category,opener_id,status,created_at) VALUES(?,?,?,?,?,?)",
                            ("a2", "10", "middleman", "20", "creating", "2026-01-01T00:00:00+00:00"))
        self.db.execute("INSERT INTO tickets(id,guild_id,category,opener_id,status,created_at) VALUES(?,?,?,?,?,?)",
                        ("b1", "10", "support", "20", "open", "2026-01-01T00:00:00+00:00"))
        self.db.execute("UPDATE tickets SET status='closed' WHERE id='a1'")
        self.db.execute("INSERT INTO tickets(id,guild_id,category,opener_id,status,created_at) VALUES(?,?,?,?,?,?)",
                        ("a3", "10", "middleman", "20", "creating", "2026-01-02T00:00:00+00:00"))

    def test_vote_uniqueness_giveaway_entries_and_no_preloaded_triggers(self):
        tables = {row["name"] for row in self.db.all("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("suggestion_votes", tables)
        self.assertIn("giveaway_entries", tables)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM custom_embeds")["n"], 0)
        self.db.execute("INSERT INTO suggestions(guild_id,channel_id,submitter_id,content,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                        ("10", "11", "20", "idea", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"))
        self.db.execute("INSERT INTO suggestion_votes(suggestion_id,user_id,vote,created_at) VALUES(1,'20',1,'2026-01-01')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO suggestion_votes(suggestion_id,user_id,vote,created_at) VALUES(1,'20',1,'2026-01-01')")
        self.db.execute("INSERT INTO suggestion_votes(suggestion_id,user_id,vote,created_at) VALUES(1,'30',1,'2026-01-01')")
        self.db.execute("INSERT INTO giveaways(id,guild_id,channel_id,host_id,prize,winner_count,created_at,ends_at,status) VALUES('g1','10','11','20','prize',1,'2026','2027','active')")
        self.db.execute("INSERT INTO giveaway_entries(giveaway_id,user_id,entered_at) VALUES('g1','30','2026')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO giveaway_entries(giveaway_id,user_id,entered_at) VALUES('g1','30','2026')")

    def test_audit_retention(self):
        self.db.audit("test", "old", guild_id=1)
        self.db.execute("UPDATE audit_events SET created_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual(self.db.prune_audit(180), 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM audit_events")[0], 0)


class BotConfigurationTests(unittest.TestCase):
    def test_required_prefix_and_intents_are_declared_without_app_commands(self):
        root = Path(__file__).resolve().parents[1]
        bot_source = (root / "apexbot" / "bot.py").read_text(encoding="utf-8")
        self.assertIn('command_prefix="!"', bot_source)
        self.assertIn("intents.message_content = True", bot_source)
        self.assertIn("intents.members = True", bot_source)
        self.assertNotIn("app_commands", bot_source)
        self.assertNotIn("tree.command", bot_source)


if __name__ == "__main__":
    unittest.main()
