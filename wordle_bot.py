import discord
from discord.ext import commands
from discord.ext import tasks
import re
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, date
import pytz
import logging
from config import WORDLE_DATA_PATH

logging.basicConfig(level=logging.INFO)

# === File Paths ===
DATA_FILE = Path(WORDLE_DATA_PATH)
INIT_FILE = Path("scores.json")

if not DATA_FILE.exists() and INIT_FILE.exists():
    with open(INIT_FILE, "r") as f:
        data = json.load(f)
        if "players" in data:
            del data["players"]
        DATA_FILE.write_text(json.dumps(data, separators=(',', ':')))

# === Constants ===
CENTRAL_TZ = pytz.timezone("America/Chicago")
WORDLE_START_DATE = datetime(2021, 6, 19)

# === Bot Setup ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# === Load/Save Functions ===
def load_scores():
    if not DATA_FILE.exists():
        DATA_FILE.write_text("{}")
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_scores(scores):
    with open(DATA_FILE, "w") as f:
        json.dump(scores, f, separators=(',', ':'))

# === Wordle Helper ===
# Helper: check if key is a real user record (ignore internal metadata)
def _is_user_record(k, v):
    return isinstance(v, dict) and not str(k).startswith("_") and ("total" in v and "games" in v)

_DEF_META = {
    "last_podium": {"gold": [], "silver": [], "bronze": [], "waffle": []},
    "skip_penalty_days": [],   # list of ISO dates (YYYY-MM-DD) to not penalize
    "last_penalized_day": ""   # ISO date we last processed (idempotence)
}


def ensure_meta(scores: dict):
    if not isinstance(scores, dict):
        return {"_meta": dict(_DEF_META)}
    meta = scores.get("_meta")
    if not isinstance(meta, dict):
        scores["_meta"] = dict(_DEF_META)
    else:
        for k, v in _DEF_META.items():
            scores["_meta"].setdefault(k, v)
    return scores

# Use universal Wordle epoch to avoid timezone/anchor drift
WORDLE_EPOCH = date(2021, 6, 19)  # Wordle #0 release date (universal)

def wordle_to_date(wordle_num: int) -> date:
    return WORDLE_EPOCH + timedelta(days=int(wordle_num))

def date_to_wordle(some_date: date) -> int:
    return (some_date - WORDLE_EPOCH).days

async def build_leaderboard_text():
    scores = load_scores()
    ensure_meta(scores)
    if not scores:
        return "No scores yet."

    podium = scores["_meta"].get("last_podium", {"gold": [], "silver": [], "bronze": [], "waffle": []})

    entries = [(uid, data) for uid, data in scores.items()
               if isinstance(data, dict) and not str(uid).startswith("_")
               and "total" in data and "games" in data]
    entries.sort(key=lambda x: x[1]["total"])

    def medal_for(uid: str) -> str:
        if uid in podium.get("gold", []):   return "👑 "
        if uid in podium.get("silver", []): return "🥈 "
        if uid in podium.get("bronze", []): return "🥉 "
        if uid in podium.get("waffle", []): return "🧇 "
        return ""

    lines = []
    for uid, data in entries:
        user = await bot.fetch_user(int(uid))
        gp = len(data["games"])
        lines.append(f"{medal_for(uid)}**{user.display_name}** — {data['total']} tries over {gp} games")

    return "__**🏆 Wordle Leaderboard**__\n" + "\n".join(lines)

# === Scheduler ===
@tasks.loop(hours=1)
async def daily_penalty_check():
    now = datetime.now(CENTRAL_TZ)

    # We run during the midnight hour CST, and we only want to apply once per day
    if now.hour != 0:
        return

    scores = load_scores()
    ensure_meta(scores)

    target_day = (now.date() - timedelta(days=1))           # penalize yesterday's Wordle
    stamp = target_day.isoformat()

    # Already processed this day? bail
    if scores["_meta"].get("last_penalized_day") == stamp:
        return

    # Admin asked to skip this day? mark as processed and bail
    if stamp in scores["_meta"].get("skip_penalty_days", []):
        scores["_meta"]["last_penalized_day"] = stamp
        scores["_meta"]["skip_penalty_days"] = [
            d for d in scores["_meta"]["skip_penalty_days"] if d != stamp
        ]
        save_scores(scores)
        return

    wordle_num = str(date_to_wordle(target_day))

    # Penalize only joined players who didn't submit
    joined_users = {
        uid for uid, data in scores.items()
        if isinstance(data, dict) and data.get("joined")
    }
    penalized = []
    for uid in joined_users:
        if wordle_num not in scores[uid]["games"]:
            scores[uid]["games"][wordle_num] = 7
            scores[uid]["total"] += 7
            penalized.append(uid)

    # Mark processed and save
    scores["_meta"]["last_penalized_day"] = stamp
    save_scores(scores)

    if penalized:
        channel = discord.utils.get(bot.get_all_channels(), name="general")
        if channel:
            mentions = ", ".join(f"<@{uid}>" for uid in penalized)
            await channel.send(f"⏰ Auto-penalty: {mentions} were given 7 tries for missing Wordle #{wordle_num}.")


MISSING_CHANNEL_ID = "900458273117982791"  # optional channel ID

@tasks.loop(hours=1)
async def nightly_missing_alert():
    now = datetime.now(CENTRAL_TZ)
    if now.hour != 20:  # 8 PM Central
        return

    scores = load_scores()
    ensure_meta(scores)

    # 🔒 If today is marked as a skip-penalty day (e.g., resetweek run on Sunday),
    #     then don't nag people with reminders either.
    today = now.date()
    today_iso = today.isoformat()
    if today_iso in scores["_meta"].get("skip_penalty_days", []):
        return

    wordle_num = str(date_to_wordle(today))

    joined_users = {
        uid for uid, data in scores.items()
        if isinstance(data, dict) and data.get("joined")
    }
    missing_ids = [uid for uid in joined_users if wordle_num not in scores[uid]["games"]]
    if not missing_ids:
        return

    channel = None
    if MISSING_CHANNEL_ID and MISSING_CHANNEL_ID.isdigit():
        channel = bot.get_channel(int(MISSING_CHANNEL_ID))
    if channel is None:
        channel = discord.utils.get(bot.get_all_channels(), name="general")
    if channel is None:
        return

    names = []
    for uid in missing_ids:
        try:
            user = await bot.fetch_user(int(uid))
            names.append(user.display_name)
        except Exception:
            pass

    if names:
        mentions = ", ".join(f"<@{uid}>" for uid in missing_ids)
        await channel.send(f"⏰ Reminder: {mentions} still need to submit today’s Wordle!")

# === Bot Events ===
@bot.event
async def on_ready():
    print(f"✅ Bot is ready as {bot.user} (guilds={len(bot.guilds)})")
    daily_penalty_check.start()
    nightly_missing_alert.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    match = re.search(r"Wordle\s+([\d,]+)\s+(\d|X)/6", message.content)
    if match:
        wordle_number = match.group(1).replace(",", "")  # store keys as strings
        tries = 7 if match.group(2) == "X" else int(match.group(2))
        user_id = str(message.author.id)

        scores = load_scores()
        ensure_meta(scores)
        if user_id not in scores:
            scores[user_id] = {"total": 0, "games": {}, "joined": True, "wins": 0}

        if wordle_number in scores[user_id]["games"]:
            scores[user_id]["total"] -= scores[user_id]["games"][wordle_number]

        scores[user_id]["games"][wordle_number] = tries
        scores[user_id]["total"] += tries

        save_scores(scores)
        await message.channel.send(
            f"✅ Wordle #{wordle_number} recorded — {tries} tries for {message.author.display_name}!"
        )

        lb_text = await build_leaderboard_text()
        await message.channel.send(lb_text)

    await bot.process_commands(message)


# === Commands ===
@bot.command()
async def leaderboard(ctx):
    text = await build_leaderboard_text()
    await ctx.send(text)

@bot.command()
async def joinwordle(ctx):
    scores = load_scores()
    uid = str(ctx.author.id)
    if uid not in scores:
        scores[uid] = {"total": 0, "games": {}, "joined": True, "wins": 0}
    else:
        scores[uid]["joined"] = True
    save_scores(scores)
    await ctx.send(f"{ctx.author.mention} joined the daily Wordle challenge!")

@bot.command()
async def leavewordle(ctx):
    scores = load_scores()
    uid = str(ctx.author.id)
    if uid in scores:
        scores[uid]["joined"] = False
        save_scores(scores)
        await ctx.send(f"{ctx.author.mention} left the daily Wordle challenge.")

@bot.command()
@commands.has_permissions(administrator=True)
async def resetweek(ctx):
    scores = load_scores()
    ensure_meta(scores)

    # ONLY count players currently joined
    entries = [
        (uid, data) for uid, data in scores.items()
        if _is_user_record(uid, data) and data.get("joined")
    ]
    if not entries:
        await ctx.send("No joined players to score this week.")
        return

    # Sort by weekly total ascending (lower total = better)
    entries.sort(key=lambda x: x[1]["total"])
    top_total = entries[0][1]["total"]

    # Build blocks of tied ranks (competition ranking: 1,2,2,4)
    blocks, i = [], 0
    while i < len(entries):
        same = [entries[i]]
        j = i + 1
        while j < len(entries) and entries[j][1]["total"] == entries[i][1]["total"]:
            same.append(entries[j])
            j += 1
        rank = i + 1
        blocks.append((rank, same))
        i = j

    def ids(block):
        return [uid for uid, _ in block]

    # Compute last place among joined only (for waffle)
    worst_total = max(d["total"] for _, d in entries)
    waffle_ids = [uid for uid, d in entries if d["total"] == worst_total]

    # Podium:
    # gold  = everyone tied for best (rank 1)
    # silver = everyone in rank 2 block (if any)
    # bronze = everyone in rank 3 block (if any)
    rank1, block1 = blocks[0]
    gold_ids = ids(block1)

    silver_ids = []
    bronze_ids = []

    for r, blk in blocks[1:]:
        if r == 2 and not silver_ids:
            silver_ids = ids(blk)
        elif r == 3 and not bronze_ids:
            bronze_ids = ids(blk)

    # Store last week's podium + waffle
    scores["_meta"]["last_podium"] = {
        "gold": gold_ids,
        "silver": silver_ids,
        "bronze": bronze_ids,
        "waffle": waffle_ids,
    }

    # Increment wins for all gold players
    for uid in gold_ids:
        scores[uid]["wins"] = scores[uid].get("wins", 0) + 1

    # Announce winners
    if len(gold_ids) == 1:
        winner_id = gold_ids[0]
        winner_user = await bot.fetch_user(int(winner_id))
        await ctx.send(
            f"🎉 Congrats {winner_user.display_name} for winning the week with {top_total} total tries!"
        )
    else:
        names = []
        for uid in gold_ids:
            u = await bot.fetch_user(int(uid))
            names.append(u.display_name)
        await ctx.send(
            f"🎉 Weekly tie! Shared gold for: {', '.join(names)} with {top_total} total tries!"
        )

    # Announce last place (waffle) + increment waffle counters
    if waffle_ids:
        names = []
        for uid in waffle_ids:
            # increment waffle count on the user record
            scores[uid]["waffles"] = scores[uid].get("waffles", 0) + 1

            u = await bot.fetch_user(int(uid))
            names.append(f"🧇 {u.display_name}")
        await ctx.send("😬 Last place this week: " + ", ".join(names))

    # --- Keep your existing Sunday-skip logic for penalties ---
    today_cst = datetime.now(CENTRAL_TZ).date()
    if today_cst.weekday() == 6:   # Monday=0 ... Sunday=6
        sunday_iso = today_cst.isoformat()
        lst = scores["_meta"].get("skip_penalty_days", [])
        if sunday_iso not in lst:
            lst.append(sunday_iso)
        scores["_meta"]["skip_penalty_days"] = lst

    # Reset week but keep wins/joined
    for uid, data in list(scores.items()):
        if _is_user_record(uid, data):
            data["games"] = {}
            data["total"] = 0
            scores[uid] = data

    save_scores(scores)
    await ctx.send("Scores have been reset for the new week!")


@bot.command()
async def wins(ctx):
    scores = load_scores()
    lines = [
        f"**{await bot.fetch_user(int(uid))}** — {data.get('wins', 0)} wins"
        for uid, data in scores.items()
        if isinstance(data, dict) and data.get("wins", 0) > 0
    ]
    if lines:
        await ctx.send("__**🥇 Weekly Wins**__\n" + "\n".join(lines))
    else:
        await ctx.send("No wins recorded yet.")

@bot.command()
async def waffle(ctx):
    """Show how many times each player has finished last (waffle)."""
    scores = load_scores()
    lines = [
        f"**{await bot.fetch_user(int(uid))}** — {data.get('waffles', 0)} waffles"
        for uid, data in scores.items()
        if isinstance(data, dict) and data.get("waffles", 0) > 0
    ]

    if lines:
        await ctx.send("__**🧇 Waffle Count**__\n" + "\n".join(lines))
    else:
        await ctx.send("No waffles recorded yet. Everyone’s safe… for now.")

@bot.command()
async def missing(ctx):
    scores = load_scores()
    today = datetime.now(CENTRAL_TZ).date()
    wordle_num = str(date_to_wordle(today))

    joined_users = {
        uid for uid, data in scores.items()
        if isinstance(data, dict) and data.get("joined")
    }
    missing = [
        await bot.fetch_user(int(uid)) for uid in joined_users
        if wordle_num not in scores[uid]["games"]
    ]

    if missing:
        await ctx.send("__**📋 Players Missing Today's Wordle**__\n" + ", ".join(user.name for user in missing))
    else:
        await ctx.send("✅ Everyone has submitted today's Wordle!")

@bot.command()
@commands.has_permissions(administrator=True)
async def backup(ctx):
    """Create a backup and upload it as a file in Discord."""
    scores = load_scores()
    ts = datetime.now(CENTRAL_TZ).strftime("%Y%m%d_%H%M%S")
    fn = f"scores_backup_{ts}.json"
    path = f"/tmp/{fn}"

    # write to /tmp (still handy if you want to shell in later)
    with open(path, "w") as f:
        json.dump(scores, f, indent=2)

    # upload the file to the channel
    await ctx.send(
        content="💾 Backup created:",
        file=discord.File(path, filename=fn)
    )

