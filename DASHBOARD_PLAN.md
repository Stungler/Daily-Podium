# Tailscale dashboard plan

Status: first version implemented in source, 2026-11-14; not started or deployed.
See [DASHBOARD.md](DASHBOARD.md) for manual run/test instructions. The Discord bot
has not been stopped or restarted. Other ideas are retained in [ROADMAP.md](ROADMAP.md).
This plan describes the private operations dashboard. A separate, subsequently
requested public leaderboard is documented in [PUBLIC_DASHBOARD.md](PUBLIC_DASHBOARD.md).

## Goal and access

Provide a mobile-friendly dashboard for the owner to inspect Daily Podium from
devices connected to the same Tailscale network.

- Run on `bee`, beside the existing Python Discord bot.
- Proposed initial URL: `http://100.118.9.35:8765` (confirm the port is free first).
- Bind specifically to the server's Tailscale IPv4 address, not `0.0.0.0` or the
  LAN address. Tailscale access rules determine which tailnet devices can reach it.
- Do not add router forwarding or publish through external hosting.
- Direct HTTP remains inside the encrypted Tailscale connection. Tailnet HTTPS
  through Tailscale Serve can be considered later after checking existing routes.

## First-version screens

### Overview and status

- Dashboard availability and last refresh time.
- Configured Discord server selector; never mix competitions between servers.
- Bot process status, database availability, latest recorded submission, and
  latest frozen board's posting status as separate indicators.
- Current timezone, competition day, cutoff, scoring mode, and next posting time.
- Counts for today's players and submissions by game.
- Clearly label status information's source and timestamp. A running process,
  recent log line, or recent submission does not prove a live Discord connection.

### Leaderboards

- Combined, TimeGuessr, Size It Up, and Metazooa tabs.
- Today, yesterday, and an ISO-date picker.
- Show scores, awarded points, ties, failures, and provisional/final state.
- Use existing scoring and presentation functions, including `game_snapshot`.
- Read saved final snapshots for finalized days; never recalculate frozen history
  from current submissions or change placement points when filtering one game.

### Activity and history

- Recent accepted submissions with player, game, score, competition date, and time.
- Historical daily boards and whether their Discord post was recorded.
- Read-only configuration details, including channel IDs when helpful.
- A clear empty state when no server is configured or no scores are recorded.
- No raw `.env`, tokens, arbitrary file access, or unfiltered log viewer.

## Implementation approach

1. Add a separate Python dashboard entry point and small local HTML/CSS/JS UI.
   Use a maintained Python HTTP server, with dependencies explicitly declared
   rather than depending on an incidental transitive package. No Node build
   pipeline is necessary for this initial dashboard.
2. Open the configured SQLite database with a dedicated read-only connection
   (`mode=ro`), with short read transactions and a bounded busy timeout. Do not
   instantiate `Store` for this reader: its constructor writes schema and PRAGMAs.
   Preserve normal WAL visibility; do not use SQLite's `immutable` mode against
   the live, changing database. Keep connections within their owning thread.
3. Add narrowly scoped JSON endpoints for overview, boards, and recent activity.
   Validate server/game/date inputs, parameterize SQL, and bound result sizes.
4. Render names and other stored strings as text, escaping HTML as needed.
   Keep identifiers as strings in JSON to avoid JavaScript integer precision loss
   for Discord snowflake IDs. Avoid external scripts and assets.
5. Refresh approximately every 15–30 seconds and show when refreshes fail. Keep
   the previous view visible with a stale-data label rather than implying success.
6. Detect the bot process independently, exposing no environment or command secrets.
   If reliable Gateway readiness, latency, scheduler health, or a heartbeat is
   needed, add structured bot telemetry in a later opt-in change. Those signals
   are not currently persisted and cannot be inferred reliably from SQLite alone.
7. Run the dashboard independently so it can be stopped or restarted without
   interrupting Discord submissions or daily posting.

## Data preservation and rollout

- Leave the live bot running during development. The existing instruction not to
  stop it remains in effect unless the user later authorizes a restart.
- Do not alter the production schema, scores, configuration, or frozen snapshots
  to support the first dashboard version.
- Use a fixture database and mocked status information for development/tests.
- Verify the dashboard read path against SQLite while a separate connection writes.
- Before a deployment that needs bot changes, take a consistent backup using
  SQLite's backup API, or copy the database after stopping its writer cleanly.
  Do not copy only the main database file while a WAL writer is active.
- Launch the dashboard separately on the verified free Tailscale port. Check it
  from another tailnet device and confirm the listener is Tailscale-only.
- Document start/stop/log commands. Add systemd services and backup scheduling as
  the next operations task, without silently restarting the existing bot.

## Acceptance checks

- Combined and per-game results agree with Discord for the same server and day.
- Placement scoring, ties, failures, missing games, dates, and frozen snapshots
  remain consistent with existing behavior.
- Existing Discord test suite continues to pass.
- Dashboard reads leave a fixture database unchanged, including final snapshots.
- Separate servers remain isolated when changing views or supplying invalid IDs.
- Missing/unavailable databases and stale status are displayed clearly.
- Stored display names cannot inject markup or JavaScript.
- No secrets are exposed through endpoints, page source, or error responses.
- The dashboard continues working across refreshes and database reconnects.
- A normal bot restart preserves scores and settings; the dashboard has no write
  controls and makes no changes to those records.

## Later dashboard additions

Weekly/monthly tabs, personal statistics, participation trends, and backup/service
health can follow once the first version is working. Restart buttons, setup edits,
and result corrections are outside the first read-only version and would need
separate authorization and access controls.
