"""Utility commands, help, and consistent command error handling."""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord.ext import commands

from . import __version__
from .common import PRIMARY, SECONDARY, SILVER, audit, embed, has_minimum_role
from .config import ROLE_IDS


class CoreCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            message = f"Missing argument `{error.param.name}`. Use `!help` to see the command syntax."
        elif isinstance(error, commands.BadArgument):
            message = "I couldn't understand one of the arguments. Check the command syntax with `!help`."
        elif isinstance(error, commands.CheckFailure):
            message = str(error) or "You do not have permission to use this command."
        elif isinstance(error, commands.CommandOnCooldown):
            message = f"Please wait {error.retry_after:.1f} seconds before using this again."
        else:
            # Avoid dumping arbitrary exception payloads into logs; report only safe metadata.
            self.bot.logger.error("Command failure in %s (%s)", ctx.command, type(error).__name__)
            message = "The command failed. The request was not reported as successful; staff can inspect `!audit errors`."
            try:
                await audit(self.bot, "error", "command_failure", guild=ctx.guild,
                            actor_id=ctx.author.id, details={"command": str(ctx.command), "error_type": type(error).__name__}, outcome="failure")
            except Exception:
                self.bot.logger.error("Could not persist command error audit (%s)", type(error).__name__)
        await ctx.send(embed=embed("Command error", message, colour=0xD95C68))

    @commands.command(name="help")
    async def help_command(self, ctx: commands.Context):
        pages = [
            ("Moderation", [
                ("!warn @user [reason]", "Add a persistent warning", "Moderator+"),
                ("!warnings @user", "View active warning history", "Lead Designer+"),
                ("!clearwarnings @user [reason]", "Archive warnings after confirmation", "Director of Ops+"),
                ("!timeout @user 10m [reason]", "Timeout for s/m/h/d (up to 28 days)", "Lead Designer+"),
                ("!untimeout @user [reason]", "Remove a member timeout", "Lead Designer+"),
                ("!kick @user [reason]", "Kick after hierarchy checks", "Moderator+"),
                ("!ban @user [reason]", "Ban after confirmation", "Administrator+"),
                ("!unban USER_ID [reason]", "Unban a user by ID", "Server Founder+"),
                ("!purge 20", "Delete up to 100 messages after confirmation", "Administrator+"),
                ("!lock [reason] / !unlock [reason]", "Lock and restore channel overwrites", "Head Owner+"),
                ("!slowmode 5", "Set slowmode seconds", "Head Owner+"),
            ]),
            ("Tickets & builders", [
                ("!panelbuilder", "Create and manage individual ticket panels", "Server Founder+"),
                ("!adduser @user", "Grant one user access to this ticket only", "Ticket staff / Founder+"),
                ("!embedbuilder", "Create and manage custom embeds and triggers", "Server Founder+"),
            ]),
            ("Server management", [
                ("!autorole [set @role|off]", "Configure one safe join role", "Head Owner+"),
                ("!setwelcome #channel / !welcome off / !welcome-test", "Configure and preview welcome messages", "Head Owner+"),
                ("!nick @user [nickname]", "Change a nickname", "Lead Designer+"),
                ("!role add|remove @user @role", "Add or remove a non-privileged role", "Operations Lead+"),
            ]),
            ("Community", [
                ("!giveaway start <duration> <winners> <prize>", "Start a giveaway", "Director of Ops+"),
                ("!giveaway end|cancel|reroll|list ...", "Manage active giveaways", "Director of Ops+"),
                ("!suggest [idea]", "Submit a suggestion", "Everyone"),
                ("!suggestion status|approve|deny|implement ID", "Check or review suggestions", "Everyone / Lead Designer+"),
            ]),
            ("Information & audit", [
                ("!serverinfo / !botinfo / !userinfo [@user] / !roleinfo @role", "Server and account information", "Everyone"),
                ("!audit status|recent|errors|config", "Inspect system audit records", "Server Founder+"),
            ]),
        ]
        for title, entries in pages:
            page = embed(f"✦ 𝙰𝚙𝚎𝚡 • {title}", "Prefix commands only • `!help`", colour=PRIMARY,
                         footer="Apex MiddleMan Services • Community & Support")
            for syntax, purpose, access in entries:
                page.add_field(name=syntax, value=f"{purpose}\nAccess: **{access}**", inline=False)
            await ctx.send(embed=page)

    @commands.command(name="serverinfo")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context):
        guild = ctx.guild
        channels = list(guild.channels)
        out = embed(f"{guild.name}", colour=PRIMARY, footer="Apex MiddleMan Services • Community & Support")
        out.add_field(name="Members", value=f"{guild.member_count or 0:,}", inline=True)
        out.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, style="D"), inline=True)
        out.add_field(name="Owner", value=str(guild.owner or f"ID {guild.owner_id}"), inline=True)
        out.add_field(name="Channels", value=f"Text: {sum(isinstance(c, discord.TextChannel) for c in channels)}\nVoice: {sum(isinstance(c, discord.VoiceChannel) for c in channels)}\nCategories: {sum(isinstance(c, discord.CategoryChannel) for c in channels)}", inline=True)
        out.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        if guild.icon:
            out.set_thumbnail(url=guild.icon.url)
        await ctx.send(embed=out)

    @commands.command(name="botinfo")
    @commands.guild_only()
    async def botinfo(self, ctx: commands.Context):
        uptime = discord.utils.utcnow() - self.bot.started_at
        out = embed("✦ 𝙰𝚙𝚎𝚡 • Bot Status", colour=SECONDARY)
        out.add_field(name="Version", value=__version__, inline=True)
        out.add_field(name="Uptime", value=str(uptime).split(".")[0], inline=True)
        out.add_field(name="Latency", value=f"{self.bot.latency * 1000:.0f} ms", inline=True)
        out.add_field(name="Connection", value="Ready" if self.bot.is_ready() else "Connecting", inline=True)
        await ctx.send(embed=out)

    @commands.command(name="userinfo")
    @commands.guild_only()
    async def userinfo(self, ctx: commands.Context, member: discord.Member | None = None):
        member = member or ctx.author
        out = embed(f"User Information • {member}", colour=SECONDARY)
        out.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, style="F"), inline=False)
        out.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, style="F") if member.joined_at else "Unknown", inline=False)
        out.add_field(name="Nickname", value=member.nick or "—", inline=True)
        out.add_field(name="Highest role", value=member.top_role.mention, inline=True)
        out.add_field(name="Timed out", value=discord.utils.format_dt(member.timed_out_until, style="R") if member.is_timed_out() and member.timed_out_until else "No", inline=True)
        roles = [r.mention for r in reversed(member.roles) if not r.is_default()]
        out.add_field(name=f"Roles ({len(roles)})", value=", ".join(roles)[:1024] or "—", inline=False)
        out.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=out)

    @commands.command(name="roleinfo")
    @commands.guild_only()
    async def roleinfo(self, ctx: commands.Context, role: discord.Role):
        key_permissions = [name for name in ("administrator", "manage_guild", "manage_roles", "manage_channels", "kick_members", "ban_members", "moderate_members", "manage_messages") if getattr(role.permissions, name)]
        out = embed(f"Role Information • {role.name}", colour=role.colour.value or SILVER)
        out.add_field(name="Colour", value=f"#{role.colour.value:06X}", inline=True)
        out.add_field(name="Members", value=str(len(role.members)), inline=True)
        out.add_field(name="Position", value=str(role.position), inline=True)
        out.add_field(name="Key permissions", value=", ".join(key_permissions) or "None", inline=False)
        await ctx.send(embed=out)

    @commands.Cog.listener()
    async def on_ready(self):
        # Resolve configured channel/role IDs once on connect and leave readable diagnostics.
        for guild in self.bot.guilds:
            missing_channels = [key for key, channel_id in __import__("apexbot.config", fromlist=["CHANNEL_IDS"]).CHANNEL_IDS.items() if guild.get_channel(channel_id) is None]
            missing_roles = [key for key, role_id in ROLE_IDS.items() if guild.get_role(role_id) is None]
            if missing_channels or missing_roles:
                self.bot.logger.warning("Configured objects missing in guild %s: channels=%s roles=%s", guild.id, missing_channels, missing_roles)
