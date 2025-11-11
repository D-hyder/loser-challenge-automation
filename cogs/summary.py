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

class SummaryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="summary", description="Show the team progress for this week.")
    async def summary(self, interaction: discord.Interaction):
        conn = get_db(); cur = conn.cursor()
        w = str(week_start())

        ts = cur.execute("SELECT streak, best_streak FROM team_stats WHERE id=1").fetchone()
        streak, best = (ts["streak"], ts["best_streak"]) if ts else (0, 0)

        participants = cur.execute("SELECT * FROM participants WHERE active=1").fetchall()
        if not participants:
            await interaction.response.send_message("No active participants.", ephemeral=True)
            conn.close()
            return

        lines: List[str] = [
            f"**Team Progress — Week of {w}**",
            f"🏆 Team Streak: {streak} (Best: {best})",
            ""
        ]
        team_risk = False

        for p in participants:
            uid = p["user_id"]
            goals = cur.execute("SELECT * FROM goals_default WHERE user_id=?", (uid,)).fetchall()
            if not goals:
                lines.append(f"<@{uid}>: No goals set ❌")
                team_risk = True
                continue

            parts: List[str] = []
            for g in goals:
                if g["type"] == "count":
                    if g["log_style"] == "incremental":
                        r = cur.execute(
                            "SELECT value_total FROM progress WHERE user_id=? AND week_start=? AND name=?",
                            (uid, w, g["name"])
                        ).fetchone()
                        val = r["value_total"] if r else 0
                        parts.append(f"{g['name']} {val}/{g['target']}")
                        if val < g["target"]:
                            team_risk = True
                    else:
                        r = cur.execute(
                            "SELECT value FROM finals WHERE user_id=? AND week_start=? AND name=?",
                            (uid, w, g["name"])
                        ).fetchone()
                        val = r["value"] if r else 0
                        parts.append(f"{g['name']} final: {val}/{g['target']}")
                        if val < g["target"]:
                            team_risk = True
                else:
                    r = cur.execute(
                        "SELECT done FROM booleans WHERE user_id=? AND week_start=? AND name=?",
                        (uid, w, g["name"])
                    ).fetchone()
                    ok = bool(r and r["done"])
                    parts.append(f"{g['name']} {'✅' if ok else '❌'}")
                    if not ok:
                        team_risk = True

            lines.append(f"<@{uid}>: " + " | ".join(parts))

        lines.append(
            "\n⚠️ **Team at risk!** At least one goal isn’t met yet."
            if team_risk else
            "\n✅ **All on track!** Keep the streak alive."
        )

        await interaction.response.send_message("\n".join(lines))
        conn.close()

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
