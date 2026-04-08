import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands

from shop_data import SHOP_ITEMS, PHYSICAL_STOCK, SHOP_CATEGORY_LABELS, STORE_NOTES
from social_system import find_artist, notify_artist_owner

DATA_FILE = "economy_data.json"
STAFF_GUILD_ID = 1455578464320098306
STAFF_GUILD = discord.Object(id=STAFF_GUILD_ID)


def utc_now():
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.isoformat()


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def format_money(amount: float) -> str:
    if float(amount).is_integer():
        return f"${amount:,.0f}"
    return f"${amount:,.2f}"


def load_data():
    if not os.path.exists(DATA_FILE):
        return {"users": {}, "labels": {}, "artist_labels": {}}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        data.setdefault("users", {})
        data.setdefault("labels", {})
        data.setdefault("artist_labels", {})
        return data


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def normalize_key(value: str) -> str:
    return " ".join(value.strip().lower().split())


def format_label_name(value: str) -> str:
    return " ".join(value.strip().split())


def ensure_label_shape(label: dict):
    label.setdefault("name", "")
    label.setdefault("funds", 0)
    label.setdefault("owner_ids", [])
    label.setdefault("log_channel_id", None)


def get_label_record(data: dict, label_name: str):
    key = normalize_key(label_name)
    labels = data.setdefault("labels", {})
    label = labels.get(key)
    if label:
        ensure_label_shape(label)
    return key, label


def user_can_manage_label(label: dict, user_id: int) -> bool:
    return int(user_id) in {int(x) for x in label.get("owner_ids", [])}


def get_artist_label_name(data: dict, artist_name: str) -> Optional[str]:
    return data.setdefault("artist_labels", {}).get(normalize_key(artist_name))


def set_artist_label_name(data: dict, artist_name: str, label_name: str):
    data.setdefault("artist_labels", {})[normalize_key(artist_name)] = format_label_name(label_name)


def clear_artist_label_name(data: dict, artist_name: str):
    data.setdefault("artist_labels", {}).pop(normalize_key(artist_name), None)


async def enforce_staff_server(interaction: discord.Interaction) -> bool:
    if not interaction.guild or interaction.guild.id != STAFF_GUILD_ID:
        if interaction.response.is_done():
            await interaction.followup.send("This command is only available in the staff server.", ephemeral=True)
        else:
            await interaction.response.send_message("This command is only available in the staff server.", ephemeral=True)
        return False
    return True


def ensure_record_shape(record: dict):
    record.setdefault("wallet", 0)
    record.setdefault("bank", 0)
    record.setdefault("inventory", [])
    record.setdefault("pending_royalties", [])
    record.setdefault("claimed_royalties_total", 0)
    record.setdefault("total_earnings", 0)
    record.setdefault("earnings_by_source", {})
    record.setdefault("log_channel_id", None)
    record.setdefault("purchase_history", [])
    record.setdefault("cart", [])
    record.setdefault("label_roles", [])


def get_user_record(data, user_id: int):
    users = data.setdefault("users", {})
    record = users.setdefault(str(user_id), {})
    ensure_record_shape(record)
    return record


def add_pending_earning(record: dict, source: str, amount: int):
    record.setdefault("pending_royalties", []).append(
        {
            "source": source,
            "amount": amount,
            "submitted_at": to_iso(utc_now()),
            "claimed": False,
        }
    )
    record["total_earnings"] = record.get("total_earnings", 0) + amount
    earnings_by_source = record.setdefault("earnings_by_source", {})
    earnings_by_source[source] = earnings_by_source.get(source, 0) + amount


def cleanup_expired_inventory(record):
    now = utc_now()
    kept = []

    for item in record["inventory"]:
        expires_at = item.get("expires_at")
        if expires_at:
            if from_iso(expires_at) > now:
                kept.append(item)
        else:
            kept.append(item)

    record["inventory"] = kept


def add_inventory_entry(
    record,
    item_id: str,
    name: str,
    category: str,
    quantity: int = 1,
    expires_in_days: Optional[int] = None,
):
    entry = {
        "item_id": item_id,
        "name": name,
        "category": category,
        "quantity": quantity,
        "purchased_at": to_iso(utc_now()),
        "expires_at": None,
    }

    if expires_in_days:
        entry["expires_at"] = to_iso(utc_now() + timedelta(days=expires_in_days))

    record["inventory"].append(entry)


def add_purchase_history_entry(
    record,
    *,
    item_id: str,
    name: str,
    category: str,
    quantity: int,
    total_cost: float,
    tier: Optional[str] = None,
    units_added: Optional[int] = None,
    charged_to_type: str = "wallet",
    charged_to_name: Optional[str] = None,
    balance_left: Optional[float] = None,
):
    entry = {
        "item_id": item_id,
        "name": name,
        "category": category,
        "quantity": quantity,
        "total_cost": total_cost,
        "tier": tier,
        "units_added": units_added,
        "charged_to_type": charged_to_type,
        "charged_to_name": charged_to_name,
        "balance_left": balance_left,
        "purchased_at": to_iso(utc_now()),
    }

    record["purchase_history"].append(entry)

    if len(record["purchase_history"]) > 200:
        record["purchase_history"] = record["purchase_history"][-200:]

    return entry


async def get_log_channel(bot, channel_id: Optional[int]):
    if not channel_id:
        return None

    channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel

    try:
        channel = await bot.fetch_channel(channel_id)
        return channel
    except Exception:
        return None


async def send_purchase_log(bot, user: discord.abc.User, record: dict, entry: dict, data: Optional[dict] = None):
    channel_id = None
    if entry.get("charged_to_type") == "label" and data is not None and entry.get("charged_to_name"):
        _, label = get_label_record(data, entry["charged_to_name"])
        if label:
            channel_id = label.get("log_channel_id")
    if not channel_id:
        channel_id = record.get("log_channel_id")
    if not channel_id:
        return

    channel = await get_log_channel(bot, channel_id)
    if channel is None:
        return

    embed = discord.Embed(
        title="Purchase Logged",
        color=discord.Color.blurple(),
        timestamp=from_iso(entry["purchased_at"]),
    )

    embed.description = f"{user.mention} bought **{entry['name']}**"

    lines = [
        f"**ID:** `{entry['item_id']}`",
        f"**Category:** {entry['category']}",
        f"**Cost:** {format_money(entry['total_cost'])}",
    ]
    if entry.get("charged_to_type") == "label":
        lines.append(f"**Charged To:** Label • {entry.get('charged_to_name', 'Unknown')}")
    else:
        lines.append("**Charged To:** Personal Wallet")

    if entry.get("tier"):
        lines.append(f"**Tier:** {entry['tier']}")
    if entry.get("units_added") is not None:
        lines.append(f"**Units Added:** {entry['units_added']:,}")
    else:
        lines.append(f"**Quantity:** x{entry['quantity']}")

    embed.add_field(name="Details", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"User ID: {user.id}")

    try:
        await channel.send(embed=embed)
    except Exception:
        pass


def make_simple_embed(title: str, description: str, color: discord.Color):
    return discord.Embed(title=title, description=description, color=color)


def chunk_list(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def format_store_item_line(item_id: str, item: dict) -> str:
    expiry = f" • {item['expires_in_days']}d" if item["expires_in_days"] else ""
    return f"• **{item['name']}** — {format_money(item['price'])}{expiry}\n`{item_id}`"


def detect_platform_group(item: dict) -> Optional[str]:
    text = f"{item.get('name', '')} {item.get('subcategory', '')}".lower()

    if "apple music" in text:
        return "Apple Music"
    if "spotify" in text:
        return "Spotify"
    if "youtube" in text:
        return "YouTube"
    if "tiktok" in text:
        return "TikTok"
    if "twitter" in text:
        return "Twitter / X"
    if "instagram" in text:
        return "Instagram"
    if "amazon" in text:
        return "Amazon"
    if "target" in text:
        return "Target"
    if "bandcamp" in text:
        return "Bandcamp"
    if "merchbar" in text:
        return "Merchbar"
    if "radio" in text or "airplay" in text:
        return "Radio"

    return None


def resolve_charge_target(data: dict, user_id: int, label_name: Optional[str]):
    if label_name and label_name.strip():
        key, label = get_label_record(data, label_name)
        if not label:
            return None, "That label does not exist."
        if not user_can_manage_label(label, user_id):
            return None, "You are not one of the owners for that label."
        return {"type": "label", "key": key, "name": label["name"], "balance": label.get("funds", 0), "record": label}, None

    record = get_user_record(data, user_id)
    return {"type": "wallet", "name": "Personal Wallet", "balance": record.get("wallet", 0), "record": record}, None


def charge_target_balance_text(charge_target: dict) -> str:
    return "Funds Left" if charge_target["type"] == "label" else "Wallet Left"


def deduct_from_charge_target(charge_target: dict, amount: float):
    if charge_target["type"] == "label":
        charge_target["record"]["funds"] = max(0, charge_target["record"].get("funds", 0) - amount)
        charge_target["balance"] = charge_target["record"]["funds"]
    else:
        charge_target["record"]["wallet"] = max(0, charge_target["record"].get("wallet", 0) - amount)
        charge_target["balance"] = charge_target["record"]["wallet"]


def calculate_cart_entry(item_id: str, quantity: int = 1, tier: Optional[str] = None, orders: Optional[int] = None, label_name: Optional[str] = None):
    item_id = item_id.lower().strip()

    if item_id in SHOP_ITEMS:
        if quantity is None or quantity <= 0:
            return None, "Quantity has to be at least 1."
        item = SHOP_ITEMS[item_id]
        if not item["stackable"] and quantity > 1:
            return None, "That item is not stackable. Add it one at a time."
        total_cost = item["price"] * quantity
        return {
            "entry_type": "normal",
            "item_id": item_id,
            "name": item["name"],
            "category": item["category"],
            "quantity": quantity,
            "expires_in_days": item["expires_in_days"],
            "total_cost": total_cost,
            "label_name": format_label_name(label_name) if label_name else None,
        }, None

    if item_id in PHYSICAL_STOCK:
        tier = (tier or "").lower().strip()
        orders = 1 if orders is None else orders
        if orders <= 0:
            return None, "Orders must be at least 1."
        item = PHYSICAL_STOCK[item_id]
        if tier not in item["tiers"]:
            return None, f"That tier does not exist. Valid tiers: {', '.join(item['tiers'].keys())}"
        tier_price = item["tiers"][tier]
        total_cost = tier_price * orders
        units_bought = orders if tier == "single" else int(tier) * orders
        return {
            "entry_type": "physical",
            "item_id": item_id,
            "name": item["name"],
            "category": "physicals",
            "tier": tier,
            "orders": orders,
            "units_added": units_bought,
            "total_cost": total_cost,
            "label_name": format_label_name(label_name) if label_name else None,
        }, None

    return None, "That item_id does not exist."


def format_cart_line(index: int, entry: dict) -> str:
    label_piece = f" • Label: {entry['label_name']}" if entry.get("label_name") else ""
    if entry["entry_type"] == "physical":
        return (
            f"`{index}.` **{entry['name']}**\n"
            f"Tier: {entry['tier']} • Orders: x{entry['orders']} • Units: {entry['units_added']:,}{label_piece}\n"
            f"Cost: {format_money(entry['total_cost'])}"
        )
    extra = f" • {entry['expires_in_days']}d" if entry.get('expires_in_days') else ""
    return (
        f"`{index}.` **{entry['name']}**\n"
        f"Qty: x{entry['quantity']}{extra}{label_piece}\n"
        f"Cost: {format_money(entry['total_cost'])}"
    )


def add_cart_commands_marker():
    pass


def build_store_category_embeds(category: str):
    category_name = SHOP_CATEGORY_LABELS.get(category, category.title())
    note = STORE_NOTES.get(category)
    embeds = []

    if category == "physicals":
        physical_items = list(PHYSICAL_STOCK.items())

        for index, chunk in enumerate(chunk_list(physical_items, 3), start=1):
            title = "Physical Singles" if index == 1 else "Physical Singles (cont.)"
            description = ""
            if index == 1:
                description = "Use `/buyphysical item_id:<id> tier:<single/100/500/etc> orders:<amount>`"

            embed = make_simple_embed(
                title=title,
                description=description,
                color=discord.Color.orange(),
            )

            for item_id, item in chunk:
                tier_list = ", ".join(item["tiers"].keys())
                value = f"`{item_id}`\n**Tiers:** {tier_list}"
                embed.add_field(name=item["name"], value=value, inline=False)

            embeds.append(embed)

        if note and embeds:
            embeds[-1].set_footer(text=note)

        return embeds

    matching = [(item_id, item) for item_id, item in SHOP_ITEMS.items() if item["category"] == category]
    if not matching:
        return []

    grouped = {}
    for item_id, item in matching:
        subcategory = item["subcategory"]
        platform_group = detect_platform_group(item)

        if platform_group and platform_group.lower() != subcategory.lower():
            display_group = f"{subcategory} — {platform_group}"
        else:
            display_group = subcategory

        grouped.setdefault(display_group, []).append((item_id, item))

    current_embed = make_simple_embed(
        title=f"{category_name} Store",
        description="Use `/buy item_id:<id> quantity:<amount>`",
        color=discord.Color.blurple(),
    )
    fields_in_current = 0

    for subcategory, items in grouped.items():
        item_chunks = list(chunk_list(items, 5))

        for idx, chunk in enumerate(item_chunks, start=1):
            if fields_in_current == 3:
                embeds.append(current_embed)
                current_embed = make_simple_embed(
                    title=f"{category_name} Store (cont.)",
                    description="",
                    color=discord.Color.blurple(),
                )
                fields_in_current = 0

            field_name = subcategory if len(item_chunks) == 1 else f"{subcategory} ({idx})"
            field_value = "\n\n".join(format_store_item_line(item_id, item) for item_id, item in chunk)

            current_embed.add_field(
                name=field_name,
                value=field_value,
                inline=False,
            )
            fields_in_current += 1

    if fields_in_current > 0:
        embeds.append(current_embed)

    if note and embeds:
        embeds[-1].set_footer(text=note)

    return embeds


def setup_store_commands(bot):
    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="ping", description="Check if the bot is online.")
    async def ping(interaction: discord.Interaction):
        await interaction.response.send_message("Pong!")

    @bot.tree.command(name="balance", description="Check your wallet, bank, and pending royalties.")
    @app_commands.describe(member="Optional: check somebody else's balance")
    async def balance(interaction: discord.Interaction, member: Optional[discord.Member] = None):
        target = member or interaction.user
        data = load_data()
        record = get_user_record(data, target.id)
        cleanup_expired_inventory(record)
        pending_total = sum(r["amount"] for r in record.get("pending_royalties", []) if not r.get("claimed"))
        liquid_total = record["wallet"] + record["bank"]
        total_balance = liquid_total + pending_total
        save_data(data)

        desc = (
            f"**Wallet:** {format_money(record['wallet'])}\n"
            f"**Bank:** {format_money(record['bank'])}\n"
            f"**Pending Royalties:** {format_money(pending_total)}\n"
            f"**Available + Pending:** {format_money(total_balance)}"
        )

        embed = make_simple_embed(
            title=f"{target.display_name}'s Balance",
            description=desc,
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="networth", description="View your overall net worth and lifetime earnings.")
    @app_commands.describe(member="Optional: check somebody else's net worth")
    async def networth(interaction: discord.Interaction, member: Optional[discord.Member] = None):
        target = member or interaction.user
        data = load_data()
        record = get_user_record(data, target.id)
        cleanup_expired_inventory(record)

        pending_total = sum(r["amount"] for r in record.get("pending_royalties", []) if not r.get("claimed"))
        liquid_total = record.get("wallet", 0) + record.get("bank", 0)
        net_worth_total = liquid_total + pending_total
        lifetime_earnings = record.get("total_earnings", 0)
        claimed_total = record.get("claimed_royalties_total", 0)
        earnings_by_source = record.get("earnings_by_source", {})
        top_sources = sorted(earnings_by_source.items(), key=lambda item: item[1], reverse=True)[:5]
        save_data(data)

        embed = make_simple_embed(
            title=f"{target.display_name}'s Net Worth",
            description=(
                f"**Net Worth:** {format_money(net_worth_total)}\n"
                f"**Liquid Funds:** {format_money(liquid_total)}\n"
                f"**Pending Royalties:** {format_money(pending_total)}\n"
                f"**Lifetime Earnings:** {format_money(lifetime_earnings)}\n"
                f"**Claimed Royalties:** {format_money(claimed_total)}"
            ),
            color=discord.Color.gold(),
        )

        if top_sources:
            embed.add_field(
                name="Top Earnings Sources",
                value="\n".join(f"**{source}** • {format_money(amount)}" for source, amount in top_sources),
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="addmoney", description="Admin: add money to a user.")
    @app_commands.checks.has_permissions(administrator=True)
    async def addmoney(interaction: discord.Interaction, member: discord.Member, amount: int):
        if amount <= 0:
            await interaction.response.send_message("Amount has to be above 0.", ephemeral=True)
            return

        data = load_data()
        record = get_user_record(data, member.id)
        record["wallet"] += amount
        save_data(data)

        embed = make_simple_embed(
            title="Money Added",
            description=(
                f"**User:** {member.mention}\n"
                f"**Added:** {format_money(amount)}\n"
                f"**New Wallet:** {format_money(record['wallet'])}"
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="removemoney", description="Admin: remove money from a user.")
    @app_commands.checks.has_permissions(administrator=True)
    async def removemoney(interaction: discord.Interaction, member: discord.Member, amount: int):
        if amount <= 0:
            await interaction.response.send_message("Amount has to be above 0.", ephemeral=True)
            return

        data = load_data()
        record = get_user_record(data, member.id)
        record["wallet"] = max(0, record["wallet"] - amount)
        save_data(data)

        embed = make_simple_embed(
            title="Money Removed",
            description=(
                f"**User:** {member.mention}\n"
                f"**Removed:** {format_money(amount)}\n"
                f"**New Wallet:** {format_money(record['wallet'])}"
            ),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="deposit", description="Move money from your wallet into your bank.")
    async def deposit(interaction: discord.Interaction, amount: int):
        if amount <= 0:
            await interaction.response.send_message("Amount has to be above 0.", ephemeral=True)
            return

        data = load_data()
        record = get_user_record(data, interaction.user.id)

        if record["wallet"] < amount:
            await interaction.response.send_message("You do not have enough in your wallet.", ephemeral=True)
            return

        record["wallet"] -= amount
        record["bank"] += amount
        save_data(data)

        embed = make_simple_embed(
            title="Deposit Complete",
            description=(
                f"**Deposited:** {format_money(amount)}\n"
                f"**Wallet:** {format_money(record['wallet'])}\n"
                f"**Bank:** {format_money(record['bank'])}"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="withdraw", description="Move money from your bank into your wallet.")
    async def withdraw(interaction: discord.Interaction, amount: int):
        if amount <= 0:
            await interaction.response.send_message("Amount has to be above 0.", ephemeral=True)
            return

        data = load_data()
        record = get_user_record(data, interaction.user.id)

        if record["bank"] < amount:
            await interaction.response.send_message("You do not have enough in your bank.", ephemeral=True)
            return

        record["bank"] -= amount
        record["wallet"] += amount
        save_data(data)

        embed = make_simple_embed(
            title="Withdrawal Complete",
            description=(
                f"**Withdrew:** {format_money(amount)}\n"
                f"**Wallet:** {format_money(record['wallet'])}\n"
                f"**Bank:** {format_money(record['bank'])}"
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="pay", description="Pay another user from your wallet.")
    async def pay(interaction: discord.Interaction, member: discord.Member, amount: int):
        if member.id == interaction.user.id:
            await interaction.response.send_message("You cannot pay yourself.", ephemeral=True)
            return

        if amount <= 0:
            await interaction.response.send_message("Amount has to be above 0.", ephemeral=True)
            return

        data = load_data()
        sender = get_user_record(data, interaction.user.id)
        receiver = get_user_record(data, member.id)

        if sender["wallet"] < amount:
            await interaction.response.send_message("You do not have enough in your wallet.", ephemeral=True)
            return

        sender["wallet"] -= amount
        receiver["wallet"] += amount
        save_data(data)

        embed = make_simple_embed(
            title="Payment Sent",
            description=(
                f"**From:** {interaction.user.mention}\n"
                f"**To:** {member.mention}\n"
                f"**Amount:** {format_money(amount)}"
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="setpurchaselog", description="Set your personal purchase log channel.")
    async def setpurchaselog(interaction: discord.Interaction, channel: discord.TextChannel):
        data = load_data()
        record = get_user_record(data, interaction.user.id)
        record["log_channel_id"] = channel.id
        save_data(data)

        embed = make_simple_embed(
            title="Purchase Log Channel Set",
            description=f"Your purchases will now be logged in {channel.mention}.",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="viewpurchaselog", description="See your current purchase log channel.")
    async def viewpurchaselog(interaction: discord.Interaction):
        data = load_data()
        record = get_user_record(data, interaction.user.id)
        channel_id = record.get("log_channel_id")

        if not channel_id:
            await interaction.response.send_message("You have no purchase log channel set.", ephemeral=True)
            return

        embed = make_simple_embed(
            title="Your Purchase Log Channel",
            description=f"Current channel: <#{channel_id}>",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="clearpurchaselog", description="Clear your personal purchase log channel.")
    async def clearpurchaselog(interaction: discord.Interaction):
        data = load_data()
        record = get_user_record(data, interaction.user.id)
        record["log_channel_id"] = None
        save_data(data)

        embed = make_simple_embed(
            title="Purchase Log Cleared",
            description="Your purchase log channel has been removed.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="purchasehistory", description="View recent purchase history.")
    @app_commands.describe(member="Optional: check somebody else's purchase history")
    async def purchasehistory(interaction: discord.Interaction, member: Optional[discord.Member] = None):
        target = member or interaction.user
        data = load_data()
        record = get_user_record(data, target.id)
        history = record.get("purchase_history", [])

        if not history:
            await interaction.response.send_message(f"{target.display_name} has no purchase history yet.")
            return

        recent = list(reversed(history[-15:]))

        lines = []
        for entry in recent:
            stamp = from_iso(entry["purchased_at"]).strftime("%m/%d")
            line = f"`{stamp}` **{entry['name']}** — {format_money(entry['total_cost'])}"
            if entry.get("tier"):
                line += f" • {entry['tier']}"
            elif entry.get("quantity", 1) > 1:
                line += f" • x{entry['quantity']}"
            if entry.get("charged_to_type") == "label" and entry.get("charged_to_name"):
                line += f" • {entry['charged_to_name']}"
            lines.append(line)

        embed = make_simple_embed(
            title=f"{target.display_name}'s Purchase History",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Showing {len(recent)} most recent purchase(s)")
        await interaction.response.send_message(embed=embed)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="store", description="View the store or a specific category.")
    @app_commands.describe(category="Example: promotion, tours, airplay, playlisting")
    async def store(interaction: discord.Interaction, category: Optional[str] = None):
        if not category:
            categories = []
            for cat_key, cat_name in SHOP_CATEGORY_LABELS.items():
                count = len([x for x in SHOP_ITEMS.values() if x["category"] == cat_key])
                if cat_key == "physicals":
                    count = len(PHYSICAL_STOCK)
                categories.append((cat_key, cat_name, count))

            embed = make_simple_embed(
                title="Encore ENT Store",
                description="Pick a category with `/store category:<name>`",
                color=discord.Color.blurple(),
            )

            left_side = []
            right_side = []

            for index, (cat_key, cat_name, count) in enumerate(categories):
                line = f"`{cat_key}` — {cat_name} ({count})"
                if index % 2 == 0:
                    left_side.append(line)
                else:
                    right_side.append(line)

            embed.add_field(
                name="Categories",
                value="\n".join(left_side) if left_side else "—",
                inline=True,
            )
            embed.add_field(
                name="More",
                value="\n".join(right_side) if right_side else "—",
                inline=True,
            )
            embed.add_field(
                name="Buy Commands",
                value=(
                    "`/buy item_id:<id> quantity:<amount>`\n"
                    "`/buyphysical item_id:<id> tier:<single/100/500/etc> orders:<amount>`"
                ),
                inline=False,
            )

            await interaction.response.send_message(embed=embed)
            return

        category = category.lower().strip()
        embeds = build_store_category_embeds(category)

        if not embeds:
            await interaction.response.send_message("That category does not exist.", ephemeral=True)
            return

        await interaction.response.send_message(embeds=embeds[:10])

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="buy", description="Buy a normal store item.")
    async def buy(interaction: discord.Interaction, item_id: str, quantity: Optional[int] = 1):
        item_id = item_id.lower().strip()

        if item_id not in SHOP_ITEMS:
            await interaction.response.send_message("That item_id does not exist.", ephemeral=True)
            return

        if quantity is None or quantity <= 0:
            await interaction.response.send_message("Quantity has to be at least 1.", ephemeral=True)
            return

        item = SHOP_ITEMS[item_id]

        if not item["stackable"] and quantity > 1:
            await interaction.response.send_message("That item is not stackable. Buy it one at a time.", ephemeral=True)
            return

        total_cost = item["price"] * quantity

        data = load_data()
        record = get_user_record(data, interaction.user.id)
        cleanup_expired_inventory(record)

        if record["wallet"] < total_cost:
            await interaction.response.send_message(
                f"You need {format_money(total_cost)} but only have {format_money(record['wallet'])} in your wallet.",
                ephemeral=True,
            )
            return

        record["wallet"] -= total_cost

        add_inventory_entry(
            record=record,
            item_id=item_id,
            name=item["name"],
            category=item["category"],
            quantity=quantity,
            expires_in_days=item["expires_in_days"],
        )

        history_entry = add_purchase_history_entry(
            record,
            item_id=item_id,
            name=item["name"],
            category=item["category"],
            quantity=quantity,
            total_cost=total_cost,
        )

        save_data(data)

        desc = (
            f"**Item:** {item['name']}\n"
            f"**Quantity:** x{quantity}\n"
            f"**Cost:** {format_money(total_cost)}\n"
            f"**Wallet Left:** {format_money(record['wallet'])}"
        )
        if item["expires_in_days"]:
            desc += f"\n**Expires In:** {item['expires_in_days']} day(s)"

        embed = make_simple_embed(
            title="Purchase Complete",
            description=desc,
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

        await send_purchase_log(bot, interaction.user, record, history_entry)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="buyphysical", description="Buy physical stock by tier.")
    async def buyphysical(interaction: discord.Interaction, item_id: str, tier: str, orders: Optional[int] = 1):
        item_id = item_id.lower().strip()
        tier = tier.lower().strip()

        if item_id not in PHYSICAL_STOCK:
            await interaction.response.send_message("That physical item_id does not exist.", ephemeral=True)
            return

        if orders is None or orders <= 0:
            await interaction.response.send_message("Orders must be at least 1.", ephemeral=True)
            return

        item = PHYSICAL_STOCK[item_id]

        if tier not in item["tiers"]:
            await interaction.response.send_message(
                f"That tier does not exist. Valid tiers: {', '.join(item['tiers'].keys())}",
                ephemeral=True,
            )
            return

        tier_price = item["tiers"][tier]
        total_cost = tier_price * orders

        if tier == "single":
            units_bought = 1 * orders
        else:
            units_bought = int(tier) * orders

        data = load_data()
        record = get_user_record(data, interaction.user.id)

        if record["wallet"] < total_cost:
            await interaction.response.send_message(
                f"You need {format_money(total_cost)} but only have {format_money(record['wallet'])} in your wallet.",
                ephemeral=True,
            )
            return

        record["wallet"] -= total_cost

        add_inventory_entry(
            record=record,
            item_id=f"{item_id}:{tier}",
            name=f"{item['name']} ({tier})",
            category="physicals",
            quantity=units_bought,
            expires_in_days=None,
        )

        history_entry = add_purchase_history_entry(
            record,
            item_id=item_id,
            name=item["name"],
            category="physicals",
            quantity=orders,
            total_cost=total_cost,
            tier=tier,
            units_added=units_bought,
        )

        save_data(data)

        embed = make_simple_embed(
            title="Physical Purchase Complete",
            description=(
                f"**Item:** {item['name']}\n"
                f"**Tier:** {tier}\n"
                f"**Orders:** x{orders}\n"
                f"**Units Added:** {units_bought:,}\n"
                f"**Cost:** {format_money(total_cost)}\n"
                f"**Wallet Left:** {format_money(record['wallet'])}"
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

        await send_purchase_log(bot, interaction.user, record, history_entry)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="addtocart", description="Add a store item or physical tier to your cart.")
    async def addtocart(
        interaction: discord.Interaction,
        item_id: str,
        quantity: Optional[int] = 1,
        tier: Optional[str] = None,
        orders: Optional[int] = 1,
    ):
        entry, error = calculate_cart_entry(item_id=item_id, quantity=quantity, tier=tier, orders=orders)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        data = load_data()
        record = get_user_record(data, interaction.user.id)
        record["cart"].append(entry)
        save_data(data)

        embed = make_simple_embed(
            title="Added to Cart",
            description=(
                f"**Item:** {entry['name']}\n"
                f"**Type:** {'Physical' if entry['entry_type'] == 'physical' else 'Standard'}\n"
                f"**Cost:** {format_money(entry['total_cost'])}\n"
                f"**Cart Items:** {len(record['cart'])}"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="viewcart", description="View everything currently in your cart.")
    async def viewcart(interaction: discord.Interaction):
        data = load_data()
        record = get_user_record(data, interaction.user.id)
        cart = record.get("cart", [])

        if not cart:
            await interaction.response.send_message("Your cart is empty.", ephemeral=True)
            return

        total = sum(entry["total_cost"] for entry in cart)
        lines = [format_cart_line(i, entry) for i, entry in enumerate(cart, start=1)]
        embed = make_simple_embed(
            title="Your Cart",
            description="\n\n".join(lines[:15]),
            color=discord.Color.gold(),
        )
        embed.add_field(name="Cart Total", value=format_money(total), inline=False)
        if len(lines) > 15:
            embed.set_footer(text=f"Showing 15 of {len(lines)} cart item(s)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="removefromcart", description="Remove one item from your cart by number.")
    async def removefromcart(interaction: discord.Interaction, item_number: int):
        data = load_data()
        record = get_user_record(data, interaction.user.id)
        cart = record.get("cart", [])

        if item_number <= 0 or item_number > len(cart):
            await interaction.response.send_message("That cart item number is invalid.", ephemeral=True)
            return

        removed = cart.pop(item_number - 1)
        save_data(data)
        await interaction.response.send_message(
            f"Removed **{removed['name']}** from your cart.",
            ephemeral=True,
        )

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="clearcart", description="Clear your whole shopping cart.")
    async def clearcart(interaction: discord.Interaction):
        data = load_data()
        record = get_user_record(data, interaction.user.id)
        removed = len(record.get("cart", []))
        record["cart"] = []
        save_data(data)
        await interaction.response.send_message(f"Cleared {removed} item(s) from your cart.", ephemeral=True)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="checkout", description="Purchase everything currently in your cart.")
    async def checkout(interaction: discord.Interaction):
        data = load_data()
        record = get_user_record(data, interaction.user.id)
        cleanup_expired_inventory(record)
        cart = record.get("cart", [])

        if not cart:
            await interaction.response.send_message("Your cart is empty.", ephemeral=True)
            return

        total_cost = sum(entry["total_cost"] for entry in cart)
        if record["wallet"] < total_cost:
            await interaction.response.send_message(
                f"You need {format_money(total_cost)} but only have {format_money(record['wallet'])} in your wallet.",
                ephemeral=True,
            )
            return

        record["wallet"] -= total_cost
        history_entries = []
        purchased_count = 0
        for entry in cart:
            if entry["entry_type"] == "physical":
                add_inventory_entry(
                    record=record,
                    item_id=f"{entry['item_id']}:{entry['tier']}",
                    name=f"{entry['name']} ({entry['tier']})",
                    category="physicals",
                    quantity=entry["units_added"],
                    expires_in_days=None,
                )
                history_entries.append(add_purchase_history_entry(
                    record,
                    item_id=entry["item_id"],
                    name=entry["name"],
                    category="physicals",
                    quantity=entry["orders"],
                    total_cost=entry["total_cost"],
                    tier=entry["tier"],
                    units_added=entry["units_added"],
                ))
            else:
                add_inventory_entry(
                    record=record,
                    item_id=entry["item_id"],
                    name=entry["name"],
                    category=entry["category"],
                    quantity=entry["quantity"],
                    expires_in_days=entry.get("expires_in_days"),
                )
                history_entries.append(add_purchase_history_entry(
                    record,
                    item_id=entry["item_id"],
                    name=entry["name"],
                    category=entry["category"],
                    quantity=entry["quantity"],
                    total_cost=entry["total_cost"],
                ))
            purchased_count += 1

        record["cart"] = []
        save_data(data)

        embed = make_simple_embed(
            title="Checkout Complete",
            description=(
                f"**Purchased Items:** {purchased_count}\n"
                f"**Total Cost:** {format_money(total_cost)}\n"
                f"**Wallet Left:** {format_money(record['wallet'])}"
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

        for history_entry in history_entries:
            await send_purchase_log(bot, interaction.user, record, history_entry)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="createlabel", description="Owner only: create a label with starting funds.")
    async def createlabel(interaction: discord.Interaction, label_name: str, starting_funds: Optional[int] = 0, owner: Optional[discord.Member] = None):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("Only the server owner can use this.", ephemeral=True)
            return
        formatted = format_label_name(label_name)
        if not formatted:
            await interaction.response.send_message("Label name cannot be empty.", ephemeral=True)
            return
        data = load_data()
        key, existing = get_label_record(data, formatted)
        if existing:
            await interaction.response.send_message("That label already exists.", ephemeral=True)
            return
        primary_owner = owner.id if owner else interaction.user.id
        data.setdefault("labels", {})[key] = {"name": formatted, "funds": max(0, int(starting_funds or 0)), "owner_ids": [primary_owner], "log_channel_id": None}
        save_data(data)
        await interaction.response.send_message(embed=make_simple_embed("Label Created", f"**Label:** {formatted}\n**Starting Funds:** {format_money(max(0, int(starting_funds or 0)))}\n**Owners:** <@{primary_owner}>", discord.Color.blurple()), ephemeral=True)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="addlabelowner", description="Owner only: add another CEO/owner to a label.")
    async def addlabelowner(interaction: discord.Interaction, label_name: str, member: discord.Member):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("Only the server owner can use this.", ephemeral=True)
            return
        data = load_data()
        _, label = get_label_record(data, label_name)
        if not label:
            await interaction.response.send_message("That label does not exist.", ephemeral=True)
            return
        owner_ids = {int(x) for x in label.get("owner_ids", [])}
        owner_ids.add(member.id)
        label["owner_ids"] = sorted(owner_ids)
        save_data(data)
        await interaction.response.send_message(f"Added {member.mention} as an owner of **{label['name']}**.", ephemeral=True)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="removelabelowner", description="Owner only: remove an owner from a label.")
    async def removelabelowner(interaction: discord.Interaction, label_name: str, member: discord.Member):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("Only the server owner can use this.", ephemeral=True)
            return
        data = load_data()
        _, label = get_label_record(data, label_name)
        if not label:
            await interaction.response.send_message("That label does not exist.", ephemeral=True)
            return
        owner_ids = [int(x) for x in label.get("owner_ids", []) if int(x) != member.id]
        if not owner_ids:
            await interaction.response.send_message("A label needs at least one owner.", ephemeral=True)
            return
        label["owner_ids"] = owner_ids
        save_data(data)
        await interaction.response.send_message(f"Removed {member.mention} from **{label['name']}** owners.", ephemeral=True)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="setlabelfunds", description="Owner only: set a label's funds.")
    async def setlabelfunds(interaction: discord.Interaction, label_name: str, amount: int):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("Only the server owner can change label funds.", ephemeral=True)
            return
        data = load_data()
        _, label = get_label_record(data, label_name)
        if not label:
            await interaction.response.send_message("That label does not exist.", ephemeral=True)
            return
        label["funds"] = max(0, amount)
        save_data(data)
        await interaction.response.send_message(embed=make_simple_embed("Label Funds Updated", f"**Label:** {label['name']}\n**Funds:** {format_money(label['funds'])}", discord.Color.green()), ephemeral=True)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="labelbalance", description="View a label's available funds and owners.")
    async def labelbalance(interaction: discord.Interaction, label_name: str):
        data = load_data()
        _, label = get_label_record(data, label_name)
        if not label:
            await interaction.response.send_message("That label does not exist.", ephemeral=True)
            return
        owners = " ".join(f"<@{owner_id}>" for owner_id in label.get("owner_ids", [])) or "None"
        log_text = f"<#{label['log_channel_id']}>" if label.get("log_channel_id") else "Not set"
        desc = f"**Funds:** {format_money(label['funds'])}\n**Owners:** {owners}\n**Log Channel:** {log_text}"
        await interaction.response.send_message(embed=make_simple_embed(f"{label['name']} Label", desc, discord.Color.gold()), ephemeral=True)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="setlabellog", description="Set a label purchase log channel.")
    async def setlabellog(interaction: discord.Interaction, label_name: str, channel: discord.TextChannel):
        data = load_data()
        _, label = get_label_record(data, label_name)
        if not label:
            await interaction.response.send_message("That label does not exist.", ephemeral=True)
            return
        if not interaction.guild or (interaction.user.id != interaction.guild.owner_id and not user_can_manage_label(label, interaction.user.id)):
            await interaction.response.send_message("Only the server owner or one of that label's owners can use this.", ephemeral=True)
            return
        label["log_channel_id"] = channel.id
        save_data(data)
        await interaction.response.send_message(f"Purchases for **{label['name']}** will now log in {channel.mention}.", ephemeral=True)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="assignartistlabel", description="Assign a registered artist to a label.")
    async def assignartistlabel(interaction: discord.Interaction, artist_name: str, label_name: str):
        if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("Only the server owner can use this.", ephemeral=True)
            return
        artist = find_artist(artist_name)
        if not artist:
            await interaction.response.send_message("That artist is not registered.", ephemeral=True)
            return
        data = load_data()
        _, label = get_label_record(data, label_name)
        if not label:
            await interaction.response.send_message("That label does not exist.", ephemeral=True)
            return
        set_artist_label_name(data, artist['stage_name'], label['name'])
        save_data(data)
        await interaction.response.send_message(f"Assigned **{artist['stage_name']}** to **{label['name']}**.", ephemeral=True)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="artistlabel", description="View what label an artist is assigned to.")
    async def artistlabel(interaction: discord.Interaction, artist_name: str):
        artist = find_artist(artist_name)
        if not artist:
            await interaction.response.send_message("That artist is not registered.", ephemeral=True)
            return
        data = load_data()
        label_name = get_artist_label_name(data, artist['stage_name'])
        text = label_name or "No label assigned"
        await interaction.response.send_message(embed=make_simple_embed("Artist Label", f"**Artist:** {artist['stage_name']}\n**Label:** {text}", discord.Color.blurple()), ephemeral=True)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="inventory", description="View your active inventory.")
    @app_commands.describe(member="Optional: check somebody else's inventory")
    async def inventory(interaction: discord.Interaction, member: Optional[discord.Member] = None):
        target = member or interaction.user
        data = load_data()
        record = get_user_record(data, target.id)
        cleanup_expired_inventory(record)
        save_data(data)

        if not record["inventory"]:
            await interaction.response.send_message(f"{target.display_name} has no active inventory.")
            return

        lines = []
        for item in record["inventory"][-20:]:
            line = f"**{item['name']}** • x{item['quantity']}"
            if item.get("expires_at"):
                expires = from_iso(item["expires_at"])
                days_left = max((expires - utc_now()).days, 0)
                line += f" • {days_left} day(s) left"
            lines.append(line)

        embed = make_simple_embed(
            title=f"{target.display_name}'s Inventory",
            description="\n".join(reversed(lines)),
            color=discord.Color.gold(),
        )

        total_items = len(record["inventory"])
        if total_items > 20:
            embed.set_footer(text=f"Showing 20 of {total_items} item(s)")

        await interaction.response.send_message(embed=embed)

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="payout", description="Owner/admin: send a payout to a registered artist.")
    @app_commands.checks.has_permissions(administrator=True)
    async def payout(interaction: discord.Interaction, artist_name: str, amount: int, source: Optional[str] = "Royalties"):
        if amount <= 0:
            await interaction.response.send_message("Amount has to be above 0.", ephemeral=True)
            return

        artist = find_artist(artist_name)
        if not artist:
            await interaction.response.send_message("That artist is not registered.", ephemeral=True)
            return

        data = load_data()
        record = get_user_record(data, int(artist["owner_user_id"]))
        payout_source = f"{source} • {artist['stage_name']}"
        add_pending_earning(record, payout_source, amount)
        save_data(data)

        label_name = get_artist_label_name(data, artist["stage_name"])
        payout_desc = (
            f"**Artist:** {artist['stage_name']}\n"
            f"**Owner:** <@{artist['owner_user_id']}>\n"
            f"**Source:** {source}\n"
            f"**Amount:** {format_money(amount)}"
        )
        if label_name:
            payout_desc += f"\n**Label:** {label_name}"

        embed = make_simple_embed(
            title="Payout Submitted",
            description=payout_desc,
            color=discord.Color.purple(),
        )
        await interaction.response.send_message(embed=embed)

        await notify_artist_owner(
            interaction.client,
            artist,
            "Royalties Released",
            f"You received a payout for **{artist['stage_name']}**.\nSource: **{source}**\nAmount: **{format_money(amount)}**",
        )

    @app_commands.guilds(STAFF_GUILD)
    @bot.tree.command(name="submitroyalties", description="Admin: submit pending royalties to a user.")
    @app_commands.checks.has_permissions(administrator=True)
    async def submitroyalties(interaction: discord.Interaction, member: discord.Member, source: str, amount: int):
        if amount <= 0:
            await interaction.response.send_message("Amount has to be above 0.", ephemeral=True)
            return

        data = load_data()
        record = get_user_record(data, member.id)

        add_pending_earning(record, source, amount)
        save_data(data)

        embed = make_simple_embed(
            title="Royalties Submitted",
            description=(
                f"**User:** {member.mention}\n"
                f"**Source:** {source}\n"
                f"**Amount:** {format_money(amount)}"
            ),
            color=discord.Color.purple(),
        )
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="royalties", description="Check your pending royalties.")
    @app_commands.describe(member="Optional: check somebody else's pending royalties")
    async def royalties(interaction: discord.Interaction, member: Optional[discord.Member] = None):
        target = member or interaction.user
        data = load_data()
        record = get_user_record(data, target.id)

        pending = [r for r in record["pending_royalties"] if not r["claimed"]]
        total = sum(r["amount"] for r in pending)

        if not pending:
            await interaction.response.send_message(f"{target.display_name} has no pending royalties.")
            return

        lines = [f"**{r['source']}** • {format_money(r['amount'])}" for r in pending[:20]]

        embed = make_simple_embed(
            title=f"{target.display_name}'s Pending Royalties",
            description="\n".join(lines),
            color=discord.Color.purple(),
        )
        embed.add_field(name="Total Pending", value=format_money(total), inline=False)
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="claimroyalties", description="Claim all your pending royalties into your wallet.")
    async def claimroyalties(interaction: discord.Interaction):
        data = load_data()
        record = get_user_record(data, interaction.user.id)

        pending = [r for r in record["pending_royalties"] if not r["claimed"]]
        total = sum(r["amount"] for r in pending)

        if not pending:
            await interaction.response.send_message("You have no pending royalties to claim.", ephemeral=True)
            return

        for royalty in pending:
            royalty["claimed"] = True

        record["wallet"] += total
        record["claimed_royalties_total"] += total
        save_data(data)

        lines = [f"**{r['source']}** • {format_money(r['amount'])}" for r in pending[:15]]

        embed = make_simple_embed(
            title="Royalties Claimed",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        embed.add_field(name="Total Claimed", value=format_money(total), inline=True)
        embed.add_field(name="New Wallet", value=format_money(record["wallet"]), inline=True)
        await interaction.response.send_message(embed=embed)

    @addmoney.error
    @removemoney.error
    @submitroyalties.error
    async def admin_command_error(interaction: discord.Interaction, error):
        if isinstance(error, app_commands.errors.MissingPermissions):
            if interaction.response.is_done():
                await interaction.followup.send("You need administrator permission to use that command.", ephemeral=True)
            else:
                await interaction.response.send_message("You need administrator permission to use that command.", ephemeral=True)
        else:
            if interaction.response.is_done():
                await interaction.followup.send(f"Error: {error}", ephemeral=True)
            else:
                await interaction.response.send_message(f"Error: {error}", ephemeral=True)


async def setup_store_system(bot):
    setup_store_commands(bot)