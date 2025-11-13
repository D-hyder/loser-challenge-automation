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

    @app_commands.command(
        name="setdefault",
        description="Add, remove, or list your default goals."
    )
    @app_commands.describe(
        action="What do you want to do? (add/remove/list)",
        name="Goal name (e.g., Gym, Water, No_sugar)",
        goal_type="For add: count = numeric, boolean = done/not-done",
        target="For count goals: how many per week (e.g., 3, 7, 10)",
        log_style="For count goals: incremental = /loser, weekly_final = /final",
        unit="Optional unit label (sessions, days, miles, pages...)"
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add / update goal", value="add"),
            app_commands.Choice(name="Remove goal", value="remove"),
            app_commands.Choice(name="List my goals", value="list"),
        ],
        goal_type=[
            app_commands.Choice(name="Count (numeric)", value="count"),
            app_commands.Choice(name="Boolean (done/not done)", value="boolean"),
        ],
        log_style=[
            app_commands.Choice(name="Incremental (use /loser)", value="incremental"),
            app_commands.Choice(name="Weekly final (use /final)", value="weekly_final"),
        ],
    )
    async def setdefault(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        name: Optional[str] = None,
        goal_type: Optional[app_commands.Choice[str]] = None,
        target: Optional[int] = None,
        log_style: Optional[app_commands.Choice[str]] = None,
        unit: Optional[str] = None,
    ):
        uid = interaction.user.id
        conn = get_db(); cur = conn.cursor()

        # ---- LIST ----
        if action.value == "list":
            rows = cur.execute(
                "SELECT name, type, target, log_style, COALESCE(unit,'') AS unit "
                "FROM goals_default WHERE user_id=? ORDER BY name",
                (uid,)
            ).fetchall()

            if not rows:
                await interaction.response.send_message(
                    "You have no default goals set.", ephemeral=True
                )
                conn.close(); return

            lines = ["**Your default goals:**"]
            for r in rows:
                t = r["type"]
                style = r["log_style"] or ""
                unit_label = f" {r['unit']}".rstrip()

                if t == "count":
                    lines.append(
                        f"• `{r['name']}` — count, target **{r['target']}**{unit_label} "
                        f"({style})"
                    )
                else:
                    lines.append(
                        f"• `{r['name']}` — boolean (uses `/complete`)"
                    )

            await interaction.response.send_message("\n".join(lines), ephemeral=True)
            conn.close(); return

        # ---- REMOVE ----
        if action.value == "remove":
            if not name:
                await interaction.response.send_message(
                    "❌ You must provide `name` to remove a goal.",
                    ephemeral=True
                )
                conn.close(); return

            cur.execute(
                "DELETE FROM goals_default WHERE user_id=? AND name=?",
                (uid, name)
            )
            conn.commit()
            await interaction.response.send_message(
                f"🗑️ Removed default goal `{name}` (if it existed).",
                ephemeral=True
            )
            conn.close(); return

        # ---- ADD / UPDATE ----
        if action.value == "add":
            if not name:
                await interaction.response.send_message(
                    "❌ You must provide `name` when adding a goal.",
                    ephemeral=True
                )
                conn.close(); return

            if not goal_type:
                await interaction.response.send_message(
                    "❌ You must choose `goal_type` (count or boolean) when adding a goal.",
                    ephemeral=True
                )
                conn.close(); return

            gtype = goal_type.value  # 'count' | 'boolean'

            # BOOLEAN GOALS: force weekly_final and ignore target/log_style/unit
            if gtype == "boolean":
                # We can store target NULL or 1; it doesn't matter for behavior.
                cur.execute("""
                    INSERT OR REPLACE INTO goals_default (user_id, name, type, target, log_style, unit)
                    VALUES (?, ?, 'boolean', NULL, 'weekly_final', NULL)
                """, (uid, name))
                conn.commit()
                await interaction.response.send_message(
                    f"✅ Saved boolean goal `{name}`.\n"
                    f"• Use `/complete name:{name}` to mark it done each week.\n"
                    f"• Use `/undo name:{name}` to reverse it.",
                    ephemeral=True
                )
                conn.close(); return

            # COUNT GOALS
            if gtype == "count":
                if target is None or target <= 0:
                    await interaction.response.send_message(
                        "❌ Count goals need a positive `target` (e.g., 3, 5, 7).",
                        ephemeral=True
                    )
                    conn.close(); return

                # Determine style: default to incremental if none provided
                style_value = log_style.value if log_style else "incremental"
                if style_value not in ("incremental", "weekly_final"):
                    await interaction.response.send_message(
                        "❌ Invalid log_style for count goal. Choose incremental or weekly_final.",
                        ephemeral=True
                    )
                    conn.close(); return

                unit_value = unit.strip() if unit else None

                cur.execute("""
                    INSERT OR REPLACE INTO goals_default (user_id, name, type, target, log_style, unit)
                    VALUES (?, ?, 'count', ?, ?, ?)
                """, (uid, name, target, style_value, unit_value))
                conn.commit()

                if style_value == "incremental":
                    text = (
                        f"✅ Saved count goal `{name}`: target **{target}**"
                        f"{(' ' + unit_value) if unit_value else ''} per week "
                        f"(incremental — use `/loser`)."
                    )
                else:
                    text = (
                        f"✅ Saved count goal `{name}`: target **{target}**"
                        f"{(' ' + unit_value) if unit_value else ''} per week "
                        f"(weekly-final — use `/final`)."
                    )

                await interaction.response.send_message(text, ephemeral=True)
                conn.close(); return

        # If something weird slips through:
        await interaction.response.send_message(
            "❌ Unsupported `action` for /setdefault.", ephemeral=True
        )
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
        description="Log progress: incremental-style goals (amount/set_to)."
    )
    @app_commands.describe(
        name="Your goal name (as saved)",
        amount="Add this number (e.g., +1 (default))",
        set_to="Set your running total to this number",
        note="Optional note (shown in /history and /me)"
    )
    async def loser(
        self,
        interaction: discord.Interaction,
        name: str,
        amount: Optional[int] = 1,
        set_to: Optional[int] = None,
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
        
        # Weekly-final count goals: use /final instead
        if gtype == "count" and style == "weekly_final":
            await interaction.response.send_message(
                f"ℹ️ `{goal_name}` is a weekly-final goal. Use `/final name:{goal_name} value:<number>`.",
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

            # current total
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

        # fallback
        await interaction.response.send_message("❌ Unsupported goal configuration.", ephemeral=True)
        conn.close()

    @app_commands.command(
        name="final",
        description="Set the final weekly value for a weekly-final count goal."
    )
    @app_commands.describe(
        name="Your weekly-final goal name (exact as saved)",
        value="Your final number for this week (e.g., 7)",
        note="Optional note (shown in /history and /me)"
    )
    async def final(
        self,
        interaction: discord.Interaction,
        name: str,
        value: int,
        note: Optional[str] = None,
    ):
        uid = interaction.user.id
        w = str(week_start())
        conn = get_db(); cur = conn.cursor()

        g = cur.execute(
            "SELECT name, type, target, log_style, COALESCE(unit,'') AS unit "
            "FROM goals_default WHERE user_id=? AND name=?",
            (uid, name)
        ).fetchone()

        if not g:
            await interaction.response.send_message(
                f"❌ Goal `{name}` not found. Use `/setdefault action:list`.",
                ephemeral=True
            )
            conn.close(); return

        goal_name = g["name"]
        gtype     = g["type"]          # 'count' | 'boolean'
        style     = g["log_style"]     # should be 'weekly_final'
        target    = g["target"]
        unit      = g["unit"]
        unit_sfx  = f" {unit}".rstrip()

        if gtype != "count":
            await interaction.response.send_message(
                f"ℹ️ `{goal_name}` is not a count goal. Use `/complete` for boolean goals.",
                ephemeral=True
            )
            conn.close(); return

        if style != "weekly_final":
            await interaction.response.send_message(
                f"ℹ️ `{goal_name}` is not configured as weekly-final. "
                f"Use `/loser` for incremental updates instead.",
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
        conn.close()

        msg = (
            f"**{interaction.user.display_name}** set weekly-final `{goal_name}` = "
            f"**{final_val}/{target}**{unit_sfx}."
        )
        if note:
            msg += f"  _{note}_"

        # PUBLIC
        await interaction.response.send_message(msg)

    @app_commands.command(
        name="complete",
        description="Mark a boolean goal as complete for this week."
    )
    @app_commands.describe(
        name="Your boolean goal name (exact as saved)",
        note="Optional note (shown in /history and /me)"
    )
    async def complete(
        self,
        interaction: discord.Interaction,
        name: str,
        note: Optional[str] = None,
    ):
        uid = interaction.user.id
        w = str(week_start())
        conn = get_db(); cur = conn.cursor()

        g = cur.execute(
            "SELECT name, type, log_style FROM goals_default WHERE user_id=? AND name=?",
            (uid, name)
        ).fetchone()

        if not g:
            await interaction.response.send_message(
                f"❌ Goal `{name}` not found. Use `/setdefault action:list`.",
                ephemeral=True
            )
            conn.close(); return

        goal_name = g["name"]
        gtype     = g["type"]

        if gtype != "boolean":
            await interaction.response.send_message(
                f"ℹ️ `{goal_name}` is not a boolean goal. Use `/loser` or `/final` for count goals.",
                ephemeral=True
            )
            conn.close(); return

        # mark as done
        cur.execute("""
            INSERT OR REPLACE INTO booleans (user_id, week_start, name, done)
            VALUES (?, ?, ?, 1)
        """, (uid, w, goal_name))

        cur.execute("""
            INSERT INTO logs (user_id, week_start, name, kind, delta, set_to, note, ts_utc)
            VALUES (?, ?, ?, 'boolean', NULL, 1, ?, ?)
        """, (uid, w, goal_name, note, _utc_now_iso()))

        conn.commit()
        conn.close()

        msg = f"**{interaction.user.display_name}** completed boolean goal `{goal_name}` ✅."
        if note:
            msg += f"  _{note}_"

        # PUBLIC
        await interaction.response.send_message(msg)


    @app_commands.command(
        name="undo",
        description="Undo completion of a boolean goal for this week."
    )
    @app_commands.describe(
        name="Your boolean goal name (exact as saved)"
    )
    async def undo(
        self,
        interaction: discord.Interaction,
        name: str,
    ):
        uid = interaction.user.id
        w = str(week_start())
        conn = get_db(); cur = conn.cursor()

        g = cur.execute(
            "SELECT name, type FROM goals_default WHERE user_id=? AND name=?",
            (uid, name)
        ).fetchone()

        if not g:
            await interaction.response.send_message(
                f"❌ Goal `{name}` not found. Use `/setdefault action:list`.",
                ephemeral=True
            )
            conn.close(); return

        goal_name = g["name"]
        gtype     = g["type"]

        if gtype != "boolean":
            await interaction.response.send_message(
                f"ℹ️ `{goal_name}` is not a boolean goal. `/undo` only applies to boolean goals.",
                ephemeral=True
            )
            conn.close(); return

        # delete completion
        cur.execute("""
            DELETE FROM booleans
            WHERE user_id=? AND week_start=? AND name=?
        """, (uid, w, goal_name))

        cur.execute("""
            INSERT INTO logs (user_id, week_start, name, kind, delta, set_to, note, ts_utc)
            VALUES (?, ?, ?, 'undo', NULL, NULL, NULL, ?)
        """, (uid, w, goal_name, _utc_now_iso()))

        conn.commit()
        conn.close()

        # PUBLIC
        await interaction.response.send_message(
            f"**{interaction.user.display_name}** undid completion for `{goal_name}` ↩️."
        )


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
