# Daily Games Bot implementation plan

## Agreed scope

- Python, developed/run in WSL2; test using the Windows Discord client.
- TimeGuessr, standard Magnitudle Size It Up, and Metazooa only.
- Independent competitions per Discord server; users may play in several servers.
- Players paste the games' Share text into a configured channel; no registration.
- One combined daily board, automatically posted after the cutoff.
- `/leaderboard` and `!lb` for current standings; yesterday or an ISO date supported.
- Persistent duplicate prevention per server + player + game + puzzle, across days/restarts.
- Metazooa failures remain visible and receive zero points.

## Defaults pending real-world feedback

- Timezone Europe/Berlin; competition date D runs from D 00:00 to D+1 00:00.
- Final board posts five minutes after the deadline; this is not a submission grace period.
- Successful result: one point plus one for each participant strictly beaten in that game.
- Ties share points; failures/missing games earn zero; combined ties share a rank.
- Optional placement scoring uses the day's overall participant count as each game's
  first-place value. More sparsely played games therefore have the same winning value.
- First submission wins; deleting/editing/reposting a Discord message cannot replace it.
  An admin can void an erroneous result (with audit history), then allow a fresh submission
  for the same still-open day.

## Puzzle identity and date policy

Confirmed by the user: 2026-11-13 corresponds to TimeGuessr #1262 and Metazooa #1201.
Numbered puzzle dates use these documented, configurable reference points and the assumption
of one sequential puzzle number per calendar date. Size It Up supplies an explicit ISO date.
We do not infer a new competition date merely because a message arrives after midnight.
Old, future, or already finalized competition dates are rejected, including previously
unseen stale results. Per-game number/date overrides can handle a skipped/renumbered puzzle.

Metazooa's public countdown code uses midnight UTC. Magnitudle's official FAQ says midnight
in the game timezone. TimeGuessr's precise reset is unverified: no guessed reset is hardcoded.
The bot follows puzzle labels/reference dates, not a claimed common game reset time.
Players need to use the puzzle assigned to the server's competition date. Future puzzle
results must wait until that date opens. A game may still offer yesterday's puzzle after
the server has closed yesterday's competition.

## Architecture and build sequence

1. Pure models, three parsers, puzzle-date mapping and timezone-aware day boundaries.
2. SQLite schema for server configuration, immutable accepted results, audit events,
   parser number/date overrides, recovery cursors and frozen daily boards.
3. Pure scoring and formatting shared by commands and scheduled posts.
4. discord.py hybrid commands, admin setup/configuration, message handling and reactions.
5. Startup/reconnect history recovery using original message timestamps; late recovery may
   enter a closed day only before its final snapshot is created. Finalized days never mutate.
6. Scheduler, persisted board snapshots and post reconciliation after ambiguous send failures.
7. Tests covering supplied share texts, invalid input, ties/failures, server isolation,
   cross-day duplicate prevention, DST, recovery, and scheduling across restarts.
8. WSL run instructions, dependency lock and deployment guidance.

One process and one SQLite database are sufficient for an initial small community deployment.
A file lock prevents two local processes using the same database. Use a single deployment
for the bot token. Store new results immediately; history is recovery, not the main database.
No AWS infrastructure, website, OAuth user registration, game account credentials, or paid
API is needed. Shared text is self-reported, not authenticated proof of a game score.

## Completion boundary

Local implementation and offline tests can be completed without credentials. A real Discord
smoke test needs the user to put a bot token in the local .env and install the application.
Do not print/read the token, commit secrets, or claim live testing without connecting.
