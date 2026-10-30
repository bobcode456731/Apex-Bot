"""Bot startup, intents, persistent database, and custom trigger dispatch."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from . import __version__
from .config import ROLE_IDS, Settings
from .database import Database
from .validation import safe_text


class ApexBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, help_command=None, case_insensitive=True)
        self.settings = settings
        self.db = Database(settings.database_path)
        self.started_at = discord.utils.utcnow()
        self.logger = logging.getLogger("apexbot")

    async def setup_hook(self) -> None:
        self.db.initialize()
        removed_audit_rows = self.db.prune_audit(self.settings.log_retention_days)
        self.logger.info("Applied audit log retention: removed %s expired records", removed_audit_rows)
        from . import community, core, moderation, server, tickets
        await self.add_cog(core.CoreCog(self))
        await self.add_cog(moderation.ModerationCog(self))
        await self.add_cog(server.ServerCog(self))
        ticket_cog = tickets.TicketCog(self)
        await self.add_cog(ticket_cog)
        community_cog = community.CommunityCog(self)
        await self.add_cog(community_cog)
        from .builders import BuilderCog
        builder_cog = BuilderCog(self)
        await self.add_cog(builder_cog)
        # Views use stable custom IDs and rows loaded from persistent storage.
        ticket_cog.register_persistent_views()
        community_cog.register_persistent_views()
        builder_cog.register_persistent_views()

    async def on_ready(self):
        self.logger.info("Connected as %s (%s); discord.py=%s", self.user, self.user.id, discord.__version__)

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        await self.process_commands(message)
        # Custom educational embeds execute only for Middleman role and above.
        from .common import has_minimum_role
        if not isinstance(message.author, discord.Member) or not has_minimum_role(message.author, "middleman"):
            return
        content = message.content.strip()
        if not content.startswith("!"):
            return
        command_parts = content[1:].split(maxsplit=1)
        if not command_parts:
            return
        trigger = command_parts[0].lower()
        if self.get_command(trigger):
            return
        row = self.db.one(
            "SELECT * FROM custom_embeds WHERE guild_id=? AND enabled=1",
            (str(message.guild.id),),
        )
        if row is None:
            return
        import json
        if trigger != row["trigger"] and trigger not in json.loads(row["aliases_json"]):
            row = next((r for r in self.db.all(
                "SELECT * FROM custom_embeds WHERE guild_id=? AND enabled=1", (str(message.guild.id),)
            ) if trigger == r["trigger"] or trigger in json.loads(r["aliases_json"])), None)
        if row is None:
            return
        from .common import PRIMARY
        out = discord.Embed(title=row["title"] or None, description=safe_text(row["description"], 4096),
                            colour=int(row["colour"]))
        for field in json.loads(row["fields_json"]):
            out.add_field(name=safe_text(field["name"], 256), value=safe_text(field["value"], 1024), inline=field.get("inline", False))
        if row["image_url"]:
            out.set_image(url=row["image_url"])
        if row["footer"]:
            out.set_footer(text=safe_text(row["footer"], 2048))
        try:
            await message.channel.send(embed=out, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            self.logger.error("Custom embed trigger could not be published (%s)", type(exc).__name__)
            from .common import audit
            await audit(self, "embed", "trigger_publish_failed", guild=message.guild,
                        actor_id=message.author.id, target_id=row["id"],
                        details={"trigger": trigger, "error_type": type(exc).__name__}, outcome="failure")
            return
        from .common import audit
        await audit(self, "embed", "trigger_published", guild=message.guild,
                    actor_id=message.author.id, target_id=row["id"],
                    details={"trigger": trigger}, outcome="success")


def run(settings: Settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.token:
        raise SystemExit("DISCORD_TOKEN is missing. Copy .env.example to .env and set the token in your host secrets.")
    bot = ApexBot(settings)
    try:
        bot.run(settings.token, log_handler=None)
    finally:
        bot.db.close()
