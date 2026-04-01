# loser_challenge_bot.py
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

from config import TIMEZONE
from database import init_db, deactivate_guild
from scheduler import post_weekly_message, evaluate_week, reset_week, backup_now

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot       = commands.Bot(command_prefix="!", intents=intents)
tz        = pytz.timezone(TIMEZONE)
scheduler = AsyncIOScheduler(timezone=tz)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (guilds={len(bot.guilds)})")
    init_db()

    await bot.load_extension("cogs.setup")
    await bot.load_extension("cogs.admin")
    await bot.load_extension("cogs.goals")
    await bot.load_extension("cogs.summary")

    await bot.tree.sync()
    print("🌐 Slash commands synced")

    scheduler.add_job(post_weekly_message, "cron", day_of_week="mon", hour=9,  minute=0,  args=[bot])
    scheduler.add_job(backup_now,          "cron", day_of_week="sun", hour=23, minute=50, args=[bot])
    scheduler.add_job(evaluate_week,       "cron", day_of_week="sun", hour=23, minute=59, args=[bot])
    scheduler.add_job(reset_week,          "cron", day_of_week="mon", hour=0,  minute=1,  args=[bot])
    scheduler.start()


@bot.event
async def on_guild_join(guild: discord.Guild):
    print(f"✅ Joined new guild: {guild.name} (id={guild.id})")
    sys_ch = guild.system_channel
    if sys_ch:
        try:
            await sys_ch.send(
                "👋 Thanks for adding the Loser Challenge bot!\n\n"
                "An admin must run `/setup` to configure:\n"
                "• The challenge channel (where weekly posts appear)\n"
                "• The loser role (assigned when the team fails)\n"
                "• The timezone\n\n"
                "Then run `/wordle_setup` to enable the Wordle tracker.\n"
                "Run `/guide` to see a quick start for members."
            )
        except discord.Forbidden:
            pass


@bot.event
async def on_guild_remove(guild: discord.Guild):
    print(f"⚠️ Removed from guild: {guild.name} (id={guild.id})")
    deactivate_guild(guild.id)
