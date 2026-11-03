# Morning Discord Bot

A Discord bot that posts one message every morning to a channel you choose.
Admins control it with slash commands. Settings are saved in SQLite, so nothing
is lost when the bot restarts.

## Files

| File | What it does |
|---|---|
| `main.py` | Starts the bot, sets up logging, syncs slash commands |
| `config.py` | Reads the `.env` file, checks times and timezones |
| `database.py` | Saves settings in SQLite and prevents duplicate daily messages |
| `scheduler.py` | Schedules and sends the message, retries on errors, catches up after restarts |
| `commands.py` | The 7 slash commands |
| `.env.example` | Template for your private settings |
| `requirements.txt` | Python packages to install |
| `run_bot.bat` | Windows launcher that restarts the bot if it crashes |

## 1. Create the Discord application and bot

1. Go to https://discord.com/developers/applications and log in.
2. Click **New Application**, name it, and accept the terms.
3. Open the **Bot** tab. Click **Reset Token** and copy the token. Discord only shows it once.
4. Leave all three **Privileged Gateway Intents** switched off. This bot doesn't need them.
5. Open **OAuth2 -> URL Generator**.
   - Scopes: tick `bot` and `applications.commands`.
   - Bot Permissions: tick `View Channels` and `Send Messages`.
6. Copy the generated URL at the bottom, open it in your browser, pick your server, and click **Authorize**. You need the *Manage Server* permission on that server.

## 2. Install Python and the packages

1. Install Python 3.11 or newer from https://www.python.org/downloads/. On the first installer screen, tick **Add python.exe to PATH**.
2. Open PowerShell in the project folder and run:

```
python --version
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell says scripts are disabled, run this once and try again:

```
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 3. Add your token

```
copy .env.example .env
notepad .env
```

Replace `paste-your-bot-token-here` with your token (no quotes, no spaces) and save.
Never share `.env` or upload it anywhere. The included `.gitignore` keeps it out of GitHub.

Optional but handy while testing: set `DEV_GUILD_ID` to your server's ID so slash commands show up instantly.
(Discord Settings -> Advanced -> turn on Developer Mode, then right-click your server icon -> Copy Server ID.)

## 4. Run and test

```
python main.py
```

You should see `Logged in as ...` and `Slash commands synced`. In your server:

1. `/setchannel` and pick a channel.
2. `/testmessage` to check the bot can post.
3. `/status` to see your settings and the next scheduled time.
4. To test the real schedule, run `/settime` with a time 2 minutes from now and wait.

Stop the bot with `Ctrl+C`.

## Commands (administrators only)

| Command | What it does |
|---|---|
| `/setmessage message [add_to_list] [mode]` | Replace the daily message, or add to a list. `mode` is "same" (always the first message) or "random". Use `\n` for a new line. |
| `/settime time [timezone]` | Change the time (`08:00`, `14:30`, `8:00 AM`) and optionally the timezone (`Asia/Manila`). |
| `/setchannel [channel]` | Choose the channel (defaults to the channel you're in). |
| `/testmessage` | Post a message right now. Doesn't count as today's message. |
| `/status` | Show settings and the next scheduled message. |
| `/pause` / `/resume` | Stop or restart the daily messages. |

Non-admins can't see these commands, and the bot blocks them even if they find a way to run them.

## How duplicates are prevented

Before sending, the bot writes today's date (in your timezone) to the database in one atomic step. If the date is already there, it skips. This works across crashes, restarts, and the "catch-up" feature. If sending fails, the date is cleared so it can try again.

If the bot was off at 8:00 and starts again within `CATCHUP_WINDOW_MINUTES` (default 180), it sends the missed message once.

If Discord is down, it retries 4 times quickly, then every 10 minutes until the catch-up window ends.

## 5. Keep it running 24/7

**Option A: your own Windows PC (free, only runs while the PC is on and awake)**

1. Run the bot with `run_bot.bat` (double-click it). It restarts the bot 10 seconds after a crash.
2. To start it automatically: open **Task Scheduler -> Create Task**.
   - General: name it, tick **Run only when user is logged on**.
   - Triggers: **At log on**.
   - Actions: Start a program -> browse to `run_bot.bat`, set **Start in** to the project folder.
   - Conditions: untick "Start the task only if the computer is on AC power" if it's a laptop.

**Option B: a cloud host (runs without your PC)**

Services like Railway, Fly.io or a small VPS can run `python main.py` and restart it on failure. Prices and free tiers change, so check the current ones. Things to know:

- Put your settings in the host's **environment variables** instead of uploading `.env`.
- Start command: `python main.py`
- SQLite is a file. On most hosts the disk is wiped on every deploy unless you attach a **persistent volume**. Mount one (for example at `/data`) and set `DATABASE_PATH=/data/bot.db`.
- Make sure the host uses Python 3.11 or newer.

**Option C: a Linux VPS with systemd (auto-restart)**

Create `/etc/systemd/system/morning-bot.service`:

```
[Unit]
Description=Morning Discord Bot
After=network-online.target

[Service]
WorkingDirectory=/home/youruser/morning-bot
ExecStart=/home/youruser/morning-bot/.venv/bin/python main.py
Restart=always
RestartSec=10
User=youruser

[Install]
WantedBy=multi-user.target
```

Then `sudo systemctl enable --now morning-bot`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `DISCORD_TOKEN is missing` | You didn't create `.env`, or the token line is still the placeholder. |
| `Discord rejected the token` | Reset the token in the Developer Portal and paste the new one into `.env`. |
| `ModuleNotFoundError` | The virtual environment isn't active. Run `.venv\Scripts\Activate.ps1`, then `pip install -r requirements.txt`. |
| `No time zone found` / `ZoneInfoNotFoundError` | Run `pip install tzdata` (Windows needs it). |
| Slash commands don't appear | Re-invite the bot with the `applications.commands` scope, restart the bot, refresh Discord with `Ctrl+R`. Set `DEV_GUILD_ID` for instant updates. Global commands can take a while to show up. |
| "Missing Access" or 403 in the log | The bot can't see or post in that channel. Give it **View Channel** and **Send Messages** there. |
| "You need the Administrator permission" | Only server admins can use the commands. |
| No message at the scheduled time | Is the bot running? Check `/status` (paused? channel set? right timezone?) and `logs\bot.log`. |
| Changed the time but nothing sent today | The bot only sends once per day. If today's message already went out, the new time applies tomorrow. Use `/testmessage` to send one now. |
| PowerShell: "running scripts is disabled" | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |

Logs are written to `logs\bot.log`.

## Final checklist

- [ ] Bot created and invited with `bot` + `applications.commands` scopes
- [ ] `.env` exists and contains the real token
- [ ] `pip install -r requirements.txt` finished without errors
- [ ] `python main.py` shows "Logged in as ..."
- [ ] `/setchannel` done and `/testmessage` arrives
- [ ] `/status` shows the right time and timezone
- [ ] A real scheduled message arrived (try a time 2 minutes ahead)
- [ ] Bot restarted and `/status` still shows your settings
- [ ] `run_bot.bat` or a host is keeping the bot alive
- [ ] `.env` is not shared or uploaded anywhere
