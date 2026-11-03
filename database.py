"""database.py - stores each server's settings in a SQLite file.

Because everything is saved here, nothing is lost when the bot restarts.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import aiosqlite

from config import Config

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id       INTEGER PRIMARY KEY,
    channel_id     INTEGER,
    send_hour      INTEGER NOT NULL,
    send_minute    INTEGER NOT NULL,
    timezone       TEXT    NOT NULL,
    messages       TEXT    NOT NULL,
    mode           TEXT    NOT NULL DEFAULT 'same',
    paused         INTEGER NOT NULL DEFAULT 0,
    last_sent_date TEXT
)
"""

# Only these columns may be changed through update() (protects against typos/injection).
EDITABLE = {"channel_id", "send_hour", "send_minute", "timezone", "messages", "mode", "paused"}


class Database:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.path = config.database_path

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute(SCHEMA)
            await conn.commit()
        log.info("Database ready at %s", self.path)

    @staticmethod
    def _to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        data = dict(row)
        data["messages"] = json.loads(data["messages"])
        data["paused"] = bool(data["paused"])
        return data

    async def ensure_guild(self, guild_id: int) -> None:
        """Create default settings for a server the first time we see it."""
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                """INSERT OR IGNORE INTO guild_settings
                   (guild_id, channel_id, send_hour, send_minute, timezone, messages, mode, paused)
                   VALUES (?, ?, ?, ?, ?, ?, 'same', 0)""",
                (
                    guild_id,
                    self.config.default_channel_id,
                    self.config.default_time.hour,
                    self.config.default_time.minute,
                    self.config.default_timezone,
                    json.dumps([self.config.default_message]),
                ),
            )
            await conn.commit()

    async def get_settings(self, guild_id: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
            )
            row = await cursor.fetchone()
        return self._to_dict(row) if row else None

    async def get_all_guild_ids(self) -> list[int]:
        async with aiosqlite.connect(self.path) as conn:
            cursor = await conn.execute("SELECT guild_id FROM guild_settings")
            rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def update(self, guild_id: int, **fields: Any) -> None:
        if not fields:
            return
        unknown = set(fields) - EDITABLE
        if unknown:
            raise ValueError(f"Cannot edit column(s): {', '.join(sorted(unknown))}")
        if "messages" in fields:
            fields["messages"] = json.dumps(fields["messages"])
        if "paused" in fields:
            fields["paused"] = int(bool(fields["paused"]))

        assignments = ", ".join(f"{col} = ?" for col in fields)
        values = list(fields.values()) + [guild_id]
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                f"UPDATE guild_settings SET {assignments} WHERE guild_id = ?", values
            )
            await conn.commit()

    # ---- duplicate protection -------------------------------------------------
    async def claim_send(self, guild_id: int, date_str: str) -> bool:
        """Try to reserve today's message. Returns True only for the first caller.

        This is a single atomic UPDATE, so even if the scheduler and the
        'catch-up after restart' logic fire at the same moment, only one wins.
        """
        async with aiosqlite.connect(self.path) as conn:
            cursor = await conn.execute(
                """UPDATE guild_settings SET last_sent_date = ?
                   WHERE guild_id = ?
                     AND (last_sent_date IS NULL OR last_sent_date != ?)""",
                (date_str, guild_id, date_str),
            )
            await conn.commit()
            return cursor.rowcount == 1

    async def release_claim(self, guild_id: int, date_str: str) -> None:
        """Undo a claim when sending failed, so it can be tried again."""
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                "UPDATE guild_settings SET last_sent_date = NULL "
                "WHERE guild_id = ? AND last_sent_date = ?",
                (guild_id, date_str),
            )
            await conn.commit()
