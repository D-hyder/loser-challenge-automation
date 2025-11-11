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

DISCORD_TOKEN        = os.getenv("DISCORD_TOKEN", "")
TIMEZONE             = os.getenv("TIMEZONE", "America/Chicago")
CHALLENGE_CHANNEL_ID = _int_env("CHALLENGE_CHANNEL_ID", 0)
LOSER_ROLE_ID        = _int_env("LOSER_ROLE_ID", 0)
DATABASE_PATH        = os.getenv("DATABASE_PATH", "data/loser_data.db")



# DISCORD_TOKEN = "YOUR_DMTQzNDY2NDY1MzI0OTU4MTExNw.GTAg68.OTqtahhLv7g4ylujUSOgYH3-g3QsKd79sSMoTU"
# TIMEZONE = "America/Chicago"
# CHALLENGE_CHANNEL_ID = 988292940474515507  # right-click your #loser-challenge channel → Copy ID
# LOSER_ROLE_ID = 982434020816207893        # right-click the Loser role → Copy ID
# DATABASE_PATH = "/opt/render/project/src/data/loser_data.db"      # local; will change for Render
