# Daily Podium dashboard

A separate, read-only web process for bot status, leaderboards, recent submissions,
final boards, and server settings. It reads the bot's existing SQLite database;
it does not start, stop, restart, or configure the Discord bot.

This is the private operations dashboard. For shareable per-server and global
scoreboards, use the separate [public service](PUBLIC_DASHBOARD.md); never point
Funnel at this private listener.

## Start on bee

The dashboard uses aiohttp, already installed by `requirements.lock`. No Node,
frontend build, new tokens, database migration, or bot restart is needed.

Run from the project root so `.env`, relative database paths, and process detection
refer to this checkout:

```sh
cd /home/stefan/dev/Daily-Podium
.venv/bin/python -m daily_games.dashboard --host 100.118.9.35 --port 8765
```

Open **http://100.118.9.35:8765** on a device connected to your Tailscale network.
Tailnet access rules must permit that device to connect to bee on port 8765.
The explicit host binds only to Tailscale. There is no public or LAN listener,
router forwarding, external hosting, or separate dashboard login. Anyone allowed
to reach this tailnet endpoint can read all configured servers' dashboard data.

Press Ctrl+C in this dashboard terminal to stop it; the Discord bot keeps running.
The dashboard has not been started automatically by the implementation.

For localhost testing instead:

```sh
.venv/bin/python -m daily_games.dashboard --host 127.0.0.1 --port 8765
```

## Run detached

After a successful foreground test, stop that dashboard instance with Ctrl+C,
then run:

```sh
cd /home/stefan/dev/Daily-Podium
mkdir -p data
nohup .venv/bin/python -m daily_games.dashboard --host 100.118.9.35 --port 8765 \
  >> data/dashboard.log 2>&1 < /dev/null &
echo $! > data/dashboard.pid
disown
```

View dashboard logs or check its recorded PID:

```sh
tail -f /home/stefan/dev/Daily-Podium/data/dashboard.log
ps -p "$(cat /home/stefan/dev/Daily-Podium/data/dashboard.pid)" -o pid,args
```

To stop it, first confirm that the recorded PID still belongs to
`python -m daily_games.dashboard`, then send SIGTERM to that PID. Dashboard and bot
PIDs are different. Detached operation does not provide automatic restart after a
crash or reboot; systemd and scheduled backups remain roadmap tasks.

## Configuration

The process reads `.env` and existing environment variables, with command-line
options taking precedence. It does not need or display `DISCORD_BOT_TOKEN`.

| Setting | Default | CLI override |
| --- | --- | --- |
| `DATABASE_PATH` | `data/daily-games.sqlite3` | `--database /absolute/path.sqlite3` |
| `DASHBOARD_HOST` | `127.0.0.1` | `--host 100.118.9.35` |
| `DASHBOARD_PORT` | `8765` | `--port 8765` |

Only numeric loopback and Tailscale IPv4 addresses are accepted for binding.
Do not use `0.0.0.0`. This implementation is intended for direct local/tailnet
access, without putting it behind a public reverse proxy.

If you want the optional `daily-games-dashboard` console command, refresh the
editable package metadata with `.venv/bin/python -m pip install -e . --no-deps`.
Using `python -m daily_games.dashboard` works with the existing editable install.

## What the dashboard shows

- Server selection by Discord server ID; names are not stored in the database.
- Today's participation, local competition date, and countdown to cutoff.
- Combined and individual-game boards for today, yesterday, or a selected date.
- Shared tie ranks and awarded points matching Discord, including placement mode.
- Frozen snapshots for final boards; viewing them never changes results.
- Latest 20 accepted, non-voided submissions and latest 30 final boards per server.
- Whether each final board has a recorded Discord message ID. A pending board
  is not automatically treated as a posting failure.
- Timezone, cutoff, scoring, channel IDs, and today's board due time.
- Bot process detection for this checkout and database read availability.

The page refreshes every 20 seconds. A failed refresh retains the last successful
view with an explicit stale-data banner and marks status as unknown.

**Bot process detected does not prove Discord connectivity.** The current bot
does not persist Gateway readiness, latency, or heartbeat telemetry, so the page
explicitly shows Discord connection as “Not measured.” No bot changes or restart
were required for these observations.

## Manual acceptance check

1. Start only the dashboard with the command above; keep the existing bot running.
2. Open its Tailscale URL from another tailnet device and confirm your server ID.
3. Compare Combined and each game tab against Discord's `!lb` commands. An older
   running bot needs a later manual restart to load the individual-game commands.
4. Submit a result through Discord and confirm it appears after refreshing.
5. Select yesterday or a finalized date and verify final status and posting state.
6. Check the page on a narrow/mobile screen.
7. Stop only the dashboard and verify the bot still answers `!ping`.

For offline checks, run `.venv/bin/python -m pytest -q`. Dashboard tests use
temporary fixture databases and ephemeral loopback listeners, not live scores.

## Troubleshooting

- **Cannot assign requested address:** confirm Tailscale is running and use
  `tailscale ip -4` to obtain this host's current tailnet address.
- **Address already in use:** another process has that port. Select a different
  port rather than stopping an unknown process.
- **Database unavailable:** start from the project root, check `DATABASE_PATH`,
  and make sure the bot has initialized that database. Run as the same Unix user
  as the bot so SQLite can read its database and WAL sidecars.
- **Not detected:** process detection looks for `daily_games` or `daily-games-bot`
  running as the dashboard's Unix user with this checkout as working directory.
  Other launchers, containers, or restricted `/proc` access may prevent detection.
- **Works on bee but not another device:** check the listener address, Tailscale
  connectivity, tailnet access rules, and any host firewall rules.
- **No configured servers:** initialize the bot in Discord with `!setup` and
  refresh. The dashboard intentionally does not create or modify configuration.
