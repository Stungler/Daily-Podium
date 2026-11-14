# Public leaderboards

Published and checked through Funnel's public HTTPS relay on 2026-11-14:

- [Global leaderboard](https://bee.tailfa65a1.ts.net:8443/global)
- [Current community leaderboard](https://bee.tailfa65a1.ts.net:8443/s/8cd43024e1fd10147e12afc9)

The public backend is already running detached. The original Discord bot and
private dashboard were not stopped or restarted. The next manual bot restart
will load the browser links in `!lb` and synchronize community display names.

The public leaderboard runs as a separate Python process, with a separate HTTP
API and page from the private operations dashboard. It reads the same SQLite
database but cannot change scores, settings, or bot processes.

## Views and Discord links

- `/global`: one cross-server leaderboard.
- `/s/<stable-public-slug>`: one Discord server's leaderboard.
- The page has a **This server / Global leaderboard** toggle, a community
  selector, game tabs, and date controls. URLs retain the selected date and game.
- `!lb` and `/leaderboard` include links to both views once public URL
  configuration exists and the bot has been manually restarted to load this code.
- Display names are learned from Discord at the bot's next startup and when
  `!lb` is used. Until then, a community has a generic numbered label.

Public responses include player display names, game scores, points, ranks,
competition rules, and board history. Discord user/server/channel/message IDs,
process details, logs, environment variables, and administrative settings are
excluded from the public API. The existing private dashboard stays tailnet-only.

## Global scoring rules

Each player is identified internally by Discord user ID. For each game and
recorded competition date, only that player's **earliest accepted submission
across all servers** counts. A later submission with a better score does not
replace it. An earlier failure also takes precedence over a later successful
submission. Voided results are excluded.

Global points are recalculated across the shared player field using
**1 + players strictly beaten**, with zero points for failures and missing games.
Ties share rank. A player who posts in several servers is counted once per game.
Per-server scores, settings, points, and final boards are unchanged.

“Today” in the global view follows **Europe/Berlin, 00:00 cutoff**. Global results
are grouped by the competition date stored with each accepted submission, rather
than reassigning puzzles by server posting time. Servers may have different local
cutoffs or timezones; the global page explains its rules explicitly.

Finalized server days use their saved snapshot values and membership. The global
board is marked final only when every configured server that had started by that
date has a saved final board. Until then, recovery in an unfinished server may
change the global view. Global boards are derived views rather than a new stored
competition; no database migration is required.

## Server processes and ports

| Purpose | Listener | Access |
| --- | --- | --- |
| Private dashboard | `100.118.9.35:8765` | Tailnet only |
| Public leaderboard backend | `127.0.0.1:8766` | Loopback only |
| Tailscale Funnel | HTTPS `8443` | Public, forwards only to `127.0.0.1:8766` |

Port 8443 was selected because this server already has a listener on 443. No
existing service or Tailscale route should be reset to publish these leaderboards.

## Start the public backend

```sh
cd /home/stefan/dev/Daily-Podium
nohup .venv/bin/python -u -m daily_games.public_dashboard --port 8766 \
  >> data/public-dashboard.log 2>&1 < /dev/null &
echo $! > data/public-dashboard.pid
disown
```

Check `data/public-dashboard.pid` and the running process before launching another
copy. The implementation session may already have started this service.

The public backend deliberately has no option to bind to the LAN or tailnet. Its
only listener is loopback; Tailscale Funnel handles public HTTPS.

## Publish with Tailscale Funnel

The tailnet owner must enable Funnel using Tailscale's account approval page if
requested by the CLI. This cannot be replaced by local application configuration.

```sh
tailscale funnel --bg --https=8443 --yes http://127.0.0.1:8766
tailscale funnel status
```

If Tailscale reports insufficient local privileges, run the same command with
`sudo`, or have the administrator configure the appropriate Tailscale operator.
Use the returned HTTPS origin for the public link configuration. The expected
origin for this host is `https://bee.tailfa65a1.ts.net:8443`; the Funnel status and
an external HTTPS check must confirm it before claiming publication succeeded.

Configure Discord's browser links after Funnel is active:

```sh
cd /home/stefan/dev/Daily-Podium
.venv/bin/python - <<'PY'
from pathlib import Path
from dotenv import dotenv_values
from daily_games.public_links import configure
values = dotenv_values('.env')
database = Path(values.get('DATABASE_PATH') or 'data/daily-games.sqlite3')
configure(database, 'https://bee.tailfa65a1.ts.net:8443')
PY
```

Then restart the **bot manually** when ready so `!lb` loads the new link code.
Publishing and starting the public backend do not require a bot restart.

## Runtime files and backups

The public service keeps these files beside the database, inside ignored `data/`
for the default configuration:

- `public-links.key`: a local 32-byte secret used to derive stable, opaque server
  URLs. Back it up securely alongside the database; replacing it changes URLs.
- `public-dashboard.json`: the public HTTPS origin and cached community names.
- `public-dashboard.log` and `public-dashboard.pid`: detached process output/PID.

The database remains read-only for dashboard requests. Only the small public-link
metadata files are created or updated. The application does not load bot process
environment variables or proxy private dashboard requests.

## Stop public access

Disable only this Funnel listener, preserving unrelated routes:

```sh
tailscale funnel --https=8443 off
```

The loopback process may continue running after public access is disabled. To stop
it too, verify the PID in `data/public-dashboard.pid` belongs to
`daily_games.public_dashboard`, then send SIGTERM to that PID. Neither action
requires stopping the Discord bot or private dashboard.

The public backend currently runs detached. Automatic restart at boot and after
failures remains in [ROADMAP.md](ROADMAP.md)'s systemd TODO. Funnel's `--bg`
configuration persists independently; the backend must also be running to serve
pages.
