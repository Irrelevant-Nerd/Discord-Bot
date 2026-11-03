"""main.py - starts the bot. Run with:  python main.py"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

import discord
from discord.ext import commands

from commands import MorningCog  # our own commands.py (not discord.ext.commands)
from config import BASE_DIR, Config, ConfigError, load_config
from database import Database
from scheduler import DailyScheduler

log = logging.getLogger("morning-bot")

EXIT_CONFIG_PROBLEM = 2  # run_bot.bat does not restart on this code


def setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name, logging.INFO)
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)

    formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        log_dir / "bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file_handler)
    logging.getLogger("apscheduler").setLevel(logging.INFO)


class MorningBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        # Default intents are enough: we never read message text.
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        self.config = config
        self.db = Database(config)
        self.daily: DailyScheduler | None = None
        self._started = False

    async def setup_hook(self) -> None:
        await self.db.init()
        self.daily = DailyScheduler(self, self.db, self.config)
        await self.add_cog(MorningCog(self))
        await self._sync_commands()

    async def _sync_commands(self) -> None:
        try:
            if self.config.dev_guild_id:
                guild = discord.Object(id=self.config.dev_guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                log.info("Slash commands synced to dev server %s", self.config.dev_guild_id)
            else:
                await self.tree.sync()
                log.info("Slash commands synced globally")
        except discord.HTTPException:
            log.exception("Could not sync slash commands; they may be missing or out of date.")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id %s)", self.user, self.user.id)
        if self._started:  # on_ready can fire again after a reconnect
            return
        self._started = True

        for guild in self.guilds:
            await self.db.ensure_guild(guild.id)
        self.daily.start()
        await self.daily.load_all()
        await self.daily.catch_up()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Joined server %s (%s)", guild.name, guild.id)
        await self.db.ensure_guild(guild.id)
        await self.daily.refresh_guild(guild.id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info("Removed from server %s (%s)", guild.name, guild.id)
        self.daily.remove_guild_jobs(guild.id)  # settings stay in the database


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration problem: {exc}")
        sys.exit(EXIT_CONFIG_PROBLEM)

    setup_logging(config.log_level)
    bot = MorningBot(config)
    try:
        bot.run(config.token, log_handler=None)  # we set up logging ourselves
    except discord.LoginFailure:
        log.critical("Discord rejected the token. Reset it in the Developer Portal and update .env.")
        sys.exit(EXIT_CONFIG_PROBLEM)


if __name__ == "__main__":
    main()
