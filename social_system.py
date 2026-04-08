import os
import random
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

DB_PATH = os.getenv("DB_PATH", "music_sim_social.db")
DEFAULT_FOLLOWERS = 25_000
PLATFORMS = ("instagram", "twitter", "tiktok")
TREND_MULTIPLIERS = {
    "cancelled": 0.28,
    "declining": 0.68,
    "stable": 1.0,
    "rising": 1.22,
    "viral": 1.6,
    "superstar": 2.15,
}
TREND_CHOICES = tuple(TREND_MULTIPLIERS.keys())


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_handle(handle: str) -> str:
    cleaned = re.sub(r"[^a-z0-9._]", "", handle.lower().replace(" ", "_"))
    cleaned = cleaned.strip("._")
    return cleaned[:24]


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def format_number(value: int) -> str:
    return f"{int(value):,}"


def relative_timestamp_text(iso_value: str) -> str:
    dt = datetime.fromisoformat(iso_value)
    return f"<t:{int(dt.timestamp())}:R>"


def format_platform_name(platform: str) -> str:
    return {
        "instagram": "Instagram",
        "twitter": "Twitter",
        "tiktok": "TikTok",
    }.get(platform, platform.title())


def init_social_db() -> None:
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artists (
                artist_id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                stage_name TEXT NOT NULL,
                stage_name_key TEXT NOT NULL UNIQUE,
                handle TEXT NOT NULL,
                avatar_url TEXT,
                bio TEXT NOT NULL DEFAULT '',
                trend TEXT NOT NULL DEFAULT 'stable',
                instagram_followers INTEGER NOT NULL DEFAULT 25000,
                twitter_followers INTEGER NOT NULL DEFAULT 25000,
                tiktok_followers INTEGER NOT NULL DEFAULT 25000,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_personas (
                persona_id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_id INTEGER NOT NULL UNIQUE,
                owner_user_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                display_name_key TEXT NOT NULL,
                handle TEXT NOT NULL,
                avatar_url TEXT,
                bio TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS social_posts (
                post_id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_id INTEGER NOT NULL,
                owner_user_id INTEGER NOT NULL,
                stage_name TEXT NOT NULL,
                handle TEXT NOT NULL,
                avatar_url TEXT,
                platform TEXT NOT NULL,
                caption TEXT NOT NULL,
                media_url TEXT,
                channel_id INTEGER,
                message_id INTEGER,
                created_at TEXT NOT NULL,
                likes INTEGER NOT NULL DEFAULT 0,
                comments INTEGER NOT NULL DEFAULT 0,
                replies INTEGER NOT NULL DEFAULT 0,
                reposts INTEGER NOT NULL DEFAULT 0,
                views INTEGER NOT NULL DEFAULT 0,
                shares INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS post_interactions (
                interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                interaction_type TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(post_id, user_id, interaction_type)
            )
            """
        )

        artist_cols = {row["name"] for row in conn.execute("PRAGMA table_info(artists)").fetchall()}
        if "trend" not in artist_cols:
            conn.execute("ALTER TABLE artists ADD COLUMN trend TEXT NOT NULL DEFAULT 'stable'")

        conn.commit()


def register_artist(owner_user_id: int, stage_name: str, handle: str, avatar_url: Optional[str], bio: str) -> tuple[bool, str]:
    stage_name = re.sub(r"\s+", " ", stage_name.strip())
    handle = clean_handle(handle or stage_name)
    if not stage_name or not handle:
        return False, "Artist name or handle is invalid."

    try:
        with db_connect() as conn:
            conn.execute(
                """
                INSERT INTO artists (
                    owner_user_id, stage_name, stage_name_key, handle, avatar_url, bio, trend,
                    instagram_followers, twitter_followers, tiktok_followers, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_user_id,
                    stage_name,
                    normalize_name(stage_name),
                    handle,
                    avatar_url,
                    bio.strip(),
                    "stable",
                    DEFAULT_FOLLOWERS,
                    DEFAULT_FOLLOWERS,
                    DEFAULT_FOLLOWERS,
                    now_iso(),
                ),
            )
            conn.commit()
        return True, handle
    except sqlite3.IntegrityError:
        return False, "That artist name is already registered."


def list_artists_for_user(owner_user_id: int) -> list[sqlite3.Row]:
    with db_connect() as conn:
        return conn.execute(
            "SELECT * FROM artists WHERE owner_user_id = ? ORDER BY stage_name COLLATE NOCASE ASC",
            (owner_user_id,),
        ).fetchall()


def find_artist(query: str) -> Optional[sqlite3.Row]:
    key = normalize_name(query)
    handle = clean_handle(query)
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM artists WHERE stage_name_key = ? OR handle = ? ORDER BY artist_id DESC LIMIT 1",
            (key, handle),
        ).fetchone()
        if row:
            return row
        return conn.execute(
            "SELECT * FROM artists WHERE stage_name LIKE ? OR handle LIKE ? ORDER BY artist_id DESC LIMIT 1",
            (f"%{query.strip()}%", f"%{handle}%"),
        ).fetchone()


def create_or_update_persona(
    artist: sqlite3.Row,
    display_name: str,
    handle: str,
    avatar_url: Optional[str],
    bio: str,
) -> tuple[bool, str]:
    display_name = re.sub(r"\s+", " ", display_name.strip())
    handle = clean_handle(handle or display_name)
    if not display_name or not handle:
        return False, "Persona name or handle is invalid."

    with db_connect() as conn:
        existing = conn.execute(
            "SELECT * FROM social_personas WHERE artist_id = ?",
            (int(artist["artist_id"]),),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE social_personas
                SET display_name = ?, display_name_key = ?, handle = ?, avatar_url = ?, bio = ?
                WHERE artist_id = ?
                """,
                (
                    display_name,
                    normalize_name(display_name),
                    handle,
                    avatar_url,
                    bio.strip(),
                    int(artist["artist_id"]),
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO social_personas (
                    artist_id, owner_user_id, display_name, display_name_key, handle, avatar_url, bio, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(artist["artist_id"]),
                    int(artist["owner_user_id"]),
                    display_name,
                    normalize_name(display_name),
                    handle,
                    avatar_url,
                    bio.strip(),
                    now_iso(),
                ),
            )
        conn.commit()

    return True, handle


def get_persona_for_artist(artist_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as conn:
        return conn.execute(
            "SELECT * FROM social_personas WHERE artist_id = ?",
            (artist_id,),
        ).fetchone()


def list_personas_for_user(owner_user_id: int) -> list[sqlite3.Row]:
    with db_connect() as conn:
        return conn.execute(
            """
            SELECT p.*, a.stage_name
            FROM social_personas p
            JOIN artists a ON a.artist_id = p.artist_id
            WHERE p.owner_user_id = ?
            ORDER BY p.display_name COLLATE NOCASE ASC
            """,
            (owner_user_id,),
        ).fetchall()


def find_persona(query: str) -> Optional[sqlite3.Row]:
    key = normalize_name(query)
    handle = clean_handle(query)
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT p.*, a.stage_name
            FROM social_personas p
            JOIN artists a ON a.artist_id = p.artist_id
            WHERE p.display_name_key = ? OR p.handle = ?
            ORDER BY p.persona_id DESC LIMIT 1
            """,
            (key, handle),
        ).fetchone()
        if row:
            return row
        return conn.execute(
            """
            SELECT p.*, a.stage_name
            FROM social_personas p
            JOIN artists a ON a.artist_id = p.artist_id
            WHERE p.display_name LIKE ? OR p.handle LIKE ? OR a.stage_name LIKE ?
            ORDER BY p.persona_id DESC LIMIT 1
            """,
            (f"%{query.strip()}%", f"%{handle}%", f"%{query.strip()}%"),
        ).fetchone()


def update_followers(artist_id: int, platform: str, amount: int) -> Optional[sqlite3.Row]:
    if platform not in PLATFORMS:
        return None
    column = f"{platform}_followers"
    amount = max(0, int(amount))
    with db_connect() as conn:
        conn.execute(f"UPDATE artists SET {column} = ? WHERE artist_id = ?", (amount, artist_id))
        conn.commit()
        return conn.execute("SELECT * FROM artists WHERE artist_id = ?", (artist_id,)).fetchone()


def update_trend(artist_id: int, trend: str) -> Optional[sqlite3.Row]:
    if trend not in TREND_CHOICES:
        return None
    with db_connect() as conn:
        conn.execute("UPDATE artists SET trend = ? WHERE artist_id = ?", (trend, artist_id))
        conn.commit()
        return conn.execute("SELECT * FROM artists WHERE artist_id = ?", (artist_id,)).fetchone()


def get_followers(artist: sqlite3.Row, platform: str) -> int:
    return int(artist[f"{platform}_followers"])


def generate_platform_metrics(platform: str, followers: int, trend: str) -> dict:
    mult = TREND_MULTIPLIERS.get(trend, 1.0)
    followers = max(0, followers)

    if platform == "instagram":
        likes = max(20, int(followers * random.uniform(0.03, 0.11) * mult))
        comments = max(2, int(likes * random.uniform(0.015, 0.05)))
        return {
            "likes": likes,
            "comments": comments,
            "replies": 0,
            "reposts": 0,
            "views": 0,
            "shares": 0,
        }

    if platform == "twitter":
        likes = max(10, int(followers * random.uniform(0.008, 0.04) * mult))
        replies = max(1, int(likes * random.uniform(0.09, 0.24)))
        reposts = max(1, int(likes * random.uniform(0.12, 0.4)))
        return {
            "likes": likes,
            "comments": 0,
            "replies": replies,
            "reposts": reposts,
            "views": 0,
            "shares": 0,
        }

    likes = max(20, int(followers * random.uniform(0.018, 0.09) * mult))
    comments = max(2, int(likes * random.uniform(0.015, 0.05)))
    shares = max(1, int(likes * random.uniform(0.02, 0.08)))
    views = max(likes * 5, int(followers * random.uniform(0.15, 0.75) * mult))
    return {
        "likes": likes,
        "comments": comments,
        "replies": 0,
        "reposts": 0,
        "views": views,
        "shares": shares,
    }


def create_social_post(
    artist: sqlite3.Row,
    platform: str,
    caption: str,
    media_url: Optional[str],
    metrics: dict,
    persona: Optional[sqlite3.Row] = None,
) -> int:
    stage_name = persona["display_name"] if persona else artist["stage_name"]
    handle = persona["handle"] if persona else artist["handle"]
    avatar_url = persona["avatar_url"] if persona and persona["avatar_url"] else artist["avatar_url"]

    with db_connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO social_posts (
                artist_id, owner_user_id, stage_name, handle, avatar_url, platform, caption, media_url,
                channel_id, message_id, created_at, likes, comments, replies, reposts, views, shares
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(artist["artist_id"]),
                int(artist["owner_user_id"]),
                stage_name,
                handle,
                avatar_url,
                platform,
                caption.strip(),
                media_url.strip() if media_url else None,
                None,
                None,
                now_iso(),
                metrics["likes"],
                metrics["comments"],
                metrics["replies"],
                metrics["reposts"],
                metrics["views"],
                metrics["shares"],
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_post(post_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as conn:
        return conn.execute("SELECT * FROM social_posts WHERE post_id = ?", (post_id,)).fetchone()


def attach_message_to_post(post_id: int, channel_id: int, message_id: int) -> None:
    with db_connect() as conn:
        conn.execute(
            "UPDATE social_posts SET channel_id = ?, message_id = ? WHERE post_id = ?",
            (channel_id, message_id, post_id),
        )
        conn.commit()


def add_post_interaction(post_id: int, user_id: int, interaction_type: str, content: str = "") -> tuple[bool, Optional[sqlite3.Row]]:
    column_map = {
        "like": "likes",
        "comment": "comments",
        "reply": "replies",
    }
    if interaction_type not in column_map:
        return False, None

    with db_connect() as conn:
        try:
            conn.execute(
                """
                INSERT INTO post_interactions (post_id, user_id, interaction_type, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (post_id, user_id, interaction_type, content.strip(), now_iso()),
            )
        except sqlite3.IntegrityError:
            return False, conn.execute("SELECT * FROM social_posts WHERE post_id = ?", (post_id,)).fetchone()

        conn.execute(
            f"UPDATE social_posts SET {column_map[interaction_type]} = {column_map[interaction_type]} + 1 WHERE post_id = ?",
            (post_id,),
        )
        conn.commit()
        return True, conn.execute("SELECT * FROM social_posts WHERE post_id = ?", (post_id,)).fetchone()


async def notify_artist_owner(bot: commands.Bot, artist: sqlite3.Row, title: str, description: str) -> None:
    user = bot.get_user(int(artist["owner_user_id"]))
    if user is None:
        try:
            user = await bot.fetch_user(int(artist["owner_user_id"]))
        except Exception:
            return

    try:
        embed = discord.Embed(title=title, description=description, color=discord.Color.purple())
        await user.send(embed=embed)
    except Exception:
        return


def build_social_embed(post: sqlite3.Row, artist: sqlite3.Row) -> discord.Embed:
    platform = post["platform"]
    color_map = {
        "instagram": discord.Color.from_rgb(214, 41, 118),
        "twitter": discord.Color.blurple(),
        "tiktok": discord.Color.dark_embed(),
    }

    embed = discord.Embed(color=color_map.get(platform, discord.Color.blurple()))
    embed.title = f"{format_platform_name(platform)} • {post['stage_name']}"

    if post["avatar_url"]:
        embed.set_author(name=f"@{post['handle']}", icon_url=post["avatar_url"])
    else:
        embed.set_author(name=f"@{post['handle']}")

    desc = post["caption"]
    if platform == "twitter":
        desc += (
            f"\n\n❤️ {format_number(post['likes'])}  •  🔁 {format_number(post['reposts'])}"
            f"  •  💬 {format_number(post['replies'])}"
        )
    elif platform == "instagram":
        desc += f"\n\n❤️ {format_number(post['likes'])}  •  💬 {format_number(post['comments'])}"
    else:
        desc += (
            f"\n\n▶️ {format_number(post['views'])}  •  ❤️ {format_number(post['likes'])}"
            f"  •  💬 {format_number(post['comments'])}  •  ↗️ {format_number(post['shares'])}"
        )

    embed.description = desc

    if platform == "tiktok":
        if post["media_url"]:
            embed.add_field(name="Video", value=f"[Open video]({post['media_url']})", inline=False)
    elif post["media_url"]:
        embed.set_image(url=post["media_url"])

    embed.add_field(name="Followers", value=format_number(get_followers(artist, platform)), inline=True)
    embed.add_field(name="Trend", value=(artist["trend"] or "stable").title(), inline=True)
    embed.add_field(name="Posted", value=relative_timestamp_text(post["created_at"]), inline=True)
    embed.set_footer(text=f"Post #{post['post_id']} • use the buttons below to interact")
    return embed


def build_artist_embed(artist: sqlite3.Row) -> discord.Embed:
    embed = discord.Embed(
        title=artist["stage_name"],
        description=artist["bio"] or "No bio yet.",
        color=discord.Color.purple(),
    )

    if artist["avatar_url"]:
        embed.set_author(name=f"@{artist['handle']}", icon_url=artist["avatar_url"])
    else:
        embed.set_author(name=f"@{artist['handle']}")

    embed.add_field(name="Instagram", value=format_number(artist["instagram_followers"]), inline=True)
    embed.add_field(name="Twitter", value=format_number(artist["twitter_followers"]), inline=True)
    embed.add_field(name="TikTok", value=format_number(artist["tiktok_followers"]), inline=True)
    embed.add_field(name="Trend", value=(artist["trend"] or "stable").title(), inline=True)
    embed.add_field(name="Owner", value=f"<@{artist['owner_user_id']}>", inline=True)
    embed.add_field(name="Status", value="Registered", inline=True)
    return embed


def build_persona_embed(persona: sqlite3.Row, artist: sqlite3.Row) -> discord.Embed:
    embed = discord.Embed(
        title=persona["display_name"],
        description=persona["bio"] or "No social bio yet.",
        color=discord.Color.magenta(),
    )
    if persona["avatar_url"]:
        embed.set_author(name=f"@{persona['handle']}", icon_url=persona["avatar_url"])
    else:
        embed.set_author(name=f"@{persona['handle']}")
    embed.add_field(name="Linked Artist", value=artist["stage_name"], inline=True)
    embed.add_field(name="Owner", value=f"<@{persona['owner_user_id']}>", inline=True)
    embed.add_field(name="Created", value=relative_timestamp_text(persona["created_at"]), inline=True)
    return embed


def owner_only(interaction: discord.Interaction) -> bool:
    return bool(interaction.guild and interaction.user.id == interaction.guild.owner_id)


class CommentModal(discord.ui.Modal):
    comment = discord.ui.TextInput(
        label="Write your message",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )

    def __init__(self, parent_view: "SocialPostView"):
        title = "Reply to post" if parent_view.platform == "twitter" else "Comment on post"
        super().__init__(title=title)
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        interaction_type = "reply" if self.parent_view.platform == "twitter" else "comment"
        added, updated_post = add_post_interaction(
            self.parent_view.post_id,
            interaction.user.id,
            interaction_type,
            self.comment.value,
        )
        if not added:
            noun = "reply" if interaction_type == "reply" else "comment"
            await interaction.response.send_message(f"You already left a {noun} on this post.", ephemeral=True)
            return

        if updated_post is None:
            await interaction.response.send_message("That post could not be updated.", ephemeral=True)
            return

        artist = find_artist(updated_post["stage_name"]) or get_artist_by_id(int(updated_post["artist_id"]))
        if artist is None:
            await interaction.response.send_message("Artist data was missing.", ephemeral=True)
            return

        self.parent_view.refresh_from_post(updated_post)
        await interaction.message.edit(embed=build_social_embed(updated_post, artist), view=self.parent_view)
        label = "Reply" if interaction_type == "reply" else "Comment"
        await interaction.response.send_message(f"{label} added.", ephemeral=True)


def get_artist_by_id(artist_id: int) -> Optional[sqlite3.Row]:
    with db_connect() as conn:
        return conn.execute("SELECT * FROM artists WHERE artist_id = ?", (artist_id,)).fetchone()


class SocialPostView(discord.ui.View):
    def __init__(self, post_id: int, platform: str):
        super().__init__(timeout=None)
        self.post_id = int(post_id)
        self.platform = platform
        self._set_comment_label()

    def _set_comment_label(self):
        if len(self.children) >= 2:
            button = self.children[1]
            if isinstance(button, discord.ui.Button):
                button.label = "Reply" if self.platform == "twitter" else "Comment"

    def refresh_from_post(self, post: sqlite3.Row):
        self.post_id = int(post["post_id"])
        self.platform = post["platform"]
        self._set_comment_label()

    @discord.ui.button(label="Like", style=discord.ButtonStyle.success)
    async def like_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        added, updated_post = add_post_interaction(self.post_id, interaction.user.id, "like")
        if not added:
            await interaction.response.send_message("You already liked this post.", ephemeral=True)
            return

        if updated_post is None:
            await interaction.response.send_message("That post could not be updated.", ephemeral=True)
            return

        artist = get_artist_by_id(int(updated_post["artist_id"]))
        if artist is None:
            await interaction.response.send_message("Artist data was missing.", ephemeral=True)
            return

        self.refresh_from_post(updated_post)
        await interaction.response.edit_message(embed=build_social_embed(updated_post, artist), view=self)

    @discord.ui.button(label="Comment", style=discord.ButtonStyle.primary)
    async def comment_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CommentModal(self))


class SocialMedia(commands.Cog):
    def __init__(self, bot_client: commands.Bot):
        self.bot = bot_client

    @app_commands.command(name="registerartist", description="Register your artist profile.")
    async def registerartist(
        self,
        interaction: discord.Interaction,
        artist_name: str,
        handle: Optional[str] = None,
        avatar_url: Optional[str] = None,
        bio: Optional[str] = None,
    ):
        success, message = register_artist(
            interaction.user.id,
            artist_name,
            handle or artist_name,
            avatar_url,
            bio or "",
        )
        if not success:
            await interaction.response.send_message(message, ephemeral=True)
            return

        await interaction.response.send_message(
            f"Registered **{artist_name}** as **@{message}**. Instagram, Twitter, and TikTok all start at **{format_number(DEFAULT_FOLLOWERS)}** followers with trend set to **Stable**.",
            ephemeral=True,
        )

    @app_commands.command(name="createpersona", description="Create or update a separate social media persona for an artist.")
    async def createpersona(
        self,
        interaction: discord.Interaction,
        artist: str,
        display_name: str,
        handle: Optional[str] = None,
        avatar_url: Optional[str] = None,
        bio: Optional[str] = None,
    ):
        row = find_artist(artist)
        if not row:
            await interaction.response.send_message("Artist not found.", ephemeral=True)
            return

        if int(row["owner_user_id"]) != interaction.user.id and not owner_only(interaction):
            await interaction.response.send_message(
                "You can only create a persona for your own artist unless you're the server owner.",
                ephemeral=True,
            )
            return

        success, persona_handle = create_or_update_persona(
            row,
            display_name,
            handle or display_name,
            avatar_url,
            bio or "",
        )
        if not success:
            await interaction.response.send_message(persona_handle, ephemeral=True)
            return

        await interaction.response.send_message(
            f"Saved social persona **{display_name}** as **@{persona_handle}** for **{row['stage_name']}**.",
            ephemeral=True,
        )

    @app_commands.command(name="personas", description="See your saved social personas.")
    async def personas(self, interaction: discord.Interaction):
        rows = list_personas_for_user(interaction.user.id)
        if not rows:
            await interaction.response.send_message("You have not created any social personas yet.", ephemeral=True)
            return

        lines = []
        for row in rows:
            lines.append(f"**{row['display_name']}** (@{row['handle']}) → linked to **{row['stage_name']}**")

        embed = discord.Embed(title="Your Social Personas", description="\n".join(lines), color=discord.Color.magenta())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="personaprofile", description="View a social persona profile.")
    async def personaprofile(self, interaction: discord.Interaction, persona: str):
        persona_row = find_persona(persona)
        if not persona_row:
            await interaction.response.send_message("Persona not found.", ephemeral=True)
            return

        artist = get_artist_by_id(int(persona_row["artist_id"]))
        if artist is None:
            await interaction.response.send_message("Linked artist not found.", ephemeral=True)
            return

        await interaction.response.send_message(embed=build_persona_embed(persona_row, artist))

    @app_commands.command(name="artists", description="See your registered artists.")
    async def artists(self, interaction: discord.Interaction):
        rows = list_artists_for_user(interaction.user.id)
        if not rows:
            await interaction.response.send_message("You have not registered any artists yet.", ephemeral=True)
            return

        lines = []
        for row in rows:
            lines.append(
                f"**{row['stage_name']}** (@{row['handle']})\n"
                f"IG {format_number(row['instagram_followers'])} • TW {format_number(row['twitter_followers'])} • TT {format_number(row['tiktok_followers'])} • {row['trend'].title()}"
            )

        embed = discord.Embed(title="Your Artists", description="\n\n".join(lines), color=discord.Color.purple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="artistprofile", description="View a registered artist profile.")
    async def artistprofile(self, interaction: discord.Interaction, artist: str):
        row = find_artist(artist)
        if not row:
            await interaction.response.send_message("Artist not found.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_artist_embed(row))

    async def _send_social_post(
        self,
        interaction: discord.Interaction,
        platform: str,
        artist: str,
        caption: str,
        media_url: Optional[str],
    ):
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Use this inside a text channel.", ephemeral=True)
            return

        row = find_artist(artist)
        if not row:
            await interaction.response.send_message("Artist not found.", ephemeral=True)
            return

        if int(row["owner_user_id"]) != interaction.user.id and not owner_only(interaction):
            await interaction.response.send_message(
                "You can only post for your own registered artist unless you're the server owner.",
                ephemeral=True,
            )
            return

        persona = get_persona_for_artist(int(row["artist_id"]))
        metrics = generate_platform_metrics(platform, get_followers(row, platform), (row["trend"] or "stable").lower())
        post_id = create_social_post(row, platform, caption, media_url, metrics, persona=persona)
        post = get_post(post_id)
        if not post:
            await interaction.response.send_message("Could not create the post.", ephemeral=True)
            return

        view = SocialPostView(post_id, platform)
        message = await interaction.channel.send(embed=build_social_embed(post, row), view=view)
        attach_message_to_post(post_id, interaction.channel.id, message.id)

        display_name = persona["display_name"] if persona else row["stage_name"]
        await interaction.response.send_message(
            f"{format_platform_name(platform)} post for **{display_name}** is live in {interaction.channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(name="instagrampost", description="Post an Instagram update in the current channel.")
    async def instagrampost(self, interaction: discord.Interaction, artist: str, caption: str, image_url: Optional[str] = None):
        await self._send_social_post(interaction, "instagram", artist, caption, image_url)

    @app_commands.command(name="twitterpost", description="Post a Twitter update in the current channel.")
    async def twitterpost(self, interaction: discord.Interaction, artist: str, caption: str, image_url: Optional[str] = None):
        await self._send_social_post(interaction, "twitter", artist, caption, image_url)

    @app_commands.command(name="tiktokpost", description="Post a TikTok update in the current channel.")
    async def tiktokpost(self, interaction: discord.Interaction, artist: str, caption: str, video_url: str):
        await self._send_social_post(interaction, "tiktok", artist, caption, video_url)

    @app_commands.command(name="setfollowers", description="Owner only: set an artist's followers on a platform.")
    async def setfollowers(self, interaction: discord.Interaction, artist: str, platform: str, amount: int):
        if not owner_only(interaction):
            await interaction.response.send_message("Only the server owner can use this.", ephemeral=True)
            return

        row = find_artist(artist)
        platform = platform.lower().strip()
        if not row or platform not in PLATFORMS:
            await interaction.response.send_message("Artist or platform not found.", ephemeral=True)
            return

        row = update_followers(int(row["artist_id"]), platform, amount)
        await interaction.response.send_message(
            f"{row['stage_name']}'s {format_platform_name(platform)} followers are now **{format_number(row[f'{platform}_followers'])}**.",
            ephemeral=True,
        )
        await notify_artist_owner(
            self.bot,
            row,
            "Followers Updated",
            f"Your **{format_platform_name(platform)}** followers for **{row['stage_name']}** are now **{format_number(row[f'{platform}_followers'])}**.",
        )

    @app_commands.command(name="addfollowers", description="Owner only: add followers to an artist.")
    async def addfollowers(self, interaction: discord.Interaction, artist: str, platform: str, amount: int):
        if not owner_only(interaction):
            await interaction.response.send_message("Only the server owner can use this.", ephemeral=True)
            return

        row = find_artist(artist)
        platform = platform.lower().strip()
        if not row or platform not in PLATFORMS:
            await interaction.response.send_message("Artist or platform not found.", ephemeral=True)
            return

        current = int(row[f"{platform}_followers"])
        row = update_followers(int(row["artist_id"]), platform, current + max(0, amount))
        await interaction.response.send_message(
            f"Added **{format_number(amount)}** followers to **{row['stage_name']}** on {format_platform_name(platform)}. New total: **{format_number(row[f'{platform}_followers'])}**.",
            ephemeral=True,
        )
        await notify_artist_owner(
            self.bot,
            row,
            "Followers Updated",
            f"You gained **{format_number(amount)}** {format_platform_name(platform)} followers for **{row['stage_name']}**. New total: **{format_number(row[f'{platform}_followers'])}**.",
        )

    @app_commands.command(name="removefollowers", description="Owner only: remove followers from an artist.")
    async def removefollowers(self, interaction: discord.Interaction, artist: str, platform: str, amount: int):
        if not owner_only(interaction):
            await interaction.response.send_message("Only the server owner can use this.", ephemeral=True)
            return

        row = find_artist(artist)
        platform = platform.lower().strip()
        if not row or platform not in PLATFORMS:
            await interaction.response.send_message("Artist or platform not found.", ephemeral=True)
            return

        current = int(row[f"{platform}_followers"])
        row = update_followers(int(row["artist_id"]), platform, max(0, current - max(0, amount)))
        await interaction.response.send_message(
            f"Removed **{format_number(amount)}** followers from **{row['stage_name']}** on {format_platform_name(platform)}. New total: **{format_number(row[f'{platform}_followers'])}**.",
            ephemeral=True,
        )
        await notify_artist_owner(
            self.bot,
            row,
            "Followers Updated",
            f"Your **{row['stage_name']}** {format_platform_name(platform)} followers were adjusted. New total: **{format_number(row[f'{platform}_followers'])}**.",
        )

    @app_commands.command(name="settrend", description="Owner only: set an artist trend.")
    async def settrend(self, interaction: discord.Interaction, artist: str, trend: str):
        if not owner_only(interaction):
            await interaction.response.send_message("Only the server owner can use this.", ephemeral=True)
            return

        row = find_artist(artist)
        trend = trend.lower().strip()
        if not row or trend not in TREND_CHOICES:
            await interaction.response.send_message(
                f"Artist or trend not found. Trends: {', '.join(t.title() for t in TREND_CHOICES)}",
                ephemeral=True,
            )
            return

        row = update_trend(int(row["artist_id"]), trend)
        await interaction.response.send_message(
            f"{row['stage_name']}'s trend is now **{row['trend'].title()}**.",
            ephemeral=True,
        )
        await notify_artist_owner(
            self.bot,
            row,
            "Trend Updated",
            f"Your artist **{row['stage_name']}** is now set to **{row['trend'].title()}**.",
        )


async def setup_social_system(bot: commands.Bot):
    init_social_db()
    await bot.add_cog(SocialMedia(bot))
