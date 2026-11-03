"""scheduler.py - decides WHEN to send and actually sends the message.

How it works:
* One APScheduler cron job per server fires at the chosen time and timezone.
* Before sending, the bot "claims" today's date in the database, so only one
  message per day can ever go out, even after crashes or restarts.
* If Discord is down, it retries a few times, then again every 10 minutes.
* When the bot starts, catch_up() sends any message it missed recently.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import discord
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from config import Config, get_timezone
from database import Database

log = logging.getLogger(__name__)

# Results of an attempt to post a message
SENT = "sent"
NO_CHANNEL = "no_channel"
PERMANENT_FAILURE = "permanent_failure"  # e.g. missing permissions, deleted channel
TEMPORARY_FAILURE = "temporary_failure"  # e.g. Discord is down


class DailyScheduler:
    RETRY_DELAY_MINUTES = 10
    QUICK_RETRY_DELAYS = (5, 15, 30)  # seconds between attempts inside one send

    def __init__(self, bot: discord.Client, db: Database, config: Config) -> None:
        self.bot = bot
        self.db = db
        self.config = config
        self.window = timedelta(minutes=config.catchup_window_minutes)
        self.scheduler = AsyncIOScheduler(
            timezone=timezone.utc,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": max(60, config.catchup_window_minutes * 60),
            },
        )

    # ---- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()
            log.info("Scheduler started")

    async def load_all(self) -> None:
        """Create a job for every saved server (this is how the schedule survives restarts)."""
        for guild_id in await self.db.get_all_guild_ids():
            await self.refresh_guild(guild_id)

    def _remove_job(self, job_id: str) -> None:
        try:
            self.scheduler.remove_job(job_id)
        except JobLookupError:
            pass

    def remove_guild_jobs(self, guild_id: int) -> None:
        self._remove_job(f"daily-{guild_id}")
        self._remove_job(f"retry-{guild_id}")

    def _timezone_for(self, settings: dict[str, Any]):
        try:
            return get_timezone(settings["timezone"])
        except ValueError:
            log.error(
                "Server %s has invalid timezone '%s'; using default.",
                settings["guild_id"], settings["timezone"],
            )
            return get_timezone(self.config.default_timezone)

    async def refresh_guild(self, guild_id: int) -> None:
        """(Re)build this server's job from its saved settings. Call after any change."""
        settings = await self.db.get_settings(guild_id)
        if settings is None or settings["paused"]:
            self.remove_guild_jobs(guild_id)
            return

        trigger = CronTrigger(
            hour=settings["send_hour"],
            minute=settings["send_minute"],
            timezone=self._timezone_for(settings),
        )
        self.scheduler.add_job(
            self.send_daily,
            trigger,
            args=[guild_id],
            id=f"daily-{guild_id}",
            replace_existing=True,
        )
        log.info(
            "Scheduled server %s at %02d:%02d (%s)",
            guild_id, settings["send_hour"], settings["send_minute"], settings["timezone"],
        )

    # ---- helpers --------------------------------------------------------------
    @staticmethod
    def pick_message(settings: dict[str, Any]) -> str:
        messages = settings["messages"]
        if settings["mode"] == "random" and len(messages) > 1:
            return random.choice(messages)
        return messages[0]

    @staticmethod
    def _scheduled_today(settings: dict[str, Any], now: datetime) -> datetime:
        return now.replace(
            hour=settings["send_hour"], minute=settings["send_minute"],
            second=0, microsecond=0,
        )

    def next_run(self, settings: dict[str, Any]) -> datetime | None:
        """When will the next message be sent? None if paused."""
        if settings["paused"]:
            return None
        tz = self._timezone_for(settings)
        trigger = CronTrigger(
            hour=settings["send_hour"], minute=settings["send_minute"], timezone=tz
        )
        now = datetime.now(tz)
        fire = trigger.get_next_fire_time(None, now)
        # If today's message already went out, the next one is tomorrow.
        if fire and fire.date().isoformat() == settings["last_sent_date"]:
            fire = trigger.get_next_fire_time(fire, fire + timedelta(minutes=1))
        return fire

    # ---- sending --------------------------------------------------------------
    async def _post(self, settings: dict[str, Any]) -> str:
        """Post the message, retrying briefly if Discord has a hiccup."""
        channel_id = settings["channel_id"]
        if not channel_id:
            return NO_CHANNEL
        text = self.pick_message(settings)
        attempts = len(self.QUICK_RETRY_DELAYS) + 1

        for attempt in range(1, attempts + 1):
            try:
                channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
                if not isinstance(channel, discord.abc.Messageable):
                    log.error("Channel %s cannot receive messages.", channel_id)
                    return PERMANENT_FAILURE
                await channel.send(text)
                return SENT
            except discord.HTTPException as exc:
                # 4xx errors (except 429) will not fix themselves: no permission, channel gone, etc.
                if exc.status < 500 and exc.status != 429:
                    log.error(
                        "Discord refused the message for channel %s (HTTP %s: %s). "
                        "Check the bot's permissions and the channel.",
                        channel_id, exc.status, exc.text,
                    )
                    return PERMANENT_FAILURE
                log.warning("Discord problem (HTTP %s), attempt %s/%s", exc.status, attempt, attempts)
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                log.warning("Network problem (%s), attempt %s/%s", exc, attempt, attempts)

            if attempt < attempts:
                await asyncio.sleep(self.QUICK_RETRY_DELAYS[attempt - 1])
        return TEMPORARY_FAILURE

    async def send_test(self, guild_id: int) -> str:
        """Send a message right now without counting it as today's message."""
        settings = await self.db.get_settings(guild_id)
        if settings is None:
            return NO_CHANNEL
        return await self._post(settings)

    async def send_daily(self, guild_id: int) -> None:
        """Entry point used by the cron job, the retry job and catch_up()."""
        try:
            await self._send_daily(guild_id)
        except Exception:
            log.exception("Unexpected error while sending the daily message for server %s", guild_id)

    async def _send_daily(self, guild_id: int) -> None:
        settings = await self.db.get_settings(guild_id)
        if settings is None or settings["paused"]:
            return
        if not settings["channel_id"]:
            log.warning("Server %s has no channel set; skipping. Use /setchannel.", guild_id)
            return

        tz = self._timezone_for(settings)
        now = datetime.now(tz)
        today = now.date().isoformat()

        if not await self.db.claim_send(guild_id, today):
            log.info("Server %s already got today's message; skipping.", guild_id)
            return

        outcome = await self._post(settings)
        if outcome == SENT:
            log.info("Sent daily message to server %s", guild_id)
            return

        # Failed: free the claim so a retry or the next start can try again.
        await self.db.release_claim(guild_id, today)
        log.error("Could not send daily message to server %s (%s)", guild_id, outcome)

        if outcome == TEMPORARY_FAILURE:
            retry_at = now + timedelta(minutes=self.RETRY_DELAY_MINUTES)
            if retry_at - self._scheduled_today(settings, now) <= self.window:
                self.scheduler.add_job(
                    self.send_daily,
                    DateTrigger(run_date=retry_at),
                    args=[guild_id],
                    id=f"retry-{guild_id}",
                    replace_existing=True,
                    misfire_grace_time=300,
                )
                log.info("Will retry for server %s at %s", guild_id, retry_at.strftime("%H:%M"))

    async def catch_up(self) -> None:
        """After a restart, send today's message if we only just missed it."""
        for guild_id in await self.db.get_all_guild_ids():
            settings = await self.db.get_settings(guild_id)
            if settings is None or settings["paused"] or not settings["channel_id"]:
                continue
            tz = self._timezone_for(settings)
            now = datetime.now(tz)
            late_by = now - self._scheduled_today(settings, now)
            if timedelta(0) <= late_by <= self.window and settings["last_sent_date"] != now.date().isoformat():
                log.info("Catching up on a missed message for server %s", guild_id)
                await self.send_daily(guild_id)
