"""Autorole, welcome messages, nickname and restricted role management."""

from __future__ import annotations

import discord
from discord.ext import commands

from .common import PRIMARY, audit, can_bot_manage_member, embed, has_minimum_role, require_native, require_role, role_is_privileged, send_error
from .config import CHANNEL_IDS, ROLE_IDS
from .validation import safe_text


class ServerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _role_change(self, ctx, member: discord.Member, role: discord.Role, adding: bool):
        if role_is_privileged(role):
            await send_error(ctx, "Protected or privileged roles cannot be changed with this command.")
            return
        ok, message = can_bot_manage_member(ctx.guild, ctx.author, member)
        if not ok:
            await send_error(ctx, message)
            return
        if role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            await send_error(ctx, "The selected role is equal to or above your highest role.")
            return
        if role >= ctx.guild.me.top_role:
            await send_error(ctx, "The selected role is equal to or above the bot's highest role.")
            return
        if adding and role in member.roles or not adding and role not in member.roles:
            await send_error(ctx, "That role change is already in place.")
            return
        try:
            if adding:
                await member.add_roles(role, reason=f"{ctx.author} ({ctx.author.id})")
            else:
                await member.remove_roles(role, reason=f"{ctx.author} ({ctx.author.id})")
        except (discord.Forbidden, discord.HTTPException) as exc:
            await send_error(ctx, f"Discord did not confirm the role change ({type(exc).__name__}).")
            await audit(self.bot, "role", "add" if adding else "remove", guild=ctx.guild,
                        actor_id=ctx.author.id, target_id=member.id,
                        details={"role_id": role.id}, outcome="failure", channel_key="role_logs")
            return
        logged = await audit(self.bot, "role", "add" if adding else "remove", guild=ctx.guild,
                             actor_id=ctx.author.id, target_id=member.id,
                             details={"role_id": role.id, "role_name": role.name},
                             outcome="success", channel_key="role_logs")
        text = f"Discord confirmed {'adding' if adding else 'removing'} {role.mention} {('to' if adding else 'from')} {member.mention}."
        if not logged:
            text += " The role change succeeded, but at least one audit delivery failed."
        await ctx.send(embed=embed("Role updated", text, colour=PRIMARY))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        try:
            role_value = self.bot.db.setting(f"autorole:{member.guild.id}")
        except Exception as exc:
            self.bot.logger.error("Could not read autorole setting (%s)", type(exc).__name__)
            role_value = None
        if role_value:
            try:
                role_id = int(role_value)
                role = member.guild.get_role(role_id)
            except (TypeError, ValueError):
                role = None
                role_id = safe_text(str(role_value), 40)
            bot_member = member.guild.me
            if (role and bot_member and not role_is_privileged(role) and role < bot_member.top_role
                    and member.top_role < bot_member.top_role and member.id != member.guild.owner_id
                    and bot_member.guild_permissions.manage_roles):
                try:
                    await member.add_roles(role, reason="Configured Apex autorole")
                    await audit(self.bot, "role", "autorole_assigned", guild=member.guild, target_id=member.id,
                                details={"role_id": role.id}, outcome="success", channel_key="role_logs")
                except (discord.Forbidden, discord.HTTPException):
                    await audit(self.bot, "error", "autorole_assignment_failed", guild=member.guild,
                                target_id=member.id, details={"role_id": role.id}, outcome="failure")
            else:
                await audit(self.bot, "configuration", "autorole_invalid", guild=member.guild,
                            target_id=member.id, details={"role_id": role_id}, outcome="failure")

        try:
            welcome_setting = self.bot.db.setting(f"welcome:{member.guild.id}")
        except Exception as exc:
            self.bot.logger.error("Could not read welcome setting (%s)", type(exc).__name__)
            await audit(self.bot, "error", "welcome_setting_read_failed", guild=member.guild,
                        target_id=member.id, details={"error_type": type(exc).__name__}, outcome="failure")
            return
        if welcome_setting == "off":
            return
        try:
            channel_id = int(welcome_setting) if welcome_setting else CHANNEL_IDS["welcome"]
        except (TypeError, ValueError):
            await audit(self.bot, "configuration", "welcome_channel_invalid", guild=member.guild,
                        target_id=member.id, details={"configured_value": safe_text(str(welcome_setting), 40)}, outcome="failure")
            return
        channel = member.guild.get_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await audit(self.bot, "error", "welcome_channel_missing", guild=member.guild,
                        target_id=member.id, details={"channel_id": channel_id}, outcome="failure")
            return
        bot_member = member.guild.me
        if bot_member is None:
            await audit(self.bot, "error", "welcome_bot_member_missing", guild=member.guild,
                        target_id=member.id, details={"channel_id": channel_id}, outcome="failure")
            return
        permissions = channel.permissions_for(bot_member)
        if not permissions.view_channel or not permissions.send_messages or not permissions.embed_links:
            await audit(self.bot, "error", "welcome_send_unavailable", guild=member.guild,
                        target_id=member.id, details={"channel_id": channel_id}, outcome="failure")
            return
        try:
            await self._send_welcome(channel, member)
        except (discord.Forbidden, discord.HTTPException) as exc:
            await audit(self.bot, "error", "welcome_send_failed", guild=member.guild,
                        target_id=member.id, details={"channel_id": channel_id, "error_type": type(exc).__name__}, outcome="failure")

    async def _send_welcome(self, channel, member: discord.Member):
        welcome = embed("✦ WELCOME TO APEX", f"Welcome {member.mention} to Apex MiddleMan Services.\n\nPlease review the <#{CHANNEL_IDS['middleman_terms']}> and <#{CHANNEL_IDS['support_terms']}> and visit <#{CHANNEL_IDS['support_panel']}> if you need help.", colour=PRIMARY,
                        footer="Apex MiddleMan Services • Community & Support")
        await channel.send(embed=welcome, allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False))

    @commands.group(name="autorole", invoke_without_command=True)
    @commands.guild_only()
    async def autorole(self, ctx: commands.Context):
        role_value = self.bot.db.setting(f"autorole:{ctx.guild.id}")
        try:
            role = ctx.guild.get_role(int(role_value)) if role_value else None
        except (TypeError, ValueError):
            await send_error(ctx, "Saved autorole configuration is invalid; use `!autorole set @role` to replace it.")
            return
        await ctx.send(embed=embed("Autorole", f"Current role: {role.mention if role else 'Off'}\nConfigure with `!autorole set @role` or `!autorole off`.", colour=PRIMARY))

    @autorole.command(name="set")
    @require_role("head_owner")
    @require_native("manage_roles")
    async def autorole_set(self, ctx: commands.Context, role: discord.Role):
        if role_is_privileged(role):
            await send_error(ctx, "Autorole cannot assign @everyone, managed, administrator, management, or configured staff roles.")
            return
        if role >= ctx.guild.me.top_role or (role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id):
            await send_error(ctx, "The selected role must be below both your and the bot's highest role.")
            return
        if not ctx.guild.me.guild_permissions.manage_roles:
            await send_error(ctx, "The bot needs Manage Roles permission.")
            return
        self.bot.db.set_setting(f"autorole:{ctx.guild.id}", str(role.id))
        await audit(self.bot, "configuration", "autorole_set", guild=ctx.guild, actor_id=ctx.author.id,
                    target_id=role.id, details={"role_name": role.name}, outcome="success", channel_key="role_logs")
        await ctx.send(embed=embed("Autorole saved", f"New members will receive {role.mention}.", colour=PRIMARY))

    @autorole.command(name="off")
    @require_role("head_owner")
    @require_native("manage_roles")
    async def autorole_off(self, ctx: commands.Context):
        self.bot.db.execute("DELETE FROM settings WHERE key=?", (f"autorole:{ctx.guild.id}",))
        await audit(self.bot, "configuration", "autorole_off", guild=ctx.guild, actor_id=ctx.author.id,
                    outcome="success", channel_key="role_logs")
        await ctx.send(embed=embed("Autorole disabled", "New members will not receive an automatic role.", colour=PRIMARY))

    @commands.command(name="setwelcome")
    @commands.guild_only()
    @require_role("head_owner")
    @require_native("manage_guild")
    async def setwelcome(self, ctx: commands.Context, channel: discord.TextChannel):
        if ctx.guild.me is None:
            await send_error(ctx, "The bot member record is unavailable; no welcome channel was saved.")
            return
        permissions = channel.permissions_for(ctx.guild.me)
        if not permissions.view_channel or not permissions.send_messages or not permissions.embed_links:
            await send_error(ctx, "The bot needs View Channel, Send Messages, and Embed Links in the selected welcome channel.")
            return
        self.bot.db.set_setting(f"welcome:{ctx.guild.id}", str(channel.id))
        await audit(self.bot, "configuration", "welcome_channel_set", guild=ctx.guild,
                    actor_id=ctx.author.id, target_id=channel.id, outcome="success")
        await ctx.send(embed=embed("Welcome channel saved", f"New member welcomes will be sent to {channel.mention}.", colour=PRIMARY))

    @commands.group(name="welcome", invoke_without_command=True)
    @commands.guild_only()
    async def welcome(self, ctx: commands.Context):
        welcome_setting = self.bot.db.setting(f"welcome:{ctx.guild.id}")
        try:
            channel_id = int(welcome_setting) if welcome_setting and welcome_setting != "off" else CHANNEL_IDS["welcome"]
        except (TypeError, ValueError):
            await send_error(ctx, "Saved welcome configuration is invalid; use `!setwelcome #channel` to replace it.")
            return
        channel = ctx.guild.get_channel(channel_id)
        await ctx.send(embed=embed("Welcome messages", f"Status: **{'Off' if welcome_setting == 'off' else 'On'}**\nDestination: {channel.mention if channel else f'#{channel_id} (missing)'}\nUse `!setwelcome #channel`, `!welcome off`, or `!welcome-test`.", colour=PRIMARY))

    @welcome.command(name="off")
    @require_role("head_owner")
    @require_native("manage_guild")
    async def welcome_off(self, ctx: commands.Context):
        self.bot.db.set_setting(f"welcome:{ctx.guild.id}", "off")
        await audit(self.bot, "configuration", "welcome_off", guild=ctx.guild, actor_id=ctx.author.id, outcome="success")
        await ctx.send(embed=embed("Welcome messages disabled", "No member welcome will be sent until a channel is selected.", colour=PRIMARY))

    @commands.command(name="welcome-test")
    @commands.guild_only()
    @require_role("head_owner")
    @require_native("manage_guild")
    async def welcome_test(self, ctx: commands.Context):
        setting = self.bot.db.setting(f"welcome:{ctx.guild.id}")
        if setting == "off":
            await send_error(ctx, "Welcome messages are off. Use `!setwelcome #channel` first.")
            return
        try:
            channel_id = int(setting) if setting else CHANNEL_IDS["welcome"]
        except (TypeError, ValueError):
            await send_error(ctx, "Saved welcome configuration is invalid; no test message was sent.")
            return
        channel = ctx.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            await send_error(ctx, f"Configured welcome channel `{channel_id}` is missing or is not a text channel.")
            return
        if ctx.guild.me is None:
            await send_error(ctx, "The bot member record is unavailable; no welcome test was sent.")
            return
        permissions = channel.permissions_for(ctx.guild.me)
        if not permissions.view_channel or not permissions.send_messages or not permissions.embed_links:
            await send_error(ctx, f"The bot cannot send the test to {channel.mention}; check View Channel, Send Messages, and Embed Links.")
            return
        try:
            await self._send_welcome(channel, ctx.author)
        except (discord.Forbidden, discord.HTTPException) as exc:
            await audit(self.bot, "error", "welcome_test_send_failed", guild=ctx.guild,
                        actor_id=ctx.author.id, target_id=channel.id,
                        details={"error_type": type(exc).__name__}, outcome="failure")
            await send_error(ctx, f"Discord did not confirm the welcome test ({type(exc).__name__}).")
            return
        await ctx.send(embed=embed("Welcome test sent", f"Test posted to {channel.mention}.", colour=PRIMARY), delete_after=8)

    @commands.command(name="nick")
    @commands.guild_only()
    @require_role("lead_designer")
    @require_native("manage_nicknames")
    async def nickname(self, ctx: commands.Context, member: discord.Member, *, nickname: str | None = None):
        ok, message = can_bot_manage_member(ctx.guild, ctx.author, member)
        if not ok:
            await send_error(ctx, message)
            return
        if not ctx.guild.me.guild_permissions.manage_nicknames:
            await send_error(ctx, "The bot needs Manage Nicknames permission.")
            return
        new_nick = safe_text(nickname, 32).strip() if nickname else None
        old = member.nick or member.name
        try:
            await member.edit(nick=new_nick, reason=f"Nickname changed by {ctx.author} ({ctx.author.id})")
        except (discord.Forbidden, discord.HTTPException) as exc:
            await send_error(ctx, f"Discord did not confirm the nickname change ({type(exc).__name__}).")
            await audit(self.bot, "moderation", "nickname", guild=ctx.guild, actor_id=ctx.author.id,
                        target_id=member.id, details={"old": old, "new": new_nick or "cleared"}, outcome="failure", channel_key="moderation_logs")
            return
        logged = await audit(self.bot, "moderation", "nickname", guild=ctx.guild, actor_id=ctx.author.id,
                             target_id=member.id, details={"old": old, "new": new_nick or "cleared"}, outcome="success", channel_key="moderation_logs")
        suffix = "" if logged else " (logging delivery failed)"
        await ctx.send(embed=embed("Nickname updated", f"Discord confirmed the nickname change for {member.mention}.{suffix}", colour=PRIMARY))

    @commands.group(name="role", invoke_without_command=True)
    @commands.guild_only()
    async def role(self, ctx: commands.Context):
        await ctx.send(embed=embed("Role management", "Use `!role add @user @role` or `!role remove @user @role`.", colour=PRIMARY))

    @role.command(name="add")
    @require_role("operations_lead")
    @require_native("manage_roles")
    async def role_add(self, ctx: commands.Context, member: discord.Member, role: discord.Role):
        await self._role_change(ctx, member, role, True)

    @role.command(name="remove")
    @require_role("operations_lead")
    @require_native("manage_roles")
    async def role_remove(self, ctx: commands.Context, member: discord.Member, role: discord.Role):
        await self._role_change(ctx, member, role, False)
