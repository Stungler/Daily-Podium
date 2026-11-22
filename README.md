# Daily Podium

A Python Discord bot for independent daily competitions in each server. Players paste
their Share results from the active lineup. The supported games are **TimeGuessr**,
**Magnitudle Size It Up (standard)**, **Metazooa**, **The Wiki Game Daily**, and
**Krillion**. The bot
records scores, serves a combined leaderboard, and posts the final standings after
the daily deadline.

The original implementation plan is in [PLAN.md](PLAN.md).
A private [Tailscale dashboard](DASHBOARD.md) is available as a separate read-only
process. Its [design plan](DASHBOARD_PLAN.md) and other feature ideas in
[ROADMAP.md](ROADMAP.md) are retained in the repo.
The separate [public leaderboards](PUBLIC_DASHBOARD.md) provide per-server links
and a global cross-server board through Tailscale Funnel.
On the Beelink server, the bot and dashboards run as user systemd services;
see [SYSTEMD.md](SYSTEMD.md) for restarts, logs, and encrypted backups.

## Run locally (macOS or Linux/WSL2)

Use Python 3.12 or newer. On this Mac, the checkout is at
`~/dev/Daily-Podium` and `.venv` uses Homebrew Python 3.13. The macOS system
`python3` may be older; use the project's virtual environment for this app.

```sh
cd ~/dev/Daily-Podium
source .venv/bin/activate
cp -n .env.example .env
nano .env
```

Set `DISCORD_BOT_TOKEN` to the token from **Developer Portal → Bot → Reset Token**.
Keep it in `.env`; never paste it in chat or commit it. Optionally set `DEV_GUILD_ID`
to a test server ID for immediate server-specific slash command registration. In Discord,
enable **User Settings → Advanced → Developer Mode**, then right-click the server and
copy its ID. For commands in all servers, leave `DEV_GUILD_ID` empty.

Start the bot:

```sh
python -m daily_games
```

Keep this terminal running. Test through the Discord desktop app or browser; the Python bot
runs locally on your computer. No localhost website, port forwarding, tunnel,
or game account credentials are needed. Your computer must stay awake for live operation.
Stop with Ctrl+C. Restarts preserve settings, scores, and daily boards.

For a fresh checkout, create a virtual environment with Python 3.12+ (on macOS with
Homebrew Python 3.13, use `python3.13` in place of `python3` below):

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
```

The lock file includes development tools. To resolve newer compatible versions instead,
install `python -m pip install -e '.[dev]'` and rerun the tests.

The environment and `.env` are local to this checkout; Git does not copy them when
cloning. Keep any existing WSL bot process stopped while testing this copy: only one
deployment should run with the same bot token. Existing scores/settings are in the
old deployment's SQLite database, not in Git; a fresh clone starts with no server setup.

## Game rotation

Each competition date has three active games. TimeGuessr, Size It Up, and Metazooa
are the default lineup. The Wiki Game Daily is available as `wikigame` and Krillion
as `krillion`. A rotation
changes future competition days and preserves past standings, points, and results.

| Command | Who can use it | What it does |
| --- | --- | --- |
| `!rotation` | Anyone on a configured server | Show its active lineup and next change |
| `!rotation set timeguessr sizeitup wikigame` | Manage Server | Set this server's trio starting the next competition day |
| `!rotation inherit` | Manage Server | Return this server to the global lineup the next competition day |
| `!rotation_global set timeguessr sizeitup wikigame` | Bot owner | Schedule the global trio |

Slash versions are available wherever the bot's slash commands are registered.
After updating the code, restart the bot and both dashboards as described in
[SYSTEMD.md](SYSTEMD.md). The bot applies the database migration on startup.
To replace Metazooa across inheriting servers, send
`!rotation_global set timeguessr sizeitup wikigame` from the Discord application
owner account. The confirmation gives the effective date. Use `!rotation` on
each server to check its lineup and whether it inherits the global setting.
The global change takes effect on the next date that has not opened on any
configured server. Each server turns over at its own cutoff. The cross-server
leaderboard includes only games in the global trio for that date. Weekly and
monthly points earned before the swap still count. Per-game streaks pause during
inactive days and break when a player misses an active day.

Paste the Share result from [The Wiki Game Daily](https://www.thewikigamedaily.com/).
The bot checks the route against the publisher's dated challenge, then ranks by
fewest steps, with fastest time breaking ties. Equal steps and time share rank.
If the site is unavailable and the route is missing from the local cache, the bot
owner can register the published route with
`!wiki_route YYYY-MM-DD "Start article" "Target article"`. Players can retry before
cutoff. As with the other games, the bot accepts self-reported scores.

For [Krillion](https://krillion.io/), paste the daily dive's Share text, such as
`Krillion #130 🦐` followed by the score on its own line. Higher scores win
(0–700), and the emoji row is ignored. Puzzle #1 is dated 16 July 2026.
Krillion releases each daily dive at midnight New York time, so players on
servers with an earlier cutoff should wait for the new dive before posting.

## Discord application setup

Open https://discord.com/developers/applications and select the bot.

1. **Bot:** enable Message Content Intent. Presence and Server Members intents are not needed.
2. **Installation:** Guild Install checked, User Install unchecked.
3. **Guild Install scopes:** `bot`, `applications.commands`.
4. Permissions: View Channels, Send Messages, Read Message History, Add Reactions, Embed Links,
   Create Public Threads, and Send Messages in Threads. For daily results thread mode, also
   grant **Pin Messages** and **Manage Messages** in the results parent channel.
   Pin Messages lets the bot pin the daily announcement and the guide inside its
   thread. Manage Messages hides link previews on Share messages. Check the
   chosen channel's permission overwrites too.
   For an existing installation, update the bot's role and channel permissions; update Guild
   Install permissions in the Developer Portal for future invites. No new privileged intent
   is needed for threads. Without Manage Messages, channel-mode results and reactions still
   work, but previews remain.
5. Open the installation link and choose the server. Repeat for additional servers.
6. For other server administrators to install the bot, enable **Public Bot** on its Bot page.
7. Leave **Interactions Endpoint URL** empty: this bot receives commands through the Gateway.

The bot can serve several servers from one running process. Each server needs its own setup.
If `DEV_GUILD_ID` is set, slash commands are registered only in that server; `!` commands
remain available in every installed server. Global slash registrations can take time to appear.
When moving from server-scoped development commands to global commands, remove the old
guild-scoped registrations in a controlled migration if Discord shows duplicates.

## First test in Discord

Run these in your test server as someone with **Manage Server** permission:

```text
!ping
!setup #daily-games
!status
```

Or use `/setup` and select `results_channel` using Discord's command picker. Defaults:
Europe/Berlin, midnight cutoff, daily post five minutes later, and `players_beaten` scoring.
The results channel also receives daily boards unless you select a separate board channel.

Choose timezone, cutoff and scoring before posting results: after any result or final board
exists those three settings are locked to preserve competition history. Channel settings
can still change. A later version can add effective-dated settings changes.

Paste the actual Share text from a daily game in the configured results channel. Expect ✅,
then request the standings. With Manage Messages in that channel, the bot also hides
the Share message's link preview while keeping its text and link intact:

```text
!lb
!lb yesterday
!lb 2026-11-13
!lb sizeitup
!lb timeguessr
!lb metazooa
!lb sizeitup yesterday
!lb metazooa 2026-11-13
!weekly
!weekly yesterday
!weekly sizeitup
!monthly
!monthly metazooa
!stats
!stats @friend
/myday
```

`/leaderboard` offers `day` and `game` options (All games, TimeGuessr, Size It Up,
Metazooa, The Wiki Game Daily, and Krillion). Existing `!lb [day]` commands still
work, along with `!lb yesterday sizeitup`, `!lb wikigame`, and `!lb krillion`.
Single-game boards show only that game's participants, ranked by its awarded points with
shared ranks for ties. Points match the combined board, including placement scoring.
Historical boards use the saved final snapshot; viewing a game does not change it or the
combined daily posts. No manual database migration or new setup is required.
Leaderboard replies use compact aligned embeds. With public dashboard links configured,
they show the top 10 and link to the full browser view; without links, they paginate all players.
`!weekly [day] [game]` and `!monthly [day] [game]` (also slash commands) sum saved
daily points through the selected date. Omit the game for combined standings or choose
any supported game. Weeks start Monday and months start on the first.
Only finalized days count. The footer shows finalized days, days played, and tied wins.
Both dashboards offer the same daily, weekly, and monthly filters.
`!stats` and `/stats` show your own stats, or another server member's stats when
selected. They use only this server's finalized days: total and average daily
points, participation, daily wins, and each game's plays, solves, wins, average
successful score, and personal best. Tied first places count as wins. A game
failure counts as a play but not toward the raw-score average; for Metazooa,
fewer guesses are better. Today's provisional results appear after finalization.
They also show current and best participation streaks for all games combined and
each game. One accepted result, including a recorded failure, counts once for its
competition date; missing a closed day breaks the run. A streak played yesterday
remains current while today's competition is open. Voided results do not count.
Each newly accepted single-game Share message gets ✅, 🔥, and one streak marker:
1️⃣–9️⃣ show the exact length, 🔟 means 10–24, 💎 means 25–49, 🏆 means 50–99,
and 💯 means 100 or more. There is no extra reply. If one message contains results
for several games, it gets 🔥 without a number because reactions cannot label
separate game streaks on the same message. `!stats` and both dashboards show exact
current/best numbers. Dashboard game tabs show that game's streak; Combined shows
the any-game streak. Historical and final dashboard streaks use frozen boards.
`/myday` is a private slash command showing your three accepted games for the
current competition day, missing game links, provisional points, cutoff, and
current/best streaks. It sends no new message to the channel.
When public dashboard links are configured, leaderboard replies also link to
this server's full browser board and the global leaderboard. The global board
counts each player's earliest accepted submission across servers for each game
in the global lineup for that date, then recalculates points for the shared field.

With several servers, paste the same result
in each server's results channel to compete there; results do not automatically cross-post.

## Commands

| Slash command | Prefix form | Purpose |
|---|---|---|
| `/ping` | `!ping` | Connection check |
| `/help` | `!help` | Instructions |
| `/games [day]` | `!games [day]` | Links to the selected day's lineup |
| `/leaderboard [day] [game]` | `!lb [day] [game]` or `!lb game [day]` | Combined or single-game current, yesterday, or dated board |
| `/weekly [day] [game]` | `!weekly [day] [game]` | Week-to-date combined or per-game points from finalized boards |
| `/monthly [day] [game]` | `!monthly [day] [game]` | Month-to-date combined or per-game points from finalized boards |
| `/stats [member]` | `!stats [@member]` | Personal history in this server from finalized days |
| `/myday` | — | Private current-day checklist, provisional points, and streaks |
| `/status [day]` | `!status [day]` | Lineup, expected puzzles, game links, deadline, and today's TimeGuessr reset when active |
| `/rotation` | `!rotation`, `!rotation set <game1> <game2> <game3>`, `!rotation inherit` | View or manage this server's lineup |
| `/rotation_global` | `!rotation_global set <game1> <game2> <game3>` | Bot owner schedules the global lineup |
| `/wiki_route` | `!wiki_route <date> "Start article" "Target article"` | Bot owner registers the published route if the official lookup is unavailable |
| `/setup` | `!setup #results [#boards] [timezone] [cutoff] [scoring] [mode]` | Admin configuration; mode is `channel` (default) or `thread` |
| `/result_mode` | `!result_mode thread` / `!result_mode channel` | Admin switch of submission mode from the next competition day |
| `/puzzle_date` | `!puzzle_date timeguessr 1262 2026-11-13` | Admin date correction for a numbered puzzle |
| `/void_result` | `!void_result @player metazooa` | Admin correction of a current-day entry |

Prefix setup arguments are positional; slash setup allows selecting only the options you
want to change. `/setup`, `/result_mode`, `/puzzle_date` and `/void_result` check Manage Server
at runtime.
`/status` and admin replies are private for slash commands; prefix replies are normal messages.

## Competition rules

- Each server has independent submissions and rankings.
- No player registration: the Discord user ID identifies the player in that server.
- Default successful result points: **1 + number of players strictly beaten in that game**.
  A Metazooa solve beats a submitted failure. A failure or absent game earns zero points.
  Games with more participants can award more points.
- Optional `placement` scoring: first place is worth the number of participants across
  all three games that day; each worse rank earns one fewer. Each game has the same
  winning value, including when someone is its only player. Ties share the better rank
  and following places skip (1, 2, 2, 4). Failures still earn zero.
- TimeGuessr, Size It Up, and Krillion rank by highest score. Metazooa ranks by fewest guesses.
  The Wiki Game Daily ranks by fewest steps, then fastest time.
- Successful zero-point game scores (such as TimeGuessr 0/50,000) are valid submissions.
- Combined ties share a rank. Alphabetical order is only display order within that tie.
- First accepted result wins. Editing/deleting/reposting a message does not improve a score.
  An admin can void a current-day result; the original message stays voided during recovery,
  and the player must post a new message. An audit entry records the admin action.
- One message can contain Share text from several supported games.
- An old puzzle cannot count on a later day, even if the player never submitted it before.
  Future puzzles must wait for their competition date to open.
- A day's deadline is the next day's configured cutoff; the five minutes until posting
  do not extend the submission window. A cutoff of 03:00 means day D runs from D 03:00
  to D+1 03:00. Daylight saving uses the server's named timezone; an ambiguous cutoff
  uses its first occurrence and a nonexistent cutoff moves forward through the DST gap.

### Puzzle dates and differing game reset times

The user confirmed these reference results were all from **2026-11-13**:

| Game | Puzzle identity |
|---|---|
| TimeGuessr | #1262 |
| Metazooa | Animal #1201 |
| Size It Up | Date 2026-11-13 in the earlier Share format; #98 observed on 2026-11-15 |

Numbered games use one sequential number per calendar date from that reference. This is
an explicit assumption, not a live query to the game. If a game skips/repeats/renumbers a
puzzle, an admin can use `/puzzle_date` before results are recorded for that puzzle/date.
Each override names one exact puzzle for that day and server. It does not shift all later
numbering automatically. Size It Up accepts either a date or `#puzzle` in the Share
result; the numbered format uses #98 on 2026-11-15 as its reference. Both formats
count toward the same competition date, and one player can submit only once per game
and day. `/status` shows the expected Size It Up number alongside the date.

Metazooa's public client countdown uses midnight UTC; Magnitudle's official FAQ says midnight
in the game timezone. TimeGuessr's next-daily timer pointed to 08:00 Europe/Berlin on
2026-11-15; at 01:46 it still showed #1263 from 2026-11-14. A fixed reset hour is not
assumed. `/status` reads TimeGuessr's live next-reset timestamp, falling back to a general
warning if the site is unavailable. A game may still offer yesterday's puzzle after this
server's competition for yesterday has closed. Wait for the new puzzle before sharing.

Public references:

- https://magnitudle.com/
- https://metazooa.com/_build/assets/datetime-Dxnmd71F.js
- https://timeguessr.com/finalscoredaily

## Recovery and daily posts

Accepted results are stored immediately in SQLite. On startup/reconnect and before finalizing
a day, the bot recovers messages after its persisted channel cursor, oldest first. It uses
the original Discord creation timestamp. If the bot was offline at midnight, genuinely
pre-cutoff messages may still enter that day **before its final board is frozen**. Finalized
days never change. A first setup imports messages from the current competition day's start.

History contains only a message's latest edited content. Unrecorded edited messages are skipped
during recovery because their original score/time cannot be established. Repost an unedited
Share result while the day is open. Results already stored remain unchanged by later edits.

Daily boards are frozen to durable snapshots, then posted. In standard style, the final
board is one compact embed in the board channel. In channel mode, a public thread attached to that message holds game
highlights, the full per-player results, and a weekly summary. The detail content and its
delivery state are saved
in SQLite, so a restart resumes incomplete threads without changing the frozen scores.
In channel mode, after the final board, the bot posts one compact game-links and status
embed for the new competition day in the same channel. The final board posts five minutes
after cutoff.
The game-links message waits until at least 08:05 in that server's timezone. When
TimeGuessr is in the lineup, the bot checks its official daily page. If the expected puzzle is still unavailable, it retries every
five minutes until noon; it then posts with a warning even if TimeGuessr is delayed. Results
can be submitted as soon as each game's puzzle is available. Delivery state is saved and
reconciled after a restart; old days are never backfilled with new links posts. The TimeGuessr
reset shown in this embed comes from the game's live countdown, with a fallback if the site
is unavailable.
If the bot lacks thread permissions, the main board still posts; thread delivery is retried
after permissions are granted. A saved message ID prevents repeated main posts. After a crash
or ambiguous send timeout, the bot looks for its deterministic board footer in channel history
before retrying. Existing boards posted before this feature keep their original format.
This reduces duplicate posting; Discord and SQLite do not provide an atomic transaction across
both systems, so strict exactly-once delivery cannot be guaranteed under every network failure.
Read Message History permission is needed on the output channel and in threads for reconciliation.

### Daily results thread mode

The default `channel` mode accepts results in the results channel and posts the
game links at or after 08:05. An admin can choose `thread` during first `/setup`
or switch an existing server with `!result_mode thread`. Use `!result_mode channel`
to switch back. Changes for existing servers start the next competition day.
The configured results channel becomes the parent for the daily threads. The
board channel can be the same channel or a separate one.

In thread mode, the bot creates a short daily post and public results thread at
cutoff. It pins the parent post and unpins only its previous daily post. Inside
the thread it pins one guide with all the game links, the deadline, and instructions.
Open a game from the guide, copy your result with the game's Share button, then
paste it as a message in the thread. A ✅ confirms the result counted. The bot
edits the guide in place at or after 08:05 when updating the game status.
`/status` links to the active thread. Results posted in the parent channel after the
thread opens get a reply pointing to the thread. If the bot was offline before it
could create the thread, parent-channel Share messages from that gap are recovered
using their original Discord timestamps.

In standard style, the final board appears in the configured board channel five minutes after cutoff.
Its full results and weekly summary go into that day's results thread. Thread IDs,
per-thread recovery cursors, pin state, and mode history are saved in SQLite so
restarts and later mode changes do not reassign or lose accepted submissions.

### Server language and compact announcements

Admins with Manage Server can set `!language de` or `!language en` for that server.
English is the default.
Scheduled posts, leaderboards, commands, and result feedback use the selected language;
changing it does not change accepted game Share formats or stored scores. The same settings work
on any Discord server; no server name or ID is hardcoded.

In thread mode, `!announcement_style compact` keeps the main channel to one pinned
daily parent post. The choice applies to newly created daily posts; changing it during
the day does not move or rewrite an existing post. The bot creates it at cutoff so
players can submit immediately.
At 08:05 it edits that post with yesterday's winner, the current month's top three
from finalized days, and an invitation to use the thread. An edit does not create a
new morning notification. The previous day's final board, highlights, full results,
and weekly stats go into the previous day's results thread at cutoff + five minutes.
The bot bundles the final board and details in one thread message when they fit
within Discord's embed limits. The pinned guide has game links from cutoff.
If TimeGuessr is delayed, the guide keeps its reset warning while other games
remain available, and the short parent update still appears at 08:05.
If no results thread was recorded for a day, the bot posts the final board to
the configured board channel. Use `!announcement_style standard` to keep final
boards in that channel. Both styles pin the game guide inside the results thread.
Compact style has an effect only in thread mode.

A failed recovery prevents finalization rather than freezing an incomplete board. Failures
are logged and retried. Backlogged competition days are processed when the bot comes back.
One unavailable server does not stop other servers' scheduler passes.

Run one process for this bot token. A file lock blocks two processes using the same database;
it does not detect another deployment using a different database. Keep the `data/` directory
on persistent storage and back it up with SQLite's backup API (or while the bot is stopped).
`.env`, databases, virtual environments and logs should not be committed to Git.

## Architecture

```text
src/daily_games/
  parsers.py       Share text → validated game result
  calendar.py      Puzzle identity/date mapping and timezone-aware boundaries
  models.py        Shared data structures
  storage.py       SQLite settings, submissions, snapshots, cursors and audit
  service.py       Submission eligibility and duplicate rules
  rotation.py      Dated global and per-server game lineups
  wiki_daily.py    Official Wiki Game puzzle route lookup
  scoring.py       Per-game points and combined ranks
  presentation.py  Stable, serializable leaderboard snapshots
  discord_views.py Compact Discord embeds, daily highlights, weekly summaries
  bot.py          Discord commands, messages, recovery and daily posting
  __main__.py     Environment, single-process lock and startup
```

The domain logic can be tested without Discord credentials. Discord.py manages the Gateway
connection and rate limits. SQLite queries are small, indexed, synchronous operations in one
bot process; this is intended for an initial small community deployment. Larger deployments
can move storage to an async database while keeping parsing and scoring unchanged.

Architecture inspiration: https://github.com/floatyboat/daily-game-scorekeeper — game registry,
shared scoring functions, frozen days and restart-safe posting. This project implements its
own smaller Gateway + SQLite architecture; no upstream source code was copied.

## Development checks

```sh
python -m pytest -q
ruff check src tests
ruff format --check src tests
```

Tests include the exact supplied Share texts, a Metazooa failure, malformed scores, multiple
results in one message, ties, server isolation, repeated puzzles across midnight/restarts,
date overrides, DST, recovery, command registration, board pagination and posting reconciliation.
Discord tests use mocks and do not prove live credentials/permissions; finish with `/ping`,
`/setup`, one real result and `/leaderboard` in a test server.

## Startup troubleshooting

- **403 / 50001 Missing Access during command sync:** authentication succeeded, but
  Discord denied command registration. If `DEV_GUILD_ID` is set, confirm it is the
  test server's ID (right-click the server, not a channel) and install this same bot
  application in that server using **Guild Install** with `bot` and
  `applications.commands` scopes. Adding the application only to your user account
  does not install the bot in a server. Restart after correcting the ID or installation.
- **PyNaCl / davey voice warnings:** this bot uses text only; voice dependencies are
  optional and do not cause the startup failure above.
