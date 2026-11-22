# Daily Podium roadmap

Updated: 2026-11-15

## Private Tailscale dashboard

Build a private, read-only dashboard on the Beelink server (`bee`) for inspecting
the Discord bot's status, configuration, scores, and history. See
[DASHBOARD_PLAN.md](DASHBOARD_PLAN.md) for the proposed first version and rollout.

Status: implemented and running as a systemd user service. See
[SYSTEMD.md](SYSTEMD.md) for current operations.

## Other feature ideas to retain

### Operations status and remaining backup work

- [x] Create separate systemd services for the Discord bot and dashboard, running
  as `stefan` from `/home/stefan/dev/Daily-Podium` with the existing `.venv`.
- [x] Include the separate loopback public leaderboard backend (`8766`) in systemd
  setup, and retain its stable public-link key during backups/restores.
- [x] Enable startup at boot, automatic restart after failures, graceful shutdown,
  and journal logging. Account for Tailscale readiness before dashboard startup
  and retry if its address is temporarily unavailable.
- [x] Keep the dashboard listener on `100.118.9.35:8765` and retain the existing
  `.env` and SQLite database paths.
- [x] Switch from the manually detached processes in a controlled handover,
  avoiding duplicate bot processes and preserving stored scores and settings.
- [x] Schedule consistent SQLite backups separately, with retention and a tested
  restore into a scratch database.
- [x] Copy and checksum-check encrypted archives on Google Drive each day.
- [x] Save the separate decryption password outside `bee` (user confirmed
  Bitwarden storage on 2026-11-14).

The services and backup timer are active; see [SYSTEMD.md](SYSTEMD.md).

### Feature backlog

1. **More period leaderboards — done.** Weekly per-game and monthly combined/per-game
   views are available in Discord and both dashboards. Weeks start Monday, months
   start on the first, and only finalized days count. Period totals add the daily
   points saved in each final board, so later scoring changes do not rewrite history.
2. **Participation streaks — done.** Consecutive competition days played,
   current/best streaks for any game and each game, and emoji milestones on
   newly accepted results. Missing a closed day breaks the run; recorded
   failures count as participation.
3. **Full recovery drill.** Restore a downloaded encrypted backup into an isolated
   scratch checkout and verify stored scores and dashboard reads without using
   the live Discord token or overwriting the active database.
4. **Clearer rejected-submission feedback.** Improve the existing feedback for
   duplicates, wrong puzzle dates, unsupported Share text, and submissions after
   cutoff. Keep replies limited to rejected submissions so normal play stays quiet.
5. **Optional daily results threads — implemented in source.** Per-server channel
   mode remains the default. Thread mode creates a results thread at each day's
   cutoff, pins the parent announcement and one guide inside the thread, then
   edits the guide in the morning. Compact style bundles the final board and
   details in the thread when they fit in one Discord message. `/setup` selects a new server's mode;
   `/result_mode` changes an existing server from the next competition day.
   Per-thread cursors and mode history preserve recovery and prior results.
   See [README.md](README.md#daily-results-thread-mode) for use and permissions.

## Already implemented in source

- Combined daily leaderboards and scheduled final boards.
- Compact daily Discord embeds with a linked results thread, per-game winner
  highlights, and weekly/monthly combined and per-game leaderboards.
- Per-player finalized history in Discord with `!stats` / `/stats`.
- Private `/myday` checklist for accepted/missing games, provisional points,
  cutoff, and streaks, without a channel message.
- Individual game views: `!lb sizeitup`, `!lb timeguessr`, `!lb metazooa`.
- Global and per-server game rotation, with The Wiki Game Daily available as
  `wikigame`. See [README.md](README.md#game-rotation) for activation.
- Date filters and a slash-command game selector.
- Persistent scores, configuration, and frozen final boards in SQLite.
- Private operations signals for Gateway connection, scheduler, final posting,
  and verified local/offsite backups.
- Separate public per-server leaderboards and a global crossover board, with
  earliest accepted submission per player/game/date and globally recalculated
  points. Browser toggle and Discord leaderboard links are implemented; see
  [PUBLIC_DASHBOARD.md](PUBLIC_DASHBOARD.md) for publication requirements.

Source changes take effect in the running bot after its next normal restart.

## Working constraints

- Preserve current Discord commands, scoring, server isolation, and stored history.
- Never commit `.env`, tokens, databases, backups, or runtime logs.
- Test against temporary databases rather than modifying the live database.
- Keep the operations dashboard private to Tailscale. Publish only the separate
  public scoreboards through Funnel; private diagnostics and Discord IDs stay
  out of the public API.
