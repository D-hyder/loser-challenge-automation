# utils.py
from datetime import datetime, timedelta
import pytz
from config import TIMEZONE


def week_start(tz_name: str = None):
    """Return the Monday date that started the current calendar week."""
    tz = pytz.timezone(tz_name or TIMEZONE)
    now = datetime.now(tz)
    return (now - timedelta(days=now.weekday())).date()
