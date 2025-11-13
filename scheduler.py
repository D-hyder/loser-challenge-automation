import random
from pathlib import Path
from shutil import copyfile
from datetime import datetime, timedelta
from typing import Optional, Union, cast
import pytz
import discord

from database import get_db
from config import TIMEZONE, CHALLENGE_CHANNEL_ID, LOSER_ROLE_ID, DATABASE_PATH

tz = pytz.timezone(TIMEZONE)

MessageableChan = Union[
    discord.TextChannel,
    discord.Thread,
    discord.DMChannel,
    discord.GroupChannel,
]

WIN_LINES = [
    "Discipline beats motivation — and we had both. Keep it rolling!",
    "No wasabi, no mercy. Absolute unit of a team.",
    "Consistency compounding. Another brick on the wall.",
    "Streak alive. Monday belongs to us.",
    "Small wins. Big momentum. GG."
]

LOSS_LINES = [
    "The wasabi cleanses weakness. Redemption arc starts now.",
    "Pain is temporary. Clips are forever.",
    "We fall together so we can rise together.",
    "Failure is feedback — next week we cook.",
    "That dog biscuit had main-character energy."
]

def _resolve_message_channel(bot: discord.Client, channel_id: int) -> Optional[MessageableChan]:
    ch = bot.get_channel(channel_id)
    if ch is None:
        return None
    if isinstance(ch, (discord.TextChannel, discord.Thread, discord.DMChannel, discord.GroupChannel)):
        return cast(MessageableChan, ch)
    # Category/Voice/Stage/Forum cannot .send()
    return None

def week_start_date(dt=None):
    now = dt or datetime.now(tz)
    return (now - timedelta(days=now.weekday())).date()

async def post_weekly_message(bot: discord.Client):
    channel = _resolve_message_channel(bot, CHALLENGE_CHANNEL_ID) # type: ignore
    conn = get_db()
    cur = conn.cursor()

    if channel is None:
        # Optionally log an error so you fix the channel id
        print("ERROR: CHALLENGE_CHANNEL_ID is not a messageable channel or not found.")
        return

    # Fetch team streak
    ts = cur.execute("SELECT streak FROM team_stats WHERE id=1").fetchone()
    streak = ts["streak"] if ts else 0

    participants = cur.execute("SELECT * FROM participants WHERE active=1").fetchall()
    goals = cur.execute("SELECT * FROM goals_default").fetchall()

    header = f"Week of {datetime.now(tz).strftime('%m/%d')} — @LOSER Challenge (Team Mode)\n"
    header += f"🏆 Current Team Streak: {streak} week{'s' if streak != 1 else ''}\n\n"
    body = ""
    for p in participants:
        user_goals = [g for g in goals if g["user_id"] == p["user_id"]]
        if user_goals:
            glines = ", ".join([f"{g['name']} — {g['target']} ({g['log_style']})" if g["type"] == "count"
                                else f"{g['name']} — boolean" for g in user_goals])
        else:
            glines = "No goals set."
        body += f"<@{p['user_id']}>: {glines}\n"

    footer = ("\nWe’re all in this together 💪  If ANYONE fails, EVERYONE fails 🐶🔥\n"
              "Use `/loser` for incremental, `/final` for weekly-final, `/complete` for boolean. "
              "Deadline: Sunday 11:59 PM CT.")
    await channel.send(header + body + footer)
    conn.close()

async def backup_now(bot: discord.Client):
    """Create a timestamped DB backup before evaluation."""
    p = Path(DATABASE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    backup_name = p.parent / f"backup_{datetime.now(tz).strftime('%Y%m%d_%H%M%S')}.db"
    copyfile(p, backup_name)
    # ... make backup_name ...
    channel = _resolve_message_channel(bot, CHALLENGE_CHANNEL_ID) # type: ignore
    if channel:
        await channel.send(f"💾 Auto-backup saved: `{backup_name.name}`")

async def evaluate_week(bot: discord.Client):
    conn = get_db()
    cur = conn.cursor()
    participants = cur.execute("SELECT * FROM participants WHERE active=1").fetchall()
    wstart = week_start_date()
    failed_users = []

    for p in participants:
        uid = p["user_id"]
        goals = cur.execute("SELECT * FROM goals_default WHERE user_id=?", (uid,)).fetchall()
        for g in goals:
            if g["type"] == "count":
                if g["log_style"] == "incremental":
                    row = cur.execute("""
                        SELECT value_total FROM progress WHERE user_id=? AND week_start=? AND name=?
                    """, (uid, str(wstart), g["name"])).fetchone()
                    val = row["value_total"] if row else 0
                    if val < g["target"]:
                        failed_users.append(uid)
                else:  # weekly_final
                    row = cur.execute("""
                        SELECT value FROM finals WHERE user_id=? AND week_start=? AND name=?
                    """, (uid, str(wstart), g["name"])).fetchone()
                    val = row["value"] if row else 0
                    if val < g["target"]:
                        failed_users.append(uid)
            else:  # boolean
                row = cur.execute("""
                    SELECT done FROM booleans WHERE user_id=? AND week_start=? AND name=?
                """, (uid, str(wstart), g["name"])).fetchone()
                if not row or not row["done"]:
                    failed_users.append(uid)

    channel = _resolve_message_channel(bot, CHALLENGE_CHANNEL_ID) # type: ignore
    if channel is None:
        print("ERROR: CHALLENGE_CHANNEL_ID is not a messageable channel or not found.")
        return
    # loser_role needs a guild, so ensure channel is a guild text channel or thread
    guild = getattr(channel, "guild", None)
    if guild is None:
        print("ERROR: Channel has no guild (maybe DM or category?)")
        return

    loser_role = guild.get_role(LOSER_ROLE_ID)

    # Streak bookkeeping
    ts = cur.execute("SELECT streak, best_streak FROM team_stats WHERE id=1").fetchone()
    streak, best = (ts["streak"], ts["best_streak"]) if ts else (0, 0)

    if failed_users:
        # Reset streak
        prev = streak
        streak = 0
        cur.execute("UPDATE team_stats SET streak=?, best_streak=? WHERE id=1", (streak, max(best, prev)))
        # Assign loser role to everyone
        for p in participants:
            member = guild.get_member(p["user_id"])
            if member and loser_role:
                try:
                    await member.add_roles(loser_role)
                except discord.Forbidden:
                    print(f"⚠️ Missing permissions to add role for {member}")

        # Compose message
        names = "\n".join([f"• <@{uid}> — missed" for uid in sorted(set(failed_users))])
        taunt = random.choice(LOSS_LINES)
        msg = (f"💀 **TEAM LOSS** — Week of {datetime.now(tz).strftime('%m/%d')}\n\n"
               f"Streak Reset! ❌ (Previous streak: {prev} week{'s' if prev != 1 else ''})\n\n"
               f"The following members didn’t complete all their goals:\n{names}\n\n"
               f"Because we play as ONE TEAM, we all face the consequence 🐶🔥\n"
               f"👉 Dog biscuit + ½ tsp wasabi — record & share your video!\n\n"
               f"💬 *{taunt}*")
        team_result = "FAIL"
    else:
        # Increment streak
        streak += 1
        best = max(best, streak)
        cur.execute("UPDATE team_stats SET streak=?, best_streak=? WHERE id=1", (streak, best))
        # Remove loser role if anyone still had it
        for p in participants:
            member = guild.get_member(p["user_id"])
            if member and loser_role and loser_role in member.roles:
                try:
                    await member.remove_roles(loser_role)
                except discord.Forbidden:
                    print(f"⚠️ Missing permissions to remove role for {member}")

        # Compose message
        hype = random.choice(WIN_LINES)
        roster = "\n".join([f"<@{p['user_id']}> — ✅" for p in participants]) or "No participants"
        msg = (f"✅ **TEAM WIN** — Week of {datetime.now(tz).strftime('%m/%d')}\n\n"
               f"🏆 Team Streak: {streak} week{'s' if streak != 1 else ''} (Best: {best})\n\n"
               f"Everyone met their goals this week — no wasabi, just glory. 💪\n\n"
               f"{roster}\n\n"
               f"🔥 *{hype}*\n"
               f"Next check-in: Sunday 11:59 PM CT")
        team_result = "WIN"

    cur.execute("INSERT OR REPLACE INTO results VALUES (?, ?, ?)",
                (str(wstart), team_result, ", ".join([str(u) for u in sorted(set(failed_users))])))
    conn.commit()
    conn.close()

    await channel.send(msg)

# Channels you can .send() in
Messageable = Union[discord.TextChannel, discord.Thread, discord.DMChannel, discord.GroupChannel]


async def reset_week(bot: discord.Client):
    """Clear weekly progress tables and remove LOSER roles (fresh week)."""
    # wipe week tables
    conn = get_db()
    cur = conn.cursor()
    cur.executescript("DELETE FROM progress; DELETE FROM finals; DELETE FROM booleans;")
    conn.commit()
    conn.close()

    # resolve a messageable channel
    channel = _resolve_message_channel(bot, CHALLENGE_CHANNEL_ID) # type: ignore
    if channel is None:
        print("ERROR reset_week: CHALLENGE_CHANNEL_ID not found or not messageable")
        return

    # get guild safely (only text/thread channels have guild)
    guild = getattr(channel, "guild", None)
    if guild is None:
        print("ERROR reset_week: channel has no guild (DM/category/forum?)")
        return

    loser_role = guild.get_role(LOSER_ROLE_ID)
    if loser_role:
        for member in list(guild.members):
            if loser_role in member.roles:
                try:
                    await member.remove_roles(loser_role, reason="Loser Challenge weekly reset")
                except discord.Forbidden:
                    print(f"⚠️ Missing perms to remove role from {member}")
                except Exception as e:
                    print(f"⚠️ remove_roles error for {member}: {e}")

    try:
        await channel.send("🔄 New week reset complete. Set/keep your defaults and crush it! 💪")
    except Exception as e:
        print(f"⚠️ send reset message failed: {e}")

