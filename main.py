import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

import social_system
import post_system

try:
    import store_system
except ImportError:
    store_system = None

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def try_setup(module, module_name):
    if module is None:
        print(f"{module_name} not found, skipping")
        return

    try:
        if hasattr(module, "setup_social_system"):
            await module.setup_social_system(bot)
        elif hasattr(module, "setup_post_system"):
            await module.setup_post_system(bot)
        elif hasattr(module, "setup_store_system"):
            await module.setup_store_system(bot)
        elif hasattr(module, "setup"):
            result = module.setup(bot)
            if hasattr(result, "__await__"):
                await result
        else:
            print(f"No setup function found in {module_name}")
            return

        print(f"{module_name} loaded")
    except Exception as e:
        print(f"Failed to load {module_name}: {e}")


@bot.event
async def setup_hook():
    await try_setup(social_system, "social_system")
    await try_setup(post_system, "post_system")
    await try_setup(store_system, "store_system")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} guild commands to {GUILD_ID}")
        else:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} global commands")
    except Exception as e:
        print(f"Sync failed: {e}")


if not TOKEN:
    raise ValueError("No DISCORD_TOKEN or TOKEN found in Secrets.")

bot.run(TOKEN)