import sqlite3
from config import LOSER_DATA_PATH

def get_db():
    conn = sqlite3.connect(LOSER_DATA_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS participants (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        active INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS goals_default (
        user_id INTEGER,
        name TEXT,
        type TEXT,              -- 'count' | 'boolean'
        target INTEGER,         -- for 'count'
        log_style TEXT,         -- 'incremental' | 'weekly_final'
        unit TEXT,
        PRIMARY KEY (user_id, name)
    );

    CREATE TABLE IF NOT EXISTS progress (
        user_id INTEGER,
        week_start TEXT,
        name TEXT,
        value_total INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, week_start, name)
    );

    CREATE TABLE IF NOT EXISTS finals (
        user_id INTEGER,
        week_start TEXT,
        name TEXT,
        value INTEGER,
        PRIMARY KEY (user_id, week_start, name)
    );

    CREATE TABLE IF NOT EXISTS booleans (
        user_id INTEGER,
        week_start TEXT,
        name TEXT,
        done INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, week_start, name)
    );

    CREATE TABLE IF NOT EXISTS results (
        week_start TEXT PRIMARY KEY,
        team_result TEXT,       -- 'WIN' | 'FAIL'
        failed_members TEXT
    );
                      
    CREATE TABLE IF NOT EXISTS logs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        week_start TEXT    NOT NULL,
        name       TEXT    NOT NULL,
        kind       TEXT    NOT NULL, -- 'incremental' | 'weekly_final' | 'boolean' | 'undo'
        delta      INTEGER,          -- e.g., +1 or None
        set_to     INTEGER,          -- e.g., 7 for final/set
        note       TEXT,
        ts_utc     TEXT    NOT NULL  -- ISO timestamp in UTC
    );
                  
    -- Single-row table for team streak
    CREATE TABLE IF NOT EXISTS team_stats (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        streak INTEGER DEFAULT 0,
        best_streak INTEGER DEFAULT 0
    );
    INSERT OR IGNORE INTO team_stats (id, streak, best_streak) VALUES (1, 0, 0);
    """)
    conn.commit()
    conn.close()
