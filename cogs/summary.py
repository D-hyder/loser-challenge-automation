# cogs/summary.py
from datetime import datetime
from typing import List
import discord
from discord import app_commands
from discord.ext import commands

from database import get_db
from utils import week_start
from config import TIMEZONE
import pytz

tz = pytz.timezone(TIMEZONE)


def pick_humor_footer(progress_pct: int, remaining_units: int, team_risk: bool) -> str:
    weekday = datetime.now(tz).weekday()
    progress_pct   = max(0, min(progress_pct, 100))
    remaining_units = max(0, remaining_units)

    if remaining_units == 0:
        remaining_text = "All goals complete."
    elif remaining_units == 1:
        remaining_text = "1 unit left."
    else:
        remaining_text = f"{remaining_units} units left."

    if progress_pct >= 100 and not team_risk:
        return f"🎉 No wasabi biscuit this week — pack it up, Gordon Ramsay! ({remaining_text})"

    if weekday <= 1:
        if progress_pct == 0:
            msg = "🤓 Week just started — act like your life is together."
        elif progress_pct < 30:
            msg = "🍗 Light work now prevents wasabi horror later."
        else:
            msg = "🛌 Decent start, but if you're still in bed, at least log your goals."
        return f"{msg} ({remaining_text})"

    if 2 <= weekday <= 3:
        if progress_pct < 30:
            msg = "😵 Midweek slump detected — wake up, gang."
        elif progress_pct < 70:
            msg = "💀 We're doing… okay? Kinda? Maybe?"
        else:
            msg = "🫠 Solid progress, but I still wouldn't bet my life on it."
        return f"{msg} ({remaining_text})"

    if 4 <= weekday <= 5:
        if progress_pct < 50:
            msg = "🍪 Stand firm — the biscuit draws near. And it's got your name on it."
        elif progress_pct < 80:
            msg = "🧨 Team is one bad day away from chaos."
        else:
            msg = "🔥 Almost safe — don't you dare fumble now."
        return f"{msg} ({remaining_text})"

    # Sunday
    if progress_pct < 50:
        msg = "📣 SUNDAY PANIC TIME — everybody log SOMETHING."
    elif progress_pct < 80:
        msg = "😭 If you ruin this on the last day, we riot."
    else:
        msg = "🫵 Don't make us eat wasabi dog biscuits because of YOU."
    return f"{msg} ({remaining_text})"


class SummaryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="summary",
        description="Show the team progress for this week.",
    )
    async def summary(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        w   = str(week_start())
        conn = get_db(); cur = conn.cursor()

        cur.execute(
            "SELECT streak, best_streak FROM team_stats WHERE guild_id=%s", (gid,)
        )
        ts = cur.fetchone()
        streak, best = (ts["streak"], ts["best_streak"]) if ts else (0, 0)

        cur.execute(
            "SELECT * FROM participants WHERE guild_id=%s AND active=1", (gid,)
        )
        participants = cur.fetchall()
        if not participants:
            await interaction.response.send_message(
                "No active participants.", ephemeral=True
            )
            conn.close(); return

        lines: List[str] = [
            f"**Team Summary — Week of {w}**",
            f"🏆 Team Streak: {streak} (Best: {best})",
            "",
        ]
        team_risk    = False
        team_current = 0
        team_target  = 0

        for p in participants:
            uid = p["user_id"]
            cur.execute(
                "SELECT * FROM goals_default WHERE guild_id=%s AND user_id=%s",
                (gid, uid),
            )
            goals = cur.fetchall()
            if not goals:
                lines.append(f"<@{uid}>: No goals set ❌")
                team_risk = True
                continue

            parts: List[str] = []
            for g in goals:
                unit = (g["unit"] or "").strip() if "unit" in g.keys() else ""
                unit_suffix = f" {unit}" if unit else ""

                if g["type"] == "count":
                    if g["log_style"] == "incremental":
                        cur.execute(
                            "SELECT value_total FROM progress "
                            "WHERE guild_id=%s AND user_id=%s AND week_start=%s AND name=%s",
                            (gid, uid, w, g["name"]),
                        )
                        r = cur.fetchone(); val = r["value_total"] if r else 0
                        complete = val >= g["target"]
                        text = f"{g['name']} {val}/{g['target']}{unit_suffix}"
                        if complete:
                            text += " ✅"
                        parts.append(text)
                        team_current += min(val, g["target"])
                        team_target  += g["target"] or 0
                        if not complete:
                            team_risk = True
                    else:
                        cur.execute(
                            "SELECT value FROM finals "
                            "WHERE guild_id=%s AND user_id=%s AND week_start=%s AND name=%s",
                            (gid, uid, w, g["name"]),
                        )
                        r = cur.fetchone(); val = r["value"] if r else 0
                        complete = val >= g["target"]
                        text = f"{g['name']} final: {val}/{g['target']}{unit_suffix}"
                        if complete:
                            text += " ✅"
                        parts.append(text)
                        team_current += min(val, g["target"])
                        team_target  += g["target"] or 0
                        if not complete:
                            team_risk = True
                else:
                    cur.execute(
                        "SELECT done FROM booleans "
                        "WHERE guild_id=%s AND user_id=%s AND week_start=%s AND name=%s",
                        (gid, uid, w, g["name"]),
                    )
                    r = cur.fetchone(); ok = bool(r and r["done"])
                    parts.append(f"{g['name']} {'✅' if ok else '❌'}")
                    team_target += 1
                    if ok:
                        team_current += 1
                    else:
                        team_risk = True

            lines.append(f"<@{uid}>: " + " | ".join(parts))

        progress_pct    = int(round(team_current / team_target * 100)) if team_target else 0
        remaining_units = max(0, team_target - team_current)

        lines.append(f"\n**Team progress:** {team_current}/{team_target} ({progress_pct}%)")
        lines.append(pick_humor_footer(progress_pct, remaining_units, team_risk))

        await interaction.response.send_message("\n".join(lines))
        conn.close()

    @app_commands.command(name="guide", description="Show the Loser Challenge quick guide.")
    async def guide(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="💪 LOSER CHALLENGE QUICK GUIDE",
            description=(
                "**Welcome to the Loser challenge! Here's all you need to know:**\n\n"
                "**Set your goals (one-time):**\n"
                "`/setdefault action:add name:fitness_sessions type:count target:3 "
                "log_style:incremental unit:sessions`\n"
                "_Example: 3 workouts a week._\n"
                "`/setdefault action:list` – check your saved goals.\n\n"
                "**Log your progress:**\n"
                "`/loser name:fitness_sessions amount:1` – adds 1 session.\n"
                "`/complete name:drink_water` – marks a boolean goal done.\n\n"
                "**Check team progress:**\n"
                "`/summary` – see everyone's status and if the team's still safe.\n\n"
                "💀 Everyone wins or loses together.\n"
                "🕓 Goals reset Mondays automatically.\n"
                "🔥 Keep that streak alive!"
            ),
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Dog biscuit + wasabi if we fail 🥵")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(SummaryCog(bot))
