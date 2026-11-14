# Daily Podium on bee: systemd and backups

The Beelink runs these **user** units as `stefan` (no `sudo` for routine commands):

| Unit | Purpose |
|---|---|
| `daily-podium-bot.service` | Discord bot and SQLite writer |
| `daily-podium-private-dashboard.service` | Tailnet-only dashboard at `100.118.9.35:8765` |
| `daily-podium-public-dashboard.service` | Loopback backend at `127.0.0.1:8766` for Funnel |
| `daily-podium-backup.timer` | Daily encrypted backup at 03:30 Europe/Berlin |
| `daily-podium-offsite.timer` | Daily Google Drive copy and checksum check at 04:00 |

The units are versioned in `deploy/systemd/user/` and linked into
`~/.config/systemd/user/`. `loginctl show-user stefan -p Linger` reports `yes`, so
the user manager starts at boot and survives logout. Services restart automatically
after failures. The private dashboard retries every 15 seconds if Tailscale has not
yet assigned `100.118.9.35` at boot. Funnel is managed separately by Tailscale;
the public backend remains bound to loopback only.

## Routine commands

```sh
systemctl --user status daily-podium-bot.service
systemctl --user status daily-podium-private-dashboard.service
systemctl --user status daily-podium-public-dashboard.service
systemctl --user list-timers daily-podium-backup.timer daily-podium-offsite.timer
journalctl --user -u daily-podium-bot.service -f
```

Restart the affected service after a code change. For changes to bot commands and
both dashboards, restart all three after development is complete:

```sh
systemctl --user restart daily-podium-bot.service \
  daily-podium-private-dashboard.service daily-podium-public-dashboard.service
journalctl --user -u daily-podium-bot.service -n 30 --no-pager
```

After editing a unit file, run `systemctl --user daemon-reload` and restart
only that unit. The old `data/*.pid` files and `data/*.log` files came from
manual `nohup` launches; new processes use systemd PIDs and journal logs.
Do not start a second manual bot alongside the service.

## Backups

`daily-podium-backup.timer` starts `daily-podium-backup.service` daily at 03:30
local time. It uses SQLite's online backup API, so the bot can keep running.
The encrypted archive contains the database, `.env`, `data/public-links.key`,
and `data/public-dashboard.json` when present. Keeping the public-link key
preserves the existing per-server dashboard URLs after restore. Before an
archive is published, the job decrypts it into a private temporary directory,
checks file hashes, and runs SQLite `integrity_check` on the restored copy.
It retains successful local archives for 30 days. At 04:00,
`daily-podium-offsite.timer` copies the encrypted archives to
`gdrive:daily-podium-backups` and checks remote checksums. Older remote copies
are retained; the sync does not delete them. The service retries failed uploads.

Archives are under `/home/stefan/backups/daily-podium/` (directory mode 700,
files mode 600). They use OpenSSL AES-256-CBC with PBKDF2 and 200,000 iterations.
The separate password is `data/.backup-password` (mode 600), outside Git and
**not included in the archives**. Store a secure copy of this password away
from `bee`; losing it makes the archives unusable. Local archives share the SSD
with the database, while verified encrypted copies are also on Google Drive.
The password is **not** uploaded with them.

Run and inspect a backup on demand:

```sh
systemctl --user start daily-podium-backup.service
journalctl --user -u daily-podium-backup.service -n 20 --no-pager
ls -lh /home/stefan/backups/daily-podium/
systemctl --user start daily-podium-offsite.service
journalctl --user -u daily-podium-offsite.service -n 20 --no-pager
```

To inspect a selected archive in scratch space without touching live data:

```sh
mkdir -m 700 ~/daily-podium-restore
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -pass file:/home/stefan/dev/Daily-Podium/data/.backup-password \
  -in /home/stefan/backups/daily-podium/ARCHIVE.tar.gz.enc \
  | tar -xzf - -C ~/daily-podium-restore
python3 -c 'import sqlite3; db=sqlite3.connect("/home/stefan/daily-podium-restore/daily-games.sqlite3"); print(db.execute("PRAGMA integrity_check").fetchone()[0]); db.close()'
```

The scratch directory contains plaintext secrets; remove it securely when done.
For an actual restore, stop the bot and both dashboards first, preserve the
current database and its `-wal`/`-shm` files separately, then restore the
verified files. Never overwrite a running SQLite database.
