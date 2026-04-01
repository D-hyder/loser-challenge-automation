# wordle_bot.py
import json
import re
from datetime import datetime, timedelta, date

import discord
from discord.ext import commands, tasks
import pytz

from database import get_db, get_active_guilds, init_db
from config import WORDLE_BOT_TOKEN

CENTRAL_TZ   = pytz.timezone("America/Chicago")
WORDLE_EPOCH = date(2021, 6, 19)   # Wordle #0


def wordle_to_date(wordle_num: int) -> date:
    return WORDLE_EPOCH + timedelta(days=int(wordle_num))


def date_to_wordle(some_date: date) -> int:
    return (some_date - WORDLE_EPOCH).days


# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_wordle_user(guild_id: int, user_id: int):
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "SELECT * FROM wordle_users WHERE guild_id=%s AND user_id=%s", (guild_id, user_id)
    )
    row = cur.fetchone(); conn.close()
    return row


def upsert_wordle_user(guild_id: int, user_id: int, **kwargs):
    """Insert or update specific columns in wordle_users."""
    conn = get_db(); cur = conn.cursor()
    if kwargs:
        cols = list(kwargs.keys()); vals = list(kwargs.values())
        col_str    = ", ".join(["guild_id", "user_id"] + cols)
        ph_str     = ", ".join(["%s"] * (2 + len(cols)))
        set_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols)
        cur.execute(
            f"INSERT INTO wordle_users ({col_str}) VALUES ({ph_str}) "
            f"ON CONFLICT (guild_id, user_id) DO UPDATE SET {set_clause}",
            [guild_id, user_id] + vals,
        )
    else:
        cur.execute(
            "INSERT INTO wordle_users (guild_id, user_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (guild_id, user_id),
        )
    conn.commit(); conn.close()


def get_wordle_score(guild_id: int, user_id: int, wordle_num: str) -> int | None:
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "SELECT tries FROM wordle_scores WHERE guild_id=%s AND user_id=%s AND wordle_num=%s",
        (guild_id, user_id, wordle_num),
    )
    row = cur.fetchone(); conn.close()
    return row["tries"] if row else None


def upsert_wordle_score(guild_id: int, user_id: int, wordle_num: str, tries: int):
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO wordle_scores (guild_id, user_id, wordle_num, tries)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (guild_id, user_id, wordle_num) DO UPDATE SET tries=EXCLUDED.tries
        """,
        (guild_id, user_id, wordle_num, tries),
    )
    conn.commit(); conn.close()


def get_wordle_meta(guild_id: int) -> dict:
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM wordle_meta WHERE guild_id=%s", (guild_id,))
    row = cur.fetchone(); conn.close()
    if not row:
        return {
            "guild_id": guild_id,
            "last_podium": {"gold": [], "silver": [], "bronze": [], "waffle": []},
            "skip_penalty_days": [],
            "last_penalized_day": "",
        }
    return {
        "guild_id": row["guild_id"],
        "last_podium": json.loads(row["last_podium"] or "{}"),
        "skip_penalty_days": json.loads(row["skip_penalty_days"] or "[]"),
        "last_penalized_day": row["last_penalized_day"] or "",
    }


def save_wordle_meta(guild_id: int, meta: dict):
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO wordle_meta (guild_id, last_podium, skip_penalty_days, last_penalized_day)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (guild_id) DO UPDATE SET
            last_podium=EXCLUDED.last_podium,
            skip_penalty_days=EXCLUDED.skip_penalty_days,
            last_penalized_day=EXCLUDED.last_penalized_day
        """,
        (
            guild_id,
            json.dumps(meta.get("last_podium", {})),
            json.dumps(meta.get("skip_penalty_days", [])),
            meta.get("last_penalized_day", ""),
        ),
    )
    conn.commit(); conn.close()


# ── Leaderboard ───────────────────────────────────────────────────────────────

async def build_leaderboard_text(guild_id: int) -> str:
    meta   = get_wordle_meta(guild_id)
    podium = meta.get("last_podium", {})

    conn = get_db(); cur = conn.cursor()
    cur.execute(
        """
        SELECT wu.user_id, wu.total,
               COUNT(ws.wordle_num) AS game_count
        FROM wordle_users wu
        LEFT JOIN wordle_scores ws
               ON wu.guild_id = ws.guild_id AND wu.user_id = ws.user_id
        WHERE wu.guild_id = %s
        GROUP BY wu.user_id, wu.total
        ORDER BY wu.total ASC
        """,
        (guild_id,),
    )
    entries = cur.fetchall(); conn.close()

    if not entries:
        return "No scores yet."

    def medal(uid: int) -> str:
        s = str(uid)
        if s in podium.get("gold",   []): return "👑 "
        if s in podium.get("silver", []): return "🥈 "
        if s in podium.get("bronze", []): return "🥉 "
        if s in podium.get("waffle", []): return "🧇 "
        return ""

    lines = []
    for row in entries:
        uid  = row["user_id"]
        user = await bot.fetch_user(int(uid))
        lines.append(
            f"{medal(uid)}**{user.display_name}** — "
            f"{row['total']} tries over {row['game_count']} games"
        )
    return "__**🏆 Wordle Leaderboard**__\n" + "\n".join(lines)


# ── Scheduled penalty/alert helpers ──────────────────────────────────────────

async def _process_daily_penalty(cfg: dict, now: datetime):
    guild_id   = cfg["guild_id"]
    target_day = (now.date() - timedelta(days=1))
    stamp      = target_day.isoformat()
    meta       = get_wordle_meta(guild_id)

    if meta.get("last_penalized_day") == stamp:
        return

    if stamp in meta.get("skip_penalty_days", []):
        meta["last_penalized_day"] = stamp
        meta["skip_penalty_days"]  = [d for d in meta["skip_penalty_days"] if d != stamp]
        save_wordle_meta(guild_id, meta)
        return

    wordle_num = str(date_to_wordle(target_day))

    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "SELECT user_id, total FROM wordle_users WHERE guild_id=%s AND joined=TRUE", (guild_id,)
    )
    joined = cur.fetchall(); conn.close()

    penalized = []
    for wu in joined:
        uid = wu["user_id"]
        if get_wordle_score(guild_id, uid, wordle_num) is None:
            upsert_wordle_score(guild_id, uid, wordle_num, 7)
            upsert_wordle_user(guild_id, uid, total=wu["total"] + 7)
            penalized.append(uid)

    meta["last_penalized_day"] = stamp
    save_wordle_meta(guild_id, meta)

    if penalized:
        channel = _alert_channel(cfg)
        if channel:
            mentions = ", ".join(f"<@{uid}>" for uid in penalized)
            await channel.send(
                f"⏰ Auto-penalty: {mentions} were given 7 tries for missing Wordle #{wordle_num}."
            )


async def _process_missing_alert(cfg: dict, now: datetime):
    guild_id  = cfg["guild_id"]
    today     = now.date()
    today_iso = today.isoformat()
    meta      = get_wordle_meta(guild_id)

    if today_iso in meta.get("skip_penalty_days", []):
        return

    wordle_num = str(date_to_wordle(today))

    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "SELECT user_id FROM wordle_users WHERE guild_id=%s AND joined=TRUE", (guild_id,)
    )
    joined = cur.fetchall(); conn.close()

    missing_ids = [
        wu["user_id"] for wu in joined
        if get_wordle_score(guild_id, wu["user_id"], wordle_num) is None
    ]
    if not missing_ids:
        return

    channel = _alert_channel(cfg)
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
        await channel.send(f"⏰ Reminder: {mentions} still need to submit today's Wordle!")


def _alert_channel(cfg: dict) -> discord.TextChannel | None:
    """Return the alert channel for a guild config, falling back to #general."""
    guild_id = cfg["guild_id"]
    guild    = bot.get_guild(guild_id)
    if guild is None:
        return None
    cid = cfg.get("missing_channel_id") or cfg.get("wordle_channel_id")
    if cid:
        ch = guild.get_channel(int(cid))
        if ch:
            return ch
    return discord.utils.get(guild.text_channels, name="general")


# ── Hourly tasks ──────────────────────────────────────────────────────────────

@tasks.loop(hours=1)
async def daily_penalty_check():
    now = datetime.now(CENTRAL_TZ)
    if now.hour != 0:
        return
    for cfg in get_active_guilds():
        try:
            await _process_daily_penalty(cfg, now)
        except Exception as e:
            print(f"ERROR daily_penalty guild {cfg['guild_id']}: {e}")


@tasks.loop(hours=1)
async def nightly_missing_alert():
    now = datetime.now(CENTRAL_TZ)
    if now.hour != 20:
        return
    for cfg in get_active_guilds():
        try:
            await _process_missing_alert(cfg, now)
        except Exception as e:
            print(f"ERROR nightly_alert guild {cfg['guild_id']}: {e}")


# ── Bot events ────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Wordle bot ready as {bot.user} (guilds={len(bot.guilds)})")
    init_db()
    daily_penalty_check.start()
    nightly_missing_alert.start()


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if not message.guild:
        await bot.process_commands(message)
        return

    guild_id = message.guild.id
    match = re.search(r"Wordle\s+([\d,]+)\s+(\d|X)/6", message.content)
    if match:
        wordle_num = match.group(1).replace(",", "")
        tries      = 7 if match.group(2) == "X" else int(match.group(2))
        user_id    = message.author.id

        wu        = get_wordle_user(guild_id, user_id)
        old_tries = get_wordle_score(guild_id, user_id, wordle_num)

        if wu is None:
            upsert_wordle_user(guild_id, user_id, joined=True, total=tries, wins=0, waffles=0)
        else:
            delta     = tries - (old_tries or 0)
            new_total = wu["total"] + delta
            upsert_wordle_user(guild_id, user_id, total=new_total)

        upsert_wordle_score(guild_id, user_id, wordle_num, tries)

        await message.channel.send(
            f"✅ Wordle #{wordle_num} recorded — {tries} tries for {message.author.display_name}!"
        )
        lb_text = await build_leaderboard_text(guild_id)
        await message.channel.send(lb_text)

    await bot.process_commands(message)


# ── Commands ──────────────────────────────────────────────────────────────────

def _require_guild(ctx) -> int | None:
    if not ctx.guild:
        return None
    return ctx.guild.id


@bot.command()
async def leaderboard(ctx):
    gid = _require_guild(ctx)
    if gid is None:
        await ctx.send("This command must be used in a server.")
        return
    text = await build_leaderboard_text(gid)
    await ctx.send(text)


@bot.command()
async def joinwordle(ctx):
    gid = _require_guild(ctx)
    if gid is None:
        await ctx.send("This command must be used in a server.")
        return
    uid = ctx.author.id
    wu  = get_wordle_user(gid, uid)
    if wu is None:
        upsert_wordle_user(gid, uid, joined=True, total=0, wins=0, waffles=0)
    else:
        upsert_wordle_user(gid, uid, joined=True)
    await ctx.send(f"{ctx.author.mention} joined the daily Wordle challenge!")


@bot.command()
async def leavewordle(ctx):
    gid = _require_guild(ctx)
    if gid is None:
        await ctx.send("This command must be used in a server.")
        return
    uid = ctx.author.id
    if get_wordle_user(gid, uid):
        upsert_wordle_user(gid, uid, joined=False)
    await ctx.send(f"{ctx.author.mention} left the daily Wordle challenge.")


@bot.command()
@commands.has_permissions(administrator=True)
async def resetweek(ctx):
    gid = _require_guild(ctx)
    if gid is None:
        await ctx.send("This command must be used in a server.")
        return

    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "SELECT user_id, total FROM wordle_users WHERE guild_id=%s AND joined=TRUE", (gid,)
    )
    entries_raw = cur.fetchall(); conn.close()

    if not entries_raw:
        await ctx.send("No joined players to score this week.")
        return

    entries = [(str(wu["user_id"]), wu["total"]) for wu in entries_raw]
    entries.sort(key=lambda x: x[1])
    top_total = entries[0][1]

    # Build competition rank blocks
    blocks: list = []
    i = 0
    while i < len(entries):
        same = [entries[i]]
        j = i + 1
        while j < len(entries) and entries[j][1] == entries[i][1]:
            same.append(entries[j])
            j += 1
        blocks.append((i + 1, same))
        i = j

    def ids(block):
        return [uid for uid, _ in block]

    worst_total = max(d for _, d in entries)
    waffle_ids  = [uid for uid, d in entries if d == worst_total]
    gold_ids    = ids(blocks[0][1])
    silver_ids: list = []
    bronze_ids: list = []
    for rank, blk in blocks[1:]:
        if rank == 2 and not silver_ids:
            silver_ids = ids(blk)
        elif rank == 3 and not bronze_ids:
            bronze_ids = ids(blk)

    # Persist podium
    meta = get_wordle_meta(gid)
    meta["last_podium"] = {
        "gold": gold_ids, "silver": silver_ids,
        "bronze": bronze_ids, "waffle": waffle_ids,
    }

    # Increment wins & waffles
    for uid in gold_ids:
        wu = get_wordle_user(gid, int(uid))
        if wu:
            upsert_wordle_user(gid, int(uid), wins=wu["wins"] + 1)
    for uid in waffle_ids:
        wu = get_wordle_user(gid, int(uid))
        if wu:
            upsert_wordle_user(gid, int(uid), waffles=wu["waffles"] + 1)

    # Announce gold
    if len(gold_ids) == 1:
        winner = await bot.fetch_user(int(gold_ids[0]))
        await ctx.send(
            f"🎉 Congrats {winner.display_name} for winning the week with {top_total} total tries!"
        )
    else:
        names = [((await bot.fetch_user(int(uid))).display_name) for uid in gold_ids]
        await ctx.send(f"🎉 Weekly tie! Shared gold: {', '.join(names)} with {top_total} tries!")

    # Announce waffle
    if waffle_ids:
        names = [f"🧇 {(await bot.fetch_user(int(uid))).display_name}" for uid in waffle_ids]
        await ctx.send("😬 Last place this week: " + ", ".join(names))

    # Add Sunday to skip-penalty days if applicable
    today_cst = datetime.now(CENTRAL_TZ).date()
    if today_cst.weekday() == 6:
        sunday_iso = today_cst.isoformat()
        lst = meta.get("skip_penalty_days", [])
        if sunday_iso not in lst:
            lst.append(sunday_iso)
        meta["skip_penalty_days"] = lst
    save_wordle_meta(gid, meta)

    # Reset weekly scores
    conn = get_db(); cur = conn.cursor()
    cur.execute("DELETE FROM wordle_scores WHERE guild_id=%s", (gid,))
    cur.execute("UPDATE wordle_users SET total=0 WHERE guild_id=%s", (gid,))
    conn.commit(); conn.close()

    await ctx.send("Scores have been reset for the new week!")


@bot.command()
async def wins(ctx):
    gid = _require_guild(ctx)
    if gid is None:
        await ctx.send("This command must be used in a server.")
        return
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "SELECT user_id, wins FROM wordle_users WHERE guild_id=%s AND wins > 0", (gid,)
    )
    rows = cur.fetchall(); conn.close()
    lines = []
    for r in rows:
        u = await bot.fetch_user(int(r["user_id"]))
        lines.append(f"**{u.display_name}** — {r['wins']} wins")
    if lines:
        await ctx.send("__**🥇 Weekly Wins**__\n" + "\n".join(lines))
    else:
        await ctx.send("No wins recorded yet.")


@bot.command()
async def waffle(ctx):
    """Show how many times each player has finished last."""
    gid = _require_guild(ctx)
    if gid is None:
        await ctx.send("This command must be used in a server.")
        return
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "SELECT user_id, waffles FROM wordle_users WHERE guild_id=%s AND waffles > 0", (gid,)
    )
    rows = cur.fetchall(); conn.close()
    lines = []
    for r in rows:
        u = await bot.fetch_user(int(r["user_id"]))
        lines.append(f"**{u.display_name}** — {r['waffles']} waffles")
    if lines:
        await ctx.send("__**🧇 Waffle Count**__\n" + "\n".join(lines))
    else:
        await ctx.send("No waffles recorded yet. Everyone's safe… for now.")


@bot.command()
async def missing(ctx):
    gid = _require_guild(ctx)
    if gid is None:
        await ctx.send("This command must be used in a server.")
        return
    today      = datetime.now(CENTRAL_TZ).date()
    wordle_num = str(date_to_wordle(today))

    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "SELECT user_id FROM wordle_users WHERE guild_id=%s AND joined=TRUE", (gid,)
    )
    joined = cur.fetchall(); conn.close()

    missing_users = [
        await bot.fetch_user(int(wu["user_id"]))
        for wu in joined
        if get_wordle_score(gid, wu["user_id"], wordle_num) is None
    ]
    if missing_users:
        await ctx.send(
            "__**📋 Players Missing Today's Wordle**__\n"
            + ", ".join(u.name for u in missing_users)
        )
    else:
        await ctx.send("✅ Everyone has submitted today's Wordle!")


@bot.command()
@commands.has_permissions(administrator=True)
async def backup(ctx):
    """Export Wordle data as a JSON attachment."""
    gid = _require_guild(ctx)
    if gid is None:
        await ctx.send("This command must be used in a server.")
        return
    import io

    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM wordle_users  WHERE guild_id=%s", (gid,))
    users = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT * FROM wordle_scores WHERE guild_id=%s", (gid,))
    scores = [dict(r) for r in cur.fetchall()]
    conn.close()

    data = {
        "guild_id": gid,
        "wordle_users": users,
        "wordle_scores": scores,
        "wordle_meta": get_wordle_meta(gid),
    }
    ts   = datetime.now(CENTRAL_TZ).strftime("%Y%m%d_%H%M%S")
    fn   = f"wordle_backup_{ts}.json"
    buf  = io.BytesIO(json.dumps(data, indent=2, default=str).encode())
    await ctx.send(content="💾 Wordle backup:", file=discord.File(buf, filename=fn))
