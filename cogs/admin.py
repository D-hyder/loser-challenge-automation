# cogs/admin.py
import io
import json
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from database import get_db
from scheduler import post_weekly_message, evaluate_week, reset_week, backup_now


# ── Backup / restore helpers ──────────────────────────────────────────────────

def _export_guild_data(guild_id: int) -> dict:
    """Dump all Loser Challenge rows for a guild into a plain dict."""
    conn = get_db()
    cur  = conn.cursor()
    data: dict = {"guild_id": guild_id}
    for table in (
        "participants", "goals_default", "progress",
        "finals", "booleans", "results", "team_stats",
    ):
        cur.execute(f"SELECT * FROM {table} WHERE guild_id = %s", (guild_id,))
        data[table] = [dict(r) for r in cur.fetchall()]
    conn.close()
    return data


def _import_guild_data(guild_id: int, data: dict):
    """Restore Loser Challenge rows for a guild from a previously exported dict."""
    conn = get_db()
    cur  = conn.cursor()

    for table in (
        "participants", "goals_default", "progress",
        "finals", "booleans", "results", "team_stats",
    ):
        cur.execute(f"DELETE FROM {table} WHERE guild_id = %s", (guild_id,))

    # Re-insert everything except logs (logs table omitted from backup to keep size small)
    for table in (
        "participants", "goals_default", "progress",
        "finals", "booleans", "results", "team_stats",
    ):
        for row in data.get(table, []):
            cols   = list(row.keys())
            vals   = list(row.values())
            col_str = ", ".join(cols)
            ph_str  = ", ".join(["%s"] * len(cols))
            cur.execute(
                f"INSERT INTO {table} ({col_str}) VALUES ({ph_str}) ON CONFLICT DO NOTHING",
                vals,
            )

    conn.commit()
    conn.close()


# ── Cog ───────────────────────────────────────────────────────────────────────

class AdminCog(commands.Cog):
    """Admin & participation utilities for Loser Challenge."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Test / debug commands (admin only) ────────────────────────────────────

    @app_commands.command(name="test_post", description="(Admin) Post Monday kickoff now.")
    @app_commands.checks.has_permissions(administrator=True)
    async def test_post(self, interaction: discord.Interaction):
        await interaction.response.send_message("Posting weekly message…", ephemeral=True)
        await post_weekly_message(self.bot)

    @app_commands.command(name="test_eval", description="(Admin) Run end-of-week evaluation now.")
    @app_commands.checks.has_permissions(administrator=True)
    async def test_eval(self, interaction: discord.Interaction):
        await interaction.response.send_message("Running evaluation…", ephemeral=True)
        await evaluate_week(self.bot)

    @app_commands.command(name="test_reset", description="(Admin) Run Monday reset now.")
    @app_commands.checks.has_permissions(administrator=True)
    async def test_reset(self, interaction: discord.Interaction):
        await interaction.response.send_message("Resetting week…", ephemeral=True)
        await reset_week(self.bot)

    @app_commands.command(name="test_backup", description="(Admin) Run backup now.")
    @app_commands.checks.has_permissions(administrator=True)
    async def test_backup(self, interaction: discord.Interaction):
        await interaction.response.send_message("Creating backup…", ephemeral=True)
        await backup_now(self.bot)

    # ── Participation ─────────────────────────────────────────────────────────

    @app_commands.command(name="join", description="Join the weekly Loser Challenge.")
    async def join(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO participants (guild_id, user_id, username, active) VALUES (%s, %s, %s, 1) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET active=1, username=EXCLUDED.username",
            (gid, interaction.user.id, interaction.user.name),
        )
        conn.commit(); conn.close()
        await interaction.response.send_message(
            f"✅ {interaction.user.mention} joined the Loser Challenge!", ephemeral=True
        )

    @app_commands.command(name="leave", description="Leave the challenge (you can rejoin anytime).")
    async def leave(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "UPDATE participants SET active=0 WHERE guild_id=%s AND user_id=%s",
            (gid, interaction.user.id),
        )
        conn.commit(); conn.close()
        await interaction.response.send_message(
            f"👋 {interaction.user.mention} left the Loser Challenge.", ephemeral=True
        )

    @app_commands.command(name="skipweek", description="Opt out for this week only.")
    async def skipweek(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "DELETE FROM participants WHERE guild_id=%s AND user_id=%s",
            (gid, interaction.user.id),
        )
        conn.commit(); conn.close()
        await interaction.response.send_message(
            f"⏸️ {interaction.user.mention} is skipping this week.", ephemeral=True
        )

    # ── Config info ───────────────────────────────────────────────────────────

    @app_commands.command(name="config", description="How to configure this bot.")
    @app_commands.checks.has_permissions(administrator=True)
    async def config(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "ℹ️ **Bot configuration** is managed via slash commands:\n"
            "• `/setup` — set challenge channel, loser role, and timezone\n"
            "• `/wordle_setup` — set Wordle and alert channels\n"
            "• `/server_config` — view current settings\n\n"
            "**Required environment variables (set in Render dashboard):**\n"
            "• `LOSER_BOT_TOKEN`, `WORDLE_BOT_TOKEN`, `DATABASE_URL`\n"
            "• `TIMEZONE` (optional, default `America/Chicago`)",
            ephemeral=True,
        )

    # ── Backup / restore ──────────────────────────────────────────────────────

    @app_commands.command(
        name="backup",
        description="Export a JSON backup of this server's Loser Challenge data.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def backup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid  = interaction.guild_id
        data = _export_guild_data(gid)
        ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        fn   = f"loser_backup_{ts}.json"
        buf  = io.BytesIO(json.dumps(data, indent=2, default=str).encode())
        await interaction.followup.send(
            "💾 Backup attached:",
            file=discord.File(buf, filename=fn),
            ephemeral=True,
        )

    @app_commands.command(
        name="listbackups",
        description="Show recent weekly results for this server.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def listbackups(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "SELECT week_start, team_result, failed_members FROM results "
            "WHERE guild_id=%s ORDER BY week_start DESC LIMIT 10",
            (gid,),
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            await interaction.response.send_message(
                "No week results recorded yet.", ephemeral=True
            )
            return

        lines = []
        for r in rows:
            failed = r["failed_members"] or "none"
            lines.append(
                f"• `{r['week_start']}` — **{r['team_result']}** (failed: {failed})"
            )
        await interaction.response.send_message(
            "**Recent Week Results:**\n" + "\n".join(lines), ephemeral=True
        )

    @app_commands.command(
        name="restore",
        description="Restore Loser Challenge data from a backup JSON file (admin only).",
    )
    @app_commands.describe(backup_file="The .json file produced by /backup")
    @app_commands.checks.has_permissions(administrator=True)
    async def restore(
        self, interaction: discord.Interaction, backup_file: discord.Attachment
    ):
        if not backup_file.filename.endswith(".json"):
            await interaction.response.send_message(
                "❌ Please attach a `.json` file produced by `/backup`.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        raw  = await backup_file.read()
        data = json.loads(raw)

        gid = interaction.guild_id
        if data.get("guild_id") != gid:
            await interaction.followup.send(
                "❌ This backup belongs to a different server.", ephemeral=True
            )
            return

        _import_guild_data(gid, data)
        await interaction.followup.send(
            "✅ Data restored from backup.\n"
            "Tip: run `/test_reset` if you want to clear weekly progress.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
