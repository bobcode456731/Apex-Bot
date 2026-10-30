"""Interactive ticket-panel and custom embed/trigger builders."""

from __future__ import annotations

import json
import uuid

import discord
from discord.ext import commands

from .common import PRIMARY, SafeModal, SafeView, audit, confirm, embed, has_minimum_role, is_private_staff_channel, require_native, require_role, send_error
from .config import ROLE_IDS
from .database import utcnow
from .tickets import PANEL_CHANNEL, TicketPanelView
from .validation import parse_colour, safe_text, validate_button_emoji, validate_discord_id, validate_embed, validate_trigger, validate_url


TICKET_TYPES = {"middleman", "support", "rankup"}


def parse_fields(value: str) -> list[dict]:
    if not value.strip():
        return []
    try:
        fields = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("Fields must be a JSON array: [{\"name\":\"...\",\"value\":\"...\",\"inline\":false}].") from exc
    if not isinstance(fields, list) or any(not isinstance(f, dict) or "name" not in f or "value" not in f for f in fields):
        raise ValueError("Fields must be an array of objects containing name and value.")
    parsed = []
    for field in fields:
        inline = field.get("inline", False)
        if isinstance(inline, str) and inline.lower() in {"true", "false"}:
            inline = inline.lower() == "true"
        if not isinstance(inline, bool):
            raise ValueError("Field inline must be true or false.")
        parsed.append({"name": str(field["name"]), "value": str(field["value"]), "inline": inline})
    return parsed


def make_embed(title: str, description: str, fields: list[dict], colour: int, image_url: str | None, footer: str) -> discord.Embed:
    out = discord.Embed(title=title or None, description=description or None, colour=colour)
    for item in fields:
        out.add_field(name=item["name"], value=item["value"], inline=item.get("inline", False))
    if image_url:
        out.set_image(url=image_url)
    if footer:
        out.set_footer(text=footer)
    return out


class OwnerView(SafeView):
    def __init__(self, owner_id: int, bot=None):
        super().__init__(timeout=300, bot=bot)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This builder session belongs to another staff member.", ephemeral=True)
            return False
        expected_guild_id = getattr(self, "guild_id", None) or getattr(getattr(self, "wizard", None), "guild_id", None)
        if expected_guild_id is not None and str(interaction.guild_id) != str(expected_guild_id):
            await interaction.response.send_message("This builder session belongs to another server.", ephemeral=True)
            return False
        if not isinstance(interaction.user, discord.Member) or not has_minimum_role(interaction.user, "server_founder"):
            await interaction.response.send_message("Your Server Founder+ builder access changed; this session is no longer authorized.", ephemeral=True)
            return False
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Builder actions require your Discord Manage Server permission.", ephemeral=True)
            return False
        return True


class PanelWizard:
    def __init__(self, cog: "BuilderCog", guild_id: int, creator_id: int, category: str = "middleman", data: dict | None = None, existing_id: str | None = None):
        self.cog = cog
        self.guild_id = guild_id
        self.creator_id = creator_id
        self.category = category
        self.existing_id = existing_id
        self.data = data or {"name": "", "title": "", "description": "", "fields": [], "channel_id": "", "support_role_id": "", "colour": "#8B5CF6", "image_url": "", "footer": "", "button_label": "Open ticket", "button_emoji": ""}

    def current_embed(self):
        return make_embed(self.data.get("title", ""), self.data.get("description", ""),
                          self.data.get("fields", []), parse_colour(self.data.get("colour", "#8B5CF6")),
                          self.data.get("image_url") or None, self.data.get("footer", ""))

    def validate(self, guild: discord.Guild):
        data = self.data
        if guild is None or guild.id != self.guild_id:
            raise ValueError("This panel can only be saved in the server where its builder was opened.")
        if self.category not in TICKET_TYPES:
            raise ValueError("Select one approved ticket type.")
        if not data.get("name", "").strip() or len(data["name"]) > 80:
            raise ValueError("Panel name is required and limited to 80 characters.")
        fields = data.get("fields", [])
        validate_embed(data.get("title", ""), data.get("description", ""), fields, data.get("footer", ""))
        colour = parse_colour(data.get("colour", "#8B5CF6"))
        image = validate_url(data.get("image_url", ""))
        try:
            channel_id = validate_discord_id(str(data.get("channel_id", "")))
            role_id = validate_discord_id(str(data.get("support_role_id", "")))
        except ValueError as exc:
            raise ValueError("Channel and role must be valid Discord IDs (15–22 digits).") from exc
        channel = guild.get_channel(channel_id)
        role = guild.get_role(role_id)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("Destination must be an existing text channel in this server.")
        if channel.id != PANEL_CHANNEL[self.category]:
            raise ValueError(f"{self.category.title()} panels must use their configured destination channel {PANEL_CHANNEL[self.category]}.")
        if self.category == "rankup":
            if channel.id != PANEL_CHANNEL["rankup"]:
                raise ValueError("Staff rank-up panels must use the configured staff rank-up panel channel.")
            if not is_private_staff_channel(channel, guild):
                raise ValueError("The staff rank-up panel channel must deny public and non-staff role access.")
        if role is None:
            raise ValueError("Support Role to Ping must be an existing server role.")
        if role.is_default():
            raise ValueError("@everyone cannot be used as the Support Role to Ping.")
        if guild.me is None:
            raise ValueError("Bot member record is unavailable in this server.")
        if not role.mentionable and not guild.me.guild_permissions.mention_everyone:
            raise ValueError("The bot must be able to mention this role (role mentionable or bot has Mention Everyone).")
        channel_permissions = channel.permissions_for(guild.me)
        if not channel_permissions.view_channel or not channel_permissions.send_messages or not channel_permissions.embed_links:
            raise ValueError("The bot needs View Channel, Send Messages, and Embed Links in the destination.")
        button_label = data.get("button_label", "").strip()
        if not 1 <= len(button_label) <= 80:
            raise ValueError("Button label must contain 1 to 80 characters.")
        if len(data.get("button_emoji", "")) > 40:
            raise ValueError("Button emoji must be a Unicode emoji or a server emoji mention.")
        validate_button_emoji(data.get("button_emoji"), guild)
        return channel, role, colour, image

    def save(self, guild: discord.Guild) -> str:
        channel, role, colour, image = self.validate(guild)
        panel_id = self.existing_id or uuid.uuid4().hex[:12]
        now = utcnow()
        data = self.data
        with self.cog.bot.db.transaction() as connection:
            if self.existing_id:
                updated = connection.execute(
                "UPDATE panel_templates SET name=?,ticket_type=?,title=?,description=?,fields_json=?,colour=?,image_url=?,footer=?,button_label=?,button_emoji=?,channel_id=?,support_role_id=?,updated_at=? WHERE id=? AND guild_id=?",
                (safe_text(data["name"].strip(), 80), self.category, data.get("title", ""), data.get("description", ""),
                 json.dumps(data.get("fields", []), ensure_ascii=False), colour, image, data.get("footer", ""),
                 data.get("button_label", "Open ticket").strip(), data.get("button_emoji", "").strip() or None,
                 str(channel.id), str(role.id), now, panel_id, str(guild.id)),
                )
                if updated.rowcount != 1:
                    raise ValueError("Saved panel no longer exists in this server.")
            else:
                connection.execute(
                "INSERT INTO panel_templates(id,guild_id,name,ticket_type,title,description,fields_json,colour,image_url,footer,button_label,button_emoji,channel_id,support_role_id,creator_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (panel_id, str(guild.id), safe_text(data["name"].strip(), 80), self.category, data.get("title", ""), data.get("description", ""),
                 json.dumps(data.get("fields", []), ensure_ascii=False), colour, image, data.get("footer", ""),
                 data.get("button_label", "Open ticket").strip(), data.get("button_emoji", "").strip() or None,
                 str(channel.id), str(role.id), str(self.creator_id), now, now),
                )
        return panel_id


class PanelStartView(OwnerView):
    def __init__(self, wizard: PanelWizard):
        super().__init__(wizard.creator_id, wizard.cog.bot)
        self.wizard = wizard
        self.add_item(PanelTypeSelect(wizard))

    @discord.ui.button(label="Continue to content", style=discord.ButtonStyle.primary, row=1)
    async def content(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.wizard.category not in TICKET_TYPES:
            await interaction.response.send_message("Choose a ticket type first.", ephemeral=True)
            return
        await interaction.response.send_modal(PanelContentModal(self.wizard))


class PanelTypeSelect(discord.ui.Select):
    def __init__(self, wizard: PanelWizard):
        self.wizard = wizard
        options = [discord.SelectOption(label="Middleman Request", value="middleman"),
                   discord.SelectOption(label="General Support", value="support"),
                   discord.SelectOption(label="Staff-only Rank-up", value="rankup", description="Only Server Founder and above can open")]
        super().__init__(placeholder="Select one ticket workflow", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.wizard.category = self.values[0]
        await interaction.response.edit_message(content=f"Selected **{self.wizard.category}**. Continue to enter panel content.", view=self.view)


class PanelContentModal(SafeModal):
    def __init__(self, wizard: PanelWizard):
        super().__init__(title="Ticket panel • content", bot=wizard.cog.bot)
        self.wizard = wizard
        fields_json = json.dumps(wizard.data.get("fields", []), ensure_ascii=False)
        fields_over_limit = len(fields_json) > 4000
        self.name = discord.ui.TextInput(label="Draft name", default=wizard.data.get("name") or f"{wizard.category.title()} panel", max_length=80)
        self.title_input = discord.ui.TextInput(label="Embed title", default=wizard.data.get("title", ""), required=False, max_length=256)
        self.description = discord.ui.TextInput(label="Description", default=wizard.data.get("description", ""), required=False, style=discord.TextStyle.paragraph, max_length=4000)
        self.fields_input = discord.ui.TextInput(label="Fields JSON (optional)", default="" if fields_over_limit else fields_json,
                                                 required=False, style=discord.TextStyle.paragraph, max_length=4000,
                                                 placeholder="Saved fields are preserved when blank; manage them after saving.")
        for item in (self.name, self.title_input, self.description, self.fields_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            fields = parse_fields(self.fields_input.value) if self.fields_input.value.strip() else self.wizard.data.get("fields", [])
            validate_embed(self.title_input.value, self.description.value, fields)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.wizard.data.update(name=self.name.value, title=self.title_input.value,
                                description=self.description.value, fields=fields)
        await interaction.response.send_message("Content saved in this builder session. Add delivery and appearance settings next.",
                                                view=PanelDeliveryView(self.wizard), ephemeral=True)


class PanelDeliveryView(OwnerView):
    def __init__(self, wizard: PanelWizard):
        super().__init__(wizard.creator_id, wizard.cog.bot)
        self.wizard = wizard
        self.add_item(PanelChannelSelect(wizard))
        self.add_item(PanelRoleSelect(wizard))

    @discord.ui.button(label="Colour, image, and footer", style=discord.ButtonStyle.primary, row=2)
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PanelDeliveryModal(self.wizard))


class PanelChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, wizard: PanelWizard):
        self.wizard = wizard
        super().__init__(channel_types=[discord.ChannelType.text], placeholder="Select the panel destination channel", row=0)

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        if channel.guild.id != self.wizard.guild_id:
            await interaction.response.send_message("Choose a text channel from this server.", ephemeral=True)
            return
        self.wizard.data["channel_id"] = str(channel.id)
        await interaction.response.edit_message(content=f"Destination selected: {channel.mention}. Now select the Support Role to Ping, then set appearance.", view=self.view)


class PanelRoleSelect(discord.ui.RoleSelect):
    def __init__(self, wizard: PanelWizard):
        self.wizard = wizard
        super().__init__(placeholder="Select the Support Role to Ping", row=1)

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        if role.guild.id != self.wizard.guild_id:
            await interaction.response.send_message("Choose a role from this server.", ephemeral=True)
            return
        self.wizard.data["support_role_id"] = str(role.id)
        await interaction.response.edit_message(content=f"Support Role to Ping selected: {role.mention}. This role only receives opening notifications; ticket access follows the ticket category.", view=self.view,
                                                allowed_mentions=discord.AllowedMentions.none())


class PanelDeliveryModal(SafeModal):
    def __init__(self, wizard: PanelWizard):
        super().__init__(title="Ticket panel • delivery", bot=wizard.cog.bot)
        self.wizard = wizard
        d = wizard.data
        self.colour = discord.ui.TextInput(label="Embed colour (hex)", default=d.get("colour", "#8B5CF6"), max_length=7)
        self.image = discord.ui.TextInput(label="Image URL (optional)", default=d.get("image_url", ""), required=False, max_length=300)
        self.footer = discord.ui.TextInput(label="Footer", default=d.get("footer", "✦ 𝙰𝚙𝚎𝚡 • Trade Smart. Trade Safely."), required=False, max_length=2048)
        for item in (self.colour, self.image, self.footer):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            parse_colour(self.colour.value)
            validate_url(self.image.value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.wizard.data.update(colour=self.colour.value, image_url=self.image.value,
                                footer=self.footer.value)
        await interaction.response.send_message("Delivery settings saved. Configure the panel button next.",
                                                view=PanelButtonView(self.wizard), ephemeral=True)


class PanelButtonView(OwnerView):
    def __init__(self, wizard: PanelWizard):
        super().__init__(wizard.creator_id, wizard.cog.bot)
        self.wizard = wizard

    @discord.ui.button(label="Button settings", style=discord.ButtonStyle.primary)
    async def button_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PanelButtonModal(self.wizard))


class PanelButtonModal(SafeModal):
    def __init__(self, wizard: PanelWizard):
        super().__init__(title="Ticket panel • button", bot=wizard.cog.bot)
        self.wizard = wizard
        self.label = discord.ui.TextInput(label="Button label", default=wizard.data.get("button_label", "Open ticket"), max_length=80)
        self.emoji = discord.ui.TextInput(label="Button emoji (optional)", default=wizard.data.get("button_emoji", ""), required=False, max_length=40)
        self.add_item(self.label)
        self.add_item(self.emoji)

    async def on_submit(self, interaction: discord.Interaction):
        self.wizard.data.update(button_label=self.label.value, button_emoji=self.emoji.value)
        await interaction.response.send_message("Button settings saved. Preview the full panel or save this draft.",
                                                view=PanelSaveView(self.wizard), ephemeral=True)


class PanelSaveView(OwnerView):
    def __init__(self, wizard: PanelWizard):
        super().__init__(wizard.creator_id, wizard.cog.bot)
        self.wizard = wizard

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            channel, role, _, _ = self.wizard.validate(interaction.guild)
        except (ValueError, TypeError) as exc:
            await interaction.response.send_message(f"Preview needs valid settings first: {exc}", ephemeral=True)
            return
        out = self.wizard.current_embed()
        summary = (f"Type: **{self.wizard.category.title()}** • Destination: {channel.mention}\n"
                   f"Support Role to Ping: {role.mention} (notification only) • Button: "
                   f"{self.wizard.data.get('button_emoji', '')} {self.wizard.data.get('button_label', 'Open ticket')}")
        await interaction.response.send_message(content=summary, embed=out, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Save draft", style=discord.ButtonStyle.success)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            panel_id = self.wizard.save(interaction.guild)
        except (ValueError, TypeError, discord.HTTPException) as exc:
            await interaction.response.send_message(f"Draft not saved: {exc}", ephemeral=True)
            return
        if self.wizard.existing_id:
            row = self.wizard.cog.bot.db.one("SELECT state FROM panel_templates WHERE id=?", (panel_id,))
            next_step = f"The saved template changed; the published message was left untouched. Use `!panelbuilder update {panel_id}` to update that message after confirmation." if row and row["state"] == "published" else f"Publish this draft explicitly with `!panelbuilder publish {panel_id}`."
            await interaction.response.send_message(f"Panel `{panel_id}` saved. {next_step}", ephemeral=True)
        else:
            await interaction.response.send_message(f"Panel draft `{panel_id}` saved. Publish it explicitly with `!panelbuilder publish {panel_id}`.", ephemeral=True)
        await audit(self.wizard.cog.bot, "panel", "draft_saved", guild=interaction.guild,
                    actor_id=interaction.user.id, details={"panel_id": panel_id, "type": self.wizard.category}, outcome="success")


class EmbedWizard:
    def __init__(self, cog: "BuilderCog", guild_id: int, creator_id: int, data: dict | None = None):
        self.cog, self.guild_id, self.creator_id = cog, guild_id, creator_id
        self.data = data or {"trigger": "", "aliases": [], "title": "", "description": "", "fields": [], "colour": "#8B5CF6", "image_url": "", "footer": ""}

    def save(self, guild: discord.Guild, *, existing_id: str | None = None) -> str:
        data = self.data
        if guild is None or guild.id != self.guild_id:
            raise ValueError("This custom embed can only be saved in the server where its builder was opened.")
        trigger = data["trigger"].strip().lower()
        aliases = [item.strip().lower() for item in data["aliases"]]
        names = validate_trigger(trigger, aliases)
        validate_embed(data["title"], data["description"], data["fields"], data["footer"])
        colour = parse_colour(data["colour"])
        image = validate_url(data["image_url"])
        now = utcnow()
        embed_id = existing_id or uuid.uuid4().hex[:12]
        with self.cog.bot.db.transaction() as connection:
            rows = connection.execute("SELECT id,trigger,aliases_json FROM custom_embeds WHERE guild_id=?", (str(guild.id),)).fetchall()
            for row in rows:
                if existing_id and row["id"] == existing_id:
                    continue
                used = [row["trigger"], *json.loads(row["aliases_json"])]
                if any(name in used for name in names):
                    raise ValueError("That trigger or alias is already used by a saved embed.")
            if existing_id:
                updated = connection.execute(
                    "UPDATE custom_embeds SET trigger=?,aliases_json=?,title=?,description=?,fields_json=?,colour=?,image_url=?,footer=?,updated_at=? WHERE id=? AND guild_id=?",
                    (trigger, json.dumps(aliases), data["title"], data["description"],
                     json.dumps(data["fields"], ensure_ascii=False), colour, image, data["footer"], now, existing_id, str(guild.id)),
                )
                if updated.rowcount != 1:
                    raise ValueError("Saved custom embed no longer exists in this server.")
            else:
                connection.execute(
                    "INSERT INTO custom_embeds(id,guild_id,trigger,aliases_json,title,description,fields_json,colour,image_url,footer,creator_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (embed_id, str(guild.id), trigger, json.dumps(aliases), data["title"], data["description"],
                     json.dumps(data["fields"], ensure_ascii=False), colour, image, data["footer"], str(self.creator_id), now, now),
                )
        return embed_id


class EmbedWizardStart(OwnerView):
    def __init__(self, wizard: EmbedWizard, existing_id: str | None = None):
        super().__init__(wizard.creator_id, wizard.cog.bot)
        self.wizard, self.existing_id = wizard, existing_id

    @discord.ui.button(label="Embed content and trigger", style=discord.ButtonStyle.primary)
    async def content(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EmbedContentModal(self.wizard, self.existing_id))


class EmbedContentModal(SafeModal):
    def __init__(self, wizard: EmbedWizard, existing_id: str | None):
        super().__init__(title="Custom embed • trigger and content", bot=wizard.cog.bot)
        self.wizard, self.existing_id = wizard, existing_id
        data = wizard.data
        fields_json = json.dumps(data.get("fields", []), ensure_ascii=False)
        fields_over_limit = len(fields_json) > 4000
        self.trigger = discord.ui.TextInput(label="Prefix trigger (without !)", default=data.get("trigger", ""), max_length=32)
        self.aliases = discord.ui.TextInput(label="Aliases (comma separated)", default=", ".join(data.get("aliases", [])), required=False, max_length=500)
        self.title_input = discord.ui.TextInput(label="Embed title", default=data.get("title", ""), required=False, max_length=256)
        self.description = discord.ui.TextInput(label="Description", default=data.get("description", ""), required=False, style=discord.TextStyle.paragraph, max_length=4000)
        self.fields_input = discord.ui.TextInput(label="Fields JSON (optional)", default="" if fields_over_limit else fields_json,
                                                 required=False, style=discord.TextStyle.paragraph, max_length=4000,
                                                 placeholder="Saved fields are preserved when blank; manage them after saving.")
        for item in (self.trigger, self.aliases, self.title_input, self.description, self.fields_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            aliases = [x.strip().lower() for x in self.aliases.value.split(",") if x.strip()]
            validate_trigger(self.trigger.value, aliases)
            fields = parse_fields(self.fields_input.value) if self.fields_input.value.strip() else self.wizard.data.get("fields", [])
            validate_embed(self.title_input.value, self.description.value, fields)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.wizard.data.update(trigger=self.trigger.value.lower(), aliases=aliases,
                                title=self.title_input.value, description=self.description.value, fields=fields)
        await interaction.response.send_message("Trigger and content saved for this builder session. Set appearance next.",
                                                view=EmbedAppearanceView(self.wizard, self.existing_id), ephemeral=True)


class EmbedAppearanceView(OwnerView):
    def __init__(self, wizard: EmbedWizard, existing_id: str | None):
        super().__init__(wizard.creator_id, wizard.cog.bot)
        self.wizard, self.existing_id = wizard, existing_id

    @discord.ui.button(label="Colour, image, and footer", style=discord.ButtonStyle.primary)
    async def appearance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EmbedAppearanceModal(self.wizard, self.existing_id))


class EmbedAppearanceModal(SafeModal):
    def __init__(self, wizard: EmbedWizard, existing_id: str | None):
        super().__init__(title="Custom embed • appearance", bot=wizard.cog.bot)
        self.wizard, self.existing_id = wizard, existing_id
        data = wizard.data
        self.colour = discord.ui.TextInput(label="Colour (hex)", default=data.get("colour", "#8B5CF6"), max_length=7)
        self.image = discord.ui.TextInput(label="Image URL (optional)", default=data.get("image_url", ""), required=False, max_length=300)
        self.footer = discord.ui.TextInput(label="Footer", default=data.get("footer", "✦ 𝙰𝚙𝚎𝚡"), required=False, max_length=2048)
        for item in (self.colour, self.image, self.footer):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            parse_colour(self.colour.value)
            validate_url(self.image.value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.wizard.data.update(colour=self.colour.value, image_url=self.image.value, footer=self.footer.value)
        await interaction.response.send_message("Appearance saved. Preview and save the custom embed.",
                                                view=EmbedSaveView(self.wizard, self.existing_id), ephemeral=True)


class EmbedSaveView(OwnerView):
    def __init__(self, wizard: EmbedWizard, existing_id: str | None):
        super().__init__(wizard.creator_id, wizard.cog.bot)
        self.wizard, self.existing_id = wizard, existing_id

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            out = make_embed(self.wizard.data["title"], self.wizard.data["description"], self.wizard.data["fields"],
                             parse_colour(self.wizard.data["colour"]), validate_url(self.wizard.data["image_url"]), self.wizard.data["footer"])
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(content=f"Trigger: `!{self.wizard.data['trigger']}`", embed=out, ephemeral=True)

    @discord.ui.button(label="Save custom embed", style=discord.ButtonStyle.success)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            embed_id = self.wizard.save(interaction.guild, existing_id=self.existing_id)
        except ValueError as exc:
            await interaction.response.send_message(f"Embed not saved: {exc}", ephemeral=True)
            return
        await interaction.response.send_message(f"Custom embed `{embed_id}` saved. Trigger execution is restricted to Middleman+ staff.", ephemeral=True)
        await audit(self.wizard.cog.bot, "embed", "saved", guild=interaction.guild, actor_id=interaction.user.id,
                    details={"embed_id": embed_id, "trigger": self.wizard.data["trigger"]}, outcome="success")


class FieldManagerView(OwnerView):
    def __init__(self, cog: "BuilderCog", table: str, record_id: str, guild_id: int, owner_id: int):
        super().__init__(owner_id, bot=cog.bot)
        self.cog, self.table, self.record_id, self.guild_id = cog, table, record_id, guild_id
        self.selected: int | None = None
        fields = cog.fields_for(table, record_id, guild_id)
        if fields:
            options = [discord.SelectOption(label=f"{index + 1}. {field['name']}"[:100], value=str(index))
                       for index, field in enumerate(fields[:25])]
            self.add_item(FieldIndexSelect(options))

    def refreshed(self):
        return FieldManagerView(self.cog, self.table, self.record_id, self.guild_id, self.owner_id)

    @discord.ui.button(label="Add field", style=discord.ButtonStyle.success, row=1)
    async def add_field(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.cog.fields_for(self.table, self.record_id, self.guild_id)) >= 25:
            await interaction.response.send_message("This embed already has 25 fields.", ephemeral=True)
            return
        await interaction.response.send_modal(FieldModal(self, None))

    @discord.ui.button(label="Edit selected", style=discord.ButtonStyle.primary, row=1)
    async def edit_field(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = self.cog.fields_for(self.table, self.record_id, self.guild_id)
        if self.selected is None or self.selected >= len(fields):
            await interaction.response.send_message("Select a field first.", ephemeral=True)
            return
        await interaction.response.send_modal(FieldModal(self, self.selected))

    @discord.ui.button(label="Remove selected", style=discord.ButtonStyle.danger, row=1)
    async def remove_field(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = self.cog.fields_for(self.table, self.record_id, self.guild_id)
        if self.selected is None or self.selected >= len(fields):
            await interaction.response.send_message("Select a field first.", ephemeral=True)
            return
        fields.pop(self.selected)
        try:
            self.cog.save_fields(self.table, self.record_id, self.guild_id, fields)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.edit_message(content="Field removed. Changes are saved to the template; published panel messages still need an explicit confirmed update.", view=self.refreshed())
        await audit(self.cog.bot, "panel" if self.table == "panel_templates" else "embed", "field_removed",
                    guild=interaction.guild, actor_id=interaction.user.id,
                    details={"template_id": self.record_id, "field_index": self.selected}, outcome="success")

    @discord.ui.button(label="Move up", style=discord.ButtonStyle.secondary, row=1)
    async def move_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.move(interaction, -1)

    @discord.ui.button(label="Move down", style=discord.ButtonStyle.secondary, row=1)
    async def move_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.move(interaction, 1)

    async def move(self, interaction: discord.Interaction, delta: int):
        fields = self.cog.fields_for(self.table, self.record_id, self.guild_id)
        target = (self.selected or 0) + delta
        if self.selected is None or not 0 <= target < len(fields):
            await interaction.response.send_message("Select a field with a neighboring position first.", ephemeral=True)
            return
        fields[self.selected], fields[target] = fields[target], fields[self.selected]
        try:
            self.cog.save_fields(self.table, self.record_id, self.guild_id, fields)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.edit_message(content="Field order updated. Published panel messages still need an explicit confirmed update.", view=self.refreshed())
        await audit(self.cog.bot, "panel" if self.table == "panel_templates" else "embed", "field_reordered",
                    guild=interaction.guild, actor_id=interaction.user.id,
                    details={"template_id": self.record_id, "from": self.selected, "to": target}, outcome="success")


class FieldIndexSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="Choose a field", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.view.selected = int(self.values[0])
        await interaction.response.edit_message(content=f"Selected field {self.view.selected + 1}.", view=self.view)


class FieldModal(SafeModal):
    def __init__(self, view: FieldManagerView, index: int | None):
        super().__init__(title="Add embed field" if index is None else "Edit embed field", bot=view.cog.bot)
        self.manager, self.index = view, index
        fields = view.cog.fields_for(view.table, view.record_id, view.guild_id)
        current = fields[index] if index is not None and index < len(fields) else {}
        self.name_input = discord.ui.TextInput(label="Field name", default=current.get("name", ""), max_length=256)
        self.value_input = discord.ui.TextInput(label="Field value", default=current.get("value", ""), style=discord.TextStyle.paragraph, max_length=1024)
        self.inline_input = discord.ui.TextInput(label="Inline? true or false", default=str(current.get("inline", False)).lower(), max_length=5)
        self.add_item(self.name_input)
        self.add_item(self.value_input)
        self.add_item(self.inline_input)

    async def on_submit(self, interaction: discord.Interaction):
        inline = self.inline_input.value.strip().lower()
        if inline not in {"true", "false"}:
            await interaction.response.send_message("Inline must be `true` or `false`.", ephemeral=True)
            return
        fields = self.manager.cog.fields_for(self.manager.table, self.manager.record_id, self.manager.guild_id)
        item = {"name": self.name_input.value, "value": self.value_input.value, "inline": inline == "true"}
        if self.index is None:
            if len(fields) >= 25:
                await interaction.response.send_message("This embed already has 25 fields.", ephemeral=True)
                return
            fields.append(item)
        elif self.index < len(fields):
            fields[self.index] = item
        else:
            await interaction.response.send_message("That field no longer exists.", ephemeral=True)
            return
        try:
            self.manager.cog.save_fields(self.manager.table, self.manager.record_id, self.manager.guild_id, fields)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message("Field saved. Continue managing fields or publish/update the template through its builder command.",
                                                view=self.manager.refreshed(), ephemeral=True)
        await audit(self.manager.cog.bot, "panel" if self.manager.table == "panel_templates" else "embed", "field_saved",
                    guild=interaction.guild, actor_id=interaction.user.id,
                    details={"template_id": self.manager.record_id, "field_index": self.index if self.index is not None else len(fields) - 1}, outcome="success")


class BuilderCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def register_persistent_views(self):
        for row in self.bot.db.all("SELECT id,button_label,button_emoji FROM panel_templates WHERE state='published'"):
            # TicketCog also registers these; duplicate views would be redundant, so this registration is omitted.
            break

    async def _permission(self, ctx: commands.Context) -> bool:
        if not has_minimum_role(ctx.author, "server_founder"):
            await send_error(ctx, "Panel and embed management requires Server Founder and above.")
            return False
        if not ctx.author.guild_permissions.manage_guild:
            await send_error(ctx, "Your Discord permissions must include Manage Server.")
            return False
        return True

    @staticmethod
    def _published_snapshot(row) -> str:
        keys = ("name", "ticket_type", "title", "description", "fields_json", "colour", "image_url",
                "footer", "button_label", "button_emoji", "channel_id", "support_role_id")
        return json.dumps({key: row[key] for key in keys}, ensure_ascii=False)

    def fields_for(self, table: str, record_id: str, guild_id: int) -> list[dict]:
        if table not in {"panel_templates", "custom_embeds"}:
            raise ValueError("Unknown embed template type.")
        row = self.bot.db.one(f"SELECT fields_json FROM {table} WHERE id=? AND guild_id=?", (record_id, str(guild_id)))
        if not row:
            raise ValueError("Saved template ID was not found.")
        return json.loads(row["fields_json"])

    def save_fields(self, table: str, record_id: str, guild_id: int, fields: list[dict]) -> None:
        if table not in {"panel_templates", "custom_embeds"}:
            raise ValueError("Unknown embed template type.")
        row = self.bot.db.one(f"SELECT title,description,footer FROM {table} WHERE id=? AND guild_id=?", (record_id, str(guild_id)))
        if not row:
            raise ValueError("Saved template ID was not found.")
        validate_embed(row["title"], row["description"], fields, row["footer"])
        self.bot.db.execute(f"UPDATE {table} SET fields_json=?,updated_at=? WHERE id=? AND guild_id=?",
                            (json.dumps(fields, ensure_ascii=False), utcnow(), record_id, str(guild_id)))

    @commands.group(name="panelbuilder", invoke_without_command=True)
    @commands.guild_only()
    async def panelbuilder(self, ctx: commands.Context):
        if not await self._permission(ctx):
            return
        wizard = PanelWizard(self, ctx.guild.id, ctx.author.id)
        await ctx.send(embed=embed("Ticket Panel Builder", "Create one panel at a time. Select the approved ticket workflow, provide all embed fields, set its destination and Support Role to Ping, then preview and save a draft. A saved draft is not published.", colour=PRIMARY),
                       view=PanelStartView(wizard), allowed_mentions=discord.AllowedMentions.none())

    @panelbuilder.command(name="list")
    async def panel_list(self, ctx: commands.Context):
        if not await self._permission(ctx):
            return
        rows = self.bot.db.all("SELECT id,name,ticket_type,state,channel_id,support_role_id FROM panel_templates WHERE guild_id=? ORDER BY created_at DESC", (str(ctx.guild.id),))
        out = embed("Saved ticket panels", colour=PRIMARY)
        out.description = "\n".join(f"`{r['id']}` • **{r['name']}** • {r['ticket_type']} • {r['state']} • channel `{r['channel_id']}` • ping role `{r['support_role_id']}`" for r in rows[:25]) or "No saved panels. Use `!panelbuilder` to create a draft."
        await ctx.send(embed=out)

    @panelbuilder.command(name="preview")
    async def panel_preview(self, ctx: commands.Context, panel_id: str):
        if not await self._permission(ctx):
            return
        row = self.bot.db.one("SELECT * FROM panel_templates WHERE id=? AND guild_id=?", (panel_id, str(ctx.guild.id)))
        if not row:
            await send_error(ctx, "Panel ID not found.")
            return
        fields = json.loads(row["fields_json"])
        out = make_embed(row["title"], row["description"], fields, int(row["colour"]), row["image_url"], row["footer"])
        preview_view = discord.ui.View()
        preview_view.add_item(discord.ui.Button(label=row["button_label"][:80], emoji=row["button_emoji"] or None,
                                                style=discord.ButtonStyle.primary, disabled=True))
        summary = (f"Type: **{row['ticket_type']}** • Support Role to Ping: "
                   f"<@&{row['support_role_id']}> (notification only) • Button: "
                   f"{row['button_emoji'] or ''} {row['button_label']}")
        await ctx.send(content=summary, embed=out, view=preview_view, allowed_mentions=discord.AllowedMentions.none())

    @panelbuilder.command(name="edit")
    async def panel_edit(self, ctx: commands.Context, panel_id: str):
        if not await self._permission(ctx):
            return
        row = self.bot.db.one("SELECT * FROM panel_templates WHERE id=? AND guild_id=?", (panel_id, str(ctx.guild.id)))
        if not row:
            await send_error(ctx, "Panel ID not found.")
            return
        data = {"name": row["name"], "title": row["title"], "description": row["description"],
                "fields": json.loads(row["fields_json"]), "channel_id": row["channel_id"],
                "support_role_id": row["support_role_id"], "colour": f"#{row['colour']:06X}",
                "image_url": row["image_url"] or "", "footer": row["footer"],
                "button_label": row["button_label"], "button_emoji": row["button_emoji"] or ""}
        wizard = PanelWizard(self, ctx.guild.id, ctx.author.id, row["ticket_type"], data, existing_id=panel_id)
        await ctx.send("Edit the saved template. Any published message remains untouched until `!panelbuilder update ID` is confirmed.", view=PanelStartView(wizard))

    @panelbuilder.command(name="duplicate")
    async def panel_duplicate(self, ctx: commands.Context, panel_id: str):
        if not await self._permission(ctx):
            return
        row = self.bot.db.one("SELECT * FROM panel_templates WHERE id=? AND guild_id=?", (panel_id, str(ctx.guild.id)))
        if not row:
            await send_error(ctx, "Panel ID not found.")
            return
        new_id = uuid.uuid4().hex[:12]
        self.bot.db.execute(
            "INSERT INTO panel_templates(id,guild_id,name,ticket_type,title,description,fields_json,colour,image_url,footer,button_label,button_emoji,channel_id,support_role_id,creator_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id, row["guild_id"], row["name"][:75] + " copy", row["ticket_type"], row["title"], row["description"], row["fields_json"], row["colour"], row["image_url"], row["footer"], row["button_label"], row["button_emoji"], row["channel_id"], row["support_role_id"], str(ctx.author.id), utcnow(), utcnow()),
        )
        await audit(self.bot, "panel", "duplicated", guild=ctx.guild, actor_id=ctx.author.id,
                    details={"source_panel_id": panel_id, "panel_id": new_id}, outcome="success")
        await ctx.send(embed=embed("Panel duplicated", f"New draft ID: `{new_id}`.", colour=PRIMARY))

    @panelbuilder.command(name="publish")
    async def panel_publish(self, ctx: commands.Context, panel_id: str):
        if not await self._permission(ctx):
            return
        row = self.bot.db.one("SELECT * FROM panel_templates WHERE id=? AND guild_id=?", (panel_id, str(ctx.guild.id)))
        if not row:
            await send_error(ctx, "Panel ID not found.")
            return
        if row["state"] == "published":
            await send_error(ctx, "This panel is already published. Use `!panelbuilder update ID` after explicit confirmation.")
            return
        try:
            channel = ctx.guild.get_channel(int(row["channel_id"]))
            role = ctx.guild.get_role(int(row["support_role_id"]))
            bot_member = ctx.guild.me
            if bot_member is None:
                raise ValueError("Bot member record is unavailable in this server.")
            if not isinstance(channel, discord.TextChannel) or role is None or role.is_default():
                raise ValueError("Destination channel or Support Role to Ping is missing.")
            if row["ticket_type"] not in TICKET_TYPES or channel.id != PANEL_CHANNEL[row["ticket_type"]]:
                raise ValueError("Ticket panel must use the configured destination for its approved workflow.")
            if row["ticket_type"] == "rankup" and (channel.id != PANEL_CHANNEL["rankup"] or not is_private_staff_channel(channel, ctx.guild)):
                raise ValueError("Staff rank-up panel must use the configured private rank-up channel.")
            if not channel.permissions_for(bot_member).view_channel or not channel.permissions_for(bot_member).send_messages or not channel.permissions_for(bot_member).embed_links:
                raise ValueError("Bot needs View Channel, Send Messages, and Embed Links in the destination.")
            if not role.mentionable and not bot_member.guild_permissions.mention_everyone:
                raise ValueError("Bot cannot mention the saved Support Role to Ping.")
            validate_button_emoji(row["button_emoji"], ctx.guild)
            if not 1 <= len(row["button_label"].strip()) <= 80 or row["ticket_type"] not in TICKET_TYPES:
                raise ValueError("Saved button label or ticket type is invalid.")
            fields = json.loads(row["fields_json"])
            validate_embed(row["title"], row["description"], fields, row["footer"])
            validate_url(row["image_url"] or "")
            if not 0 <= int(row["colour"]) <= 0xFFFFFF:
                raise ValueError("Saved embed colour is invalid.")
            out = make_embed(row["title"], row["description"], fields, int(row["colour"]), row["image_url"], row["footer"])
            msg = await channel.send(embed=out, view=TicketPanelView(self.bot.get_cog("TicketCog"), row["id"], row["button_label"], row["button_emoji"]), allowed_mentions=discord.AllowedMentions.none())
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, discord.Forbidden, discord.HTTPException) as exc:
            await send_error(ctx, f"Panel was not published: {exc}")
            return
        snapshot = self._published_snapshot(row)
        try:
            with self.bot.db.transaction() as connection:
                updated = connection.execute("UPDATE panel_templates SET state='published',published_channel_id=?,published_message_id=?,published_config_json=?,updated_at=? WHERE id=? AND guild_id=? AND state='draft'",
                                             (str(channel.id), str(msg.id), snapshot, utcnow(), panel_id, str(ctx.guild.id)))
                if updated.rowcount != 1:
                    raise RuntimeError("Panel state changed during publish.")
        except Exception as exc:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass
            await send_error(ctx, f"Discord posted the panel, but its state could not be saved ({type(exc).__name__}); duplicate-message cleanup was attempted.")
            await audit(self.bot, "panel", "publish_persistence_failed", guild=ctx.guild, actor_id=ctx.author.id,
                        target_id=msg.id, details={"panel_id": panel_id}, outcome="failure")
            return
        self.bot.add_view(TicketPanelView(self.bot.get_cog("TicketCog"), row["id"], row["button_label"], row["button_emoji"]), message_id=msg.id)
        await audit(self.bot, "panel", "published", guild=ctx.guild, actor_id=ctx.author.id,
                    target_id=msg.id, details={"panel_id": panel_id, "channel_id": channel.id,
                                                "ticket_type": row["ticket_type"], "support_role_id": role.id}, outcome="success")
        await ctx.send(embed=embed("Panel published", f"Published only `{row['name']}` to {channel.mention}.", colour=PRIMARY))

    @panelbuilder.command(name="update")
    async def panel_update(self, ctx: commands.Context, panel_id: str):
        if not await self._permission(ctx):
            return
        row = self.bot.db.one("SELECT * FROM panel_templates WHERE id=? AND guild_id=? AND state='published'", (panel_id, str(ctx.guild.id)))
        if not row:
            await send_error(ctx, "Published panel ID not found.")
            return
        if not await confirm(ctx, f"Update published message `{row['published_message_id']}` from the saved template `{row['name']}`?"):
            return
        try:
            old_channel = ctx.guild.get_channel(int(row["published_channel_id"]))
            destination = ctx.guild.get_channel(int(row["channel_id"]))
            role = ctx.guild.get_role(int(row["support_role_id"]))
            bot_member = ctx.guild.me
            if bot_member is None or not isinstance(old_channel, discord.TextChannel) or not isinstance(destination, discord.TextChannel) or role is None or role.is_default():
                raise ValueError("Published destination or Support Role to Ping is missing or invalid.")
            if row["ticket_type"] not in TICKET_TYPES or destination.id != PANEL_CHANNEL[row["ticket_type"]]:
                raise ValueError("Ticket panel must use the configured destination for its approved workflow.")
            if row["ticket_type"] == "rankup" and (destination.id != PANEL_CHANNEL["rankup"] or not is_private_staff_channel(destination, ctx.guild)):
                raise ValueError("Staff rank-up panel must stay in the configured private rank-up channel.")
            if not role.mentionable and not bot_member.guild_permissions.mention_everyone:
                raise ValueError("Bot cannot mention the saved Support Role to Ping.")
            validate_button_emoji(row["button_emoji"], ctx.guild)
            if not 1 <= len(row["button_label"].strip()) <= 80 or row["ticket_type"] not in TICKET_TYPES:
                raise ValueError("Saved button label or ticket type is invalid.")
            for channel in {old_channel.id: old_channel, destination.id: destination}.values():
                perms = channel.permissions_for(bot_member)
                if not perms.view_channel or not perms.send_messages or not perms.embed_links:
                    raise ValueError(f"Bot needs View Channel, Send Messages, and Embed Links in channel {channel.id}.")
            message = await old_channel.fetch_message(int(row["published_message_id"]))
            fields = json.loads(row["fields_json"])
            validate_embed(row["title"], row["description"], fields, row["footer"])
            validate_url(row["image_url"] or "")
            if not 0 <= int(row["colour"]) <= 0xFFFFFF:
                raise ValueError("Saved embed colour is invalid.")
            out = make_embed(row["title"], row["description"], fields, int(row["colour"]), row["image_url"], row["footer"])
            view = TicketPanelView(self.bot.get_cog("TicketCog"), row["id"], row["button_label"], row["button_emoji"])
            if destination.id == old_channel.id:
                await message.edit(embed=out, view=view, allowed_mentions=discord.AllowedMentions.none())
                new_message = message
            else:
                new_message = await destination.send(embed=out, view=view, allowed_mentions=discord.AllowedMentions.none())
        except (AttributeError, KeyError, TypeError, json.JSONDecodeError, discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError) as exc:
            await send_error(ctx, f"Published panel was not updated: {type(exc).__name__}.")
            return
        try:
            with self.bot.db.transaction() as connection:
                updated = connection.execute("UPDATE panel_templates SET published_channel_id=?,published_message_id=?,published_config_json=?,updated_at=? WHERE id=? AND guild_id=? AND state='published' AND published_message_id=?",
                                             (str(new_message.channel.id), str(new_message.id), self._published_snapshot(row),
                                              utcnow(), panel_id, str(ctx.guild.id), row["published_message_id"]))
                if updated.rowcount != 1:
                    raise RuntimeError("Published panel state changed during update.")
        except Exception as exc:
            if new_message.id != int(row["published_message_id"]):
                try:
                    await new_message.delete()
                except discord.HTTPException:
                    pass
            else:
                try:
                    old_config = json.loads(row["published_config_json"] or "{}")
                    old_fields = json.loads(old_config.get("fields_json", row["fields_json"]))
                    old_embed = make_embed(old_config.get("title", row["title"]),
                                           old_config.get("description", row["description"]), old_fields,
                                           int(old_config.get("colour", row["colour"])),
                                           old_config.get("image_url", row["image_url"]),
                                           old_config.get("footer", row["footer"]))
                    old_view = TicketPanelView(self.bot.get_cog("TicketCog"), row["id"],
                                               old_config.get("button_label", row["button_label"]),
                                               old_config.get("button_emoji", row["button_emoji"]))
                    await message.edit(embed=old_embed, view=old_view, allowed_mentions=discord.AllowedMentions.none())
                except Exception:
                    self.bot.logger.error("Could not roll back panel message after database update failure (%s)", type(exc).__name__)
            await send_error(ctx, f"Discord updated the panel, but the published state could not be saved ({type(exc).__name__}); rollback was attempted where possible.")
            await audit(self.bot, "panel", "update_persistence_failed", guild=ctx.guild, actor_id=ctx.author.id,
                        target_id=new_message.id, details={"panel_id": panel_id}, outcome="failure")
            return
        moved = new_message.channel.id != int(row["published_channel_id"])
        retire_failed = False
        if moved:
            self.bot.add_view(TicketPanelView(self.bot.get_cog("TicketCog"), row["id"], row["button_label"], row["button_emoji"]),
                              message_id=new_message.id)
            try:
                await message.edit(content="This panel moved to its newly configured destination.", view=None,
                                        allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                retire_failed = True
                await audit(self.bot, "panel", "old_message_retire_failed", guild=ctx.guild, actor_id=ctx.author.id,
                            target_id=row["published_message_id"], details={"panel_id": panel_id}, outcome="failure")
        await audit(self.bot, "panel", "updated", guild=ctx.guild, actor_id=ctx.author.id,
                    target_id=new_message.id, details={"panel_id": panel_id, "moved": moved,
                                                       "old_channel_id": row["published_channel_id"],
                                                       "new_channel_id": new_message.channel.id}, outcome="success")
        suffix = " Check the old message because it could not be disabled." if retire_failed else ""
        await ctx.send(embed=embed("Panel updated", "Discord confirmed the saved panel update." + suffix, colour=PRIMARY))

    @panelbuilder.command(name="delete")
    async def panel_delete(self, ctx: commands.Context, panel_id: str):
        if not await self._permission(ctx):
            return
        row = self.bot.db.one("SELECT * FROM panel_templates WHERE id=? AND guild_id=?", (panel_id, str(ctx.guild.id)))
        if not row:
            await send_error(ctx, "Panel ID not found.")
            return
        if not await confirm(ctx, f"Delete saved panel `{row['name']}` ({panel_id})? Its published button will be disabled first."):
            return
        retired_message = None
        if row["state"] == "published" and row["published_message_id"]:
            if not row["published_channel_id"]:
                await send_error(ctx, "Published panel channel configuration is invalid; the saved panel was kept.")
                return
            try:
                published_channel_id = validate_discord_id(str(row["published_channel_id"]))
            except ValueError:
                await send_error(ctx, "Published panel channel configuration is invalid; the saved panel was kept.")
                return
            channel = ctx.guild.get_channel(published_channel_id)
            if not isinstance(channel, discord.TextChannel):
                await send_error(ctx, "Published panel channel is missing; the saved panel was kept so its live message can be retired safely.")
                return
            try:
                retired_message = await channel.fetch_message(int(row["published_message_id"]))
                await retired_message.edit(content="This saved ticket panel has been disabled.", view=None,
                                           allowed_mentions=discord.AllowedMentions.none())
            except discord.NotFound:
                retired_message = None
            except (discord.Forbidden, discord.HTTPException) as exc:
                await send_error(ctx, f"The published panel button could not be disabled ({type(exc).__name__}); the saved panel was kept.")
                return
        try:
            with self.bot.db.transaction() as connection:
                connection.execute("DELETE FROM panel_templates WHERE id=? AND guild_id=?", (panel_id, str(ctx.guild.id)))
        except Exception as exc:
            if retired_message is not None:
                try:
                    view = TicketPanelView(self.bot.get_cog("TicketCog"), row["id"], row["button_label"], row["button_emoji"])
                    await retired_message.edit(content=None, view=view, allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    self.bot.logger.error("Could not restore panel after database deletion failure (%s)", type(exc).__name__)
            await send_error(ctx, f"Panel could not be deleted because the database write failed ({type(exc).__name__}).")
            await audit(self.bot, "panel", "delete_persistence_failed", guild=ctx.guild,
                        actor_id=ctx.author.id, details={"panel_id": panel_id, "error_type": type(exc).__name__}, outcome="failure")
            return
        await audit(self.bot, "panel", "deleted", guild=ctx.guild, actor_id=ctx.author.id, details={"panel_id": panel_id}, outcome="success")
        await ctx.send(embed=embed("Panel deleted", "Saved panel configuration was deleted and any published button was disabled.", colour=PRIMARY))

    @panelbuilder.command(name="fields")
    async def panel_fields(self, ctx: commands.Context, panel_id: str, *, fields_json: str):
        if not await self._permission(ctx):
            return
        row = self.bot.db.one("SELECT * FROM panel_templates WHERE id=? AND guild_id=?", (panel_id, str(ctx.guild.id)))
        if not row:
            await send_error(ctx, "Panel ID not found.")
            return
        try:
            fields = parse_fields(fields_json)
            validate_embed(row["title"], row["description"], fields, row["footer"])
        except ValueError as exc:
            await send_error(ctx, str(exc))
            return
        self.bot.db.execute("UPDATE panel_templates SET fields_json=?,updated_at=? WHERE id=? AND guild_id=?",
                            (json.dumps(fields, ensure_ascii=False), utcnow(), panel_id, str(ctx.guild.id)))
        await audit(self.bot, "panel", "fields_changed", guild=ctx.guild, actor_id=ctx.author.id,
                    details={"panel_id": panel_id, "field_count": len(fields)}, outcome="success")
        await ctx.send(embed=embed("Panel fields updated", "The saved field array was replaced in the provided order. Published messages need `!panelbuilder update ID` to change.", colour=PRIMARY))

    @panelbuilder.command(name="manage-fields")
    async def panel_manage_fields(self, ctx: commands.Context, panel_id: str):
        if not await self._permission(ctx):
            return
        try:
            self.fields_for("panel_templates", panel_id, ctx.guild.id)
        except ValueError as exc:
            await send_error(ctx, str(exc))
            return
        await ctx.send("Manage this panel's complete ordered field list. Add, edit, remove, or move fields; up to 25 are supported.",
                       view=FieldManagerView(self, "panel_templates", panel_id, ctx.guild.id, ctx.author.id))

    @commands.group(name="embedbuilder", invoke_without_command=True)
    @commands.guild_only()
    async def embedbuilder(self, ctx: commands.Context):
        if not await self._permission(ctx):
            return
        wizard = EmbedWizard(self, ctx.guild.id, ctx.author.id)
        await ctx.send(embed=embed("Custom Embed & Trigger Builder", "Create a user-defined prefix trigger. No educational triggers are preloaded. Saved custom embeds and trigger execution are staff-only (Middleman+).", colour=PRIMARY),
                       view=EmbedWizardStart(wizard), allowed_mentions=discord.AllowedMentions.none())

    @embedbuilder.command(name="list")
    async def custom_list(self, ctx: commands.Context):
        if not await self._permission(ctx):
            return
        rows = self.bot.db.all("SELECT id,trigger,aliases_json,enabled FROM custom_embeds WHERE guild_id=? ORDER BY created_at DESC", (str(ctx.guild.id),))
        out = embed("Saved custom embeds", colour=PRIMARY)
        out.description = "\n".join(f"`{r['id']}` • `!{r['trigger']}` • aliases `{', '.join(json.loads(r['aliases_json'])) or '—'}` • {'enabled' if r['enabled'] else 'disabled'}" for r in rows[:25]) or "No custom embeds saved."
        await ctx.send(embed=out)

    @embedbuilder.command(name="preview")
    async def custom_preview(self, ctx: commands.Context, embed_id: str):
        if not await self._permission(ctx):
            return
        row = self.bot.db.one("SELECT * FROM custom_embeds WHERE id=? AND guild_id=?", (embed_id, str(ctx.guild.id)))
        if not row:
            await send_error(ctx, "Custom embed ID not found.")
            return
        out = make_embed(row["title"], row["description"], json.loads(row["fields_json"]), int(row["colour"]), row["image_url"], row["footer"])
        await ctx.send(embed=out)

    @embedbuilder.command(name="manage-fields")
    async def custom_manage_fields(self, ctx: commands.Context, embed_id: str):
        if not await self._permission(ctx):
            return
        try:
            self.fields_for("custom_embeds", embed_id, ctx.guild.id)
        except ValueError as exc:
            await send_error(ctx, str(exc))
            return
        await ctx.send("Manage this custom embed's complete ordered field list. Add, edit, remove, or move fields; up to 25 are supported.",
                       view=FieldManagerView(self, "custom_embeds", embed_id, ctx.guild.id, ctx.author.id))

    @embedbuilder.command(name="edit")
    async def custom_edit(self, ctx: commands.Context, embed_id: str):
        if not await self._permission(ctx):
            return
        row = self.bot.db.one("SELECT * FROM custom_embeds WHERE id=? AND guild_id=?", (embed_id, str(ctx.guild.id)))
        if not row:
            await send_error(ctx, "Custom embed ID not found.")
            return
        data = {"trigger": row["trigger"], "aliases": json.loads(row["aliases_json"]), "title": row["title"],
                "description": row["description"], "fields": json.loads(row["fields_json"]), "colour": f"#{row['colour']:06X}",
                "image_url": row["image_url"] or "", "footer": row["footer"]}
        await ctx.send("Edit the saved custom embed. Any published/test message stays unchanged.",
                       view=EmbedWizardStart(EmbedWizard(self, ctx.guild.id, ctx.author.id, data), embed_id))

    @embedbuilder.command(name="duplicate")
    async def custom_duplicate(self, ctx: commands.Context, embed_id: str, trigger: str):
        if not await self._permission(ctx):
            return
        row = self.bot.db.one("SELECT * FROM custom_embeds WHERE id=? AND guild_id=?", (embed_id, str(ctx.guild.id)))
        if not row:
            await send_error(ctx, "Custom embed ID not found.")
            return
        try:
            trigger = trigger.strip().lower()
            names = validate_trigger(trigger)
            new_id = uuid.uuid4().hex[:12]
            with self.bot.db.transaction() as connection:
                existing = connection.execute("SELECT trigger,aliases_json FROM custom_embeds WHERE guild_id=?", (str(ctx.guild.id),)).fetchall()
                used = {name for r in existing for name in [r["trigger"], *json.loads(r["aliases_json"])]}
                if any(n in used for n in names):
                    raise ValueError("Trigger is already in use.")
                connection.execute("INSERT INTO custom_embeds(id,guild_id,trigger,aliases_json,title,description,fields_json,colour,image_url,footer,creator_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                   (new_id, row["guild_id"], trigger, "[]", row["title"], row["description"], row["fields_json"], row["colour"], row["image_url"], row["footer"], str(ctx.author.id), utcnow(), utcnow()))
        except ValueError as exc:
            await send_error(ctx, str(exc))
            return
        await audit(self.bot, "embed", "duplicated", guild=ctx.guild, actor_id=ctx.author.id,
                    details={"source_embed_id": embed_id, "embed_id": new_id, "trigger": trigger.lower()}, outcome="success")
        await ctx.send(embed=embed("Custom embed duplicated", f"New ID `{new_id}` uses `!{trigger}`.", colour=PRIMARY))

    async def _toggle_custom(self, ctx: commands.Context, embed_id: str, enabled: bool):
        if not await self._permission(ctx):
            return
        row = self.bot.db.one("SELECT id FROM custom_embeds WHERE id=? AND guild_id=?", (embed_id, str(ctx.guild.id)))
        if not row:
            await send_error(ctx, "Custom embed ID not found.")
            return
        self.bot.db.execute("UPDATE custom_embeds SET enabled=?,updated_at=? WHERE id=?", (int(enabled), utcnow(), embed_id))
        await audit(self.bot, "embed", "enabled" if enabled else "disabled", guild=ctx.guild, actor_id=ctx.author.id,
                    details={"embed_id": embed_id}, outcome="success")
        await ctx.send(embed=embed("Custom embed updated", f"Custom trigger is now {'enabled' if enabled else 'disabled'}.", colour=PRIMARY))

    @embedbuilder.command(name="enable")
    async def custom_enable(self, ctx: commands.Context, embed_id: str):
        await self._toggle_custom(ctx, embed_id, True)

    @embedbuilder.command(name="disable")
    async def custom_disable(self, ctx: commands.Context, embed_id: str):
        await self._toggle_custom(ctx, embed_id, False)

    @embedbuilder.command(name="delete")
    async def custom_delete(self, ctx: commands.Context, embed_id: str):
        if not await self._permission(ctx):
            return
        row = self.bot.db.one("SELECT trigger FROM custom_embeds WHERE id=? AND guild_id=?", (embed_id, str(ctx.guild.id)))
        if not row:
            await send_error(ctx, "Custom embed ID not found.")
            return
        if not await confirm(ctx, f"Delete custom embed `{embed_id}` and free trigger `!{row['trigger']}`?"):
            return
        self.bot.db.execute("DELETE FROM custom_embeds WHERE id=?", (embed_id,))
        await audit(self.bot, "embed", "deleted", guild=ctx.guild, actor_id=ctx.author.id,
                    details={"embed_id": embed_id, "trigger": row["trigger"]}, outcome="success")
        await ctx.send(embed=embed("Custom embed deleted", f"`!{row['trigger']}` no longer resolves.", colour=PRIMARY))

    @embedbuilder.command(name="publish")
    async def custom_publish(self, ctx: commands.Context, embed_id: str):
        if not isinstance(ctx.author, discord.Member) or not has_minimum_role(ctx.author, "middleman"):
            await send_error(ctx, "Publishing and testing saved embeds requires Middleman and above.")
            return
        row = self.bot.db.one("SELECT * FROM custom_embeds WHERE id=? AND guild_id=? AND enabled=1", (embed_id, str(ctx.guild.id)))
        if not row:
            await send_error(ctx, "Enabled custom embed ID not found.")
            return
        out = make_embed(row["title"], row["description"], json.loads(row["fields_json"]), int(row["colour"]), row["image_url"], row["footer"])
        try:
            await ctx.send(embed=out, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            await send_error(ctx, f"Discord did not publish the embed ({type(exc).__name__}).")
            return
        await audit(self.bot, "embed", "published", guild=ctx.guild, actor_id=ctx.author.id,
                    details={"embed_id": embed_id, "trigger": row["trigger"]}, outcome="success")
