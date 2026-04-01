# config.py
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _int_env(key: str, default: int | None = None) -> int | None:
    v = os.getenv(key)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError:
        raise ValueError(f"Environment var {key} must be an integer; got {v!r}")


# ── Core tokens & DB ──────────────────────────────────────────────────────────
LOSER_BOT_TOKEN  = os.getenv("LOSER_BOT_TOKEN", "")
WORDLE_BOT_TOKEN = os.getenv("WORDLE_BOT_TOKEN", "")
DATABASE_URL     = os.getenv("DATABASE_URL", "")
TIMEZONE         = os.getenv("TIMEZONE", "America/Chicago")

# ── Legacy single-guild vars — used only by migrate.py ───────────────────────
CHALLENGE_CHANNEL_ID = _int_env("CHALLENGE_CHANNEL_ID", 0)
LOSER_ROLE_ID        = _int_env("LOSER_ROLE_ID", 0)
LOSER_DATA_PATH      = os.getenv("LOSER_DATA_PATH", "/data/loser_data.db")
WORDLE_DATA_PATH     = os.getenv("WORDLE_DATA_PATH", "/data/wordle_scores.json")
