"""Shared permission, embed, confirmation, and audit behavior."""

from __future__ import annotations

import discord
from discord.ext import commands

from .config import CHANNEL_IDS, PRIVILEGED_ROLE_KEYS, ROLE_IDS
from .policy import has_configured_threshold, meets_position_threshold
from .validation import safe_text

PRIMARY = 0x8B5CF6
SECONDARY = 0xA78BFA
SILVER = 0xD8D8E0
ERROR = 0xD95C68
FOOTER = "✦ 𝙰𝚙𝚎𝚡 • Moderation & Security"


def embed(title: str, description: str = "", *, colour: int = PRIMARY, footer: str = FOOTER) -> discord.Embed:
    result = discord.Embed(title=title, description=description, colour=colour, timestamp=discord.utils.utcnow())
    result.set_footer(text=footer)
    return result


def member_rank(member: discord.Member) -> int:
    return max((role.position for role in member.roles), default=0)


def has_minimum_role(member: discord.Member, key: str) -> bool:
    if not has_configured_threshold(key):
        return False
    threshold_role = member.guild.get_role(ROLE_IDS[key])
    if threshold_role is None:
        return member.id == member.guild.owner_id
    return meets_position_threshold((role.position for role in member.roles), threshold_role.position,
                                   server_owner=member.id == member.guild.owner_id)


def has_role_id(member: discord.Member, role_id: int) -> bool:
    return any(role.id == role_id for role in member.roles)


def require_role(key: str):
    async def predicate(ctx: commands.Context) -> bool:
        if not isinstance(ctx.author, discord.Member) or not has_minimum_role(ctx.author, key):
            raise commands.CheckFailure(f"This command requires the configured {key.replace('_', ' ')} role or above.")
        return True
    return commands.check(predicate)


def require_native(permission: str):
    async def predicate(ctx: commands.Context) -> bool:
        if not isinstance(ctx.author, discord.Member) or not getattr(ctx.author.guild_permissions, permission, False):
            raise commands.CheckFailure(f"Your Discord server permissions must include {permission.replace('_', ' ')}.")
        return True
    return commands.check(predicate)


def can_bot_manage_member(guild: discord.Guild, actor: discord.Member, target: discord.Member) -> tuple[bool, str]:
    me = guild.me
    if me is None:
        return False, "I could not resolve my member record in this server."
    if target.id == guild.owner_id:
        return False, "The server owner cannot be targeted."
    if target.id == actor.id:
        return False, "You cannot target yourself with this action."
    if actor.id != guild.owner_id and target.top_role >= actor.top_role:
        return False, "The target's highest role is equal to or above yours."
    if target.top_role >= me.top_role:
        return False, "The target's highest role is equal to or above the bot's highest role."
    return True, ""


def role_is_privileged(role: discord.Role) -> bool:
    dangerous_permission = any(getattr(role.permissions, permission) for permission in (
        "administrator", "manage_guild", "manage_roles", "manage_channels", "manage_messages",
        "manage_webhooks", "manage_threads", "kick_members", "ban_members", "moderate_members",
        "manage_nicknames", "manage_events", "manage_emojis_and_stickers",
        "manage_guild_expressions", "mention_everyone", "view_audit_log",
    ))
    return role.is_default() or role.managed or dangerous_permission or any(
        role.id == ROLE_IDS[key] for key in PRIVILEGED_ROLE_KEYS
    )


def is_private_staff_channel(channel: discord.abc.GuildChannel, guild: discord.Guild) -> bool:
    """Reject log channels exposed through @everyone, broad roles, or member overwrites."""
    try:
        if channel.permissions_for(guild.default_role).view_channel is not False:
            return False
        staff_ids = {ROLE_IDS[key] for key in PRIVILEGED_ROLE_KEYS}
        staff_thresholds = [guild.get_role(ROLE_IDS[key]) for key in ("moderator", "middleman", "server_founder")]
        staff_thresholds = [role.position for role in staff_thresholds if role is not None]
        staff_ids.update(role.id for role in guild.roles
                         if any(role.position >= threshold for threshold in staff_thresholds))
        for role in guild.roles:
            if role.is_default():
                continue
            if channel.permissions_for(role).view_channel is True and role.id not in staff_ids:
                return False
        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Member) and overwrite.view_channel is True:
                return False
        return True
    except (AttributeError, TypeError, ValueError):
        return False


async def audit(bot, category: str, action: str, *, guild: discord.Guild | None = None,
                actor_id: int | None = None, target_id: int | None = None,
                details: dict | None = None, outcome: str = "success", channel_key: str = "audit_logs") -> bool:
    """Record DB audit and best-effort Discord audit message; return whether both succeeded."""
    stored = True
    try:
        bot.db.audit(category, action, guild_id=guild.id if guild else None,
                     actor_id=actor_id, target_id=target_id, details=details, outcome=outcome)
    except Exception:
        stored = False
    keys = [channel_key]
    if channel_key != "audit_logs":
        keys.append("audit_logs")
    destinations = []
    if guild:
        for key in keys:
            channel = guild.get_channel(CHANNEL_IDS[key])
            if channel is None:
                sent = False
                bot.logger.error("Configured %s channel %s is missing in guild %s", key, CHANNEL_IDS[key], guild.id)
            else:
                if not is_private_staff_channel(channel, guild):
                    bot.logger.error("Refusing to post %s audit data to non-private channel %s in guild %s", category, channel.id, guild.id)
                    sent = False
                else:
                    destinations.append(channel)
    sent = bool(guild) and len(destinations) == len(keys)
    for channel in {c.id: c for c in destinations}.values():
        try:
            summary = discord.Embed(title=f"{category.title()} • {action}", colour=PRIMARY if outcome == "success" else ERROR)
            summary.description = f"Outcome: **{outcome}**\nActor: `{actor_id or 'system'}`\nTarget: `{target_id or '—'}`"
            for key, value in (details or {}).items():
                summary.add_field(name=safe_text(str(key), 256), value=safe_text(str(value), 1024) or "—", inline=True)
            summary.timestamp = discord.utils.utcnow()
            summary.set_footer(text="✦ 𝙰𝚙𝚎𝚡 • Audit & Security")
            await channel.send(embed=summary, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            sent = False
            bot.logger.error("Audit channel delivery failed for %s/%s", category, action)
    if not stored or not sent:
        bot.logger.error("Audit record incomplete for %s/%s (database=%s, discord_delivery=%s)", category, action, stored, sent)
    return stored and sent


class ConfirmView(discord.ui.View):
    def __init__(self, user_id: int, *, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.confirmed = False
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the command author can confirm this action.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Confirmed.", view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Cancelled.", view=self)
        self.stop()


class SafeView(discord.ui.View):
    """Persistent/transient view base that reports callback failures without leaking details."""

    def __init__(self, *args, bot=None, **kwargs):
        self.bot = bot
        super().__init__(*args, **kwargs)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        item_id = getattr(item, "custom_id", None) or type(item).__name__
        if self.bot is not None:
            self.bot.logger.error("Component callback failed (%s, %s)", item_id, type(error).__name__)
            try:
                await audit(self.bot, "error", "component_callback_failed", guild=interaction.guild,
                            actor_id=interaction.user.id, details={"component": item_id,
                                                                 "error_type": type(error).__name__}, outcome="failure")
            except Exception:
                self.bot.logger.error("Could not persist component callback failure (%s)", type(error).__name__)
        message = "This control failed. The requested action was not reported as successful; staff can inspect the audit log."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


class SafeModal(discord.ui.Modal):
    """Modal base that audits callback failures and returns a bounded private error."""

    def __init__(self, *args, bot=None, **kwargs):
        self.bot = bot
        super().__init__(*args, **kwargs)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        if self.bot is not None:
            self.bot.logger.error("Modal callback failed (%s)", type(error).__name__)
            try:
                await audit(self.bot, "error", "modal_callback_failed", guild=interaction.guild,
                            actor_id=interaction.user.id, details={"modal": type(self).__name__,
                                                                 "error_type": type(error).__name__}, outcome="failure")
            except Exception:
                self.bot.logger.error("Could not persist modal callback failure (%s)", type(error).__name__)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("This form failed to save. No success is reported; staff can inspect the audit log.", ephemeral=True)
            else:
                await interaction.response.send_message("This form failed to save. No success is reported; staff can inspect the audit log.", ephemeral=True)
        except discord.HTTPException:
            pass


async def confirm(ctx: commands.Context, prompt: str) -> bool:
    view = ConfirmView(ctx.author.id)
    view.message = await ctx.send(embed=embed("Confirm action", prompt, colour=ERROR), view=view)
    await view.wait()
    if not view.confirmed:
        if view.message:
            try:
                await view.message.edit(content="Confirmation expired or was cancelled.", embed=None, view=None)
            except discord.HTTPException:
                pass
    elif view.message:
        # Remove the confirmation control so destructive operations cannot count it as user content.
        ctx._last_confirmation_message_id = view.message.id
        try:
            await view.message.delete()
        except discord.HTTPException:
            pass
    return view.confirmed


async def send_error(ctx: commands.Context, message: str):
    await ctx.send(embed=embed("Action unavailable", message, colour=ERROR))


async def send_private_log(ctx: commands.Context, output: discord.Embed, *, channel_key: str, label: str) -> bool:
    """Deliver staff history by DM or into its configured @everyone-private log channel."""
    channel = ctx.guild.get_channel(CHANNEL_IDS[channel_key]) if ctx.guild else None
    if channel is not None and ctx.channel.id == channel.id and is_private_staff_channel(channel, ctx.guild):
        await ctx.send(embed=output, allowed_mentions=discord.AllowedMentions.none())
        return True
    try:
        await ctx.author.send(embed=output, allowed_mentions=discord.AllowedMentions.none())
        await ctx.send(embed=embed("Sent privately", f"{label} was sent to your DMs.", colour=PRIMARY))
        return True
    except (discord.Forbidden, discord.HTTPException):
        await send_error(ctx, f"I could not send {label.lower()} by DM. Run this command in the configured private <#{CHANNEL_IDS[channel_key]}> channel.")
        return False


async def moderation_log(bot, guild: discord.Guild, *, action: str, actor_id: int,
                         target_id: int | str, reason: str, outcome: str, dm_result: str,
                         case_id: int | str | None = None):
    ok = await audit(bot, "moderation", action, guild=guild, actor_id=actor_id,
                     target_id=int(target_id) if str(target_id).isdigit() else None,
                     details={"reason": reason, "case_id": case_id or "—", "dm": dm_result},
                     outcome=outcome, channel_key="moderation_logs")
    if not ok:
        bot.logger.error("Moderation action completed with audit delivery failure: %s", action)
    return ok
