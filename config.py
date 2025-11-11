# config.py
import os
from dotenv import load_dotenv
load_dotenv()

def _int_env(name: str, default: int = 0) -> int:
    val = os.getenv(name, "")
    try:
        return int(val) if val else int(default)
    except ValueError:
        print(f"WARNING: {name}='{val}' is not an integer; using {default}")
        return int(default)

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
