"""Persistent moderation actions with confirmation and hierarchy checks."""

from __future__ import annotations

import json
import re
from datetime import timedelta

import discord
from discord.ext import commands

from .common import (
    ConfirmView, ERROR, PRIMARY, audit, can_bot_manage_member, confirm, embed,
    moderation_log, require_native, require_role, send_error, send_private_log,
)
from .database import utcnow
from .validation import parse_duration, safe_text, validate_discord_id


def purge_message_filter(amount: int, control_ids: set[int | None]):
    """Exclude command/confirmation controls and cap deletes at the requested count."""
    remaining = max(0, amount)

    def allowed(message: discord.Message) -> bool:
        nonlocal remaining
        if message.id in control_ids or remaining <= 0:
            return False
        remaining -= 1
        return True

    return allowed


class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def dm_member(self, member: discord.Member, action: str, reason: str) -> str:
        try:
            await member.send(embed=embed(f"Apex moderation notice • {action}", f"Reason: {safe_text(reason, 700)}", colour=ERROR))
            return "sent"
        except (discord.Forbidden, discord.HTTPException):
            return "failed"

    def record_case(self, guild_id: int, action: str, actor_id: int, target_id: int | str,
                    reason: str, outcome: str, dm_result: str) -> int:
        try:
            cur = self.bot.db.execute(
                "INSERT INTO moderation_cases(guild_id,action,actor_id,target_id,reason,outcome,dm_result,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (str(guild_id), action, str(actor_id), str(target_id), safe_text(reason, 1000), outcome, dm_result, utcnow()),
            )
            return int(cur.lastrowid)
        except Exception:
            self.bot.logger.error("Moderation case record failed for action %s", action)
            return 0

    @staticmethod
    def case_note(case_id: int) -> str:
        return f"Case `M-{case_id}`." if case_id else "Action succeeded, but the case record could not be saved."

    async def require_target(self, ctx: commands.Context, member: discord.Member) -> bool:
        ok, message = can_bot_manage_member(ctx.guild, ctx.author, member)
        if not ok:
            await send_error(ctx, message)
        return ok

    async def log(self, ctx, action, member_or_id, reason, outcome, dm_result="not_applicable", case_id=None):
        logged = await moderation_log(self.bot, ctx.guild, action=action, actor_id=ctx.author.id,
                                      target_id=getattr(member_or_id, "id", member_or_id), reason=reason,
                                      outcome=outcome, dm_result=dm_result, case_id=case_id)
        if not logged:
            await send_error(ctx, f"The moderation action outcome was **{outcome}**, but its audit record did not reach every configured log destination. Staff should inspect `!audit errors`.")
        return logged

    @commands.command(name="warn")
    @commands.guild_only()
    @require_role("moderator")
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
        if not await self.require_target(ctx, member):
            return
        reason = safe_text(reason, 1000)
        try:
            cur = self.bot.db.execute(
                "INSERT INTO warnings(guild_id,user_id,actor_id,reason,created_at) VALUES(?,?,?,?,?)",
                (str(ctx.guild.id), str(member.id), str(ctx.author.id), reason, utcnow()),
            )
            case_id = int(cur.lastrowid)
        except Exception:
            self.bot.logger.error("Warning database write failed")
            await audit(self.bot, "moderation", "warn", guild=ctx.guild, actor_id=ctx.author.id,
                        target_id=member.id, details={"reason": reason}, outcome="failure", channel_key="moderation_logs")
            await send_error(ctx, "The warning was not recorded, so no warning success is reported.")
            return
        dm_result = await self.dm_member(member, "Warning", reason)
        await self.log(ctx, "warn", member, reason, "success", dm_result, case_id)
        await ctx.send(embed=embed("Warning recorded", f"{member.mention} received warning case `W-{case_id}`. DM: **{dm_result}**.", colour=PRIMARY))

    @commands.command(name="warnings")
    @commands.guild_only()
    @require_role("lead_designer")
    async def warnings(self, ctx: commands.Context, member: discord.Member):
        rows = self.bot.db.all(
            "SELECT id,actor_id,reason,created_at FROM warnings WHERE guild_id=? AND user_id=? AND archived_at IS NULL ORDER BY id DESC LIMIT 20",
            (str(ctx.guild.id), str(member.id)),
        )
        out = embed(f"Active warnings • {member}", colour=PRIMARY)
        if not rows:
            out.description = "No active warnings."
        else:
            out.description = "\n".join(f"`W-{r['id']}` • {r['created_at'][:10]} • staff `{r['actor_id']}`\n{safe_text(r['reason'], 250)}" for r in rows)
        await send_private_log(ctx, out, channel_key="moderation_logs", label="Warning history")

    @commands.command(name="clearwarnings")
    @commands.guild_only()
    @require_role("director_ops")
    async def clearwarnings(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
        if not await self.require_target(ctx, member):
            return
        if not await confirm(ctx, f"Archive all active warnings for {member.mention}? This cannot be undone by this command."):
            return
        reason = safe_text(reason, 1000)
        try:
            with self.bot.db.transaction() as connection:
                cur = connection.execute(
                    "UPDATE warnings SET archived_at=? WHERE guild_id=? AND user_id=? AND archived_at IS NULL",
                    (utcnow(), str(ctx.guild.id), str(member.id)),
                )
                count = cur.rowcount
        except Exception as exc:
            await send_error(ctx, f"Warnings were not archived because the database write failed ({type(exc).__name__}).")
            await audit(self.bot, "moderation", "clearwarnings", guild=ctx.guild, actor_id=ctx.author.id,
                        target_id=member.id, details={"reason": reason, "error_type": type(exc).__name__},
                        outcome="failure", channel_key="moderation_logs")
            return
        case_id = self.record_case(ctx.guild.id, "clearwarnings", ctx.author.id, member.id, reason, "success", "not_applicable")
        await self.log(ctx, "clearwarnings", member, reason, "success", case_id=case_id)
        await ctx.send(embed=embed("Warnings archived", f"Archived {count} warning(s) for {member.mention}. {self.case_note(case_id)}", colour=PRIMARY))

    @commands.command(name="timeout")
    @commands.guild_only()
    @require_role("lead_designer")
    @require_native("moderate_members")
    async def timeout(self, ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "No reason provided."):
        try:
            seconds = parse_duration(duration)
        except ValueError as exc:
            await send_error(ctx, str(exc))
            return
        if not await self.require_target(ctx, member):
            return
        if not ctx.guild.me.guild_permissions.moderate_members:
            await send_error(ctx, "The bot needs Discord's Moderate Members permission.")
            return
        reason = safe_text(reason, 1000)
        try:
            await member.timeout(discord.utils.utcnow() + timedelta(seconds=seconds), reason=reason)
        except (discord.Forbidden, discord.HTTPException) as exc:
            await self.log(ctx, "timeout", member, reason, "failure", case_id=None)
            await send_error(ctx, f"Discord did not confirm the timeout ({type(exc).__name__}).")
            return
        dm = await self.dm_member(member, f"Timeout ({duration})", reason)
        case = self.record_case(ctx.guild.id, "timeout", ctx.author.id, member.id, reason, "success", dm)
        await self.log(ctx, "timeout", member, reason, "success", dm, case)
        await ctx.send(embed=embed("Timeout applied", f"{member.mention} was timed out for `{duration}`. DM: **{dm}**. {self.case_note(case)}", colour=PRIMARY))

    @commands.command(name="untimeout")
    @commands.guild_only()
    @require_role("lead_designer")
    @require_native("moderate_members")
    async def untimeout(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
        if not await self.require_target(ctx, member):
            return
        if not ctx.guild.me.guild_permissions.moderate_members:
            await send_error(ctx, "The bot needs Discord's Moderate Members permission.")
            return
        reason = safe_text(reason, 1000)
        try:
            await member.timeout(None, reason=reason)
        except (discord.Forbidden, discord.HTTPException) as exc:
            await self.log(ctx, "untimeout", member, reason, "failure")
            await send_error(ctx, f"Discord did not confirm removing the timeout ({type(exc).__name__}).")
            return
        dm = await self.dm_member(member, "Timeout removed", reason)
        case = self.record_case(ctx.guild.id, "untimeout", ctx.author.id, member.id, reason, "success", dm)
        await self.log(ctx, "untimeout", member, reason, "success", dm, case)
        await ctx.send(embed=embed("Timeout removed", f"Removed timeout from {member.mention}. DM: **{dm}**. {self.case_note(case)}", colour=PRIMARY))

    @commands.command(name="kick")
    @commands.guild_only()
    @require_role("moderator")
    @require_native("kick_members")
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
        if not await self.require_target(ctx, member):
            return
        if not ctx.guild.me.guild_permissions.kick_members:
            await send_error(ctx, "The bot needs Discord's Kick Members permission.")
            return
        reason = safe_text(reason, 1000)
        try:
            await member.kick(reason=f"{ctx.author} ({ctx.author.id}): {reason}")
        except (discord.Forbidden, discord.HTTPException) as exc:
            await self.log(ctx, "kick", member, reason, "failure")
            await send_error(ctx, f"Discord did not confirm the kick ({type(exc).__name__}).")
            return
        dm = await self.dm_member(member, "Kick", reason)
        case = self.record_case(ctx.guild.id, "kick", ctx.author.id, member.id, reason, "success", dm)
        await self.log(ctx, "kick", member, reason, "success", dm, case)
        await ctx.send(embed=embed("Member kicked", f"Discord confirmed the kick for `{member}`. DM: **{dm}**. {self.case_note(case)}", colour=PRIMARY))

    @commands.command(name="ban")
    @commands.guild_only()
    @require_role("administrator")
    @require_native("ban_members")
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided."):
        if not await self.require_target(ctx, member):
            return
        if not ctx.guild.me.guild_permissions.ban_members:
            await send_error(ctx, "The bot needs Discord's Ban Members permission.")
            return
        if not await confirm(ctx, f"Ban {member.mention}? Confirming will immediately ban the member."):
            return
        reason = safe_text(reason, 1000)
        try:
            await ctx.guild.ban(member, reason=f"{ctx.author} ({ctx.author.id}): {reason}", delete_message_seconds=0)
        except (discord.Forbidden, discord.HTTPException) as exc:
            await self.log(ctx, "ban", member, reason, "failure")
            await send_error(ctx, f"Discord did not confirm the ban ({type(exc).__name__}).")
            return
        dm = await self.dm_member(member, "Ban", reason)
        case = self.record_case(ctx.guild.id, "ban", ctx.author.id, member.id, reason, "success", dm)
        await self.log(ctx, "ban", member, reason, "success", dm, case)
        await ctx.send(embed=embed("Member banned", f"Discord confirmed the ban for `{member}`. DM: **{dm}**. {self.case_note(case)}", colour=PRIMARY))

    @commands.command(name="unban")
    @commands.guild_only()
    @require_role("server_founder")
    @require_native("ban_members")
    async def unban(self, ctx: commands.Context, user_id: str, *, reason: str = "No reason provided."):
        try:
            numeric_user_id = validate_discord_id(user_id)
        except ValueError as exc:
            await send_error(ctx, str(exc))
            return
        if not ctx.guild.me.guild_permissions.ban_members:
            await send_error(ctx, "The bot needs Discord's Ban Members permission.")
            return
        reason = safe_text(reason, 1000)
        try:
            user = await self.bot.fetch_user(numeric_user_id)
            await ctx.guild.unban(user, reason=f"{ctx.author} ({ctx.author.id}): {reason}")
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            await self.log(ctx, "unban", user_id, reason, "failure")
            await send_error(ctx, f"Discord did not confirm the unban ({type(exc).__name__}).")
            return
        case = self.record_case(ctx.guild.id, "unban", ctx.author.id, user.id, reason, "success", "not_applicable")
        await self.log(ctx, "unban", user.id, reason, "success", case_id=case)
        await ctx.send(embed=embed("Member unbanned", f"Discord confirmed the unban for `{user}`. {self.case_note(case)}", colour=PRIMARY))

    @commands.command(name="purge")
    @commands.guild_only()
    @require_role("administrator")
    @require_native("manage_messages")
    async def purge(self, ctx: commands.Context, amount: int):
        if not 1 <= amount <= 100:
            await send_error(ctx, "Choose a message count from 1 to 100.")
            return
        if not ctx.guild.me.guild_permissions.manage_messages or not ctx.guild.me.guild_permissions.read_message_history:
            await send_error(ctx, "The bot needs Manage Messages and Read Message History permissions.")
            return
        if not await confirm(ctx, f"Delete up to {amount} messages from {ctx.channel.mention}? Messages older than 14 days are excluded from bulk deletion."):
            return
        try:
            control_ids = {ctx.message.id, getattr(ctx, "_last_confirmation_message_id", None)}
            deleted = await ctx.channel.purge(limit=amount + 2, check=purge_message_filter(amount, control_ids),
                                              bulk=True, reason=f"Purged by {ctx.author} ({ctx.author.id})")
        except (discord.Forbidden, discord.HTTPException) as exc:
            await self.log(ctx, "purge", ctx.channel.id, "Bulk deletion", "failure")
            await send_error(ctx, f"Discord did not confirm the purge ({type(exc).__name__}).")
            return
        removed = len(deleted)  # Do not retain or report deleted message contents.
        case = self.record_case(ctx.guild.id, "purge", ctx.author.id, ctx.channel.id, f"Deleted {removed} messages", "success", "not_applicable")
        await audit(self.bot, "moderation", "purge", guild=ctx.guild, actor_id=ctx.author.id,
                    target_id=ctx.channel.id, details={"deleted_count": removed, "case_id": case},
                    outcome="success", channel_key="moderation_logs")
        await ctx.send(embed=embed("Purge complete", f"Discord confirmed deletion of {removed} message(s). {self.case_note(case)}", colour=PRIMARY), delete_after=8)

    @commands.command(name="lock")
    @commands.guild_only()
    @require_role("head_owner")
    @require_native("manage_channels")
    async def lock(self, ctx: commands.Context, *, reason: str = "No reason provided."):
        if not isinstance(ctx.channel, discord.TextChannel):
            await send_error(ctx, "Channel locking is supported only in text channels.")
            return
        if not ctx.guild.me.guild_permissions.manage_channels:
            await send_error(ctx, "The bot needs Manage Channels permission.")
            return
        reason = safe_text(reason, 400)
        existing = self.bot.db.one("SELECT 1 FROM locked_channels WHERE guild_id=? AND channel_id=?", (str(ctx.guild.id), str(ctx.channel.id)))
        if existing:
            await send_error(ctx, "This channel is already locked by the bot; use `!unlock` to restore its saved overwrites.")
            return
        overwrite_data = []
        for target, overwrite in ctx.channel.overwrites.items():
            allow, deny = overwrite.pair()
            overwrite_data.append({"type": "role" if isinstance(target, discord.Role) else "member",
                                  "id": target.id, "allow": allow.value, "deny": deny.value})
        try:
            with self.bot.db._lock, self.bot.db._db():
                self.bot.db._db().execute(
                    "INSERT INTO locked_channels(guild_id,channel_id,overwrites_json,actor_id,updated_at) VALUES(?,?,?,?,?)",
                    (str(ctx.guild.id), str(ctx.channel.id), json.dumps(overwrite_data), str(ctx.author.id), utcnow()),
                )
            await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False, add_reactions=False, reason=safe_text(reason, 400))
        except Exception as exc:
            # The saved snapshot remains available if Discord failed after the DB write.
            await send_error(ctx, f"Lock was not confirmed ({type(exc).__name__}); use `!unlock` if the channel permissions changed.")
            await audit(self.bot, "moderation", "lock", guild=ctx.guild, actor_id=ctx.author.id, target_id=ctx.channel.id,
                        details={"reason": safe_text(reason, 400)}, outcome="failure", channel_key="moderation_logs")
            return
        case = self.record_case(ctx.guild.id, "lock", ctx.author.id, ctx.channel.id, reason, "success", "not_applicable")
        await audit(self.bot, "moderation", "lock", guild=ctx.guild, actor_id=ctx.author.id, target_id=ctx.channel.id,
                    details={"reason": safe_text(reason, 400), "case_id": case}, outcome="success", channel_key="moderation_logs")
        await ctx.send(embed=embed("Channel locked", f"{ctx.channel.mention} is locked. Use `!unlock` to restore saved overwrites.", colour=PRIMARY))

    @commands.command(name="unlock")
    @commands.guild_only()
    @require_role("head_owner")
    @require_native("manage_channels")
    async def unlock(self, ctx: commands.Context, *, reason: str = "No reason provided."):
        if not isinstance(ctx.channel, discord.TextChannel):
            await send_error(ctx, "Channel unlocking is supported only in text channels.")
            return
        if not ctx.guild.me.guild_permissions.manage_channels:
            await send_error(ctx, "The bot needs Manage Channels permission.")
            return
        reason = safe_text(reason, 400)
        row = self.bot.db.one("SELECT overwrites_json FROM locked_channels WHERE guild_id=? AND channel_id=?", (str(ctx.guild.id), str(ctx.channel.id)))
        if not row:
            await send_error(ctx, "No saved overwrite snapshot exists for this channel; refusing to reset permissions blindly.")
            return
        try:
            saved = json.loads(row["overwrites_json"])
            resolved = []
            missing = []
            for entry in saved:
                if entry["type"] == "role":
                    target = ctx.guild.get_role(int(entry["id"]))
                elif entry["type"] == "member":
                    target = ctx.guild.get_member(int(entry["id"]))
                    if target is None:
                        try:
                            target = await ctx.guild.fetch_member(int(entry["id"]))
                        except discord.HTTPException:
                            target = None
                else:
                    raise ValueError("Saved overwrite target type is invalid.")
                if target is None:
                    missing.append(str(entry["id"]))
                else:
                    resolved.append((entry, target))
            if missing:
                raise RuntimeError("Saved overwrite targets are missing: " + ", ".join(missing[:10]))
            for target in list(ctx.channel.overwrites):
                await ctx.channel.set_permissions(target, overwrite=None, reason="Restore saved Apex lock snapshot")
            for entry, target in resolved:
                overwrite = discord.PermissionOverwrite.from_pair(discord.Permissions(entry["allow"]), discord.Permissions(entry["deny"]))
                await ctx.channel.set_permissions(target, overwrite=overwrite, reason=safe_text(reason, 400))
        except (discord.Forbidden, discord.HTTPException, ValueError, TypeError, RuntimeError) as exc:
            await send_error(ctx, f"Saved overwrites could not be fully restored ({type(exc).__name__}); the snapshot remains for retry.")
            await audit(self.bot, "moderation", "unlock", guild=ctx.guild, actor_id=ctx.author.id, target_id=ctx.channel.id,
                        details={"reason": safe_text(reason, 400)}, outcome="failure", channel_key="moderation_logs")
            return
        try:
            with self.bot.db.transaction() as connection:
                connection.execute("DELETE FROM locked_channels WHERE guild_id=? AND channel_id=?", (str(ctx.guild.id), str(ctx.channel.id)))
        except Exception as exc:
            await audit(self.bot, "moderation", "unlock_snapshot_cleanup_failed", guild=ctx.guild,
                        actor_id=ctx.author.id, target_id=ctx.channel.id,
                        details={"error_type": type(exc).__name__}, outcome="failure", channel_key="moderation_logs")
            await ctx.send(embed=embed("Channel restored", "Saved permission overwrites were restored, but the snapshot could not be cleared. Check the database before running `!unlock` again.", colour=PRIMARY))
            return
        case = self.record_case(ctx.guild.id, "unlock", ctx.author.id, ctx.channel.id, reason, "success", "not_applicable")
        await audit(self.bot, "moderation", "unlock", guild=ctx.guild, actor_id=ctx.author.id, target_id=ctx.channel.id,
                    details={"reason": safe_text(reason, 400), "case_id": case}, outcome="success", channel_key="moderation_logs")
        await ctx.send(embed=embed("Channel unlocked", "Saved permission overwrites were restored.", colour=PRIMARY))

    @commands.command(name="slowmode")
    @commands.guild_only()
    @require_role("head_owner")
    @require_native("manage_channels")
    async def slowmode(self, ctx: commands.Context, seconds: int):
        if not 0 <= seconds <= 21600:
            await send_error(ctx, "Slowmode must be from 0 to 21600 seconds.")
            return
        if not ctx.guild.me.guild_permissions.manage_channels:
            await send_error(ctx, "The bot needs Manage Channels permission.")
            return
        try:
            await ctx.channel.edit(slowmode_delay=seconds, reason=f"Set by {ctx.author} ({ctx.author.id})")
        except (discord.Forbidden, discord.HTTPException) as exc:
            await send_error(ctx, f"Discord did not confirm slowmode ({type(exc).__name__}).")
            await audit(self.bot, "moderation", "slowmode", guild=ctx.guild, actor_id=ctx.author.id,
                        target_id=ctx.channel.id, details={"seconds": seconds}, outcome="failure", channel_key="moderation_logs")
            return
        case = self.record_case(ctx.guild.id, "slowmode", ctx.author.id, ctx.channel.id, f"{seconds}s", "success", "not_applicable")
        await audit(self.bot, "moderation", "slowmode", guild=ctx.guild, actor_id=ctx.author.id,
                    target_id=ctx.channel.id, details={"seconds": seconds, "case_id": case}, outcome="success", channel_key="moderation_logs")
        await ctx.send(embed=embed("Slowmode updated", f"Slowmode is now {seconds} second(s).", colour=PRIMARY))
