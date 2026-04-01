# cogs/setup.py
import discord
from discord import app_commands
from discord.ext import commands

from database import upsert_guild_config, get_guild_config, ensure_team_stats


class SetupCog(commands.Cog):
    """First-time and ongoing configuration for this server."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Loser Challenge setup ─────────────────────────────────────────────────

    @app_commands.command(
        name="setup",
        description="Configure the Loser Challenge bot for this server (admin only).",
    )
    @app_commands.describe(
        challenge_channel="Channel for weekly Loser Challenge posts",
        loser_role="Role assigned to the team when they lose",
        timezone="Timezone for weekly schedule (default: America/Chicago)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(
        self,
        interaction: discord.Interaction,
        challenge_channel: discord.TextChannel,
        loser_role: discord.Role,
        timezone: str = "America/Chicago",
    ):
        gid = interaction.guild_id
        upsert_guild_config(
            gid,
            challenge_channel_id=challenge_channel.id,
            loser_role_id=loser_role.id,
            timezone=timezone,
            active=True,
        )
        ensure_team_stats(gid)
        await interaction.response.send_message(
            f"✅ **Loser Challenge configured!**\n"
            f"• Challenge channel: {challenge_channel.mention}\n"
            f"• Loser role: {loser_role.mention}\n"
            f"• Timezone: `{timezone}`\n\n"
            f"Run `/wordle_setup` to configure the Wordle tracker.\n"
            f"Members can now use `/join` to sign up.",
            ephemeral=True,
        )

    # ── Wordle setup ──────────────────────────────────────────────────────────

    @app_commands.command(
        name="wordle_setup",
        description="Configure the Wordle tracker for this server (admin only).",
    )
    @app_commands.describe(
        wordle_channel="Channel where Wordle results are shared (bot listens here)",
        missing_channel="Channel for penalty/reminder alerts (defaults to wordle_channel)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def wordle_setup(
        self,
        interaction: discord.Interaction,
        wordle_channel: discord.TextChannel,
        missing_channel: discord.TextChannel = None,
    ):
        gid = interaction.guild_id
        alert_ch = missing_channel or wordle_channel
        upsert_guild_config(
            gid,
            wordle_channel_id=wordle_channel.id,
            missing_channel_id=alert_ch.id,
        )
        await interaction.response.send_message(
            f"✅ **Wordle tracker configured!**\n"
            f"• Wordle channel: {wordle_channel.mention}\n"
            f"• Alert channel: {alert_ch.mention}\n\n"
            f"The bot will track `Wordle X/6` messages in {wordle_channel.mention}.\n"
            f"Players use `!joinwordle` to opt in.",
            ephemeral=True,
        )

    # ── View current config ───────────────────────────────────────────────────

    @app_commands.command(
        name="server_config",
        description="Show the current bot configuration for this server (admin only).",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def server_config(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        cfg = get_guild_config(gid)
        if not cfg:
            await interaction.response.send_message(
                "❌ This server has not been set up yet. Run `/setup` first.",
                ephemeral=True,
            )
            return

        def fmt_channel(cid):
            if not cid:
                return "Not set"
            ch = interaction.guild.get_channel(int(cid))
            return ch.mention if ch else f"Unknown (ID: {cid})"

        def fmt_role(rid):
            if not rid:
                return "Not set"
            r = interaction.guild.get_role(int(rid))
            return r.mention if r else f"Unknown (ID: {rid})"

        await interaction.response.send_message(
            f"**Bot Configuration — {interaction.guild.name}**\n"
            f"• Challenge channel: {fmt_channel(cfg['challenge_channel_id'])}\n"
            f"• Loser role: {fmt_role(cfg['loser_role_id'])}\n"
            f"• Wordle channel: {fmt_channel(cfg['wordle_channel_id'])}\n"
            f"• Alert channel: {fmt_channel(cfg['missing_channel_id'])}\n"
            f"• Timezone: `{cfg['timezone'] or 'America/Chicago'}`\n"
            f"• Active: {cfg['active']}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(SetupCog(bot))
