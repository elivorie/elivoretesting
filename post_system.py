import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from social_system import find_artist, notify_artist_owner


drafts: dict[int, dict] = {}


def make_empty_draft(user_id: int, channel_id: Optional[int]) -> dict:
    return {
        "user_id": user_id,
        "post_type": None,
        "ping": "",
        "channel_id": channel_id,
        "image": None,
        "title": "",
        "content": "",
        "date": "",
        "source": "",
        "notes": "",
        "shop_name": "",
        "artist_name": "",
        "product": "",
        "quantity": "",
        "ship_date": "",
        "target_artist": "",
    }


def build_post_embed(draft: dict) -> discord.Embed:
    post_type = draft.get("post_type")
    target_line = f"\n🎯 {draft.get('target_artist', '').strip()}" if draft.get("target_artist", "").strip() else ""

    if post_type == "data":
        notes = draft.get("notes", "").strip()
        notes_text = f"\n\n— {notes}" if notes else ""
        embed = discord.Embed(
            title="♪‧₊˚ Encore Data",
            description=(
                f"`{draft.get('date', '')}`\n\n"
                f"{draft.get('content', '')}"
                f"{notes_text}"
                f"{target_line}"
            ),
            color=discord.Color.from_rgb(255, 182, 193),
        )
    elif post_type == "interview":
        embed = discord.Embed(
            title=f"♪‧₊˚ {draft.get('title', '') or 'Untitled Interview'}",
            description=(
                f"{draft.get('content', '')}\n\n"
                f"📆 {draft.get('date', '')}\n"
                f"👥 {draft.get('source', '')}"
                f"{target_line}"
            ),
            color=discord.Color.from_rgb(186, 104, 200),
        )
    elif post_type == "magazine":
        embed = discord.Embed(
            title=f"♪‧₊˚ {draft.get('title', '') or 'Untitled Magazine Feature'}",
            description=(
                f"{draft.get('content', '')}\n\n"
                f"📆 {draft.get('date', '')}\n"
                f"👥 {draft.get('source', '')}"
                f"{target_line}"
            ),
            color=discord.Color.from_rgb(186, 104, 200),
        )
    elif post_type == "merch":
        artist_name = draft.get("artist_name", "")
        target_display = draft.get("target_artist", "").strip() or artist_name
        merch_target = f"\n🎯 {target_display}" if target_display else ""
        embed = discord.Embed(
            title="♪‧₊˚ Artist Merch",
            description=(
                f"### {draft.get('shop_name', '')}\n\n"
                f"**{artist_name}** has launched new products including ‘{draft.get('product', '')}’\n"
                f"‘{draft.get('quantity', '')}’\n\n"
                f"> Available on their Artist site, ships on [{draft.get('ship_date', '')}]"
                f"{merch_target}"
            ),
            color=discord.Color.from_rgb(129, 199, 132),
        )
    else:
        embed = discord.Embed(
            title="♪‧₊˚ Encore Post Preview",
            description="Pick a post type first.",
            color=discord.Color.light_grey(),
        )

    if draft.get("image") is not None:
        embed.set_image(url=f"attachment://{draft['image'].filename}")
    return embed


def build_status_text(draft: dict, guild: Optional[discord.Guild]) -> str:
    channel_mention = "Not set"
    if guild and draft.get("channel_id"):
        channel = guild.get_channel(draft["channel_id"])
        channel_mention = channel.mention if channel else f"`{draft['channel_id']}`"

    image_status = "Attached" if draft.get("image") else "No image"
    post_type = draft.get("post_type") or "Not set"
    ping = draft.get("ping") or "Not set"
    target_artist = draft.get("target_artist") or "Not set"
    return (
        f"**♪‧₊˚ Draft Builder**\n"
        f"Type: **{post_type}**\n"
        f"Ping: {ping}\n"
        f"Target Artist: {target_artist}\n"
        f"Channel: {channel_mention}\n"
        f"Image: {image_status}\n\n"
        f"This post will go to the channel you're using now unless you start the draft in another channel."
    )


class TypeSelect(discord.ui.Select):
    def __init__(self, user_id: int):
        self.user_id = user_id
        options = [
            discord.SelectOption(label="Data", value="data", emoji="🎵"),
            discord.SelectOption(label="Interview", value="interview", emoji="🎤"),
            discord.SelectOption(label="Magazine", value="magazine", emoji="📰"),
            discord.SelectOption(label="Merch", value="merch", emoji="🛍️"),
        ]
        super().__init__(placeholder="Choose a post type...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("That isn't your draft.", ephemeral=True)
            return
        draft = drafts.get(self.user_id)
        if not draft:
            await interaction.response.send_message("Your draft expired. Run /post again.", ephemeral=True)
            return
        draft["post_type"] = self.values[0]
        await interaction.response.edit_message(content=build_status_text(draft, interaction.guild), view=PostBuilderView(self.user_id))


class TypeSelectView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.add_item(TypeSelect(user_id))


class BasicInfoModal(discord.ui.Modal, title="Set Basic Info"):
    ping = discord.ui.TextInput(label="Ping", placeholder="@here or <@&role_id>", required=False, max_length=100)
    target_artist = discord.ui.TextInput(label="Target Artist (optional)", placeholder="Artist to notify when mentioned", required=False, max_length=100)

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        draft = drafts.get(self.user_id)
        if not draft:
            await interaction.response.send_message("Your draft expired. Run /post again.", ephemeral=True)
            return
        draft["ping"] = self.ping.value.strip()
        draft["target_artist"] = self.target_artist.value.strip()
        await interaction.response.send_message("Saved basic info.", ephemeral=True)


class DataModal(discord.ui.Modal, title="Data Post Fields"):
    date = discord.ui.TextInput(label="Date", placeholder="January 03, 2020", required=True, max_length=50)
    content = discord.ui.TextInput(label="Main Content", placeholder="Write the full data post text here", style=discord.TextStyle.paragraph, required=True, max_length=4000)
    notes = discord.ui.TextInput(label="Notes (optional)", placeholder="Extra note for the bottom line", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        draft = drafts.get(self.user_id)
        if not draft:
            await interaction.response.send_message("Your draft expired. Run /post again.", ephemeral=True)
            return
        draft["date"] = self.date.value
        draft["content"] = self.content.value
        draft["notes"] = self.notes.value
        await interaction.response.send_message("Saved data post fields.", ephemeral=True)


class InterviewMagazineModal(discord.ui.Modal):
    title_input = discord.ui.TextInput(label="Title", placeholder="Billie Eilish on Zeus Network Talk", required=True, max_length=256)
    content = discord.ui.TextInput(label="Content", placeholder="Write the full paragraph here", style=discord.TextStyle.paragraph, required=True, max_length=4000)
    date = discord.ui.TextInput(label="Date", placeholder="January 31, 2020", required=True, max_length=50)
    source = discord.ui.TextInput(label="Source", placeholder="Zeus Network Talk", required=True, max_length=100)
    target_artist = discord.ui.TextInput(label="Target Artist (optional)", placeholder="Artist to notify", required=False, max_length=100)

    def __init__(self, user_id: int, post_type: str):
        super().__init__(title="Interview Fields" if post_type == "interview" else "Magazine Fields")
        self.user_id = user_id
        self.post_type = post_type

    async def on_submit(self, interaction: discord.Interaction):
        draft = drafts.get(self.user_id)
        if not draft:
            await interaction.response.send_message("Your draft expired. Run /post again.", ephemeral=True)
            return
        draft["title"] = self.title_input.value
        draft["content"] = self.content.value
        draft["date"] = self.date.value
        draft["source"] = self.source.value
        if self.target_artist.value.strip():
            draft["target_artist"] = self.target_artist.value.strip()
        await interaction.response.send_message(f"Saved {self.post_type} fields.", ephemeral=True)


class MerchModal(discord.ui.Modal, title="Merch Fields"):
    shop_name = discord.ui.TextInput(label="Shop Name", placeholder="Target, Urban, Artist site, etc", required=True, max_length=100)
    artist_name = discord.ui.TextInput(label="Artist Name", placeholder="Artist name", required=True, max_length=100)
    product = discord.ui.TextInput(label="Product", placeholder="Product name", required=True, max_length=100)
    quantity = discord.ui.TextInput(label="Quantity", placeholder="2,000 units", required=True, max_length=100)
    ship_date = discord.ui.TextInput(label="Ship Date", placeholder="April 20, 2026", required=True, max_length=100)
    target_artist = discord.ui.TextInput(label="Target Artist (optional)", placeholder="Artist to notify", required=False, max_length=100)

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        draft = drafts.get(self.user_id)
        if not draft:
            await interaction.response.send_message("Your draft expired. Run /post again.", ephemeral=True)
            return
        draft["shop_name"] = self.shop_name.value
        draft["artist_name"] = self.artist_name.value
        draft["product"] = self.product.value
        draft["quantity"] = self.quantity.value
        draft["ship_date"] = self.ship_date.value
        draft["target_artist"] = self.target_artist.value.strip() or self.artist_name.value.strip()
        await interaction.response.send_message("Saved merch fields.", ephemeral=True)


class PreviewView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id

    @discord.ui.button(label="Back to Builder", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("That isn't your draft.", ephemeral=True)
            return
        draft = drafts.get(self.user_id)
        if not draft:
            await interaction.response.send_message("Your draft expired. Run /post again.", ephemeral=True)
            return
        await interaction.response.edit_message(content=build_status_text(draft, interaction.guild), embed=None, attachments=[], view=PostBuilderView(self.user_id))


class PostBuilderView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=900)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("That isn't your draft.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Set Type", style=discord.ButtonStyle.primary, row=0)
    async def set_type(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Pick your post type below.", view=TypeSelectView(self.user_id), ephemeral=True)

    @discord.ui.button(label="Set Basic Info", style=discord.ButtonStyle.secondary, row=0)
    async def set_basic(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BasicInfoModal(self.user_id))

    @discord.ui.button(label="Set Body Fields", style=discord.ButtonStyle.secondary, row=0)
    async def set_body(self, interaction: discord.Interaction, button: discord.ui.Button):
        draft = drafts.get(self.user_id)
        if not draft:
            await interaction.response.send_message("Your draft expired. Run /post again.", ephemeral=True)
            return
        post_type = draft.get("post_type")
        if not post_type:
            await interaction.response.send_message("Set the post type first.", ephemeral=True)
            return
        if post_type == "data":
            await interaction.response.send_modal(DataModal(self.user_id))
        elif post_type in ("interview", "magazine"):
            await interaction.response.send_modal(InterviewMagazineModal(self.user_id, post_type))
        elif post_type == "merch":
            await interaction.response.send_modal(MerchModal(self.user_id))

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.success, row=1)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        draft = drafts.get(self.user_id)
        if not draft:
            await interaction.response.send_message("Your draft expired. Run /post again.", ephemeral=True)
            return
        embed = build_post_embed(draft)
        attachments = []
        if draft.get("image") is not None:
            attachments = [await draft["image"].to_file()]
        await interaction.response.edit_message(content=draft.get("ping") or "", embed=embed, attachments=attachments, view=PreviewView(self.user_id))

    @discord.ui.button(label="Post", style=discord.ButtonStyle.primary, row=1)
    async def post_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        draft = drafts.get(self.user_id)
        if not draft:
            await interaction.response.send_message("Your draft expired. Run /post again.", ephemeral=True)
            return
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this inside your server.", ephemeral=True)
            return
        if not draft.get("post_type"):
            await interaction.response.send_message("Set the post type first.", ephemeral=True)
            return

        target_channel = interaction.guild.get_channel(draft.get("channel_id") or interaction.channel.id)
        if not isinstance(target_channel, discord.TextChannel):
            target_channel = interaction.channel

        embed = build_post_embed(draft)
        kwargs = {
            "content": draft.get("ping", ""),
            "embed": embed,
            "allowed_mentions": discord.AllowedMentions(everyone=True, roles=True),
        }
        if draft.get("image") is not None:
            kwargs["file"] = await draft["image"].to_file()
        await target_channel.send(**kwargs)

        target_artist_name = (draft.get("target_artist") or "").strip()
        if target_artist_name:
            artist = find_artist(target_artist_name)
            if artist:
                await notify_artist_owner(
                    interaction.client,
                    artist,
                    "You Were Mentioned",
                    f"Your artist **{artist['stage_name']}** was mentioned in a **{draft['post_type'].title()}** post in **{interaction.guild.name}**.",
                )

        drafts.pop(self.user_id, None)
        await interaction.response.send_message(f"Posted in {target_channel.mention}.", ephemeral=True)

    @discord.ui.button(label="Cancel Draft", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        drafts.pop(self.user_id, None)
        await interaction.response.edit_message(content="Draft cancelled.", embed=None, attachments=[], view=None)


class PostSystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="post", description="Create an Encore post draft in the current channel")
    async def post(self, interaction: discord.Interaction, image: Optional[discord.Attachment] = None):
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this inside your server.", ephemeral=True)
            return
        draft = drafts.get(interaction.user.id) or make_empty_draft(interaction.user.id, interaction.channel.id)
        draft["channel_id"] = interaction.channel.id
        if image is not None:
            draft["image"] = image
        drafts[interaction.user.id] = draft
        await interaction.response.send_message(build_status_text(draft, interaction.guild), view=PostBuilderView(interaction.user.id), ephemeral=True)


async def setup_post_system(bot: commands.Bot):
    await bot.add_cog(PostSystem(bot))
