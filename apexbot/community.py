"""Persistent giveaway and suggestion workflows, plus restricted audit inspection."""

from __future__ import annotations

import json
import random
import sqlite3
import uuid
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from .common import ERROR, PRIMARY, SafeView, audit, confirm, embed, has_minimum_role, require_native, require_role, send_error, send_private_log
from .config import CHANNEL_IDS, ROLE_IDS
from .database import utcnow
from .validation import parse_duration, safe_text


def iso_now() -> datetime:
    return datetime.now(timezone.utc)


class GiveawayEntryView(SafeView):
    def __init__(self, cog: "CommunityCog", giveaway_id: str):
        super().__init__(timeout=None, bot=cog.bot)
        self.cog = cog
        button = discord.ui.Button(label="Enter giveaway", emoji="🎉", style=discord.ButtonStyle.primary,
                                   custom_id=f"giveaway:enter:{giveaway_id}")
        button.callback = self.enter
        self.add_item(button)

    async def enter(self, interaction: discord.Interaction):
        giveaway_id = interaction.data["custom_id"].split(":", 2)[2]
        if interaction.user.bot:
            await interaction.response.send_message("Bots cannot enter giveaways.", ephemeral=True)
            return
        ended = False
        try:
            with self.cog.bot.db.transaction() as connection:
                row = connection.execute("SELECT guild_id,message_id,status,ends_at FROM giveaways WHERE id=?", (giveaway_id,)).fetchone()
                if (not row or str(interaction.guild_id) != row["guild_id"]
                        or str(getattr(interaction.message, "id", "")) != str(row["message_id"])
                        or row["status"] != "active" or datetime.fromisoformat(row["ends_at"]) <= iso_now()):
                    ended = True
                else:
                    connection.execute("INSERT INTO giveaway_entries(giveaway_id,user_id,entered_at) VALUES(?,?,?)",
                                       (giveaway_id, str(interaction.user.id), utcnow()))
                    entrants = [r["user_id"] for r in connection.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=? ORDER BY entered_at", (giveaway_id,))]
                    connection.execute("UPDATE giveaways SET entrants_json=? WHERE id=?", (json.dumps(entrants), giveaway_id))
        except sqlite3.IntegrityError:
            await interaction.response.send_message("You already entered this giveaway.", ephemeral=True)
            return
        except Exception:
            await interaction.response.send_message("The entry could not be recorded because the database is unavailable. Please try again later.", ephemeral=True)
            await self.cog.audit_giveaway(giveaway_id, "entry_failed", interaction.user.id, outcome="failure")
            return
        if ended:
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return
        await interaction.response.send_message("Your entry is recorded. Good luck!", ephemeral=True)
        await self.cog.audit_giveaway(giveaway_id, "entry", interaction.user.id)


class SuggestionView(SafeView):
    def __init__(self, cog: "CommunityCog", suggestion_id: int):
        super().__init__(timeout=None, bot=cog.bot)
        self.cog = cog
        for value, label, emoji in ((1, "Upvote", "👍"), (-1, "Downvote", "👎")):
            button = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary,
                                       custom_id=f"suggest:{value}:{suggestion_id}")
            button.callback = self.vote
            self.add_item(button)

    async def vote(self, interaction: discord.Interaction):
        _, vote, suggestion_id = interaction.data["custom_id"].split(":")
        await self.cog.record_vote(interaction, int(suggestion_id), int(vote))


class CommunityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.draw_loop.start()

    def cog_unload(self):
        self.draw_loop.cancel()

    def register_persistent_views(self):
        for row in self.bot.db.all("SELECT id FROM giveaways WHERE status='active'"):
            self.bot.add_view(GiveawayEntryView(self, row["id"]))
        for row in self.bot.db.all("SELECT id FROM suggestions WHERE message_id IS NOT NULL"):
            self.bot.add_view(SuggestionView(self, row["id"]))

    async def _management_ok(self, ctx: commands.Context) -> bool:
        if not has_minimum_role(ctx.author, "director_ops"):
            await send_error(ctx, "Giveaway management requires Director of Ops and above.")
            return False
        if not ctx.author.guild_permissions.manage_guild:
            await send_error(ctx, "Your Discord permissions must include Manage Server.")
            return False
        return True

    async def audit_giveaway(self, giveaway_id: str, action: str, actor_id: int | None, details: dict | None = None, outcome="success"):
        try:
            row = self.bot.db.one("SELECT * FROM giveaways WHERE id=?", (giveaway_id,))
            guild = self.bot.get_guild(int(row["guild_id"])) if row else None
            await audit(self.bot, "giveaway", action, guild=guild, actor_id=actor_id,
                        details={"giveaway_id": giveaway_id, **(details or {})}, outcome=outcome)
        except Exception as exc:
            self.bot.logger.error("Giveaway audit failed for %s/%s (%s)", giveaway_id, action, type(exc).__name__)

    @tasks.loop(seconds=30)
    async def draw_loop(self):
        try:
            rows = self.bot.db.all("SELECT id FROM giveaways WHERE status='active' AND ends_at<=?", (iso_now().isoformat(),))
        except Exception as exc:
            self.bot.logger.error("Scheduled giveaway scan failed (%s)", type(exc).__name__)
            return
        for row in rows:
            try:
                await self.end_giveaway(row["id"], actor_id=None, announce=True)
            except Exception as exc:
                self.bot.logger.error("Scheduled giveaway end failed (%s)", type(exc).__name__)
                await self.audit_giveaway(row["id"], "scheduled_end_failed", None, outcome="failure")

    @draw_loop.before_loop
    async def before_draw(self):
        await self.bot.wait_until_ready()

    async def end_giveaway(self, giveaway_id: str, *, actor_id: int | None, announce: bool) -> tuple[bool, str]:
        try:
            with self.bot.db.transaction() as connection:
                row = connection.execute("SELECT * FROM giveaways WHERE id=? AND status='active'", (giveaway_id,)).fetchone()
                if not row:
                    return False, "Active giveaway ID not found."
                entrants = [r["user_id"] for r in connection.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=? ORDER BY entered_at", (giveaway_id,))]
                winners = random.SystemRandom().sample(entrants, min(int(row["winner_count"]), len(entrants))) if entrants else []
                outcome = "no eligible entrants" if not winners else "winner(s) selected"
                updated = connection.execute("UPDATE giveaways SET status='ended',winners_json=?,outcome=? WHERE id=? AND status='active'",
                                             (json.dumps(winners), outcome, giveaway_id))
                if updated.rowcount != 1:
                    return False, "Giveaway was already ended or cancelled."
        except Exception as exc:
            await self.audit_giveaway(giveaway_id, "end_persistence_failed", actor_id,
                                      {"error_type": type(exc).__name__}, outcome="failure")
            return False, "Giveaway could not be ended because the database write failed."
        await self.audit_giveaway(giveaway_id, "end", actor_id, {"winners": winners, "entrant_count": len(entrants)})
        await self.audit_giveaway(giveaway_id, "winners_selected" if winners else "no_winner", actor_id,
                                  {"winners": winners, "entrant_count": len(entrants)})
        announcement_failed = False
        if announce:
            channel = self.bot.get_channel(int(row["channel_id"]))
            if channel:
                if row["message_id"]:
                    try:
                        original = await channel.fetch_message(int(row["message_id"]))
                        await original.edit(view=None)
                    except discord.HTTPException:
                        announcement_failed = True
                        await self.audit_giveaway(giveaway_id, "entry_control_disable_failed", actor_id, outcome="failure")
                if winners:
                    mentions = " ".join(f"<@{user_id}>" for user_id in winners)
                    text = f"Congratulations {mentions}! You won **{safe_text(row['prize'], 200)}**."
                    allowed = discord.AllowedMentions(users=[discord.Object(id=int(x)) for x in winners], roles=False, everyone=False)
                else:
                    text = f"The giveaway for **{safe_text(row['prize'], 200)}** ended with no eligible entrants, so there is no winner."
                    allowed = discord.AllowedMentions.none()
                try:
                    await channel.send(text, allowed_mentions=allowed)
                except discord.HTTPException:
                    announcement_failed = True
                    await self.audit_giveaway(giveaway_id, "winner_announcement_failed", actor_id, outcome="failure")
            else:
                announcement_failed = True
                await self.audit_giveaway(giveaway_id, "winner_announcement_channel_missing", actor_id, outcome="failure")
        result = f"Winners: {', '.join(winners)}" if winners else "Giveaway ended with no eligible entrants."
        if announcement_failed:
            result += " Discord could not complete every announcement update; staff should check the giveaway channel."
        return True, result

    async def _giveaway_row(self, ctx, giveaway_id: str):
        row = self.bot.db.one("SELECT * FROM giveaways WHERE id=? AND guild_id=?", (giveaway_id, str(ctx.guild.id)))
        if row is None:
            await send_error(ctx, "Giveaway ID not found in this server.")
        return row

    @commands.group(name="giveaway", invoke_without_command=True)
    @commands.guild_only()
    async def giveaway(self, ctx: commands.Context):
        await ctx.send(embed=embed("Giveaway commands", "`!giveaway start 1d 2 prize` • `!giveaway end ID` • `!giveaway reroll ID` • `!giveaway list` • `!giveaway cancel ID`", colour=PRIMARY))

    @giveaway.command(name="start")
    async def giveaway_start(self, ctx: commands.Context, duration: str, winner_count: int, *, prize: str):
        if not await self._management_ok(ctx):
            return
        channel = ctx.guild.get_channel(CHANNEL_IDS["giveaways"])
        if not isinstance(channel, discord.TextChannel):
            await send_error(ctx, f"Configured giveaway channel `{CHANNEL_IDS['giveaways']}` is missing or not text.")
            return
        if ctx.guild.me is None:
            await send_error(ctx, "The bot member record is unavailable; no giveaway was started.")
            return
        if not channel.permissions_for(ctx.guild.me).view_channel or not channel.permissions_for(ctx.guild.me).send_messages or not channel.permissions_for(ctx.guild.me).embed_links:
            await send_error(ctx, "The bot needs View Channel, Send Messages, and Embed Links in the configured giveaway channel.")
            return
        try:
            seconds = parse_duration(duration)
        except ValueError as exc:
            await send_error(ctx, str(exc))
            return
        if not 1 <= winner_count <= 20 or not prize.strip() or len(prize) > 200:
            await send_error(ctx, "Winner count must be 1–20 and prize must be 1–200 characters.")
            return
        giveaway_id = uuid.uuid4().hex[:10]
        end = iso_now().timestamp() + seconds
        ends_at = datetime.fromtimestamp(end, timezone.utc).isoformat()
        message = None
        try:
            with self.bot.db.transaction() as connection:
                connection.execute("INSERT INTO giveaways(id,guild_id,channel_id,host_id,prize,winner_count,created_at,ends_at,status) VALUES(?,?,?,?,?,?,?,?,?)",
                                   (giveaway_id, str(ctx.guild.id), str(channel.id), str(ctx.author.id), safe_text(prize, 200), winner_count, utcnow(), ends_at, "active"))
            out = embed("🎉 Apex Giveaway", f"Prize: **{safe_text(prize, 200)}**\nWinners: **{winner_count}**\nHosted by <@{ctx.author.id}>\nEnds {discord.utils.format_dt(datetime.fromisoformat(ends_at), style='R')}\nEntries: **0**\nGiveaway ID: `{giveaway_id}`", colour=PRIMARY,
                        footer="Apex MiddleMan Services • One entry per member")
            message = await channel.send(embed=out, view=GiveawayEntryView(self, giveaway_id), allowed_mentions=discord.AllowedMentions(users=[ctx.author]))
            with self.bot.db.transaction() as connection:
                updated = connection.execute("UPDATE giveaways SET message_id=? WHERE id=? AND status='active'", (str(message.id), giveaway_id))
                if updated.rowcount != 1:
                    raise RuntimeError("Giveaway state changed before its announcement could be saved.")
        except (discord.Forbidden, discord.HTTPException) as exc:
            self.bot.db.execute("DELETE FROM giveaways WHERE id=?", (giveaway_id,))
            await audit(self.bot, "giveaway", "create_failed", guild=ctx.guild, actor_id=ctx.author.id,
                        details={"giveaway_id": giveaway_id, "error_type": type(exc).__name__}, outcome="failure")
            await send_error(ctx, f"Giveaway was not started because Discord did not confirm the announcement ({type(exc).__name__}).")
            return
        except Exception as exc:
            if message is not None:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
            self.bot.db.execute("DELETE FROM giveaways WHERE id=?", (giveaway_id,))
            await send_error(ctx, f"Giveaway setup could not be saved ({type(exc).__name__}); duplicate-message cleanup was attempted.")
            await audit(self.bot, "giveaway", "create_failed", guild=ctx.guild, actor_id=ctx.author.id,
                        details={"giveaway_id": giveaway_id}, outcome="failure")
            return
        await audit(self.bot, "giveaway", "created", guild=ctx.guild, actor_id=ctx.author.id,
                    details={"giveaway_id": giveaway_id, "prize": safe_text(prize, 200), "duration_seconds": seconds, "winner_count": winner_count}, outcome="success")
        await ctx.send(embed=embed("Giveaway started", f"ID `{giveaway_id}` • published in {channel.mention}.", colour=PRIMARY))

    @giveaway.command(name="end")
    async def giveaway_end(self, ctx: commands.Context, giveaway_id: str):
        if not await self._management_ok(ctx):
            return
        row = await self._giveaway_row(ctx, giveaway_id)
        if not row:
            return
        ok, result = await self.end_giveaway(giveaway_id, actor_id=ctx.author.id, announce=True)
        if not ok:
            await send_error(ctx, result)
        else:
            await ctx.send(embed=embed("Giveaway ended", result, colour=PRIMARY))

    @giveaway.command(name="cancel")
    async def giveaway_cancel(self, ctx: commands.Context, giveaway_id: str):
        if not await self._management_ok(ctx):
            return
        row = await self._giveaway_row(ctx, giveaway_id)
        if not row:
            return
        try:
            with self.bot.db.transaction() as connection:
                updated = connection.execute("UPDATE giveaways SET status='cancelled',outcome='cancelled; no winner' WHERE id=? AND guild_id=? AND status='active'",
                                             (giveaway_id, str(ctx.guild.id)))
        except Exception as exc:
            await send_error(ctx, f"Giveaway cancellation could not be saved ({type(exc).__name__}).")
            await self.audit_giveaway(giveaway_id, "cancel_failed", ctx.author.id, outcome="failure")
            return
        if updated.rowcount != 1:
            await send_error(ctx, "Only an active giveaway can be cancelled.")
            return
        await audit(self.bot, "giveaway", "cancelled", guild=ctx.guild, actor_id=ctx.author.id,
                    details={"giveaway_id": giveaway_id}, outcome="success")
        channel = ctx.guild.get_channel(int(row["channel_id"]))
        announcement_failed = False
        if channel and row["message_id"]:
            try:
                message = await channel.fetch_message(int(row["message_id"]))
                await message.edit(view=None)
                await channel.send(f"Giveaway `{giveaway_id}` was cancelled. No winner was selected.", allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as exc:
                announcement_failed = True
                await self.audit_giveaway(giveaway_id, "cancel_announcement_failed", ctx.author.id,
                                          {"error_type": type(exc).__name__}, outcome="failure")
        else:
            announcement_failed = True
            await self.audit_giveaway(giveaway_id, "cancel_announcement_channel_missing", ctx.author.id, outcome="failure")
        suffix = " Discord could not update the giveaway announcement; staff should remove its entry button." if announcement_failed else ""
        await ctx.send(embed=embed("Giveaway cancelled", "No winner was selected." + suffix, colour=PRIMARY))

    @giveaway.command(name="reroll")
    async def giveaway_reroll(self, ctx: commands.Context, giveaway_id: str):
        if not await self._management_ok(ctx):
            return
        row = await self._giveaway_row(ctx, giveaway_id)
        if not row:
            return
        no_remaining = False
        try:
            with self.bot.db.transaction() as connection:
                row = connection.execute("SELECT * FROM giveaways WHERE id=? AND guild_id=? AND status='ended'", (giveaway_id, str(ctx.guild.id))).fetchone()
                if not row:
                    winner = None
                    no_remaining = True
                else:
                    entrants = [r["user_id"] for r in connection.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))]
                    previous = set(json.loads(row["winners_json"]))
                    remaining = [x for x in entrants if x not in previous]
                    if not remaining:
                        winner = None
                        no_remaining = True
                    else:
                        winner = random.SystemRandom().choice(remaining)
                        winners = [*previous, winner]
                        connection.execute("UPDATE giveaways SET winners_json=?,outcome=? WHERE id=? AND status='ended'",
                                           (json.dumps(winners), "rerolled winner selected", giveaway_id))
        except Exception as exc:
            await self.audit_giveaway(giveaway_id, "reroll_persistence_failed", ctx.author.id,
                                      {"error_type": type(exc).__name__}, outcome="failure")
            await send_error(ctx, "The reroll was not selected because the database write failed.")
            return
        if no_remaining or winner is None:
            await send_error(ctx, "No eligible entrants remain or the giveaway is no longer ended.")
            return
        await audit(self.bot, "giveaway", "reroll", guild=ctx.guild, actor_id=ctx.author.id,
                    target_id=int(winner), details={"giveaway_id": giveaway_id}, outcome="success")
        channel = ctx.guild.get_channel(int(row["channel_id"]))
        announcement_failed = False
        if channel:
            try:
                await channel.send(f"Reroll for giveaway `{giveaway_id}`: <@{winner}> wins **{safe_text(row['prize'], 200)}**.",
                                   allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=int(winner))]))
            except discord.HTTPException as exc:
                announcement_failed = True
                await audit(self.bot, "giveaway", "reroll_announcement_failed", guild=ctx.guild, actor_id=ctx.author.id,
                            target_id=int(winner), details={"giveaway_id": giveaway_id, "error_type": type(exc).__name__}, outcome="failure")
        else:
            announcement_failed = True
            await audit(self.bot, "giveaway", "reroll_channel_missing", guild=ctx.guild, actor_id=ctx.author.id,
                        target_id=int(winner), details={"giveaway_id": giveaway_id}, outcome="failure")
        suffix = " The reroll result was saved, but its announcement failed." if announcement_failed else ""
        await ctx.send(embed=embed("Giveaway rerolled", f"New winner: <@{winner}>.{suffix}", colour=PRIMARY),
                       allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=int(winner))]))

    @giveaway.command(name="list")
    async def giveaway_list(self, ctx: commands.Context):
        if not await self._management_ok(ctx):
            return
        rows = self.bot.db.all("SELECT id,prize,status,ends_at,winner_count FROM giveaways WHERE guild_id=? ORDER BY created_at DESC LIMIT 25", (str(ctx.guild.id),))
        out = embed("Recent giveaways", colour=PRIMARY)
        out.description = "\n".join(f"`{r['id']}` • {r['status']} • {safe_text(r['prize'], 100)} • {r['winner_count']} winner(s) • ends {r['ends_at'][:16]} UTC" for r in rows) or "No giveaways recorded."
        await ctx.send(embed=out)

    @commands.command(name="suggest")
    @commands.guild_only()
    async def suggest(self, ctx: commands.Context, *, idea: str):
        idea = safe_text(idea.strip(), 3000)
        if not idea:
            await send_error(ctx, "Suggestion text cannot be empty.")
            return
        channel = ctx.guild.get_channel(CHANNEL_IDS["suggestions"])
        if not isinstance(channel, discord.TextChannel):
            await send_error(ctx, f"Configured suggestion channel `{CHANNEL_IDS['suggestions']}` is missing or not text; no suggestion was submitted.")
            return
        bot_permissions = channel.permissions_for(ctx.guild.me)
        if not bot_permissions.view_channel or not bot_permissions.send_messages or not bot_permissions.embed_links:
            await send_error(ctx, "The bot needs View Channel, Send Messages, and Embed Links in the configured suggestions channel; no suggestion was submitted.")
            return
        # Prefix commands include the submitted text in the source message. Delete it
        # before placing the idea in the public suggestion channel to preserve identity.
        if not ctx.channel.permissions_for(ctx.guild.me).manage_messages:
            await send_error(ctx, "Anonymous suggestions require the bot's Manage Messages permission here so the source message can be removed.")
            return
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            await send_error(ctx, "I could not remove the command message, so the suggestion was not posted publicly.")
            return
        now = utcnow()
        try:
            with self.bot.db.transaction() as connection:
                cursor = connection.execute("INSERT INTO suggestions(guild_id,channel_id,submitter_id,content,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                                            (str(ctx.guild.id), str(channel.id), str(ctx.author.id), idea, now, now))
                suggestion_id = int(cursor.lastrowid)
        except Exception as exc:
            self.bot.logger.error("Suggestion persistence failed (%s)", type(exc).__name__)
            await audit(self.bot, "suggestion", "submission_persistence_failed", guild=ctx.guild,
                        actor_id=ctx.author.id, outcome="failure")
            try:
                await ctx.author.send("Your command was removed, but the suggestion could not be recorded. Please try again later.")
            except discord.HTTPException:
                await ctx.send("Your command was removed, but the suggestion could not be recorded. No suggestion was posted.",
                               allowed_mentions=discord.AllowedMentions.none())
            return
        out = embed(f"Suggestion #{suggestion_id}", idea, colour=PRIMARY,
                    footer="Apex MiddleMan Services • Suggestions • Submitter identity is private")
        try:
            msg = await channel.send(embed=out, view=SuggestionView(self, suggestion_id), allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException) as exc:
            with self.bot.db.transaction() as connection:
                connection.execute("DELETE FROM suggestions WHERE id=?", (suggestion_id,))
            await audit(self.bot, "suggestion", "submission_publish_failed", guild=ctx.guild,
                        actor_id=ctx.author.id, target_id=suggestion_id,
                        details={"error_type": type(exc).__name__}, outcome="failure")
            try:
                await ctx.author.send(f"Suggestion was not submitted because Discord did not confirm posting ({type(exc).__name__}).")
            except discord.HTTPException:
                await ctx.send("Suggestion was not submitted because Discord did not confirm posting.",
                               allowed_mentions=discord.AllowedMentions.none())
            return
        try:
            with self.bot.db.transaction() as connection:
                updated = connection.execute("UPDATE suggestions SET message_id=? WHERE id=? AND message_id IS NULL",
                                             (str(msg.id), suggestion_id))
                if updated.rowcount != 1:
                    raise RuntimeError("Suggestion record changed before publication was saved.")
                connection.execute("INSERT INTO suggestion_history(suggestion_id,actor_id,new_status,reason,created_at) VALUES(?,?,?,?,?)",
                                   (suggestion_id, str(ctx.author.id), "Pending", "submitted", utcnow()))
        except Exception as exc:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass
            with self.bot.db.transaction() as connection:
                connection.execute("DELETE FROM suggestions WHERE id=?", (suggestion_id,))
            await audit(self.bot, "suggestion", "submission_state_save_failed", guild=ctx.guild,
                        actor_id=ctx.author.id, target_id=suggestion_id,
                        details={"error_type": type(exc).__name__}, outcome="failure")
            try:
                await ctx.author.send("Your suggestion could not be finalized and was not retained. Please try again later.")
            except discord.HTTPException:
                await ctx.send("Your suggestion could not be finalized and was not retained.",
                               allowed_mentions=discord.AllowedMentions.none())
            return
        await audit(self.bot, "suggestion", "submitted", guild=ctx.guild, actor_id=ctx.author.id,
                    target_id=suggestion_id, details={"suggestion_id": suggestion_id}, outcome="success")
        try:
            await ctx.author.send(f"Your suggestion was submitted anonymously as **#{suggestion_id}** in <#{CHANNEL_IDS['suggestions']}>.")
        except discord.HTTPException:
            await ctx.send(embed=embed("Suggestion submitted", f"Your suggestion was submitted anonymously to <#{CHANNEL_IDS['suggestions']}>. The suggestion ID could not be sent by DM.", colour=PRIMARY), allowed_mentions=discord.AllowedMentions.none())

    async def record_vote(self, interaction: discord.Interaction, suggestion_id: int, vote: int):
        if vote not in {-1, 1} or interaction.guild_id is None:
            await interaction.response.send_message("That suggestion vote control is invalid.", ephemeral=True)
            return
        row = self.bot.db.one("SELECT * FROM suggestions WHERE id=? AND guild_id=?", (suggestion_id, str(interaction.guild_id)))
        if not row or not row["message_id"] or str(getattr(interaction.message, "id", "")) != str(row["message_id"]):
            await interaction.response.send_message("Suggestion not found.", ephemeral=True)
            return
        try:
            self.bot.db.execute("INSERT INTO suggestion_votes(suggestion_id,user_id,vote,created_at) VALUES(?,?,?,?)",
                                (suggestion_id, str(interaction.user.id), vote, utcnow()))
        except sqlite3.IntegrityError:
            await interaction.response.send_message("You have already voted on this suggestion.", ephemeral=True)
            return
        except Exception:
            await interaction.response.send_message("Your vote could not be recorded because the database is unavailable.", ephemeral=True)
            await audit(self.bot, "suggestion", "vote_failed", guild=interaction.guild, actor_id=interaction.user.id,
                        target_id=suggestion_id, outcome="failure")
            return
        await interaction.response.send_message("Your vote was recorded.", ephemeral=True)
        await audit(self.bot, "suggestion", "vote", guild=interaction.guild, actor_id=interaction.user.id,
                    target_id=suggestion_id, details={"vote": "up" if vote == 1 else "down"}, outcome="success")
        await self.refresh_suggestion(suggestion_id)

    async def refresh_suggestion(self, suggestion_id: int) -> bool:
        try:
            row = self.bot.db.one("SELECT * FROM suggestions WHERE id=?", (suggestion_id,))
            if not row or not row["message_id"]:
                return False
            votes = self.bot.db.all("SELECT vote,COUNT(*) AS count FROM suggestion_votes WHERE suggestion_id=? GROUP BY vote", (suggestion_id,))
            counts = {r["vote"]: r["count"] for r in votes}
        except Exception as exc:
            self.bot.logger.error("Suggestion refresh state read failed (%s)", type(exc).__name__)
            await audit(self.bot, "suggestion", "refresh_state_read_failed", target_id=suggestion_id,
                        details={"error_type": type(exc).__name__}, outcome="failure")
            return False
        channel = self.bot.get_channel(int(row["channel_id"]))
        if not channel:
            await audit(self.bot, "suggestion", "message_channel_missing", guild=self.bot.get_guild(int(row["guild_id"])),
                        target_id=suggestion_id, details={"channel_id": row["channel_id"]}, outcome="failure")
            return False
        try:
            msg = await channel.fetch_message(int(row["message_id"]))
            out = embed(f"Suggestion #{suggestion_id} • {row['status']}", row["content"], colour=PRIMARY,
                        footer=f"Apex MiddleMan Services • 👍 {counts.get(1, 0)} • 👎 {counts.get(-1, 0)}")
            await msg.edit(embed=out, view=SuggestionView(self, suggestion_id))
        except discord.HTTPException as exc:
            await audit(self.bot, "suggestion", "message_refresh_failed", guild=self.bot.get_guild(int(row["guild_id"])),
                        target_id=suggestion_id, details={"error_type": type(exc).__name__}, outcome="failure")
            return False
        return True

    @commands.group(name="suggestion", invoke_without_command=True)
    @commands.guild_only()
    async def suggestion(self, ctx: commands.Context):
        await ctx.send(embed=embed("Suggestion status", "Use `!suggestion status ID` to check a suggestion.", colour=PRIMARY))

    @suggestion.command(name="status")
    async def suggestion_status(self, ctx: commands.Context, suggestion_id: int):
        row = self.bot.db.one("SELECT id,status,created_at,updated_at FROM suggestions WHERE id=? AND guild_id=?", (suggestion_id, str(ctx.guild.id)))
        if not row:
            await send_error(ctx, "Suggestion ID not found.")
            return
        await ctx.send(embed=embed(f"Suggestion #{row['id']}", f"Status: **{row['status']}**\nSubmitted: {row['created_at'][:10]}\nLast updated: {row['updated_at'][:10]}", colour=PRIMARY))

    async def decide_suggestion(self, ctx: commands.Context, suggestion_id: int, new_status: str, reason: str = ""):
        if not has_minimum_role(ctx.author, "lead_designer"):
            await send_error(ctx, "Suggestion review requires Lead Designer and above.")
            return
        allowed = {"Pending": {"Approved", "Declined"}, "Approved": {"Implemented"}}
        reason = safe_text(reason, 800)
        try:
            with self.bot.db.transaction() as connection:
                row = connection.execute("SELECT * FROM suggestions WHERE id=? AND guild_id=?", (suggestion_id, str(ctx.guild.id))).fetchone()
                if not row:
                    failure = "Suggestion ID not found."
                elif new_status not in allowed.get(row["status"], set()):
                    failure = f"Cannot move a **{row['status']}** suggestion to **{new_status}**."
                else:
                    failure = None
                    connection.execute("UPDATE suggestions SET status=?,decision_actor_id=?,reason=?,updated_at=? WHERE id=?",
                                       (new_status, str(ctx.author.id), reason, utcnow(), suggestion_id))
                    connection.execute("INSERT INTO suggestion_history(suggestion_id,actor_id,old_status,new_status,reason,created_at) VALUES(?,?,?,?,?,?)",
                                       (suggestion_id, str(ctx.author.id), row["status"], new_status, reason, utcnow()))
        except Exception as exc:
            await audit(self.bot, "suggestion", "decision_persistence_failed", guild=ctx.guild,
                        actor_id=ctx.author.id, target_id=suggestion_id,
                        details={"new_status": new_status, "error_type": type(exc).__name__}, outcome="failure")
            await send_error(ctx, f"Suggestion status could not be saved ({type(exc).__name__}).")
            return
        if failure:
            await send_error(ctx, failure)
            return
        await audit(self.bot, "suggestion", new_status.lower(), guild=ctx.guild, actor_id=ctx.author.id,
                    target_id=suggestion_id, details={"old_status": row["status"], "new_status": new_status, "reason": reason}, outcome="success")
        refreshed = await self.refresh_suggestion(suggestion_id)
        suffix = " The saved status is correct, but Discord did not refresh the suggestion post." if not refreshed else ""
        await ctx.send(embed=embed("Suggestion updated", f"Suggestion **#{suggestion_id}** is now **{new_status}**.{suffix}", colour=PRIMARY))

    @suggestion.command(name="approve")
    async def suggestion_approve(self, ctx: commands.Context, suggestion_id: int):
        await self.decide_suggestion(ctx, suggestion_id, "Approved")

    @suggestion.command(name="deny")
    async def suggestion_deny(self, ctx: commands.Context, suggestion_id: int, *, reason: str = ""):
        await self.decide_suggestion(ctx, suggestion_id, "Declined", reason)

    @suggestion.command(name="implement")
    async def suggestion_implement(self, ctx: commands.Context, suggestion_id: int):
        await self.decide_suggestion(ctx, suggestion_id, "Implemented")

    @commands.group(name="audit", invoke_without_command=True)
    @commands.guild_only()
    async def audit_group(self, ctx: commands.Context):
        await ctx.send(embed=embed("Audit inspection", "Use `!audit status`, `!audit recent`, `!audit errors`, or `!audit config`.", colour=PRIMARY))

    async def _audit_permission(self, ctx):
        if not has_minimum_role(ctx.author, "server_founder"):
            await send_error(ctx, "Audit inspection requires Server Founder and above.")
            return False
        return True

    @audit_group.command(name="status")
    async def audit_status(self, ctx: commands.Context):
        if not await self._audit_permission(ctx):
            return
        db = "ready" if self.bot.db.connection else "unavailable"
        out = embed("Audit status", f"Database: **{db}**\nDiscord latency: **{self.bot.latency * 1000:.0f} ms**\nGuilds connected: **{len(self.bot.guilds)}**\nAudit destination: <#{CHANNEL_IDS['audit_logs']}>\nLog retention setting: **{self.bot.settings.log_retention_days} days**", colour=PRIMARY)
        await send_private_log(ctx, out, channel_key="audit_logs", label="Audit status")

    @audit_group.command(name="recent")
    async def audit_recent(self, ctx: commands.Context):
        if not await self._audit_permission(ctx):
            return
        rows = self.bot.db.all("SELECT category,action,actor_id,target_id,outcome,created_at FROM audit_events WHERE guild_id=? ORDER BY id DESC LIMIT 15", (str(ctx.guild.id),))
        out = embed("Recent audit events", colour=PRIMARY)
        out.description = "\n".join(f"`{r['created_at']}` • **{r['category']}/{r['action']}** • {r['outcome']} • actor `{r['actor_id'] or 'system'}` • target `{r['target_id'] or '—'}`" for r in rows) or "No recent events."
        await send_private_log(ctx, out, channel_key="audit_logs", label="Recent audit events")

    @audit_group.command(name="errors")
    async def audit_errors(self, ctx: commands.Context):
        if not await self._audit_permission(ctx):
            return
        rows = self.bot.db.all("SELECT category,action,actor_id,details_json,created_at FROM audit_events WHERE guild_id=? AND outcome='failure' ORDER BY id DESC LIMIT 15", (str(ctx.guild.id),))
        out = embed("Recent errors", colour=ERROR)
        out.description = "\n".join(f"`{r['created_at']}` • `{r['category']}/{r['action']}` • actor `{r['actor_id'] or 'system'}` • `{safe_text(r['details_json'], 250)}`" for r in rows) or "No recorded failures."
        await send_private_log(ctx, out, channel_key="audit_logs", label="Recent errors")

    @audit_group.command(name="config")
    async def audit_config(self, ctx: commands.Context):
        if not await self._audit_permission(ctx):
            return
        out = embed("Configured Discord IDs", "Non-secret configuration only. Token and host secrets are never shown.", colour=PRIMARY)
        out.add_field(name="Channels", value="\n".join(f"{key}: `{value}`" for key, value in CHANNEL_IDS.items()), inline=False)
        out.add_field(name="Roles", value="\n".join(f"{key}: `{value}`" for key, value in ROLE_IDS.items()), inline=False)
        runtime = []
        for key in (f"autorole:{ctx.guild.id}", f"welcome:{ctx.guild.id}"):
            value = self.bot.db.setting(key)
            runtime.append(f"{key.split(':')[0]}: `{value or 'default/off'}`")
        out.add_field(name="Runtime settings", value="\n".join(runtime), inline=False)
        await send_private_log(ctx, out, channel_key="audit_logs", label="Configuration report")
