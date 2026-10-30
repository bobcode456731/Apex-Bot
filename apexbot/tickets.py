"""Private ticket panels, category-specific access, lifecycle controls, and transcripts."""

from __future__ import annotations

import io
import asyncio
import json
import re
import uuid
from datetime import datetime, timezone

import discord
from discord.ext import commands

from .common import PRIMARY, SafeModal, SafeView, audit, embed, has_minimum_role, has_role_id, is_private_staff_channel, send_error
from .config import CHANNEL_IDS, ROLE_IDS
from .database import utcnow
from .policy import TICKET_MINIMUM_ROLE
from .validation import safe_text, validate_discord_id

PANEL_CHANNEL = {
    "middleman": CHANNEL_IDS["middleman_panel"],
    "support": CHANNEL_IDS["support_panel"],
    "rankup": CHANNEL_IDS["rankup_panel"],
}
TICKET_FIELDS = {
    "middleman": [
        ("Trading partner's Discord username", "Who is the other trader?"),
        ("What is being traded?", "Describe both sides of the trade."),
        ("Estimated value", "Optional; describe the agreed value."),
        ("Who gives first?", "State the agreed first party."),
    ],
    "support": [
        ("What help is needed?", "Trade dispute, report, or general help?"),
        ("Explanation", "Share a clear timeline and details."),
        ("Relevant user IDs/evidence", "IDs and evidence references; do not post secrets."),
    ],
    "rankup": [
        ("Current staff role", "Your current staff role."),
        ("Requested role/rank", "The staff role or rank requested."),
        ("Reason", "Why are you requesting this change?"),
        ("Approval/poll/arrangement", "Relevant staff approval or arrangement."),
    ],
}


def staff_for_category(member: discord.Member, category: str) -> bool:
    minimum = TICKET_MINIMUM_ROLE.get(category)
    return bool(minimum and has_minimum_role(member, minimum))


def category_staff_roles(guild: discord.Guild, category: str) -> list[discord.Role]:
    minimum_key = TICKET_MINIMUM_ROLE.get(category)
    threshold = guild.get_role(ROLE_IDS[minimum_key]) if minimum_key else None
    if threshold is None:
        return []
    return [role for role in guild.roles if not role.is_default() and role.position >= threshold.position]


def ticket_key(ticket_id: str) -> str:
    return f"ticket-{ticket_id.lower()}"


class TicketForm(SafeModal):
    def __init__(self, cog: "TicketCog", panel: dict):
        self.cog = cog
        self.panel = panel
        category = panel["ticket_type"]
        super().__init__(title=f"{category.title()} ticket request", bot=cog.bot)
        self.inputs: list[discord.ui.TextInput] = []
        for name, placeholder in TICKET_FIELDS[category]:
            field = discord.ui.TextInput(label=name[:45], placeholder=placeholder[:100], style=discord.TextStyle.paragraph,
                                         required=True, max_length=1000)
            self.inputs.append(field)
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.open_ticket(interaction, self.panel,
                                   {item.label: item.value for item in self.inputs})


class TicketPanelView(SafeView):
    def __init__(self, cog: "TicketCog", panel_id: str, label: str, emoji: str | None):
        super().__init__(timeout=None, bot=cog.bot)
        self.cog = cog
        button = discord.ui.Button(label=label[:80], emoji=emoji or None, style=discord.ButtonStyle.primary,
                                   custom_id=f"ticket:open:{panel_id}")
        button.callback = self.open
        self.add_item(button)

    async def open(self, interaction: discord.Interaction):
        panel_id = interaction.data["custom_id"].split(":", 2)[2]
        row = self.cog.bot.db.one("SELECT * FROM panel_templates WHERE id=? AND guild_id=? AND state='published'",
                                  (panel_id, str(interaction.guild_id))) if interaction.guild_id else None
        try:
            valid_panel = bool(row and row["ticket_type"] in PANEL_CHANNEL
                               and int(row["published_channel_id"]) == PANEL_CHANNEL[row["ticket_type"]])
        except (KeyError, TypeError, ValueError):
            valid_panel = False
        if (not valid_panel or not interaction.guild or not isinstance(interaction.user, discord.Member)
                or str(getattr(interaction.message, "id", "")) != str(row["published_message_id"])
                or str(getattr(interaction.message, "channel_id", "")) != str(row["published_channel_id"])):
            await interaction.response.send_message("This panel is unavailable.", ephemeral=True)
            return
        panel = dict(row)
        if panel.get("published_config_json"):
            try:
                snapshot = json.loads(panel["published_config_json"])
            except (TypeError, json.JSONDecodeError):
                snapshot = {}
            panel.update(snapshot)
        if panel["ticket_type"] == "rankup" and not has_minimum_role(interaction.user, "server_founder"):
            await interaction.response.send_message("This rank-up request is staff-only. Ordinary members cannot open it.", ephemeral=True)
            return
        await interaction.response.send_modal(TicketForm(self.cog, panel))


class TicketCloseConfirm(SafeView):
    def __init__(self, cog: "TicketCog", ticket_id: str, actor_id: int, reason: str):
        super().__init__(timeout=60, bot=cog.bot)
        self.cog, self.ticket_id, self.actor_id, self.reason = cog, ticket_id, actor_id, reason

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("Only the staff member who requested closure can confirm it.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Save transcript and close", style=discord.ButtonStyle.danger)
    async def confirm_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.cog.bot.db.one("SELECT * FROM tickets WHERE id=? AND status='open'", (self.ticket_id,))
        if not row or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Ticket or staff member is no longer available.", ephemeral=True)
            return
        if not staff_for_category(interaction.user, row["category"]) and not has_minimum_role(interaction.user, "server_founder"):
            await interaction.response.send_message("Your ticket staff access changed; closure was not allowed.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message("Closing requires your Discord Manage Channels permission.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        success, message = await self.cog.close_ticket(self.ticket_id, interaction.user, self.reason)
        await interaction.followup.send(message, ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Ticket closure cancelled.", view=None)
        self.stop()


class TicketControls(SafeView):
    def __init__(self, cog: "TicketCog", ticket_id: str):
        super().__init__(timeout=None, bot=cog.bot)
        self.cog, self.ticket_id = cog, ticket_id
        self.add_item(TicketPrioritySelect(cog, ticket_id))
        claim = discord.ui.Button(label="Claim", style=discord.ButtonStyle.success, custom_id=f"ticket:claim:{ticket_id}", row=1)
        claim.callback = self.claim
        self.add_item(claim)
        unclaim = discord.ui.Button(label="Unclaim", style=discord.ButtonStyle.secondary, custom_id=f"ticket:unclaim:{ticket_id}", row=1)
        unclaim.callback = self.unclaim
        self.add_item(unclaim)
        close = discord.ui.Button(label="Close", style=discord.ButtonStyle.danger, custom_id=f"ticket:close:{ticket_id}", row=1)
        close.callback = self.close
        self.add_item(close)

    async def permitted_staff(self, interaction: discord.Interaction, row) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        category = row["category"]
        if staff_for_category(interaction.user, category) or has_minimum_role(interaction.user, "server_founder"):
            return True
        await interaction.response.send_message("Only authorized staff for this ticket category can use these controls.", ephemeral=True)
        return False

    async def claim(self, interaction: discord.Interaction):
        row = self.cog.ticket_by_channel(interaction.channel_id)
        if not row or not await self.permitted_staff(interaction, row):
            if not row:
                await interaction.response.send_message("Ticket record not found.", ephemeral=True)
            return
        if row["claimant_id"]:
            await interaction.response.send_message("This ticket is already claimed.", ephemeral=True)
            return
        if not self.cog.update_claim(row["id"], interaction.user.id, "claim", actor_id=interaction.user.id):
            await interaction.response.send_message("Another staff member claimed this ticket first.", ephemeral=True)
            return
        logged = await self.cog.log_ticket(row["id"], "claim", interaction.user.id, target_id=interaction.user.id,
                                           old_value="unclaimed", new_value=str(interaction.user.id))
        suffix = " Ticket audit delivery failed; staff should inspect the private logs." if not logged else ""
        await interaction.response.send_message(f"Claimed by {interaction.user.mention}.{suffix}",
                                                allowed_mentions=discord.AllowedMentions(users=[interaction.user]))

    async def unclaim(self, interaction: discord.Interaction):
        row = self.cog.ticket_by_channel(interaction.channel_id)
        if not row or not await self.permitted_staff(interaction, row):
            if not row:
                await interaction.response.send_message("Ticket record not found.", ephemeral=True)
            return
        if not row["claimant_id"]:
            await interaction.response.send_message("This ticket is not claimed.", ephemeral=True)
            return
        old = row["claimant_id"]
        if not self.cog.update_claim(row["id"], None, "unclaim", actor_id=interaction.user.id):
            await interaction.response.send_message("This ticket was already unclaimed.", ephemeral=True)
            return
        logged = await self.cog.log_ticket(row["id"], "unclaim", interaction.user.id, target_id=int(old),
                                           old_value=old, new_value="unclaimed")
        suffix = " Ticket audit delivery failed; staff should inspect the private logs." if not logged else ""
        await interaction.response.send_message("Ticket unclaimed." + suffix)

    async def close(self, interaction: discord.Interaction):
        row = self.cog.ticket_by_channel(interaction.channel_id)
        if not row or not await self.permitted_staff(interaction, row):
            if not row:
                await interaction.response.send_message("Ticket record not found.", ephemeral=True)
            return
        await interaction.response.send_modal(TicketCloseReasonModal(self.cog, row["id"], interaction.user.id))


class TicketCloseReasonModal(SafeModal):
    def __init__(self, cog: "TicketCog", ticket_id: str, actor_id: int):
        super().__init__(title="Close ticket", bot=cog.bot)
        self.cog, self.ticket_id, self.actor_id = cog, ticket_id, actor_id
        self.reason = discord.ui.TextInput(label="Closure reason (optional)", default="Trade Complete", required=False, max_length=500)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("Confirm saving the transcript before closing this ticket.",
                                                view=TicketCloseConfirm(self.cog, self.ticket_id, self.actor_id, self.reason.value), ephemeral=True)


class TicketPrioritySelect(discord.ui.Select):
    def __init__(self, cog: "TicketCog", ticket_id: str):
        self.cog, self.ticket_id = cog, ticket_id
        options = [discord.SelectOption(label=x) for x in ("Low", "Normal", "High", "Urgent")]
        super().__init__(placeholder="Set priority", min_values=1, max_values=1, options=options,
                         custom_id=f"ticket:priority:{ticket_id}", row=0)

    async def callback(self, interaction: discord.Interaction):
        row = self.cog.ticket_by_channel(interaction.channel_id)
        if not row:
            await interaction.response.send_message("Ticket record not found.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not staff_for_category(interaction.user, row["category"]) and not has_minimum_role(interaction.user, "server_founder"):
            await interaction.response.send_message("Only authorized staff for this ticket category can change priority.", ephemeral=True)
            return
        new = self.values[0]
        old = row["priority"]
        stale = False
        try:
            with self.cog.bot.db.transaction() as connection:
                updated = connection.execute("UPDATE tickets SET priority=? WHERE id=? AND status='open' AND priority=?",
                                             (new, row["id"], old))
                if updated.rowcount != 1:
                    stale = True
                else:
                    connection.execute("INSERT INTO ticket_events(ticket_id,kind,actor_id,old_value,new_value,created_at) VALUES(?,?,?,?,?,?)",
                                       (row["id"], "priority", str(interaction.user.id), old, new, utcnow()))
        except Exception:
            await interaction.response.send_message("Priority could not be saved because the database write failed.", ephemeral=True)
            await self.cog.log_ticket(row["id"], "priority_failed", interaction.user.id,
                                      old_value=old, new_value=new, outcome="failure")
            return
        if stale:
            await interaction.response.send_message("Ticket priority changed before your selection was saved; refresh and try again.", ephemeral=True)
            return
        logged = await self.cog.log_ticket(row["id"], "priority", interaction.user.id, old_value=old, new_value=new)
        suffix = " Ticket audit delivery failed; staff should inspect the private logs." if not logged else ""
        await interaction.response.send_message(f"Priority changed from **{old}** to **{new}**.{suffix}")


class TicketCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._close_locks: dict[str, asyncio.Lock] = {}

    def register_persistent_views(self):
        for row in self.bot.db.all("SELECT id,button_label,button_emoji FROM panel_templates WHERE state='published'"):
            self.bot.add_view(TicketPanelView(self, row["id"], row["button_label"], row["button_emoji"]))
        for row in self.bot.db.all("SELECT id FROM tickets WHERE status='open'"):
            self.bot.add_view(TicketControls(self, row["id"]))

    def ticket_by_channel(self, channel_id: int | None):
        return self.bot.db.one("SELECT * FROM tickets WHERE channel_id=? AND status='open'", (str(channel_id),))

    def event(self, ticket_id: str, kind: str, actor_id: int | None, *, target_id=None, old_value=None, new_value=None, detail=None):
        self.bot.db.execute(
            "INSERT INTO ticket_events(ticket_id,kind,actor_id,target_id,old_value,new_value,detail,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (ticket_id, kind, str(actor_id) if actor_id else None, str(target_id) if target_id else None,
             str(old_value) if old_value is not None else None, str(new_value) if new_value is not None else None,
             detail, utcnow()),
        )

    async def log_ticket(self, ticket_id: str, action: str, actor_id: int, *, target_id=None, old_value=None, new_value=None, detail=None, outcome="success"):
        try:
            row = self.bot.db.one("SELECT * FROM tickets WHERE id=?", (ticket_id,))
            if not row:
                return False
            return await audit(self.bot, "ticket", action, guild=self.bot.get_guild(int(row["guild_id"])),
                               actor_id=actor_id, target_id=target_id,
                               details={"ticket_id": ticket_id, "category": row["category"], "channel_id": row["channel_id"],
                                        "old": old_value or "—", "new": new_value or "—", "detail": detail or "—"},
                               outcome=outcome, channel_key="ticket_logs")
        except Exception as exc:
            self.bot.logger.error("Ticket audit failed for %s/%s (%s)", ticket_id, action, type(exc).__name__)
            return False

    def update_claim(self, ticket_id: str, user_id: int | None, event_name: str, *, actor_id: int) -> bool:
        with self.bot.db.transaction() as connection:
            row = connection.execute("SELECT claimant_id,status FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            if not row or row["status"] != "open":
                return False
            old_id = row["claimant_id"]
            if (event_name == "claim" and old_id is not None) or (event_name == "unclaim" and old_id is None):
                return False
            updated = connection.execute("UPDATE tickets SET claimant_id=? WHERE id=? AND status='open' AND claimant_id IS ?",
                                         (str(user_id) if user_id is not None else None, ticket_id, old_id))
            if updated.rowcount != 1:
                return False
            new_id = str(user_id) if user_id is not None else None
            target_id = new_id if new_id is not None else old_id
            connection.execute("INSERT INTO ticket_events(ticket_id,kind,actor_id,target_id,old_value,new_value,created_at) VALUES(?,?,?,?,?,?,?)",
                               (ticket_id, event_name, str(actor_id), target_id, old_id, new_id, utcnow()))
            return True

    async def open_ticket(self, interaction: discord.Interaction, panel: dict, answers: dict[str, str]):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        user = interaction.user
        category = panel["ticket_type"]
        if not guild or not isinstance(user, discord.Member):
            await interaction.followup.send("Tickets can only be opened in a server.", ephemeral=True)
            return
        if category == "rankup" and not has_minimum_role(user, "server_founder"):
            await interaction.followup.send("Only Server Founder and above may open a staff rank-up ticket.", ephemeral=True)
            return
        try:
            ping_role_id = validate_discord_id(str(panel["support_role_id"]))
        except ValueError:
            await interaction.followup.send("The saved Support Role to Ping configuration is invalid. Staff have been alerted; no ticket was opened.", ephemeral=True)
            await audit(self.bot, "ticket", "support_role_invalid", guild=guild, actor_id=user.id,
                        details={"panel_id": panel.get("id"), "role_id": safe_text(str(panel.get("support_role_id")), 40)},
                        outcome="failure", channel_key="audit_logs")
            return
        ping_role = guild.get_role(ping_role_id)
        if ping_role is None or ping_role.is_default():
            await interaction.followup.send("The configured Support Role to Ping no longer exists. Staff have been alerted.", ephemeral=True)
            await audit(self.bot, "ticket", "support_role_missing", guild=guild, actor_id=user.id,
                        details={"panel_id": panel["id"], "role_id": panel["support_role_id"]}, outcome="failure", channel_key="audit_logs")
            return
        bot_member = guild.me
        if bot_member is None:
            await interaction.followup.send("The bot member record is unavailable; no ticket was opened.", ephemeral=True)
            return
        if not ping_role.mentionable and not bot_member.guild_permissions.mention_everyone:
            await interaction.followup.send("The configured Support Role to Ping cannot be mentioned with current Discord permissions. Staff have been alerted.", ephemeral=True)
            await audit(self.bot, "ticket", "support_role_unusable", guild=guild, actor_id=user.id,
                        target_id=ping_role.id, details={"panel_id": panel["id"]}, outcome="failure", channel_key="audit_logs")
            return
        existing = self.bot.db.one("SELECT id,channel_id FROM tickets WHERE guild_id=? AND opener_id=? AND category=? AND status IN ('creating','open')",
                                   (str(guild.id), str(user.id), category))
        if existing:
            await interaction.followup.send(f"You already have an open {category} ticket: <#{existing['channel_id']}>.", ephemeral=True)
            return
        ticket_id = uuid.uuid4().hex[:12]
        try:
            self.bot.db.execute(
                "INSERT INTO tickets(id,guild_id,category,opener_id,panel_id,support_role_id,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (ticket_id, str(guild.id), category, str(user.id), panel["id"], str(ping_role.id), "creating", utcnow()),
            )
        except Exception:
            await interaction.followup.send("A ticket is already open for this category or the database is unavailable.", ephemeral=True)
            return
        category_roles = category_staff_roles(guild, category)
        if not category_roles:
            self.bot.db.execute("DELETE FROM tickets WHERE id=?", (ticket_id,))
            await interaction.followup.send("No configured staff role exists for this ticket category. Staff have been alerted.", ephemeral=True)
            await audit(self.bot, "ticket", "staff_role_missing", guild=guild, actor_id=user.id,
                        details={"ticket_id": ticket_id, "category": category}, outcome="failure")
            return
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
        }
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, attach_files=True, embed_links=True)
        for staff_role in category_roles:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
        slug = re.sub(r"[^a-z0-9-]+", "-", user.name.lower()).strip("-")[:40] or "member"
        prefix = {"middleman": "mm", "support": "support", "rankup": "rankup"}[category]
        channel_name = f"{prefix}・{slug}"[:95]
        try:
            channel = await guild.create_text_channel(channel_name, overwrites=overwrites, reason=f"Apex {category} ticket {ticket_id}")
        except (discord.Forbidden, discord.HTTPException) as exc:
            self.bot.db.execute("DELETE FROM tickets WHERE id=?", (ticket_id,))
            await interaction.followup.send(f"Discord could not create the private ticket ({type(exc).__name__}).", ephemeral=True)
            await audit(self.bot, "ticket", "open_failed", guild=guild, actor_id=user.id,
                        details={"ticket_id": ticket_id, "category": category}, outcome="failure", channel_key="ticket_logs")
            return
        try:
            with self.bot.db.transaction() as connection:
                connection.execute("UPDATE tickets SET status='open',channel_id=? WHERE id=?", (str(channel.id), ticket_id))
                connection.execute("INSERT INTO ticket_events(ticket_id,kind,actor_id,target_id,detail,created_at) VALUES(?,?,?,?,?,?)",
                                   (ticket_id, "open", str(user.id), str(user.id), "intake form submitted", utcnow()))
        except Exception as exc:
            try:
                await channel.delete(reason="Apex ticket persistence failed")
            except discord.HTTPException:
                pass
            self.bot.db.execute("DELETE FROM tickets WHERE id=?", (ticket_id,))
            await interaction.followup.send(f"Ticket setup could not be saved ({type(exc).__name__}); no open ticket is reported.", ephemeral=True)
            await audit(self.bot, "ticket", "open_persistence_failed", guild=guild, actor_id=user.id,
                        details={"ticket_id": ticket_id, "category": category}, outcome="failure", channel_key="ticket_logs")
            return
        panel_embed = discord.Embed(title=f"{category.title()} request • {ticket_id}", colour=PRIMARY)
        panel_embed.add_field(name="Opened by", value=f"{user.mention} (`{user.id}`)", inline=False)
        panel_embed.add_field(name="Priority", value="Normal", inline=True)
        for title, answer in answers.items():
            panel_embed.add_field(name=safe_text(title, 256), value=safe_text(answer, 1000) or "—", inline=False)
        panel_embed.set_footer(text="Apex MiddleMan Services • Private ticket")
        mentions = discord.AllowedMentions(users=[user], roles=[ping_role], everyone=False, replied_user=False)
        ping_status = "sent"
        try:
            await channel.send(f"{user.mention} • {ping_role.mention}", embed=panel_embed,
                               view=TicketControls(self, ticket_id), allowed_mentions=mentions)
        except discord.HTTPException as exc:
            ping_status = "failed"
            # Channel exists; keep the record and provide a clear staff-visible failure.
            try:
                await channel.send("Ticket opened, but the initial role notification failed. Staff may continue here.",
                                   view=TicketControls(self, ticket_id), allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass
            await self.log_ticket(ticket_id, "opening_notification_failed", user.id, detail=type(exc).__name__, outcome="failure")
        user_notice = f"Your private {category} ticket is ready: {channel.mention}."
        if ping_status != "sent":
            user_notice += " The ticket exists, but the opening notification may not have been delivered."
        logged = await self.log_ticket(ticket_id, "open", user.id, target_id=user.id,
                                       detail=f"ping_role={ping_role.id}; ping_status={ping_status}")
        if not logged:
            user_notice += " Ticket audit delivery also failed; staff should inspect the private logs."
        await interaction.followup.send(user_notice, ephemeral=True)

    async def _save_transcript(self, row, channel: discord.TextChannel, closer: discord.abc.User, reason: str) -> discord.Message:
        log_channel = channel.guild.get_channel(CHANNEL_IDS["ticket_logs"])
        if not isinstance(log_channel, discord.TextChannel):
            raise RuntimeError("Configured ticket transcript channel is missing or not text.")
        if not is_private_staff_channel(log_channel, channel.guild):
            raise RuntimeError("The configured transcript channel must deny public and non-staff role access.")
        if not log_channel.permissions_for(channel.guild.me).send_messages or not log_channel.permissions_for(channel.guild.me).attach_files:
            raise RuntimeError("Bot needs Send Messages and Attach Files in the private transcript channel.")
        events = self.bot.db.all("SELECT * FROM ticket_events WHERE ticket_id=? ORDER BY id", (row["id"],))
        lines = [
            f"Apex Ticket Transcript • ID {row['id']}", f"Category: {row['category']}",
            f"Opener ID: {row['opener_id']}", f"Claimant ID: {row['claimant_id'] or 'None'}",
            f"Priority: {row['priority']}", f"Closure actor ID: {closer.id}",
            f"Closure reason: {safe_text(reason, 500)}", f"Closed at: {utcnow()}",
            "Transcript status: saved successfully before ticket channel deletion", "", "TIMELINE",
        ]
        for event in events:
            kind = "close" if event["kind"] == "close_requested" else event["kind"]
            lines.append(safe_text(f"[{event['created_at']}] {kind} actor={event['actor_id']} target={event['target_id']} old={event['old_value']} new={event['new_value']} {event['detail'] or ''}", 8000))
        lines.extend(["", "MESSAGES"])
        byte_count = sum(len(line.encode("utf-8")) + 1 for line in lines)
        async for message in channel.history(limit=None, oldest_first=True):
            body = safe_text(message.content, 8000)
            line = safe_text(f"[{message.created_at.isoformat()}] {message.author} ({message.author.id}): {body}", 9000)
            byte_count += len(line.encode("utf-8")) + 1
            if byte_count > 20_000_000:
                raise RuntimeError("Transcript exceeds the 20 MB safe upload size; ticket kept open for staff review.")
            lines.append(line)
            for attachment in message.attachments:
                line = safe_text(f"  Attachment: {attachment.filename} • {attachment.url}", 3000)
                byte_count += len(line.encode("utf-8")) + 1
                if byte_count > 20_000_000:
                    raise RuntimeError("Transcript exceeds the 20 MB safe upload size; ticket kept open for staff review.")
                lines.append(line)
            for sticker in message.stickers:
                line = safe_text(f"  Sticker: {sticker.name}", 256)
                byte_count += len(line.encode("utf-8")) + 1
                if byte_count > 20_000_000:
                    raise RuntimeError("Transcript exceeds the 20 MB safe upload size; ticket kept open for staff review.")
                lines.append(line)
        data = "\n".join(lines).encode("utf-8")
        upload = discord.File(io.BytesIO(data), filename=f"apex-ticket-{row['id']}.txt")
        return await log_channel.send(
            content=f"Private transcript • `{row['id']}` • {row['category']} • opener `{row['opener_id']}`",
            file=upload, allowed_mentions=discord.AllowedMentions.none(),
        )

    async def close_ticket(self, ticket_id: str, closer: discord.abc.User, reason: str = "Trade Complete") -> tuple[bool, str]:
        lock = self._close_locks.setdefault(ticket_id, asyncio.Lock())
        async with lock:
            return await self._close_ticket_impl(ticket_id, closer, reason)

    async def _close_ticket_impl(self, ticket_id: str, closer: discord.abc.User, reason: str = "Trade Complete") -> tuple[bool, str]:
        row = self.bot.db.one("SELECT * FROM tickets WHERE id=? AND status='open'", (ticket_id,))
        if not row:
            return False, "This ticket is not open."
        guild = self.bot.get_guild(int(row["guild_id"]))
        channel = guild.get_channel(int(row["channel_id"])) if guild and row["channel_id"] else None
        if not isinstance(channel, discord.TextChannel):
            return False, "Ticket channel is missing; no transcript was saved and the record remains open for staff review."
        reason = safe_text(reason.strip() or "Trade Complete", 500)
        try:
            self.event(ticket_id, "close_requested", closer.id, detail=reason)
        except Exception as exc:
            await self.log_ticket(ticket_id, "close_request_persistence_failed", closer.id, detail=type(exc).__name__, outcome="failure")
            return False, "Closure could not be recorded. The ticket remains open."
        try:
            transcript_message = await self._save_transcript(row, channel, closer, reason)
        except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
            try:
                self.event(ticket_id, "transcript_failure", closer.id, detail=type(exc).__name__)
            except Exception:
                self.bot.logger.error("Could not save transcript failure event for ticket %s", ticket_id)
            await self.log_ticket(ticket_id, "transcript_failure", closer.id, detail=type(exc).__name__, outcome="failure")
            # Alert category staff in the ticket itself; do not delete it.
            try:
                alert_role = guild.get_role(ROLE_IDS[TICKET_MINIMUM_ROLE[row["category"]]])
                role_mentions = [alert_role.mention] if alert_role else []
                await channel.send(f"Transcript saving failed. The ticket remains open. Authorized staff: {' '.join(role_mentions)}",
                                   allowed_mentions=discord.AllowedMentions(roles=[alert_role] if alert_role else [], users=False, everyone=False))
            except discord.HTTPException:
                pass
            return False, f"Transcript could not be saved ({type(exc).__name__}). The ticket remains open; staff were alerted where possible."
        try:
            with self.bot.db.transaction() as connection:
                connection.execute("INSERT INTO ticket_events(ticket_id,kind,actor_id,detail,created_at) VALUES(?,?,?,?,?)",
                                   (ticket_id, "close", str(closer.id), reason, utcnow()))
                updated = connection.execute("UPDATE tickets SET status='closed',closed_at=?,close_reason=?,transcript_saved=1 WHERE id=? AND status='open'",
                                             (utcnow(), reason, ticket_id))
                if updated.rowcount != 1:
                    raise RuntimeError("Ticket state changed during closure")
        except Exception as exc:
            await self.log_ticket(ticket_id, "transcript_saved_db_failed", closer.id, detail=type(exc).__name__, outcome="failure")
            return False, "Transcript uploaded, but the database close record failed. The channel was kept open for staff review."
        logged = await self.log_ticket(ticket_id, "close", closer.id, detail=f"reason={reason}; transcript_message={transcript_message.id}")
        try:
            await channel.delete(reason=f"Ticket {ticket_id} closed by {closer.id}; transcript saved")
        except (discord.Forbidden, discord.HTTPException) as exc:
            suffix = " The closure audit could not be fully delivered." if not logged else ""
            return True, f"Transcript saved. Discord could not delete the channel ({type(exc).__name__}); staff can remove it manually.{suffix}"
        suffix = " The closure audit could not be fully delivered." if not logged else ""
        return True, "Transcript saved successfully and the ticket channel was deleted." + suffix

    @commands.command(name="adduser")
    @commands.guild_only()
    async def adduser(self, ctx: commands.Context, member: discord.Member):
        row = self.ticket_by_channel(ctx.channel.id)
        if not row:
            await send_error(ctx, "Use `!adduser` inside an open Apex ticket.")
            return
        if not staff_for_category(ctx.author, row["category"]) and not has_minimum_role(ctx.author, "server_founder"):
            await send_error(ctx, "Only authorized staff for this category and Server Founder+ may add one user.")
            return
        if ctx.author.id == int(row["opener_id"]):
            await send_error(ctx, "The ticket opener cannot use `!adduser`; ask authorized staff to add any additional participant.")
            return
        if member.id == int(row["opener_id"]):
            await send_error(ctx, "The ticket opener already has access.")
            return
        if not isinstance(ctx.channel, discord.TextChannel) or not ctx.channel.permissions_for(ctx.guild.me).manage_channels:
            await send_error(ctx, "The bot needs Manage Channels to add a user to this ticket.")
            return
        if not ctx.author.guild_permissions.manage_channels:
            await send_error(ctx, "Adding a user requires your Discord Manage Channels permission.")
            return
        try:
            await ctx.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True,
                                              attach_files=True, embed_links=True, reason=f"Added by {ctx.author.id} to ticket {row['id']}")
        except (discord.Forbidden, discord.HTTPException) as exc:
            await send_error(ctx, f"Discord did not confirm access for that user ({type(exc).__name__}).")
            await self.log_ticket(row["id"], "add_user", ctx.author.id, target_id=member.id, outcome="failure")
            return
        try:
            with self.bot.db.transaction() as connection:
                connection.execute("INSERT INTO ticket_events(ticket_id,kind,actor_id,target_id,detail,created_at) VALUES(?,?,?,?,?,?)",
                                   (row["id"], "add_user", str(ctx.author.id), str(member.id), "explicit access to this ticket only", utcnow()))
        except Exception as exc:
            rolled_back = False
            try:
                await ctx.channel.set_permissions(member, overwrite=None, reason=f"Rollback unrecorded Apex ticket access for {row['id']}")
                rolled_back = True
            except (discord.Forbidden, discord.HTTPException):
                pass
            await audit(self.bot, "ticket", "add_user_record_failed", guild=ctx.guild,
                        actor_id=ctx.author.id, target_id=member.id,
                        details={"ticket_id": row["id"], "rollback_succeeded": rolled_back,
                                 "error_type": type(exc).__name__}, outcome="failure", channel_key="ticket_logs")
            note = " The access grant was rolled back." if rolled_back else " The access grant may still exist; staff must inspect this ticket immediately."
            await send_error(ctx, "Discord changed access, but the ticket event could not be saved." + note)
            return
        logged = await self.log_ticket(row["id"], "add_user", ctx.author.id, target_id=member.id,
                                       detail="explicit access to this ticket only")
        message = f"{member.mention} can access this ticket only."
        if not logged:
            message += " Access was granted, but ticket log delivery failed."
        await ctx.send(embed=embed("User added", message, colour=PRIMARY), allowed_mentions=discord.AllowedMentions(users=[member]))
