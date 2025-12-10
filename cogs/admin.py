# cogs/admin.py
from pathlib import Path
from datetime import datetime
import shutil
import discord
from discord import app_commands
from discord.ext import commands

from database import get_db
from config import LOSER_DATA_PATH
from scheduler import post_weekly_message, evaluate_week, reset_week, backup_now

class AdminCog(commands.Cog):
    """Admin & participation utilities for Loser Challenge."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---- TEMP TEST COMMANDS (admin only) ----
    @app_commands.command(name="test_post", description="(Admin) Post Monday kickoff now")
    @app_commands.checks.has_permissions(administrator=True)
    async def test_post(self, interaction: discord.Interaction):
        await interaction.response.send_message("Posting weekly message…", ephemeral=True)
        await post_weekly_message(self.bot)

    @app_commands.command(name="test_eval", description="(Admin) Run end-of-week evaluation now")
    @app_commands.checks.has_permissions(administrator=True)
    async def test_eval(self, interaction: discord.Interaction):
        await interaction.response.send_message("Running evaluation…", ephemeral=True)
        await evaluate_week(self.bot)

    @app_commands.command(name="test_reset", description="(Admin) Run Monday reset now")
    @app_commands.checks.has_permissions(administrator=True)
    async def test_reset(self, interaction: discord.Interaction):
        await interaction.response.send_message("Resetting week…", ephemeral=True)
        await reset_week(self.bot)

    @app_commands.command(name="test_backup", description="(Admin) Run backup now")
    @app_commands.checks.has_permissions(administrator=True)
    async def test_backup(self, interaction: discord.Interaction):
        await interaction.response.send_message("Backing up DB…", ephemeral=True)
        await backup_now(self.bot)

    # --- Participation ---

    @app_commands.command(name="join", description="Join the weekly Loser Challenge.")
    async def join(self, interaction: discord.Interaction):
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO participants (user_id, username, active) VALUES (?, ?, 1)",
            (interaction.user.id, interaction.user.name),
        )
        conn.commit(); conn.close()
        await interaction.response.send_message(
            f"✅ {interaction.user.mention} joined the Loser Challenge!", ephemeral=True
        )

    @app_commands.command(name="leave", description="Leave the challenge (you can rejoin anytime).")
    async def leave(self, interaction: discord.Interaction):
        conn = get_db(); cur = conn.cursor()
        cur.execute("UPDATE participants SET active=0 WHERE user_id=?", (interaction.user.id,))
        conn.commit(); conn.close()
        await interaction.response.send_message(
            f"👋 {interaction.user.mention} left the Loser Challenge.", ephemeral=True
        )

    @app_commands.command(name="skipweek", description="Opt out for this week only.")
    async def skipweek(self, interaction: discord.Interaction):
        conn = get_db(); cur = conn.cursor()
        cur.execute("DELETE FROM participants WHERE user_id=?", (interaction.user.id,))
        conn.commit(); conn.close()
        await interaction.response.send_message(
            f"⏸️ {interaction.user.mention} is skipping this week.", ephemeral=True
        )

    # --- Config note (IDs are managed via env vars) ---

    @app_commands.command(name="config", description="Info-only: show how to set channel/role/vars.")
    @app_commands.describe(
        timezone="Your timezone label, e.g., America/Chicago",
        cutoff_sun="Sunday cutoff in 24h HH:MM, e.g., 23:59"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def config(
        self,
        interaction: discord.Interaction,
        timezone: str = "America/Chicago",
        cutoff_sun: str = "23:59",
    ):
        await interaction.response.send_message(
            "ℹ️ Config is controlled by **environment variables** in your host (Render):\n"
            "• `CHALLENGE_CHANNEL_ID` (TextChannel ID)\n"
            "• `LOSER_ROLE_ID` (Role ID)\n"
            "• `TIMEZONE` (e.g., America/Chicago)\n"
            "• `DATABASE_PATH` (e.g., /opt/render/project/src/data/loser_data.db)\n\n"
            f"Preview — timezone: `{timezone}`, Sunday cutoff: `{cutoff_sun}`.",
            ephemeral=True,
        )

    # --- Backups (admin only) ---

    @app_commands.command(name="backup", description="Save the database now (manual snapshot).")
    @app_commands.checks.has_permissions(administrator=True)
    async def backup(self, interaction: discord.Interaction):
        p = Path(DATABASE_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        backup_name = p.parent / f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        try:
            shutil.copy(p, backup_name)
        except FileNotFoundError:
            await interaction.response.send_message("❌ DB file not found.", ephemeral=True)
            return
        await interaction.response.send_message(f"💾 Backup saved: `{backup_name.name}`", ephemeral=True)

    @app_commands.command(name="listbackups", description="List available DB backups.")
    @app_commands.checks.has_permissions(administrator=True)
    async def listbackups(self, interaction: discord.Interaction):
        base = Path(DATABASE_PATH).parent
        backups = sorted(base.glob("backup_*.db"), reverse=True)[:20]
        if not backups:
            await interaction.response.send_message("No backups found.", ephemeral=True)
            return
        lines = "\n".join(f"• {b.name}" for b in backups)
        await interaction.response.send_message("Available backups:\n" + lines, ephemeral=True)

    @app_commands.command(name="restore", description="Restore DB from a backup file (admin only).")
    @app_commands.describe(backup_filename="Filename shown in /listbackups")
    @app_commands.checks.has_permissions(administrator=True)
    async def restore(self, interaction: discord.Interaction, backup_filename: str):
        base = Path(DATABASE_PATH).parent

        # basic filename guard
        if not (backup_filename.startswith("backup_") and backup_filename.endswith(".db")):
            await interaction.response.send_message("❌ Invalid filename. Use one from `/listbackups`.", ephemeral=True)
            return

        src = base / backup_filename
        if not src.exists():
            await interaction.response.send_message("❌ Backup not found. Use `/listbackups`.", ephemeral=True)
            return

        cur_db = Path(DATABASE_PATH)
        safety = base / f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"

        try:
            if cur_db.exists():
                shutil.copy(cur_db, safety)  # safety copy of current DB
            shutil.copy(src, cur_db)         # restore selected backup
        except Exception as e:
            await interaction.response.send_message(f"❌ Restore failed: {e}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ Restored from `{backup_filename}`.\n"
            f"(Safety copy: `{safety.name}` if previous DB existed)\n"
            f"Please **restart the service** so the bot reopens the DB cleanly.",
            ephemeral=True,
        )


# discord.py 2.x expects an async setup when add_cog is a coroutine.
async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
