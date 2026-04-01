#!/usr/bin/env python3
"""
migrate.py — One-time migration from SQLite + JSON to PostgreSQL.

Run this ONCE before deploying the updated bot to preserve all existing data.

Usage
-----
    python migrate.py --guild-id <DISCORD_GUILD_ID>

Optional flags
--------------
    --loser-db PATH          Path to SQLite DB  (default: LOSER_DATA_PATH env)
    --wordle-json PATH        Path to Wordle JSON (default: WORDLE_DATA_PATH env)
    --challenge-channel INT   Override CHALLENGE_CHANNEL_ID env var
    --loser-role INT          Override LOSER_ROLE_ID env var
    --timezone STR            Override TIMEZONE env var  (default: America/Chicago)

How to get your Guild ID
------------------------
In Discord, enable Developer Mode (User Settings → App Settings → Advanced → Developer Mode).
Then right-click your server icon → Copy Server ID.
"""
import argparse
import json
import os
import sqlite3
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import psycopg2
import psycopg2.extras


def main():
    parser = argparse.ArgumentParser(description="Migrate Loser Challenge data to PostgreSQL.")
    parser.add_argument("--guild-id",          required=True, type=int,
                        help="Discord Guild (server) ID")
    parser.add_argument("--loser-db",          default=os.getenv("LOSER_DATA_PATH",  "/data/loser_data.db"),
                        help="Path to SQLite .db file (binary)")
    parser.add_argument("--loser-sql",         default=None,
                        help="Path to SQL dump file (from sqlite3 .dump) — use instead of --loser-db")
    parser.add_argument("--wordle-json",        default=os.getenv("WORDLE_DATA_PATH", "/data/wordle_scores.json"))
    parser.add_argument("--challenge-channel",  type=int,
                        default=int(os.getenv("CHALLENGE_CHANNEL_ID", "0") or "0"))
    parser.add_argument("--loser-role",         type=int,
                        default=int(os.getenv("LOSER_ROLE_ID", "0") or "0"))
    parser.add_argument("--timezone",           default=os.getenv("TIMEZONE", "America/Chicago"))
    args = parser.parse_args()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL environment variable is not set.")
        sys.exit(1)

    guild_id = args.guild_id
    print(f"\n── Migrating data for guild_id={guild_id} ──\n")

    pg = psycopg2.connect(db_url)
    pg.cursor_factory = psycopg2.extras.RealDictCursor
    cur = pg.cursor()

    # Ensure schema is up to date
    from database import init_db
    init_db()
    print("  ✅ PostgreSQL schema initialised")

    # ── 1. Seed guild_configs ─────────────────────────────────────────────────
    cur.execute(
        """
        INSERT INTO guild_configs
            (guild_id, challenge_channel_id, loser_role_id, timezone, active)
        VALUES (%s, %s, %s, %s, TRUE)
        ON CONFLICT (guild_id) DO UPDATE SET
            challenge_channel_id = EXCLUDED.challenge_channel_id,
            loser_role_id        = EXCLUDED.loser_role_id,
            timezone             = EXCLUDED.timezone
        """,
        (
            guild_id,
            args.challenge_channel or None,
            args.loser_role or None,
            args.timezone,
        ),
    )
    print(f"  ✅ guild_configs seeded  "
          f"(channel={args.challenge_channel or 'not set'}, "
          f"role={args.loser_role or 'not set'})")

    # ── 2. Migrate SQLite (Loser Challenge) ───────────────────────────────────
    # Support either a binary .db file or a plain SQL dump file
    loser_db  = args.loser_db
    loser_sql = args.loser_sql

    # Resolve which source to use
    if loser_sql and os.path.exists(loser_sql):
        # Load the SQL dump into an in-memory SQLite database
        import sqlite3 as _sqlite3
        print(f"  📂 Loading SQL dump from {loser_sql!r} into memory…")
        sl = _sqlite3.connect(":memory:")
        sl.row_factory = _sqlite3.Row
        with open(loser_sql, "r", encoding="utf-8") as f:
            sl.executescript(f.read())
        use_sqlite = True
    elif os.path.exists(loser_db):
        import sqlite3 as _sqlite3
        sl = _sqlite3.connect(loser_db)
        sl.row_factory = _sqlite3.Row
        use_sqlite = True
    else:
        use_sqlite = False

    if use_sqlite:
        sl = sqlite3.connect(loser_db)
        sl.row_factory = sqlite3.Row
        sl_cur = sl.cursor()
        # Skip tables that may not exist in a partial dump
        existing_tables = {
            r[0] for r in sl_cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        def migrate_table(table, insert_fn):
            if table not in existing_tables:
                print(f"  ⏭️  {table:<20}: not in dump, skipping")
                return
            rows = sl_cur.execute(f"SELECT * FROM {table}").fetchall()
            for r in rows:
                insert_fn(r)
            print(f"  ✅ {table:<20}: {len(rows)} rows")

        def ins_participants(r):
            cur.execute(
                "INSERT INTO participants (guild_id, user_id, username, active) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (guild_id, r["user_id"], r["username"], r["active"]),
            )
        migrate_table("participants", ins_participants)

        def ins_goals(r):
            cur.execute(
                "INSERT INTO goals_default "
                "(guild_id, user_id, name, type, target, log_style, unit) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (guild_id, r["user_id"], r["name"], r["type"],
                 r["target"], r["log_style"], r["unit"]),
            )
        migrate_table("goals_default", ins_goals)

        def ins_progress(r):
            cur.execute(
                "INSERT INTO progress (guild_id, user_id, week_start, name, value_total) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (guild_id, r["user_id"], r["week_start"], r["name"], r["value_total"]),
            )
        migrate_table("progress", ins_progress)

        def ins_finals(r):
            cur.execute(
                "INSERT INTO finals (guild_id, user_id, week_start, name, value) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (guild_id, r["user_id"], r["week_start"], r["name"], r["value"]),
            )
        migrate_table("finals", ins_finals)

        def ins_booleans(r):
            cur.execute(
                "INSERT INTO booleans (guild_id, user_id, week_start, name, done) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (guild_id, r["user_id"], r["week_start"], r["name"], r["done"]),
            )
        migrate_table("booleans", ins_booleans)

        def ins_results(r):
            cur.execute(
                "INSERT INTO results (guild_id, week_start, team_result, failed_members) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (guild_id, r["week_start"], r["team_result"], r["failed_members"]),
            )
        migrate_table("results", ins_results)

        def ins_logs(r):
            cur.execute(
                "INSERT INTO logs "
                "(guild_id, user_id, week_start, name, kind, delta, set_to, note, ts_utc) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (guild_id, r["user_id"], r["week_start"], r["name"], r["kind"],
                 r["delta"], r["set_to"], r["note"], r["ts_utc"]),
            )
        migrate_table("logs", ins_logs)

        # team_stats (single row — handle separately)
        if "team_stats" in existing_tables:
            row = sl_cur.execute(
                "SELECT streak, best_streak FROM team_stats WHERE id=1"
            ).fetchone()
            if row:
                cur.execute(
                    "INSERT INTO team_stats (guild_id, streak, best_streak) VALUES (%s, %s, %s) "
                    "ON CONFLICT (guild_id) DO UPDATE SET "
                    "streak=EXCLUDED.streak, best_streak=EXCLUDED.best_streak",
                    (guild_id, row["streak"], row["best_streak"]),
                )
                print(f"  ✅ team_stats        : streak={row['streak']}, best={row['best_streak']}")
        else:
            print("  ⏭️  team_stats           : not in dump, skipping")

        sl.close()
    else:
        print(f"  ⚠️  No SQLite source found — skipping Loser Challenge migration")
        cur.execute(
            "INSERT INTO team_stats (guild_id, streak, best_streak) VALUES (%s, 0, 0) "
            "ON CONFLICT DO NOTHING",
            (guild_id,),
        )

    # ── 3. Migrate Wordle JSON ────────────────────────────────────────────────
    wordle_json = args.wordle_json
    if os.path.exists(wordle_json):
        with open(wordle_json, "r") as f:
            scores = json.load(f)

        meta = scores.get("_meta", {})
        cur.execute(
            """
            INSERT INTO wordle_meta
                (guild_id, last_podium, skip_penalty_days, last_penalized_day)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (guild_id) DO UPDATE SET
                last_podium        = EXCLUDED.last_podium,
                skip_penalty_days  = EXCLUDED.skip_penalty_days,
                last_penalized_day = EXCLUDED.last_penalized_day
            """,
            (
                guild_id,
                json.dumps(meta.get("last_podium",
                                    {"gold": [], "silver": [], "bronze": [], "waffle": []})),
                json.dumps(meta.get("skip_penalty_days", [])),
                meta.get("last_penalized_day", ""),
            ),
        )

        user_count  = 0
        score_count = 0
        for uid_str, data in scores.items():
            if uid_str.startswith("_") or not isinstance(data, dict):
                continue
            if "total" not in data or "games" not in data:
                continue

            user_id = int(uid_str)
            cur.execute(
                "INSERT INTO wordle_users (guild_id, user_id, joined, total, wins, waffles) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (guild_id, user_id, data.get("joined", True), data.get("total", 0),
                 data.get("wins", 0), data.get("waffles", 0)),
            )
            user_count += 1

            for wnum, tries in data.get("games", {}).items():
                cur.execute(
                    "INSERT INTO wordle_scores (guild_id, user_id, wordle_num, tries) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (guild_id, user_id, wnum, tries),
                )
                score_count += 1

        print(f"  ✅ wordle_users      : {user_count} rows")
        print(f"  ✅ wordle_scores     : {score_count} rows")
        print(f"  ✅ wordle_meta       : seeded")
    else:
        print(f"  ⚠️  Wordle JSON not found at {wordle_json!r} — skipping Wordle migration")

    pg.commit()
    pg.close()
    print("\n🎉 Migration complete!\n")
    print("Next steps:")
    print("  1. Set DATABASE_URL in your Render environment (if not already done).")
    print("  2. Remove LOSER_DATA_PATH, WORDLE_DATA_PATH, CHALLENGE_CHANNEL_ID,")
    print("     and LOSER_ROLE_ID from your Render environment variables.")
    print("  3. Remove the Render disk mount (data is now in PostgreSQL).")
    print("  4. Deploy the updated code.")
    print("  5. Run /server_config in Discord to verify the config was imported.\n")


if __name__ == "__main__":
    main()
