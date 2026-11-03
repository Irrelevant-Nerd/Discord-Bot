"""commands.py - the slash commands (admin-only)."""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import MAX_MESSAGE_LENGTH, get_timezone, parse_time
from scheduler import NO_CHANNEL, PERMANENT_FAILURE, SENT, TEMPORARY_FAILURE

log = logging.getLogger(__name__)

MAX_MESSAGES_IN_LIST = 50

# Decorators that every command shares: server-only + administrators only.
# default_permissions hides the commands from non-admins in Discord's menu;
# has_permissions is the real check that runs when someone tries anyway.


class MorningCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ---- helpers --------------------------------------------------------------
    async def _settings(self, interaction: discord.Interaction) -> dict:
        await self.bot.db.ensure_guild(interaction.guild_id)
        return await self.bot.db.get_settings(interaction.guild_id)

    @staticmethod
    def _time_text(settings: dict) -> str:
        return f"{settings['send_hour']:02d}:{settings['send_minute']:02d}"

    def _next_text(self, settings: dict) -> str:
        if settings["paused"]:
            return "Paused (no message scheduled)"
        nxt = self.bot.daily.next_run(settings)
        if nxt is None:
            return "Unknown"
        return f"{discord.utils.format_dt(nxt, 'F')} ({discord.utils.format_dt(nxt, 'R')})"

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            text = "You need the **Administrator** permission to use this command."
        elif isinstance(error, app_commands.NoPrivateMessage):
            text = "This command only works inside a server."
        else:
            log.error("Command error", exc_info=error)
            text = "Something went wrong. Please try again; details were written to the log."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    # ---- /setmessage ----------------------------------------------------------
    @app_commands.command(name="setmessage", description="Set or change the daily message")
    @app_commands.describe(
        message="The message to send. Type \\n for a new line.",
        add_to_list="Add to the list of messages instead of replacing everything",
        mode="Same message every day, or a random one from the list",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Same message every day", value="same"),
            app_commands.Choice(name="Random message from the list", value="random"),
        ]
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def setmessage(
        self,
        interaction: discord.Interaction,
        message: app_commands.Range[str, 1, MAX_MESSAGE_LENGTH],
        add_to_list: bool = False,
        mode: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        settings = await self._settings(interaction)
        text = message.replace("\\n", "\n")

        if add_to_list:
            if len(settings["messages"]) >= MAX_MESSAGES_IN_LIST:
                await interaction.response.send_message(
                    f"The list is full ({MAX_MESSAGES_IN_LIST} messages). "
                    "Use /setmessage without add_to_list to start a new list.",
                    ephemeral=True,
                )
                return
            messages = settings["messages"] + [text]
        else:
            messages = [text]

        new_mode = mode.value if mode else settings["mode"]
        await self.bot.db.update(interaction.guild_id, messages=messages, mode=new_mode)

        reply = f"Saved. You now have **{len(messages)}** message(s). Mode: **{new_mode}**."
        if len(messages) > 1 and new_mode == "same":
            reply += (
                "\nThe mode is 'same', so only the first message is used. "
                "Run /setmessage again with mode set to random to rotate them."
            )
        await interaction.response.send_message(reply, ephemeral=True)

    # ---- /settime -------------------------------------------------------------
    @app_commands.command(name="settime", description="Change the time the message is sent")
    @app_commands.describe(
        time="Time of day, like 08:00, 14:30 or 8:00 AM",
        timezone="Optional timezone, like Asia/Manila (leave empty to keep the current one)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def settime(
        self,
        interaction: discord.Interaction,
        time: str,
        timezone: Optional[str] = None,
    ) -> None:
        settings = await self._settings(interaction)
        try:
            parsed = parse_time(time)
            tz_name = settings["timezone"]
            if timezone:
                get_timezone(timezone)  # validates
                tz_name = timezone.strip()
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await self.bot.db.update(
            interaction.guild_id,
            send_hour=parsed.hour, send_minute=parsed.minute, timezone=tz_name,
        )
        await self.bot.daily.refresh_guild(interaction.guild_id)
        settings = await self.bot.db.get_settings(interaction.guild_id)
        await interaction.response.send_message(
            f"Daily time set to **{self._time_text(settings)}** ({tz_name}).\n"
            f"Next message: {self._next_text(settings)}",
            ephemeral=True,
        )

    # ---- /setchannel ----------------------------------------------------------
    @app_commands.command(name="setchannel", description="Choose the channel for the daily message")
    @app_commands.describe(channel="The channel to post in (leave empty to use this channel)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def setchannel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                "Please pick a normal text channel.", ephemeral=True
            )
            return

        perms = target.permissions_for(interaction.guild.me)
        missing = [
            name for name, ok in (
                ("View Channel", perms.view_channel),
                ("Send Messages", perms.send_messages),
            ) if not ok
        ]
        if missing:
            await interaction.response.send_message(
                f"I can't post in {target.mention}. I'm missing: **{', '.join(missing)}**. "
                "Fix the channel permissions and try again.",
                ephemeral=True,
            )
            return

        await self._settings(interaction)  # make sure the row exists
        await self.bot.db.update(interaction.guild_id, channel_id=target.id)
        await self.bot.daily.refresh_guild(interaction.guild_id)
        await interaction.response.send_message(
            f"Daily messages will be posted in {target.mention}.", ephemeral=True
        )

    # ---- /testmessage ---------------------------------------------------------
    @app_commands.command(name="testmessage", description="Send a test message right now")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def testmessage(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)  # sending may take a few seconds
        settings = await self._settings(interaction)
        outcome = await self.bot.daily.send_test(interaction.guild_id)

        if outcome == SENT:
            text = f"Test message sent to <#{settings['channel_id']}>. (It does not count as today's message.)"
        elif outcome == NO_CHANNEL:
            text = "No channel is set yet. Use /setchannel first."
        elif outcome == PERMANENT_FAILURE:
            text = ("Discord refused the message. Check that the bot can **View Channel** "
                    "and **Send Messages** there, and that the channel still exists.")
        elif outcome == TEMPORARY_FAILURE:
            text = "Discord seems to be having problems right now. Try again in a few minutes."
        else:
            text = "Unknown result; check the log."
        await interaction.followup.send(text, ephemeral=True)

    # ---- /status --------------------------------------------------------------
    @app_commands.command(name="status", description="Show the current settings and next message")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction) -> None:
        s = await self._settings(interaction)
        embed = discord.Embed(title="Morning message status", colour=discord.Colour.blurple())
        embed.add_field(name="State", value="Paused" if s["paused"] else "Active", inline=True)
        embed.add_field(
            name="Channel",
            value=f"<#{s['channel_id']}>" if s["channel_id"] else "Not set (use /setchannel)",
            inline=True,
        )
        embed.add_field(name="Time", value=f"{self._time_text(s)} ({s['timezone']})", inline=True)
        embed.add_field(
            name="Mode",
            value="Random from list" if s["mode"] == "random" else "Same every day",
            inline=True,
        )
        embed.add_field(name="Last sent", value=s["last_sent_date"] or "Never", inline=True)
        embed.add_field(name="Next message", value=self._next_text(s), inline=False)

        shown = []
        for i, msg in enumerate(s["messages"][:5], start=1):
            short = msg if len(msg) <= 100 else msg[:97] + "..."
            shown.append(f"{i}. {short}")
        if len(s["messages"]) > 5:
            shown.append(f"...and {len(s['messages']) - 5} more")
        embed.add_field(name=f"Messages ({len(s['messages'])})", value="\n".join(shown), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---- /pause and /resume ---------------------------------------------------
    @app_commands.command(name="pause", description="Temporarily stop the daily messages")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def pause(self, interaction: discord.Interaction) -> None:
        await self._settings(interaction)
        await self.bot.db.update(interaction.guild_id, paused=True)
        await self.bot.daily.refresh_guild(interaction.guild_id)
        await interaction.response.send_message(
            "Daily messages are **paused**. Use /resume to turn them back on.", ephemeral=True
        )

    @app_commands.command(name="resume", description="Resume the daily messages")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def resume(self, interaction: discord.Interaction) -> None:
        await self._settings(interaction)
        await self.bot.db.update(interaction.guild_id, paused=False)
        await self.bot.daily.refresh_guild(interaction.guild_id)
        settings = await self.bot.db.get_settings(interaction.guild_id)
        await interaction.response.send_message(
            f"Daily messages **resumed**.\nNext message: {self._next_text(settings)}",
            ephemeral=True,
        )
