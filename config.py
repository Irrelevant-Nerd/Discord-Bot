"""config.py - loads settings from the .env file and provides small helpers."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", encoding="utf-8")

MAX_MESSAGE_LENGTH = 2000  # Discord's limit for one message


class ConfigError(Exception):
    """Raised when something in the .env file is missing or invalid."""


def parse_time(text: str) -> time:
    """Turn '08:00', '14:30', '8:00 AM' or '8am' into a time object."""
    cleaned = " ".join(text.strip().upper().replace(".", "").split())
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
        try:
            return datetime.strptime(cleaned, fmt).time()
        except ValueError:
            continue
    raise ValueError("Please use a time like 08:00, 14:30 or 8:00 AM.")


def get_timezone(name: str) -> ZoneInfo:
    """Return a ZoneInfo for names like 'Asia/Manila', or raise ValueError."""
    try:
        return ZoneInfo(name.strip())
    except Exception as exc:  # ZoneInfoNotFoundError, ValueError, ...
        raise ValueError(
            f"'{name}' is not a valid timezone. "
            "Examples: Asia/Manila, America/New_York, Europe/London"
        ) from exc


@dataclass(frozen=True)
class Config:
    token: str
    default_timezone: str
    default_time: time
    default_message: str
    default_channel_id: int | None
    dev_guild_id: int | None
    database_path: Path
    log_level: str
    catchup_window_minutes: int


def _get_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number (got '{raw}').") from None


def load_config() -> Config:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token or token == "paste-your-bot-token-here":
        raise ConfigError(
            "DISCORD_TOKEN is missing. Copy .env.example to .env and paste "
            "your bot token there."
        )

    tz_name = os.getenv("DEFAULT_TIMEZONE", "").strip() or "Asia/Manila"
    try:
        get_timezone(tz_name)
        default_time = parse_time(os.getenv("DEFAULT_SEND_TIME", "").strip() or "08:00")
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    message = os.getenv("DEFAULT_MESSAGE", "").strip() or "Good morning! Have a great day!"
    if len(message) > MAX_MESSAGE_LENGTH:
        raise ConfigError(f"DEFAULT_MESSAGE is longer than {MAX_MESSAGE_LENGTH} characters.")

    db_path = Path(os.getenv("DATABASE_PATH", "").strip() or "data/bot.db")
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    catchup = _get_int("CATCHUP_WINDOW_MINUTES")
    if catchup is None:
        catchup = 180
    if catchup < 0:
        raise ConfigError("CATCHUP_WINDOW_MINUTES cannot be negative.")

    return Config(
        token=token,
        default_timezone=tz_name,
        default_time=default_time,
        default_message=message,
        default_channel_id=_get_int("DEFAULT_CHANNEL_ID"),
        dev_guild_id=_get_int("DEV_GUILD_ID"),
        database_path=db_path,
        log_level=(os.getenv("LOG_LEVEL", "").strip() or "INFO").upper(),
        catchup_window_minutes=catchup,
    )
