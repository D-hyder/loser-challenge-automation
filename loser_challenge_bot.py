# loser_challenge_bot.py
import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz
from config import TIMEZONE
from database import init_db
from scheduler import post_weekly_message, evaluate_week, reset_week, backup_now

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tz = pytz.timezone(TIMEZONE)
scheduler = AsyncIOScheduler(timezone=tz)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    init_db()

    # load extensions (async because cogs expose `async def setup(...)`)
    await bot.load_extension("cogs.admin")
    await bot.load_extension("cogs.goals")
    await bot.load_extension("cogs.summary")

    # register slash commands
    await bot.tree.sync()
    print("🌐 Slash commands synced")

    # schedules
    scheduler.add_job(post_weekly_message, "cron", day_of_week="mon", hour=9,  minute=0, args=[bot])
    scheduler.add_job(backup_now,         "cron", day_of_week="sun", hour=23, minute=50, args=[bot])
    scheduler.add_job(evaluate_week,      "cron", day_of_week="sun", hour=23, minute=59, args=[bot])
    scheduler.add_job(reset_week,         "cron", day_of_week="mon", hour=0,  minute=1,  args=[bot])
    scheduler.start()
