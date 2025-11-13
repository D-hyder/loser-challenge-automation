# cogs/goals.py — discord.py 2.x (app_commands) version
from typing import Optional, Literal
from datetime import datetime, timedelta, timezone
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

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

class GoalsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- Goal Management ----------

    @app_commands.command(name="setdefault", description="Add, remove, or list your persistent goals.")
    @app_commands.describe(
        action="Choose: add, remove, or list",
        name="Goal name (e.g., fitness, water, stretch)",
        type="count or boolean",
        target="Target number for count goals (e.g., 3, 7)",
        log_style="incremental or weekly_final",
        unit="Optional unit (sessions, days, glasses...)"
    )
    async def setdefault(
        self,
        interaction: discord.Interaction,
        action: Literal["add", "remove", "list"],
        name: Optional[str] = None,
        type: Optional[Literal["count", "boolean"]] = None,
        target: Optional[int] = None,
        log_style: Optional[Literal["incremental", "weekly_final"]] = None,
        unit: Optional[str] = None,
    ):
        conn = get_db(); cur = conn.cursor()
        uid = interaction.user.id

        if action == "add":
            if not (name and type):
                await interaction.response.send_message(
                    "❌ You must specify at least a goal `name` and `type`.",
                    ephemeral=True
                )
                conn.close(); return
            cur.execute("""
                INSERT OR REPLACE INTO goals_default (user_id, name, type, target, log_style, unit)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (uid, name.lower(), type, target or 1, (log_style or "incremental"), unit))
            conn.commit()
            await interaction.response.send_message(
                f"✅ Added/updated `{name}` ({type}, target={target or 1}, style={log_style or 'incremental'})",
                ephemeral=True
            )

        elif action == "remove":
            if not name:
                await interaction.response.send_message("❌ Provide `name` to remove.", ephemeral=True)
                conn.close(); return
            cur.execute("DELETE FROM goals_default WHERE user_id=? AND name=?", (uid, name.lower()))
            conn.commit()
            await interaction.response.send_message(f"🗑️ Removed `{name}`.", ephemeral=True)

        else:  # list
            goals = cur.execute("SELECT * FROM goals_default WHERE user_id=?", (uid,)).fetchall()
            if not goals:
                await interaction.response.send_message(
                    "You have no goals set. Use `/setdefault action:add ...`",
                    ephemeral=True
                )
            else:
                msg = "\n".join([f"• {g['name']} — {g['type']} {g['target']} ({g['log_style']})" for g in goals])
                await interaction.response.send_message(f"Your current goals:\n{msg}", ephemeral=True)
        conn.close()

    # ---------- Weekly override (simple) ----------
    @app_commands.command(name="setweek", description="Override one of your goals for this week only.")
    @app_commands.describe(
        name="Existing goal name",
        target="New target for this week (optional)",
        log_style="Override style for this week (optional)"
    )
    async def setweek(
        self,
        interaction: discord.Interaction,
        name: str,
        target: Optional[int] = None,
        log_style: Optional[Literal["incremental", "weekly_final"]] = None
    ):
        conn = get_db(); cur = conn.cursor()
        uid = interaction.user.id
        g = cur.execute("SELECT * FROM goals_default WHERE user_id=? AND name=?", (uid, name.lower())).fetchone()
        if not g:
            await interaction.response.send_message("❌ You don't have a goal by that name.", ephemeral=True)
            conn.close(); return
        cur.execute("""
            UPDATE goals_default SET target=?, log_style=? WHERE user_id=? AND name=?
        """, (target or g["target"], (log_style or g["log_style"]), uid, name.lower()))
        conn.commit(); conn.close()
        await interaction.response.send_message(
            f"✅ This week: `{name}` → target={target or g['target']}, style={log_style or g['log_style']}",
            ephemeral=True
        )

    # ---------- Logging ----------

    @app_commands.command(
        name="loser",
        description="Log progress: incremental (amount/set_to) or weekly-final (value)."
    )
    @app_commands.describe(
        name="Your goal name (exact as saved)",
        amount="Incremental: add this number (e.g., +1 (default))",
        set_to="Incremental: set your running total to this number",
        value="Weekly-final goals: your final number for this week (e.g., 7)",
        note="Optional note (shown in /history and /me)"
    )
    async def loser(
        self,
        interaction: discord.Interaction,
        name: str,
        amount: Optional[int] = 1,
        set_to: Optional[int] = None,
        value: Optional[int] = None,
        note: Optional[str] = None,
    ):
        uid = interaction.user.id
        w = str(week_start())
        conn = get_db(); cur = conn.cursor()

        # Look up goal definition
        g = cur.execute(
            "SELECT name, type, target, log_style, COALESCE(unit,'') AS unit "
            "FROM goals_default WHERE user_id=? AND name=?",
            (uid, name)
        ).fetchone()

        if not g:
            await interaction.response.send_message(
                f"❌ Goal `{name}` not found. Use `/setdefault action:list`.",
                ephemeral=True  # keep errors private
            )
            conn.close(); return

        goal_name = g["name"]
        gtype     = g["type"]            # 'count' | 'boolean'
        style     = g["log_style"]       # 'incremental' | 'weekly_final'
        target    = g["target"]
        unit      = g["unit"]
        unit_sfx  = f" {unit}".rstrip()

        # Boolean goals: use /complete instead
        if gtype == "boolean":
            await interaction.response.send_message(
                f"ℹ️ `{goal_name}` is a boolean goal. Use `/complete name:{goal_name}` (or `/undo`).",
                ephemeral=True
            )
            conn.close(); return

        # COUNT + INCREMENTAL
        if gtype == "count" and style == "incremental":
            if amount is None and set_to is None:
                await interaction.response.send_message(
                    "❌ Incremental goal needs `amount` (add) or `set_to` (overwrite total).",
                    ephemeral=True
                )
                conn.close(); return

            r = cur.execute(
                "SELECT value_total FROM progress WHERE user_id=? AND week_start=? AND name=?",
                (uid, w, goal_name)
            ).fetchone()
            current = r["value_total"] if r else 0

            if set_to is not None:
                new_total = max(0, int(set_to))
                cur.execute("""
                    INSERT OR REPLACE INTO progress (user_id, week_start, name, value_total)
                    VALUES (?, ?, ?, ?)
                """, (uid, w, goal_name, new_total))
                cur.execute("""
                    INSERT INTO logs (user_id, week_start, name, kind, delta, set_to, note, ts_utc)
                    VALUES (?, ?, ?, 'incremental', NULL, ?, ?, ?)
                """, (uid, w, goal_name, new_total, note, _utc_now_iso()))
                conn.commit()
                msg = (f"**{interaction.user.display_name}** set `{goal_name}` → "
                    f"**{new_total}/{target}**{unit_sfx} (incremental).")
            else:
                add = int(amount) # type: ignore
                new_total = max(0, current + add)
                cur.execute("""
                    INSERT OR REPLACE INTO progress (user_id, week_start, name, value_total)
                    VALUES (?, ?, ?, ?)
                """, (uid, w, goal_name, new_total))
                cur.execute("""
                    INSERT INTO logs (user_id, week_start, name, kind, delta, set_to, note, ts_utc)
                    VALUES (?, ?, ?, 'incremental', ?, NULL, ?, ?)
                """, (uid, w, goal_name, add, note, _utc_now_iso()))
                conn.commit()
                msg = (f"**{interaction.user.display_name}** updated `{goal_name}`: +{add} → "
                    f"**{new_total}/{target}**{unit_sfx} (incremental).")

            if note:
                msg += f"  _{note}_"
            await interaction.response.send_message(msg)  # PUBLIC
            conn.close(); return

        # COUNT + WEEKLY_FINAL
        if gtype == "count" and style == "weekly_final":
            if value is None:
                await interaction.response.send_message(
                    "❌ Weekly-final goal needs `value` (your final number for the week).",
                    ephemeral=True
                )
                conn.close(); return

            final_val = max(0, int(value))
            cur.execute("""
                INSERT OR REPLACE INTO finals (user_id, week_start, name, value)
                VALUES (?, ?, ?, ?)
            """, (uid, w, goal_name, final_val))
            cur.execute("""
                INSERT INTO logs (user_id, week_start, name, kind, delta, set_to, note, ts_utc)
                VALUES (?, ?, ?, 'weekly_final', NULL, ?, ?, ?)
            """, (uid, w, goal_name, final_val, note, _utc_now_iso()))
            conn.commit()

            msg = (f"**{interaction.user.display_name}** set weekly-final `{goal_name}` = "
                f"**{final_val}/{target}**{unit_sfx}.")
            if note:
                msg += f"  _{note}_"
            await interaction.response.send_message(msg)  # PUBLIC
            conn.close(); return

        # fallback
        await interaction.response.send_message("❌ Unsupported goal configuration.", ephemeral=True)
        conn.close()

    @app_commands.command(name="final", description="Submit your total for a weekly-final goal.")
    @app_commands.describe(name="Goal name (weekly_final goal)", value="Your final number for this week")
    async def final(self, interaction: discord.Interaction, name: str, value: int):
        conn = get_db(); cur = conn.cursor()
        uid = interaction.user.id; w = str(week_start())
        cur.execute("""
            INSERT OR REPLACE INTO finals (user_id, week_start, name, value)
            VALUES (?, ?, ?, ?)
        """, (uid, w, name.lower(), value))

        # after upsert into finals
        cur.execute("""
            INSERT INTO logs (user_id, week_start, name, kind, delta, set_to, note, ts_utc)
            VALUES (?, ?, ?, 'weekly_final', NULL, ?, NULL, ?)
        """, (uid, w, name.lower(), value, _utc_now_iso()))
        conn.commit(); conn.close()
        await interaction.response.send_message(f"✅ Final for `{name}` set to {value}", ephemeral=True)

    @app_commands.command(name="complete", description="Mark a boolean goal as complete for this week.")
    @app_commands.describe(name="Boolean goal name")
    async def complete(self, interaction: discord.Interaction, name: str):
        conn = get_db(); cur = conn.cursor()
        uid = interaction.user.id; w = str(week_start())
        cur.execute("""
            INSERT OR REPLACE INTO booleans (user_id, week_start, name, done)
            VALUES (?, ?, ?, 1)
        """, (uid, w, name.lower()))
        # /complete
        cur.execute("""
            INSERT INTO logs (user_id, week_start, name, kind, delta, set_to, note, ts_utc)
            VALUES (?, ?, ?, 'boolean', NULL, 1, NULL, ?)
        """, (uid, w, name.lower(), _utc_now_iso()))
        conn.commit(); conn.close()
        await interaction.response.send_message(f"✅ `{name}` marked complete.", ephemeral=True)

    @app_commands.command(name="undo", description="Undo completion for a boolean goal (this week).")
    @app_commands.describe(name="Boolean goal name")
    async def undo(self, interaction: discord.Interaction, name: str):
        conn = get_db(); cur = conn.cursor()
        uid = interaction.user.id; w = str(week_start())
        cur.execute("DELETE FROM booleans WHERE user_id=? AND week_start=? AND name=?", (uid, w, name.lower()))
        # /undo (boolean)
        cur.execute("""
            INSERT INTO logs (user_id, week_start, name, kind, delta, set_to, note, ts_utc)
            VALUES (?, ?, ?, 'undo', NULL, NULL, NULL, ?)
        """, (uid, w, name.lower(), _utc_now_iso()))
        conn.commit(); conn.close()
        await interaction.response.send_message(f"↩️ `{name}` undone for this week.", ephemeral=True)

    # ---------- Personal summary/history ----------

    @app_commands.command(name="me", description="Show your goals and current progress for this week.")
    async def me(self, interaction: discord.Interaction):
        conn = get_db(); cur = conn.cursor()
        uid = interaction.user.id; w = str(week_start())

        goals = cur.execute("SELECT * FROM goals_default WHERE user_id=?", (uid,)).fetchall()
        if not goals:
            await interaction.response.send_message(
                "You have no goals set. Use `/setdefault action:add ...`",
                ephemeral=True
            )
            conn.close(); return

        lines = [f"**Your Goals – Week of {w}**"]
        for g in goals:
            if g["type"] == "count":
                if g["log_style"] == "incremental":
                    r = cur.execute("SELECT value_total FROM progress WHERE user_id=? AND week_start=? AND name=?", (uid, w, g["name"])).fetchone()
                    val = r["value_total"] if r else 0

                    # ✅ Fetch last note (incremental)
                    rnote = cur.execute("""
                        SELECT note FROM logs
                        WHERE user_id=? AND week_start=? AND name=? AND note IS NOT NULL AND note <> ''
                        ORDER BY id DESC LIMIT 1
                    """, (uid, w, g["name"].lower())).fetchone()
                    suffix = f" _(Last note: {rnote['note']})_" if rnote else ""

                    lines.append(f"• {g['name']} – {val}/{g['target']} (incremental){suffix}")

                else:
                    r = cur.execute("SELECT value FROM finals WHERE user_id=? AND week_start=? AND name=?", (uid, w, g["name"])).fetchone()
                    val = r["value"] if r else 0

                    # ✅ Fetch last note (final)
                    rnote = cur.execute("""
                        SELECT note FROM logs
                        WHERE user_id=? AND week_start=? AND name=? AND note IS NOT NULL AND note <> ''
                        ORDER BY id DESC LIMIT 1
                    """, (uid, w, g["name"].lower())).fetchone()
                    suffix = f" _(Last note: {rnote['note']})_" if rnote else ""

                    lines.append(f"• {g['name']} – final: {val}/{g['target']}{suffix}")

            else:
                r = cur.execute("SELECT done FROM booleans WHERE user_id=? AND week_start=? AND name=?", (uid, w, g["name"])).fetchone()
                done = bool(r and r["done"])

                # ✅ Fetch last note (boolean)
                rnote = cur.execute("""
                    SELECT note FROM logs
                    WHERE user_id=? AND week_start=? AND name=? AND note IS NOT NULL AND note <> ''
                    ORDER BY id DESC LIMIT 1
                """, (uid, w, g["name"].lower())).fetchone()
                suffix = f" _(Last note: {rnote['note']})_" if rnote else ""

                lines.append(f"• {g['name']} – {'✅' if done else '❌'}{suffix}")


        await interaction.response.send_message("\n".join(lines), ephemeral=True)
        conn.close()

    @app_commands.command(name="history", description="Show your log history for this week (with notes).")
    @app_commands.describe(
        name="Filter by goal name (optional)",
        limit="Max entries to show (default 10, max 50)"
    )
    async def history(self, interaction: discord.Interaction, name: Optional[str] = None, limit: Optional[int] = 10):
        conn = get_db(); cur = conn.cursor()
        uid = interaction.user.id
        w = str(week_start())
        lim = max(1, min(limit or 10, 50))

        if name:
            rows = cur.execute("""
                SELECT name, kind, delta, set_to, note, ts_utc
                FROM logs
                WHERE user_id=? AND week_start=? AND name=?
                ORDER BY id DESC
                LIMIT ?
            """, (uid, w, name.lower(), lim)).fetchall()
        else:
            rows = cur.execute("""
                SELECT name, kind, delta, set_to, note, ts_utc
                FROM logs
                WHERE user_id=? AND week_start=?
                ORDER BY id DESC
                LIMIT ?
            """, (uid, w, lim)).fetchall()

        if not rows:
            await interaction.response.send_message(
                "No history yet for this week." + (f" (goal: `{name}`)" if name else ""),
                ephemeral=True
            )
            conn.close(); return

        # Build a compact list
        lines = []
        for r in rows:
            goal = r["name"]
            kind = r["kind"]
            ts   = r["ts_utc"].replace("T", " ") + " UTC"
            if kind == "incremental":
                body = f"+{r['delta']}" if r["delta"] is not None else f"set→{r['set_to']}"
            elif kind == "weekly_final":
                body = f"final={r['set_to']}"
            elif kind == "boolean":
                body = "complete ✅"
            else:  # undo
                body = "undo ↩️"

            note = f" — _{r['note']}_" if r["note"] else ""
            lines.append(f"• **{goal}** — {body}{note}  ·  `{ts}`")

        # Reply (ephemeral to avoid channel spam)
        await interaction.response.send_message("\n".join(lines), ephemeral=True)
        conn.close()    

async def setup(bot: commands.Bot):
    await bot.add_cog(GoalsCog(bot))
