# cogs/summary.py
from datetime import datetime, timedelta
from typing import List
import pytz
import discord
from discord import app_commands
from discord.ext import commands

from database import get_db
from config import TIMEZONE

tz = pytz.timezone(TIMEZONE)

def week_start():
    now = datetime.now(tz)
    return (now - timedelta(days=now.weekday())).date()

def pick_humor_footer(progress_pct: int, remaining_units: int) -> str:
    """
    Deterministic humorous footer based on:
      - progress_pct (0–100)
      - remaining_units (how many 'units' of goals are left overall)
      - day of week (Mon–Sun)
    """
    weekday = datetime.now().weekday()  # Monday=0, Sunday=6

    # Clamp values
    progress_pct = max(0, min(progress_pct, 100))
    remaining_units = max(0, remaining_units)

    # Remaining text
    if remaining_units == 0:
        remaining_text = "All goals complete."
    elif remaining_units == 1:
        remaining_text = "1 unit left."
    else:
        remaining_text = f"{remaining_units} units left."

    # 100% done: same message for any day
    if progress_pct >= 100:
        return f"🎉 No wasabi biscuit this week — pack it up, Gordon Ramsay! ({remaining_text})"

    # ---------- EARLY WEEK: Monday–Tuesday ----------
    if weekday <= 1:
        if progress_pct == 0:
            msg = "🤓 Week just started — act like your life is together."
        elif progress_pct < 30:
            msg = "🍗 Light work now prevents wasabi horror later."
        else:
            msg = "🛌 Decent start, but if you’re still in bed, at least log your goals."
        return f"{msg} ({remaining_text})"

    # ---------- MIDWEEK: Wednesday–Thursday ----------
    if 2 <= weekday <= 3:
        if progress_pct < 30:
            msg = "😵 Midweek slump detected — wake up, gang."
        elif progress_pct < 70:
            msg = "💀 We’re doing… okay? Kinda? Maybe?"
        else:
            msg = "🫠 Solid progress, but I still wouldn’t bet my life on it."
        return f"{msg} ({remaining_text})"

    # ---------- LATE WEEK: Friday–Saturday ----------
    if 4 <= weekday <= 5:
        if progress_pct < 50:
            msg = "🍪 Stand firm — the biscuit draws near. And it’s got your name on it."
        elif progress_pct < 80:
            msg = "🧨 Team is one bad day away from chaos."
        else:
            msg = "🔥 Almost safe — don’t you dare fumble now."
        return f"{msg} ({remaining_text})"

    # ---------- SUNDAY ----------
    # weekday == 6
    if progress_pct < 50:
        msg = "📣 SUNDAY PANIC TIME — everybody log SOMETHING."
    elif progress_pct < 80:
        msg = "😭 If you ruin this on the last day, we riot."
    else:
        msg = "🫵 Don’t make us eat wasabi dog biscuits because of YOU."
    return f"{msg} ({remaining_text})"


class GoalsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="summary",
        description="Show team progress for this week."
    )
    async def summary(self, interaction: discord.Interaction):
        conn = get_db(); cur = conn.cursor()
        w = str(week_start())

        lines = [f"**Team Summary – Week of {w}**"]

        # Team-level totals
        team_current_total = 0   # numerator
        team_target_total = 0    # denominator

        participants = cur.execute("""
            SELECT DISTINCT user_id FROM goals_default
        """).fetchall()

        if not participants:
            await interaction.response.send_message(
                "No goals have been set yet.", ephemeral=True
            )
            conn.close()
            return

        for row in participants:
            uid = row["user_id"]
            guild = interaction.guild
            if guild:
                member = guild.get_member(uid)
            else:
                member = None
            display = member.display_name if member else f"User {uid}"

            user_goals = cur.execute("""
                SELECT name, type, target, log_style, COALESCE(unit,'') AS unit
                FROM goals_default
                WHERE user_id=?
                ORDER BY name
            """, (uid,)).fetchall()

            if not user_goals:
                continue

            lines.append(f"\n__**{display}**__")

            for g in user_goals:
                goal_name = g["name"]
                gtype     = g["type"]
                target    = g["target"]
                style     = g["log_style"]
                unit      = g["unit"]
                unit_sfx  = f" {unit}".rstrip()

                is_complete = False
                current     = 0
                goal_target = 0
                text        = ""

                # COUNT GOALS
                if gtype == "count":
                    goal_target = target or 0

                    if style == "incremental":
                        r = cur.execute("""
                            SELECT value_total FROM progress
                            WHERE user_id=? AND week_start=? AND name=?
                        """, (uid, w, goal_name)).fetchone()
                        current = r["value_total"] if r else 0
                        is_complete = current >= target
                        text = f"{current}/{target}{unit_sfx}"

                    elif style == "weekly_final":
                        r = cur.execute("""
                            SELECT value FROM finals
                            WHERE user_id=? AND week_start=? AND name=?
                        """, (uid, w, goal_name)).fetchone()
                        current = r["value"] if r else 0
                        is_complete = current >= target
                        text = f"{current}/{target}{unit_sfx}"

                    # Add to team totals
                    team_target_total += goal_target
                    team_current_total += current

                # BOOLEAN GOALS
                elif gtype == "boolean":
                    r = cur.execute("""
                        SELECT done FROM booleans
                        WHERE user_id=? AND week_start=? AND name=?
                    """, (uid, w, goal_name)).fetchone()
                    done = bool(r and r["done"])
                    is_complete = done
                    text = "done" if done else "not done"

                    # Boolean counts as 1/1
                    goal_target = 1
                    current = 1 if done else 0

                    team_target_total += goal_target
                    team_current_total += current

                status_emoji = "✅" if is_complete else "⬜"
                lines.append(f"{status_emoji} `{goal_name}` — {text}")

        # Compute team totals
        if team_target_total > 0:
            progress_ratio = team_current_total / team_target_total
        else:
            progress_ratio = 0.0

        progress_pct = int(round(progress_ratio * 100))
        remaining_units = max(0, team_target_total - team_current_total)

        lines.append("")
        lines.append(
            f"**Team progress:** {team_current_total}/{team_target_total} total units ({progress_pct}%)"
        )

        footer = pick_humor_footer(progress_pct, remaining_units)
        lines.append(footer)

        conn.close()
        await interaction.response.send_message("\n".join(lines))

    @app_commands.command(name="guide", description="Show Loser Challenge guide")
    async def guide(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="💪 LOSER CHALLENGE QUICK GUIDE",
            description=(
                "**Welcome to the Loser challenge! Here's all you need to know:**\n\n"
                "**Set your goals (one-time):**\n"
                "`/setdefault action:add name:fitness_sessions type:count target:3 log_style:incremental unit:sessions`\n"
                "_Example: 3 workouts a week._\n"
                "`/setdefault action:list` – check your saved goals.\n\n"
                "**Log your progress:**\n"
                "`/loser name:fitness_sessions value:1` – adds 1 session.\n"
                "`/loser name:gallon_water done:true` – marks weekly goal complete.\n\n"
                "**Check team progress:**\n"
                "`/summary` – see everyone’s status and if the team’s still safe.\n\n"
                "💀 Everyone wins or loses together.\n"
                "🕓 Goals reset Mondays automatically.\n"
                "🔥 Keep that streak alive!"
            ),
            color=discord.Color.orange()
        )
        embed.set_footer(text="Dog biscuit + wasabi if we fail 🥵")
        await interaction.response.send_message(embed=embed)

# async setup so Pylance stops warning and discord.py can await add_cog
async def setup(bot: commands.Bot):
    await bot.add_cog(SummaryCog(bot))
