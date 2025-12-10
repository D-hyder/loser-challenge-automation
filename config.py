# config.py
import os

# Try to load .env locally; ignore if missing on Render
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
        # allow numeric strings only; raise with context
        raise ValueError(f"Environment var {key} must be an integer; got {v!r}")

LOSER_BOT_TOKEN        = os.getenv("LOSER_BOT_TOKEN", "")
TIMEZONE             = os.getenv("TIMEZONE", "America/Chicago")
CHALLENGE_CHANNEL_ID = _int_env("CHALLENGE_CHANNEL_ID", 0)
LOSER_ROLE_ID        = _int_env("LOSER_ROLE_ID", 0)
LOSER_DATA_PATH        = os.getenv("LOSER_DATA_PATH", "/data/loser_data.db")
WORDLE_BOT_TOKEN     = os.getenv("WORDLE_BOT_TOKEN", "")
WORDLE_DATA_PATH     = os.getenv("WORDLE_DATA_PATH", "/data/wordle_scores.json")