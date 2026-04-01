# scheduler.py
import io
import json
import random
from datetime import datetime
from typing import Optional, Union, cast

import discord
import pytz

from config import TIMEZONE
from database import get_db, get_active_guilds, ensure_team_stats
from utils import week_start

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
    "Small wins. Big momentum. GG.",
]

LOSS_LINES = [
    "The wasabi cleanses weakness. Redemption arc starts now.",
    "Pain is temporary. Clips are forever.",
    "We fall together so we can rise together.",
    "Failure is feedback — next week we cook.",
    "That dog biscuit had main-character energy.",
]


def _resolve_channel(bot: discord.Client, channel_id: int) -> Optional[MessageableChan]:
    ch = bot.get_channel(channel_id)
    if ch is None:
        return None
    if isinstance(ch, (discord.TextChannel, discord.Thread, discord.DMChannel, discord.GroupChannel)):
        return cast(MessageableChan, ch)
    return None


# ── Per-guild helpers ─────────────────────────────────────────────────────────

async def _post_weekly_for_guild(channel: MessageableChan, cfg: dict):
    guild_id = cfg["guild_id"]
    ensure_team_stats(guild_id)

    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT streak FROM team_stats WHERE guild_id=%s", (guild_id,))
    ts = cur.fetchone()
    streak = ts["streak"] if ts else 0

    cur.execute("SELECT * FROM participants WHERE guild_id=%s AND active=1", (guild_id,))
    participants = cur.fetchall()

    cur.execute("SELECT * FROM goals_default WHERE guild_id=%s", (guild_id,))
    all_goals = cur.fetchall()
    conn.close()

    header = (
        f"Week of {datetime.now(tz).strftime('%m/%d')} — @LOSER Challenge (Team Mode)\n"
        f"🏆 Current Team Streak: {streak} week{'s' if streak != 1 else ''}\n\n"
    )
    body = ""
    for p in participants:
        user_goals = [g for g in all_goals if g["user_id"] == p["user_id"]]
        if user_goals:
            glines = ", ".join(
                f"{g['name']} — {g['target']} ({g['log_style']})" if g["type"] == "count"
                else f"{g['name']} — boolean"
                for g in user_goals
            )
        else:
            glines = "No goals set."
        body += f"<@{p['user_id']}>: {glines}\n"

    footer = (
        "\nWe're all in this together 💪  If ANYONE fails, EVERYONE fails 🐶🔥\n"
        "Use `/loser` for incremental, `/final` for weekly-final, `/complete` for boolean. "
        "Deadline: Sunday 11:59 PM CT."
    )
    await channel.send(header + body + footer)


async def _evaluate_for_guild(bot: discord.Client, channel: MessageableChan, cfg: dict):
    guild_id      = cfg["guild_id"]
    loser_role_id = cfg.get("loser_role_id")
    ensure_team_stats(guild_id)

    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM participants WHERE guild_id=%s AND active=1", (guild_id,))
    participants = cur.fetchall()
    wstart = week_start()
    failed_users = []

    for p in participants:
        uid = p["user_id"]
        cur.execute(
            "SELECT * FROM goals_default WHERE guild_id=%s AND user_id=%s", (guild_id, uid)
        )
        goals = cur.fetchall()
        for g in goals:
            if g["type"] == "count":
                if g["log_style"] == "incremental":
                    cur.execute(
                        "SELECT value_total FROM progress "
                        "WHERE guild_id=%s AND user_id=%s AND week_start=%s AND name=%s",
                        (guild_id, uid, str(wstart), g["name"]),
                    )
                    row = cur.fetchone()
                    if (row["value_total"] if row else 0) < g["target"]:
                        failed_users.append(uid)
                else:
                    cur.execute(
                        "SELECT value FROM finals "
                        "WHERE guild_id=%s AND user_id=%s AND week_start=%s AND name=%s",
                        (guild_id, uid, str(wstart), g["name"]),
                    )
                    row = cur.fetchone()
                    if (row["value"] if row else 0) < g["target"]:
                        failed_users.append(uid)
            else:
                cur.execute(
                    "SELECT done FROM booleans "
                    "WHERE guild_id=%s AND user_id=%s AND week_start=%s AND name=%s",
                    (guild_id, uid, str(wstart), g["name"]),
                )
                row = cur.fetchone()
                if not row or not row["done"]:
                    failed_users.append(uid)

    guild = getattr(channel, "guild", None)
    if guild is None:
        print(f"ERROR evaluate: channel has no guild for guild_id={guild_id}")
        conn.close(); return

    loser_role = guild.get_role(loser_role_id) if loser_role_id else None

    cur.execute(
        "SELECT streak, best_streak FROM team_stats WHERE guild_id=%s", (guild_id,)
    )
    ts = cur.fetchone()
    streak, best = (ts["streak"], ts["best_streak"]) if ts else (0, 0)

    if failed_users:
        prev   = streak
        streak = 0
        cur.execute(
            "UPDATE team_stats SET streak=%s, best_streak=%s WHERE guild_id=%s",
            (streak, max(best, prev), guild_id),
        )
        for p in participants:
            member = guild.get_member(p["user_id"])
            if member and loser_role:
                try:
                    await member.add_roles(loser_role)
                except discord.Forbidden:
                    print(f"⚠️ Missing perms to add role for {member}")

        names  = "\n".join(f"• <@{uid}> — missed" for uid in sorted(set(failed_users)))
        taunt  = random.choice(LOSS_LINES)
        msg = (
            f"💀 **TEAM LOSS** — Week of {datetime.now(tz).strftime('%m/%d')}\n\n"
            f"Streak Reset! ❌ (Previous streak: {prev} week{'s' if prev != 1 else ''})\n\n"
            f"The following members didn't complete all their goals:\n{names}\n\n"
            f"Because we play as ONE TEAM, we all face the consequence 🐶🔥\n"
            f"👉 Dog biscuit + ½ tsp wasabi — record & share your video!\n\n"
            f"💬 *{taunt}*"
        )
        team_result = "FAIL"
    else:
        streak += 1
        best    = max(best, streak)
        cur.execute(
            "UPDATE team_stats SET streak=%s, best_streak=%s WHERE guild_id=%s",
            (streak, best, guild_id),
        )
        for p in participants:
            member = guild.get_member(p["user_id"])
            if member and loser_role and loser_role in member.roles:
                try:
                    await member.remove_roles(loser_role)
                except discord.Forbidden:
                    print(f"⚠️ Missing perms to remove role for {member}")

        hype   = random.choice(WIN_LINES)
        roster = "\n".join(f"<@{p['user_id']}> — ✅" for p in participants) or "No participants"
        msg = (
            f"✅ **TEAM WIN** — Week of {datetime.now(tz).strftime('%m/%d')}\n\n"
            f"🏆 Team Streak: {streak} week{'s' if streak != 1 else ''} (Best: {best})\n\n"
            f"Everyone met their goals this week — no wasabi, just glory. 💪\n\n"
            f"{roster}\n\n"
            f"🔥 *{hype}*\n"
            f"Next check-in: Sunday 11:59 PM CT"
        )
        team_result = "WIN"

    cur.execute(
        """
        INSERT INTO results (guild_id, week_start, team_result, failed_members)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (guild_id, week_start) DO UPDATE SET
            team_result=EXCLUDED.team_result,
            failed_members=EXCLUDED.failed_members
        """,
        (guild_id, str(wstart), team_result, ", ".join(str(u) for u in sorted(set(failed_users)))),
    )
    conn.commit(); conn.close()
    await channel.send(msg)


async def _reset_for_guild(bot: discord.Client, channel: MessageableChan, cfg: dict):
    guild_id      = cfg["guild_id"]
    loser_role_id = cfg.get("loser_role_id")

    conn = get_db(); cur = conn.cursor()
    cur.execute("DELETE FROM progress WHERE guild_id=%s", (guild_id,))
    cur.execute("DELETE FROM finals   WHERE guild_id=%s", (guild_id,))
    cur.execute("DELETE FROM booleans WHERE guild_id=%s", (guild_id,))
    conn.commit(); conn.close()

    guild = getattr(channel, "guild", None)
    if guild is None:
        return

    loser_role = guild.get_role(loser_role_id) if loser_role_id else None
    if loser_role:
        for member in list(guild.members):
            if loser_role in member.roles:
                try:
                    await member.remove_roles(loser_role, reason="Loser Challenge weekly reset")
                except discord.Forbidden:
                    print(f"⚠️ Missing perms removing role from {member}")
                except Exception as e:
                    print(f"⚠️ remove_roles error for {member}: {e}")

    try:
        await channel.send("🔄 New week reset complete. Set/keep your defaults and crush it! 💪")
    except Exception as e:
        print(f"⚠️ send reset message failed: {e}")


# ── Public scheduler entry points ─────────────────────────────────────────────

async def post_weekly_message(bot: discord.Client):
    for cfg in get_active_guilds():
        channel_id = cfg.get("challenge_channel_id")
        if not channel_id:
            continue
        channel = _resolve_channel(bot, channel_id)
        if channel is None:
            print(f"ERROR post_weekly: channel {channel_id} not found (guild {cfg['guild_id']})")
            continue
        try:
            await _post_weekly_for_guild(channel, cfg)
        except Exception as e:
            print(f"ERROR post_weekly guild {cfg['guild_id']}: {e}")


async def backup_now(bot: discord.Client):
    """Export each active guild's data as a JSON file and post to its challenge channel."""
    for cfg in get_active_guilds():
        channel_id = cfg.get("challenge_channel_id")
        if not channel_id:
            continue
        channel = _resolve_channel(bot, channel_id)
        if channel is None:
            continue
        try:
            guild_id = cfg["guild_id"]
            conn = get_db(); cur = conn.cursor()
            snapshot: dict = {"guild_id": guild_id}
            for table in (
                "participants", "goals_default", "progress",
                "finals", "booleans", "results", "team_stats",
            ):
                cur.execute(f"SELECT * FROM {table} WHERE guild_id = %s", (guild_id,))
                snapshot[table] = [dict(r) for r in cur.fetchall()]
            conn.close()

            ts  = datetime.now(tz).strftime("%Y%m%d_%H%M%S")
            fn  = f"loser_backup_{ts}.json"
            buf = io.BytesIO(json.dumps(snapshot, indent=2, default=str).encode())
            await channel.send("💾 Auto-backup saved:", file=discord.File(buf, filename=fn))
        except Exception as e:
            print(f"ERROR backup_now guild {cfg['guild_id']}: {e}")


async def evaluate_week(bot: discord.Client):
    for cfg in get_active_guilds():
        channel_id = cfg.get("challenge_channel_id")
        if not channel_id:
            continue
        channel = _resolve_channel(bot, channel_id)
        if channel is None:
            print(f"ERROR evaluate: channel {channel_id} not found (guild {cfg['guild_id']})")
            continue
        try:
            await _evaluate_for_guild(bot, channel, cfg)
        except Exception as e:
            print(f"ERROR evaluate_week guild {cfg['guild_id']}: {e}")


async def reset_week(bot: discord.Client):
    for cfg in get_active_guilds():
        channel_id = cfg.get("challenge_channel_id")
        if not channel_id:
            continue
        channel = _resolve_channel(bot, channel_id)
        if channel is None:
            print(f"ERROR reset: channel {channel_id} not found (guild {cfg['guild_id']})")
            continue
        try:
            await _reset_for_guild(bot, channel, cfg)
        except Exception as e:
            print(f"ERROR reset_week guild {cfg['guild_id']}: {e}")
