# database.py
import psycopg2
import psycopg2.extras
from config import DATABASE_URL

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Add your PostgreSQL connection string as an environment variable."
    )


def get_db():
    """Open a new PostgreSQL connection with dict-style row access."""
    conn = psycopg2.connect(DATABASE_URL)
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn


def init_db():
    """Create all tables if they don't already exist. Safe to call on every startup."""
    conn = get_db()
    cur = conn.cursor()

    # ── Per-guild configuration ───────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS guild_configs (
            guild_id              BIGINT PRIMARY KEY,
            challenge_channel_id  BIGINT,
            loser_role_id         BIGINT,
            wordle_channel_id     BIGINT,
            missing_channel_id    BIGINT,
            timezone              TEXT DEFAULT 'America/Chicago',
            active                BOOLEAN DEFAULT TRUE
        )
    """)

    # ── Loser Challenge tables ────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            guild_id  BIGINT NOT NULL,
            user_id   BIGINT NOT NULL,
            username  TEXT,
            active    INTEGER DEFAULT 1,
            PRIMARY KEY (guild_id, user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS goals_default (
            guild_id   BIGINT NOT NULL,
            user_id    BIGINT NOT NULL,
            name       TEXT NOT NULL,
            type       TEXT,
            target     INTEGER,
            log_style  TEXT,
            unit       TEXT,
            PRIMARY KEY (guild_id, user_id, name)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            guild_id    BIGINT NOT NULL,
            user_id     BIGINT NOT NULL,
            week_start  TEXT NOT NULL,
            name        TEXT NOT NULL,
            value_total INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id, week_start, name)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS finals (
            guild_id   BIGINT NOT NULL,
            user_id    BIGINT NOT NULL,
            week_start TEXT NOT NULL,
            name       TEXT NOT NULL,
            value      INTEGER,
            PRIMARY KEY (guild_id, user_id, week_start, name)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS booleans (
            guild_id   BIGINT NOT NULL,
            user_id    BIGINT NOT NULL,
            week_start TEXT NOT NULL,
            name       TEXT NOT NULL,
            done       INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id, week_start, name)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS results (
            guild_id       BIGINT NOT NULL,
            week_start     TEXT NOT NULL,
            team_result    TEXT,
            failed_members TEXT,
            PRIMARY KEY (guild_id, week_start)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id         BIGSERIAL PRIMARY KEY,
            guild_id   BIGINT NOT NULL,
            user_id    BIGINT NOT NULL,
            week_start TEXT NOT NULL,
            name       TEXT NOT NULL,
            kind       TEXT NOT NULL,
            delta      INTEGER,
            set_to     INTEGER,
            note       TEXT,
            ts_utc     TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS team_stats (
            guild_id     BIGINT PRIMARY KEY,
            streak       INTEGER DEFAULT 0,
            best_streak  INTEGER DEFAULT 0
        )
    """)

    # ── Wordle tables ─────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS wordle_scores (
            guild_id   BIGINT NOT NULL,
            user_id    BIGINT NOT NULL,
            wordle_num TEXT NOT NULL,
            tries      INTEGER NOT NULL,
            PRIMARY KEY (guild_id, user_id, wordle_num)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS wordle_users (
            guild_id BIGINT NOT NULL,
            user_id  BIGINT NOT NULL,
            joined   BOOLEAN DEFAULT TRUE,
            total    INTEGER DEFAULT 0,
            wins     INTEGER DEFAULT 0,
            waffles  INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS wordle_meta (
            guild_id             BIGINT PRIMARY KEY,
            last_podium          TEXT DEFAULT '{}',
            skip_penalty_days    TEXT DEFAULT '[]',
            last_penalized_day   TEXT DEFAULT ''
        )
    """)

    conn.commit()
    conn.close()


# ── Guild config helpers ──────────────────────────────────────────────────────

def get_guild_config(guild_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM guild_configs WHERE guild_id = %s", (guild_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_active_guilds():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM guild_configs WHERE active = TRUE")
    rows = cur.fetchall()
    conn.close()
    return rows


def upsert_guild_config(guild_id: int, **kwargs):
    """Insert or update fields in guild_configs for the given guild."""
    if not kwargs:
        return
    conn = get_db()
    cur = conn.cursor()
    cols = list(kwargs.keys())
    vals = list(kwargs.values())
    col_str = ", ".join(["guild_id"] + cols)
    ph_str  = ", ".join(["%s"] * (1 + len(cols)))
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
    cur.execute(
        f"INSERT INTO guild_configs ({col_str}) VALUES ({ph_str}) "
        f"ON CONFLICT (guild_id) DO UPDATE SET {set_clause}",
        [guild_id] + vals,
    )
    conn.commit()
    conn.close()


def ensure_team_stats(guild_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO team_stats (guild_id, streak, best_streak) VALUES (%s, 0, 0) "
        "ON CONFLICT DO NOTHING",
        (guild_id,),
    )
    conn.commit()
    conn.close()


def deactivate_guild(guild_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE guild_configs SET active = FALSE WHERE guild_id = %s", (guild_id,))
    conn.commit()
    conn.close()
