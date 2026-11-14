# Daily Podium roadmap

Updated: 2026-11-14

## First priority: Tailscale dashboard

Build a private, read-only dashboard on the Beelink server (`bee`) for inspecting
the Discord bot's status, configuration, scores, and history. See
[DASHBOARD_PLAN.md](DASHBOARD_PLAN.md) for the proposed first version and rollout.

Status: first dashboard version implemented and tested in source; not deployed.
See [DASHBOARD.md](DASHBOARD.md) to start it manually. The running Discord bot has
been left untouched.

## Other feature ideas to retain

### Pending operations TODO: permanent systemd services

- [ ] Create separate systemd services for the Discord bot and dashboard, running
  as `stefan` from `/home/stefan/dev/Daily-Podium` with the existing `.venv`.
- [ ] Include the separate loopback public leaderboard backend (`8766`) in systemd
  setup, and retain its stable public-link key during backups/restores.
- [ ] Enable startup at boot, automatic restart after failures, graceful shutdown,
  and journal logging. Account for Tailscale readiness before dashboard startup
  and retry if its address is temporarily unavailable.
- [ ] Keep the dashboard listener on `100.118.9.35:8765` and retain the existing
  `.env` and SQLite database paths.
- [ ] Switch from the manually detached processes in a controlled handover,
  avoiding duplicate bot processes and preserving stored scores and settings.
  Do not stop/restart either live process until the user authorizes the handover.
- [ ] Schedule consistent SQLite backups separately, with retention and a tested
  restore into a scratch database.

For now, the user is running the dashboard manually with `nohup`; systemd setup
remains pending. See [DASHBOARD.md](DASHBOARD.md#run-detached) for the commands.

### Feature backlog

1. **Automatic startup, restarts, and database backups.** Use systemd for the bot
   and dashboard, with journal logs and consistent SQLite backups. Configure
   retention and verify a restore into a separate scratch database.
2. **Weekly and monthly leaderboards.** Combined and individual-game views, with
   explicit period boundaries in each Discord server's timezone. Define how to
   aggregate daily points and handle scoring changes before implementation.
3. **Personal statistics.** A `!stats` command showing participation, wins,
   average scores, and personal bests, separated by game where appropriate.
4. **Participation streaks.** Consecutive competition days played, current/best
   streaks, and optional milestone celebrations. Define whether playing any game
   or all three counts, and handle missing days consistently.
5. **Optional deadline reminders.** A configurable reminder before cutoff,
   showing missing games. Avoid unsolicited individual mentions by default;
   persist delivery state so restarts do not repeat reminders.
6. **Daily winner highlights.** Highlight each game's winner alongside the
   combined daily podium. Preserve ties, failures, pagination, frozen results,
   and restart-safe posting.
7. **Clearer rejected-submission feedback.** Review existing feedback and improve
   explanations for duplicates, wrong puzzle dates, unsupported Share text, and
   submissions after cutoff. Provide a useful next step for the player.

## Already implemented in source

- Combined daily leaderboards and scheduled final boards.
- Individual game views: `!lb sizeitup`, `!lb timeguessr`, `!lb metazooa`.
- Date filters and a slash-command game selector.
- Persistent scores, configuration, and frozen final boards in SQLite.
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
