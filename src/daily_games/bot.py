import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .calendar import (
    ANCHOR_DAY,
    ANCHORS,
    active_day,
    boundary,
    closes_at,
    parse_cutoff,
    post_due_at,
    validate_timezone,
)
from .discord_views import (
    MAUVE,
    compact_boards,
    daily_highlights,
    period_embed,
    player_name,
    player_stats_embed,
    player_stats_snapshot,
    weekly_embed,
    weekly_snapshot,
)
from .health import HealthReporter
from .i18n import command_error, parser_error, phrase, scoring_label
from .models import DEFAULT_GAMES, GAME_TITLES, GAME_URLS, Game, ServerConfig
from .parsers import parse_message
from .periods import period_snapshot, period_start
from .presentation import game_snapshot, make_snapshot, score_label
from .public_links import leaderboard_links
from .rotation import validate_lineup
from .service import accept_result, requested_day
from .storage import Store
from .streaks import streak_lengths, streak_reactions
from .timeguessr import current_daily_puzzle, next_reset
from .wiki_daily import fetch_route

log = logging.getLogger(__name__)
ScoringMode = Literal["players_beaten", "placement"]
ResultMode = Literal["channel", "thread"]
Language = Literal["en", "de"]
AnnouncementStyle = Literal["standard", "compact"]
NumberedGame = Literal["timeguessr", "sizeitup", "metazooa"]
GameName = Literal["timeguessr", "sizeitup", "metazooa", "wikigame"]
RotationAction = Literal["show", "set", "inherit"]
GLOBAL_CALENDAR = ServerConfig(0, 0, 0, "Europe/Berlin", "00:00", "players_beaten", ANCHOR_DAY)


def lineup_label(games) -> str:
    return " · ".join(GAME_TITLES[game] for game in games)


class CommandSyncError(RuntimeError):
    """Discord denied access while registering commands during startup."""


def board_marker(guild_id: int, day: date) -> str:
    return f"daily-games:{guild_id}:{day.isoformat()}"


def prompt_marker(guild_id: int, day: date) -> str:
    return f"daily-games-prompt:{guild_id}:{day.isoformat()}"


def links_marker(guild_id: int, day: date) -> str:
    return f"daily-games-links:{guild_id}:{day.isoformat()}"


def status_embed(
    store: Store,
    cfg: ServerConfig,
    day: date,
    timeguessr_reset: datetime | None,
    *,
    scheduled: bool = False,
    thread_id: int | None = None,
) -> discord.Embed:
    language = cfg.language
    expected = []
    lineup = store.rotation(cfg.guild_id).games_on(day)
    for game in lineup:
        if game == Game.WIKI:
            expected.append(
                f"{GAME_TITLES[game]}: "
                f"[{phrase(language, 'Play daily', 'Tagesrätsel spielen')}](<{GAME_URLS[game]}>)"
            )
            continue
        override = store.puzzle_for_day(cfg.guild_id, game, day)
        puzzle = override or str(ANCHORS[game] + (day - ANCHOR_DAY).days)
        label = (
            phrase(language, "expected ", "erwartet ")
            if game in (Game.TIMEGUESSR, Game.SIZE_IT_UP)
            else ""
        )
        identity = f"#{puzzle}" if game == Game.SIZE_IT_UP else puzzle
        if game == Game.SIZE_IT_UP:
            identity += f" ({day.isoformat()})"
        expected.append(
            f"{GAME_TITLES[game]}: {label}`{identity}` · "
            f"[{phrase(language, 'Play daily', 'Tagesrätsel spielen')}](<{GAME_URLS[game]}>)"
        )
    deadline = int(closes_at(day, cfg).timestamp())
    if Game.TIMEGUESSR not in lineup:
        reset_note = ""
    elif timeguessr_reset:
        reset_note = phrase(
            language,
            f"TimeGuessr's [live timer](https://timeguessr.com/finalscoredaily) says its next "
            f"daily starts <t:{int(timeguessr_reset.timestamp())}:f>. "
            "If it still shows the previous puzzle, wait; that puzzle cannot count "
            "for this competition day.",
            "Laut [Live-Timer](https://timeguessr.com/finalscoredaily) beginnt das nächste "
            f"TimeGuessr-Tagesrätsel <t:{int(timeguessr_reset.timestamp())}:f>. "
            "Falls noch das alte Rätsel erscheint, warte bitte: Es zählt heute nicht mehr.",
        )
    else:
        reset_note = phrase(
            language,
            "TimeGuessr may reset later than this server's cutoff. "
            "Check its timer and wait for the new puzzle before sharing.",
            "TimeGuessr setzt möglicherweise später zurück als dieser Server. "
            "Prüfe den Timer und warte vor dem Teilen auf das neue Rätsel.",
        )
    heading = (
        phrase(language, "Play today", "Heute spielen")
        if scheduled
        else phrase(language, "Competition", "Wettbewerb")
    )
    result_label = phrase(language, "Results", "Ergebnisse")
    board_label = phrase(language, "Boards", "Ranglisten")
    deadline_label = phrase(language, "Deadline", "Abgabe bis")
    board_destination = (
        thread_id if thread_id and cfg.announcement_style == "compact" else cfg.output_channel_id
    )
    embed = discord.Embed(
        title=f"{heading} · {day}",
        color=MAUVE,
        description=(
            f"**{result_label}:** <#{thread_id or cfg.input_channel_id}> · "
            f"**{board_label}:** <#{board_destination}>\n"
            f"**{deadline_label}:** <t:{deadline}:f> (<t:{deadline}:R>)\n"
            + "\n".join(expected)
            + (f"\n\n{reset_note}" if reset_note else "")
        ),
    )
    embed.set_footer(
        text=(
            prompt_marker(cfg.guild_id, day)
            if scheduled
            else phrase(
                language,
                f"{cfg.timezone} · {cfg.cutoff} · {scoring_label(language, cfg.scoring)} · "
                "!weekly for weekly stats",
                f"{cfg.timezone} · {cfg.cutoff} · {scoring_label(language, cfg.scoring)} · "
                "!weekly für die Wochenwertung",
            )
        )
    )
    return embed


def thread_prompt_embed(
    cfg: ServerConfig, day: date, thread_id: int | None = None
) -> discord.Embed:
    destination = (
        f"<#{thread_id}>"
        if thread_id
        else phrase(cfg.language, "the thread below this post", "den Thread unter diesem Beitrag")
    )
    if cfg.announcement_style == "compact":
        description = phrase(
            cfg.language,
            f"Open {destination}, pick a game from the pinned guide and paste "
            "your Share result straight into the thread.",
            f"Öffne {destination}, wähle ein Spiel aus der angepinnten Anleitung "
            "und poste deinen Teilen-Text direkt im Thread.",
        )
    else:
        description = phrase(
            cfg.language,
            f"Open {destination}, pick a game from the pinned guide and paste "
            f"your Share result into the thread. Final standings go to <#{cfg.output_channel_id}>.",
            f"Öffne {destination}, wähle ein Spiel aus der angepinnten Anleitung "
            "und poste deinen Teilen-Text im Thread. "
            f"Die Tageswertung erscheint in <#{cfg.output_channel_id}>.",
        )
    embed = discord.Embed(
        title=f"{phrase(cfg.language, 'Play today', 'Heute spielen')} · {day}",
        color=MAUVE,
        description=description,
    )
    embed.set_footer(text=prompt_marker(cfg.guild_id, day))
    return embed


def thread_guide_embed(
    store: Store, cfg: ServerConfig, day: date, thread_id: int, reset: datetime | None = None
) -> discord.Embed:
    embed = status_embed(store, cfg, day, reset, scheduled=True, thread_id=thread_id)
    instructions = phrase(
        cfg.language,
        "**How to join**\nPick a game below. When you finish, use the game's Share button "
        "to copy your result, then paste it as a message in this thread. "
        "Look for ✅ to see that it counted.",
        "**So machst du mit**\nWähle unten ein Spiel. Kopiere danach dein Ergebnis über "
        "„Teilen“ und poste den Text als Nachricht direkt in diesem Thread. "
        "Ein ✅ zeigt dir, dass dein Ergebnis zählt.",
    )
    embed.description = f"{instructions}\n\n{embed.description}"
    embed.set_footer(text=links_marker(cfg.guild_id, day))
    return embed


def compact_announcement_embed(
    cfg: ServerConfig, day: date, thread_id: int, monthly: dict, yesterday: dict | None
) -> discord.Embed:
    """One short, editable parent post; detailed results stay in its thread."""
    language = cfg.language
    winners = (
        [row for row in yesterday["rows"] if row["rank"] == 1 and row["total"] > 0]
        if yesterday
        else []
    )
    winner_names = ", ".join(player_name(row["name"]) for row in winners[:3])
    if len(winners) > 3:
        winner_names += f" +{len(winners) - 3}"
    winner_line = phrase(
        language,
        f"**Yesterday's winner:** {winner_names}" if winners else "**Yesterday:** No winner yet",
        f"**Gestern gewonnen:** {winner_names}" if winners else "**Gestern:** noch kein Sieger",
    )
    leaders = monthly["rows"][:3]
    monthly_lines = [
        f"{row['rank']}. {player_name(row['name'])} · {row['points']} "
        f"{phrase(language, 'pts', 'Pkt.')}"
        for row in leaders
    ]
    if leaders:
        hidden_ties = sum(
            row["rank"] == leaders[-1]["rank"] for row in monthly["rows"][len(leaders) :]
        )
        if hidden_ties:
            monthly_lines.append(
                phrase(
                    language,
                    f"+{hidden_ties} tied at rank {leaders[-1]['rank']}",
                    f"+{hidden_ties} gleichauf auf Platz {leaders[-1]['rank']}",
                )
            )
    if not monthly_lines:
        monthly_lines = [
            phrase(language, "No finalized days yet.", "Noch keine abgeschlossenen Spieltage.")
        ]
    heading = phrase(language, "Daily Games are back", "Die Daily Games gehen wieder los")
    embed = discord.Embed(
        title=f"{heading} · {day}",
        color=MAUVE,
        description=(
            f"{winner_line}\n"
            f"**{phrase(language, 'Monthly top 3', 'Monats-Top-3')}:**\n"
            + "\n".join(monthly_lines)
            + "\n\n"
            + phrase(
                language,
                f"Want to join? Open <#{thread_id}> for the pinned game links. "
                "Paste your Share result straight into that thread.",
                f"Mitmachen? Die Spiellinks sind in <#{thread_id}> angepinnt. "
                "Kopiere dein Ergebnis über „Teilen“ und poste den Text direkt im Thread.",
            )
        ),
    )
    embed.set_footer(text=prompt_marker(cfg.guild_id, day))
    return embed


def board_embeds(
    snapshot: dict,
    guild_id: int,
    *,
    final: bool,
    scheduled: bool = False,
    game: Game | None = None,
    language: str = "en",
) -> list[discord.Embed]:
    """Page embeds to respect Discord limits without silently truncating players."""
    day = date.fromisoformat(snapshot["day"])
    heading = (
        phrase(language, "Final standings", "Endstand")
        if final
        else phrase(language, "Current standings · provisional", "Aktueller Stand · vorläufig")
    )
    title = "Daily Games"
    short_names = {
        Game.TIMEGUESSR: "TG",
        Game.SIZE_IT_UP: "Size",
        Game.METAZOOA: "Zoo",
        Game.WIKI: "Wiki",
    }
    games = [
        (Game(item), short_names[Game(item)]) for item in snapshot.get("lineup", DEFAULT_GAMES)
    ]
    if game is not None:
        snapshot = game_snapshot(snapshot, game)
        title = GAME_TITLES[game]
        games = [(game, title)]
    lines = []
    for row in snapshot["rows"]:
        name = discord.utils.escape_markdown(discord.utils.escape_mentions(row["name"]))[:80]
        scores = " · ".join(
            f"{label}: {score_label(game, row['games'].get(game.value), language)}"
            for game, label in games
        )
        points_unit = phrase(language, "pts", "Pkt.")
        lines.append(f"**{row['rank']}. {name} — {row['total']} {points_unit}**\n{scores}")
    pages, page = [], ""
    for line in lines or [
        phrase(
            language,
            "No results for this competition day.",
            "Keine Ergebnisse für diesen Spieltag.",
        )
    ]:
        if len(page) + len(line) + 2 > 3500:
            pages.append(page)
            page = ""
        page += ("\n\n" if page else "") + line
    pages.append(page)
    embeds = []
    for index, description in enumerate(pages, start=1):
        embed = discord.Embed(
            title=f"{title} · {day} · {heading}",
            description=description,
            color=discord.Color.gold() if final else discord.Color.teal(),
        )
        mode = scoring_label(language, snapshot["scoring"])
        cutoff_label = phrase(language, "cutoff", "Stichtag")
        embed.add_field(
            name=phrase(language, "Competition rules", "Wettbewerbsregeln"),
            value=(
                f"{snapshot['timezone']} · {cutoff_label} {snapshot['cutoff']} · {mode}\n"
                + " · ".join(f"{label} = {GAME_TITLES[item]}" for item, label in games)
                + "\n"
                + phrase(
                    language,
                    "Missing: — · Failed: 0 points · Ties share rank",
                    "Fehlt: — · Fehlversuch: 0 Punkte · Gleichstände teilen den Rang",
                )
            ),
            inline=False,
        )
        marker = (
            board_marker(guild_id, day)
            if scheduled
            else phrase(language, "Final view", "Endstand")
            if final
            else phrase(language, "Live view", "Live-Ansicht")
        )
        embed.set_footer(text=f"{marker} · page {index}/{len(pages)}")
        embeds.append(embed)
    return embeds


class DailyGamesBot(commands.Bot):
    def __init__(self, store: Store, dev_guild_id: int | None = None):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.store = store
        self.wiki_route_lock = asyncio.Lock()
        self.wiki_retry: dict[date, float] = {}
        self.dev_guild_id = dev_guild_id
        self.guild_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.needs_recovery = {cfg.guild_id for cfg in store.configs()}
        self.tree.on_error = self.on_app_command_error
        database = store.db.execute("PRAGMA database_list").fetchone()[2]
        self.health = HealthReporter(Path(database)) if database else None

    async def ensure_wiki_route(self, day: date):
        if self.store.wiki_route(day) is not None:
            return
        async with self.wiki_route_lock:
            now = monotonic()
            self.wiki_retry = {key: due for key, due in self.wiki_retry.items() if due > now}
            if self.store.wiki_route(day) is not None or day in self.wiki_retry:
                return
            route = await fetch_route(day)
            if route is None:
                self.wiki_retry[day] = monotonic() + 60
                log.warning("Wiki Game route unavailable for %s", day)
                return
            if self.store.wiki_route(day) is None:
                self.store.register_wiki_route(day, *route)

    async def daily_reset(self, cfg: ServerConfig, day: date, now: datetime):
        lineup = self.store.rotation(cfg.guild_id).games_on(day)
        if Game.WIKI in lineup:
            await self.ensure_wiki_route(day)
        return await next_reset(now) if Game.TIMEGUESSR in lineup else None

    def report_health(self, **changes):
        if self.health is None:
            return
        try:
            self.health.record(**changes)
        except OSError:
            log.warning("Could not update local bot health record", exc_info=True)

    async def setup_hook(self):
        await self.add_cog(CompetitionCommands(self))
        try:
            if self.dev_guild_id:
                guild = discord.Object(id=self.dev_guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                log.info("Slash commands synced to development server %s", self.dev_guild_id)
            else:
                await self.tree.sync()
                log.info("Slash commands synced globally")
        except discord.Forbidden as exc:
            if self.dev_guild_id:
                detail = (
                    f"Could not register slash commands in development server {self.dev_guild_id}. "
                    "Check DEV_GUILD_ID in .env: right-click the server and Copy Server ID "
                    "(not a channel or application ID). Install this same bot application "
                    "to that server using Guild Install with bot and applications.commands scopes."
                )
            else:
                detail = (
                    "Could not register global slash commands. Check the application's "
                    "installation settings and reinstall it with bot and "
                    "applications.commands scopes."
                )
            raise CommandSyncError(f"{detail} Discord error: {exc.code} ({exc.text}).") from exc
        self.daily_tick.start()

    async def close(self):
        self.daily_tick.cancel()
        self.report_health(gateway="stopped")
        await super().close()

    async def on_ready(self):
        self.report_health(gateway="connected", gateway_at=datetime.now(UTC).isoformat())
        log.info(
            "Connected as %s (%s); %s configured servers",
            self.user,
            self.user.id,
            len(self.store.configs()),
        )
        database = self.store.db.execute("PRAGMA database_list").fetchone()[2]
        if database:
            for cfg in self.store.configs():
                guild = self.get_guild(cfg.guild_id)
                if guild:
                    leaderboard_links(Path(database), guild.id, guild.name, "today", "all")
        await self.recover_all()

    async def on_disconnect(self):
        self.report_health(gateway="disconnected")
        self.needs_recovery.update(cfg.guild_id for cfg in self.store.configs())

    async def on_resumed(self):
        self.report_health(gateway="connected", gateway_at=datetime.now(UTC).isoformat())
        await self.recover_all()

    async def recover_all(self):
        for cfg in self.store.configs():
            if self.get_guild(cfg.guild_id):
                try:
                    async with self.guild_locks[cfg.guild_id]:
                        await self.recover_locked(cfg)
                except Exception:
                    log.exception("Recovery failed for server %s", cfg.guild_id)

    async def recover_locked(self, cfg: ServerConfig, before: discord.Message | None = None):
        """Call while holding the server lock. Advance only after processing each message."""
        channel = self.get_channel(cfg.input_channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != cfg.guild_id:
            raise ValueError("Configured results channel is unavailable.")
        cursor = self.store.cursor(cfg.guild_id)
        after = (
            discord.Object(id=cursor)
            if cursor
            else boundary(cfg.started_day, cfg.timezone, cfg.cutoff).astimezone(UTC)
        )
        # Original Discord creation times, not recovery time, determine eligibility.
        options = {"limit": None, "after": after, "oldest_first": True}
        if before is not None:
            options["before"] = before
        async for message in channel.history(**options):
            await self.ingest(message, cfg, feedback=False)
            self.store.advance_cursor(cfg.guild_id, message.id)
        for prompt in self.store.open_thread_prompts(cfg.guild_id):
            thread = self.get_channel(prompt["thread_id"])
            if thread is None:
                thread = await self.fetch_channel(prompt["thread_id"])
            if (
                not isinstance(thread, discord.Thread)
                or thread.guild.id != cfg.guild_id
                or thread.parent_id != prompt["output_channel_id"]
            ):
                raise ValueError("Configured results thread is unavailable.")
            day = date.fromisoformat(prompt["day"])
            after = (
                discord.Object(id=prompt["thread_cursor"])
                if prompt["thread_cursor"]
                else boundary(day, cfg.timezone, cfg.cutoff).astimezone(UTC)
            )
            options = {"limit": None, "after": after, "oldest_first": True}
            if before is not None:
                options["before"] = before
            async for message in thread.history(**options):
                await self.ingest(message, cfg, feedback=False)
                self.store.advance_thread_cursor(cfg.guild_id, day, message.id)
        self.needs_recovery.discard(cfg.guild_id)

    async def ingest(self, message: discord.Message, cfg: ServerConfig, *, feedback: bool):
        if message.author.bot or message.webhook_id:
            return
        parsed, errors = parse_message(message.content)
        errors = [parser_error(cfg.language, error) for error in errors]
        if not parsed and not errors:
            return
        source_day = active_day(message.created_at, cfg)
        if message.channel.id == cfg.input_channel_id:
            if self.store.mode_for_day(cfg.guild_id, source_day) == "thread":
                prompt = self.store.prompt(cfg.guild_id, source_day)
                if (
                    prompt is not None
                    and prompt["thread_id"] is not None
                    and message.created_at >= discord.utils.snowflake_time(prompt["message_id"])
                ):
                    if feedback:
                        await self.suppress_result_preview(message, cfg)
                        try:
                            await message.reply(
                                phrase(
                                    cfg.language,
                                    f"Post today's Share results in <#{prompt['thread_id']}>.",
                                    "Poste deine heutigen Spielergebnisse in "
                                    f"<#{prompt['thread_id']}>.",
                                ),
                                mention_author=False,
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                        except discord.HTTPException:
                            log.warning("Could not redirect result in server %s", cfg.guild_id)
                    return
        else:
            prompt = self.store.prompt_for_thread(cfg.guild_id, message.channel.id)
            if prompt is None:
                return
            if source_day != date.fromisoformat(prompt["day"]):
                if feedback:
                    await self.suppress_result_preview(message, cfg)
                    current = self.store.prompt(cfg.guild_id, source_day)
                    destination = (
                        f"<#{current['thread_id']}>"
                        if current is not None and current["thread_id"]
                        else "today's results thread"
                    )
                    try:
                        await message.reply(
                            phrase(
                                cfg.language,
                                f"This thread is for {prompt['day']}. "
                                f"Post today's results in {destination}.",
                                f"Dieser Thread gehört zum {prompt['day']}. "
                                f"Poste heutige Ergebnisse in {destination}.",
                            ),
                            mention_author=False,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    except discord.HTTPException:
                        log.warning("Could not redirect result in server %s", cfg.guild_id)
                return
        if message.edited_at is not None:
            # History exposes only the edited content, so we cannot reconstruct the
            # original submission or establish whether its score changed after cutoff.
            if feedback:
                await message.reply(
                    phrase(
                        cfg.language,
                        "Edited results are not imported. Paste a fresh Share result instead.",
                        "Bearbeitete Ergebnisse werden nicht importiert. "
                        "Poste einen neuen Teilen-Text.",
                    ),
                    mention_author=False,
                )
            return
        accepted, rejected = [], []
        accepted_games = set()
        lineup = self.store.rotation(cfg.guild_id).games_on(source_day)
        if Game.WIKI in lineup and any(result.game == Game.WIKI for result in parsed):
            if not self.store.board(cfg.guild_id, source_day):
                await self.ensure_wiki_route(source_day)
        for result in parsed:
            outcome = accept_result(
                self.store,
                cfg,
                result,
                user_id=message.author.id,
                display_name=message.author.display_name,
                message_id=message.id,
                channel_id=message.channel.id,
                submitted_at=message.created_at,
                allowed_channel_id=message.channel.id,
                language=cfg.language,
            )
            label = GAME_TITLES[result.game]
            if outcome.status in ("accepted", "recorded"):
                if result.game not in accepted_games:
                    accepted.append(f"{label}: {outcome.detail}")
                    accepted_games.add(result.game)
            elif outcome.status != "ignored":
                rejected.append(f"{label}: {outcome.detail}")
        if not feedback:
            return
        acknowledged = False
        try:
            await message.add_reaction("✅" if accepted else "⚠️")
            acknowledged = True
        except discord.HTTPException:
            log.warning("Could not acknowledge result in server %s", cfg.guild_id)
        if accepted and acknowledged:
            if len(accepted_games) == 1:
                day = active_day(message.created_at, cfg)
                (game,) = accepted_games
                days = self.store.player_game_days(cfg.guild_id, message.author.id, game, day)
                streak = streak_lengths(days, day, self.store.rotation(cfg.guild_id), game)
                markers = streak_reactions(streak["current"])
            else:
                # Several games can share a message. One set of reaction icons cannot
                # associate separate numbers with each game, so show the fire only.
                markers = ["🔥"]
            for emoji in markers:
                try:
                    await message.add_reaction(emoji)
                except discord.HTTPException:
                    log.warning("Could not add streak reaction in server %s", cfg.guild_id)
                    break
        # A Share message can contain a game URL. Suppress its preview without
        # changing the player's text, and leave ordinary chat links alone.
        await self.suppress_result_preview(message, cfg)
        if errors or rejected:
            try:
                await message.reply(
                    "\n".join([*accepted, *rejected, *errors])[:1800],
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                log.warning("Could not send result feedback in server %s", cfg.guild_id)

    async def suppress_result_preview(self, message: discord.Message, cfg: ServerConfig):
        if (
            not message.flags.suppress_embeds
            and message.channel.permissions_for(message.guild.me).manage_messages
        ):
            try:
                await message.edit(suppress=True)
            except discord.HTTPException:
                log.warning("Could not suppress result link preview in server %s", cfg.guild_id)

    async def on_message(self, message: discord.Message):
        if message.author.bot or message.webhook_id or message.guild is None:
            return
        cfg = self.store.config(message.guild.id)
        is_results_source = cfg and (
            message.channel.id == cfg.input_channel_id
            or self.store.prompt_for_thread(cfg.guild_id, message.channel.id) is not None
        )
        if is_results_source:
            async with self.guild_locks[cfg.guild_id]:
                try:
                    if cfg.guild_id in self.needs_recovery:
                        # Leave this live event out of history so it still gets feedback.
                        await self.recover_locked_before(cfg, message)
                    await self.ingest(message, cfg, feedback=True)
                    if message.channel.id == cfg.input_channel_id:
                        self.store.advance_cursor(cfg.guild_id, message.id)
                    else:
                        prompt = self.store.prompt_for_thread(cfg.guild_id, message.channel.id)
                        self.store.advance_thread_cursor(
                            cfg.guild_id, date.fromisoformat(prompt["day"]), message.id
                        )
                except Exception:
                    self.needs_recovery.add(cfg.guild_id)
                    log.exception("Submission processing failed for server %s", cfg.guild_id)
                    await message.reply(
                        phrase(
                            cfg.language,
                            "Could not save this result. Please try again shortly.",
                            "Ergebnis konnte nicht gespeichert werden. "
                            "Versuche es gleich noch einmal.",
                        ),
                        mention_author=False,
                    )
        await self.process_commands(message)

    async def recover_locked_before(self, cfg: ServerConfig, current: discord.Message):
        await self.recover_locked(cfg, before=current)

    @tasks.loop(seconds=30, reconnect=True)
    async def daily_tick(self):
        now = datetime.now(UTC)
        errors = 0
        for cfg in self.store.configs():
            if not self.get_guild(cfg.guild_id):
                continue
            try:
                async with self.guild_locks[cfg.guild_id]:
                    active = active_day(now, cfg)
                    if self.store.mode_for_day(cfg.guild_id, active) == "thread":
                        await self.publish_thread_prompt_safely(cfg, active, now)
                    # Always recover before freezing to include pre-cutoff messages missed offline.
                    await self.recover_locked(cfg)
                    day = cfg.started_day
                    while post_due_at(day, cfg) <= now:
                        board = self.store.board(cfg.guild_id, day)
                        if board is None:
                            snapshot = make_snapshot(
                                self.store.results(cfg.guild_id, day),
                                day,
                                cfg,
                                self.store.rotation(cfg.guild_id).games_on(day),
                            )
                            self.store.freeze(cfg.guild_id, day, cfg.output_channel_id, snapshot)
                        await self.publish_locked(cfg, day, now)
                        next_day = day + timedelta(days=1)
                        if self.store.mode_for_day(cfg.guild_id, next_day) == "channel":
                            await self.publish_prompt_locked(cfg, next_day, now)
                        day += timedelta(days=1)
            except Exception:
                errors += 1
                log.exception("Daily scheduling failed for server %s; will retry", cfg.guild_id)
        self.report_health(scheduler_at=now.isoformat(), scheduler_errors=errors)

    @daily_tick.before_loop
    async def before_daily_tick(self):
        await self.wait_until_ready()

    async def publish_locked(self, cfg: ServerConfig, day: date, now: datetime):
        board = self.store.board(cfg.guild_id, day)
        if board is None:
            return
        details = self.store.details(cfg.guild_id, day)
        if board["message_id"] is not None:
            if details:
                await self.publish_details_safely(cfg, day, now)
            return
        prompt = self.store.prompt(cfg.guild_id, day)
        compact_thread = (
            prompt is not None
            and prompt["announcement_style"] == "compact"
            and self.store.mode_for_day(cfg.guild_id, day) == "thread"
        )
        if compact_thread and prompt is not None and prompt["thread_id"] is not None:
            channel = self.get_channel(prompt["thread_id"])
            if channel is None:
                channel = await self.fetch_channel(prompt["thread_id"])
            if (
                not isinstance(channel, discord.Thread)
                or channel.guild.id != cfg.guild_id
                or channel.parent_id != prompt["output_channel_id"]
            ):
                raise ValueError("Daily results thread is unavailable for the final board.")
            if channel.archived:
                await channel.edit(archived=False)
        else:
            channel = self.get_channel(board["output_channel_id"])
            if not isinstance(channel, discord.TextChannel) or channel.guild.id != cfg.guild_id:
                raise ValueError("Daily board channel is unavailable.")
            if compact_thread:
                log.warning(
                    "Daily results thread missing; final board falls back to board channel: "
                    "server=%s day=%s",
                    cfg.guild_id,
                    day,
                )
        snapshot = json.loads(board["snapshot"])
        if details is None and board["attempted_at"] is None:
            summary = compact_boards(
                snapshot, final=True, scheduled=True, limit=10, language=cfg.language
            )[0]
            database = self.store.db.execute("PRAGMA database_list").fetchone()[2]
            guild = self.get_guild(cfg.guild_id)
            links = (
                leaderboard_links(
                    Path(database),
                    cfg.guild_id,
                    getattr(guild, "name", "Daily Games"),
                    day.isoformat(),
                    "all",
                )
                if database
                else None
            )
            if links:
                summary.description = summary.description.replace(
                    phrase(
                        cfg.language,
                        "full standings: `!lb`",
                        "vollständige Rangliste: `!lb`",
                    ),
                    phrase(
                        cfg.language,
                        "full standings in the browser",
                        "vollständige Rangliste im Browser",
                    ),
                )
                server_label = phrase(cfg.language, "This server", "Dieser Server")
                global_label = phrase(cfg.language, "Global leaderboard", "Globale Rangliste")
                summary.add_field(
                    name=phrase(
                        cfg.language,
                        "Full leaderboard in your browser",
                        "Vollständige Rangliste im Browser",
                    ),
                    value=(
                        f"[{server_label}]({links['server']}) · [{global_label}]({links['global']})"
                    ),
                    inline=False,
                )
            monday = day - timedelta(days=day.weekday())
            weekly = weekly_snapshot(
                self.store.finalized_snapshots(cfg.guild_id, monday, day),
                day,
                cfg.started_day,
            )
            extra_embeds = (
                [
                    daily_highlights(snapshot, cfg.language),
                    *board_embeds(
                        snapshot,
                        cfg.guild_id,
                        final=True,
                        language=cfg.language,
                    ),
                    weekly_embed(weekly, cfg.language),
                ]
                if snapshot["rows"]
                else []
            )
            for index, embed in enumerate(extra_embeds, start=1):
                embed.color = MAUVE
                # Keep the weekly coverage explanation when adding the reconciliation marker.
                prior_footer = embed.footer.text or ""
                embed.set_footer(
                    text=(
                        f"Daily Podium · {day} · Details {index}/{len(extra_embeds)}"
                        + (
                            f" · {prior_footer}"
                            if embed.title.startswith(("This week", "Diese Woche"))
                            else ""
                        )
                    )
                )
            self.store.plan_details(
                cfg.guild_id,
                day,
                {
                    "summary": summary.to_dict(),
                    "embeds": [embed.to_dict() for embed in extra_embeds],
                },
            )
            details = self.store.details(cfg.guild_id, day)
        # Already-attempted old boards retain their previous layout and page markers.
        delivery = json.loads(details["payload"]) if details else None
        embeds = (
            [discord.Embed.from_dict(delivery["summary"])]
            if delivery
            else (
                board_embeds(
                    snapshot, cfg.guild_id, final=True, scheduled=True, language=cfg.language
                )
            )
        )
        extra = (
            [discord.Embed.from_dict(item) for item in delivery["embeds"]]
            if delivery and compact_thread and isinstance(channel, discord.Thread)
            else []
        )
        bundle = [*embeds, *extra]
        can_bundle = bool(extra) and len(bundle) <= 10 and sum(map(len, bundle)) <= 6000
        planned_footers = {embed.footer.text for embed in (bundle if can_bundle else embeds)}
        marker = board_marker(cfg.guild_id, day)
        # If a send timed out or we crashed after Discord accepted it, find the existing post.
        # Check each page separately: a crash partway through a large board resumes its pages.
        found = {}
        if board["attempted_at"]:
            attempt = datetime.fromisoformat(board["attempted_at"])
            async for message in channel.history(
                limit=None, after=attempt - timedelta(seconds=5), oldest_first=True
            ):
                if message.author.id != self.user.id:
                    continue
                for embed in message.embeds:
                    footer = embed.footer.text or ""
                    if footer.startswith(marker + " · page ") or footer in planned_footers:
                        found[footer] = message.id
        if not board["attempted_at"]:
            self.store.mark_attempt(cfg.guild_id, day, now)
        last_id = None
        if can_bundle and embeds[0].footer.text not in found:
            message = await channel.send(
                embeds=bundle, allowed_mentions=discord.AllowedMentions.none(), silent=True
            )
            last_id = message.id
            found.update({embed.footer.text: message.id for embed in bundle})
        else:
            for embed in embeds:
                footer = embed.footer.text
                if footer in found:
                    last_id = found[footer]
                    continue
                message = await channel.send(
                    embed=embed, allowed_mentions=discord.AllowedMentions.none()
                )
                last_id = message.id
        if can_bundle and all(found.get(embed.footer.text) == last_id for embed in bundle):
            # Save this before the board ID so a crash can't repost the bundled details.
            self.store.update_details(cfg.guild_id, day, thread_id=channel.id, complete=True)
        self.store.mark_posted(cfg.guild_id, day, last_id)
        self.report_health(
            last_final_post_at=datetime.now(UTC).isoformat(),
            last_final_post_day=day.isoformat(),
            last_final_post_server_id=str(cfg.guild_id),
        )
        log.info("Daily board posted/reconciled: server=%s day=%s", cfg.guild_id, day)
        if details:
            await self.publish_details_safely(cfg, day, now)

    async def publish_prompt_locked(self, cfg: ServerConfig, day: date, now: datetime):
        """Post today's links after the previous board, reconciling ambiguous sends."""
        if self.store.mode_for_day(cfg.guild_id, day) != "channel":
            return
        if day != active_day(now, cfg):
            return
        if now < boundary(day, cfg.timezone, "08:05").astimezone(UTC):
            return
        board = self.store.board(cfg.guild_id, day - timedelta(days=1))
        if board is None or board["message_id"] is None:
            return
        self.store.plan_prompt(cfg.guild_id, day, board["output_channel_id"])
        prompt = self.store.prompt(cfg.guild_id, day)
        if prompt["message_id"] is not None:
            await self.unpin_previous_prompts_locked(cfg.guild_id, day)
            return
        if prompt["retry_at"] and datetime.fromisoformat(prompt["retry_at"]) > now:
            return
        channel = self.get_channel(prompt["output_channel_id"])
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != cfg.guild_id:
            raise ValueError("Daily links channel is unavailable.")
        marker = prompt_marker(cfg.guild_id, day)
        if prompt["attempted_at"]:
            attempt = datetime.fromisoformat(prompt["attempted_at"])
            async for message in channel.history(
                limit=None, after=attempt - timedelta(seconds=5), oldest_first=True
            ):
                if message.author.id == self.user.id and any(
                    embed.footer.text == marker for embed in message.embeds
                ):
                    self.store.mark_prompt_posted(cfg.guild_id, day, message.id)
                    await self.unpin_previous_prompts_locked(cfg.guild_id, day)
                    return
        expected_puzzle = int(
            self.store.puzzle_for_day(cfg.guild_id, Game.TIMEGUESSR, day)
            or ANCHORS[Game.TIMEGUESSR] + (day - ANCHOR_DAY).days
        )
        live_puzzle = (
            await current_daily_puzzle()
            if Game.TIMEGUESSR in self.store.rotation(cfg.guild_id).games_on(day)
            else None
        )
        if (
            live_puzzle is not None
            and live_puzzle < expected_puzzle
            and now < boundary(day, cfg.timezone, "12:00").astimezone(UTC)
        ):
            self.store.defer_prompt(cfg.guild_id, day, now + timedelta(minutes=5))
            log.info(
                "Daily links waiting for TimeGuessr: server=%s day=%s live=%s expected=%s",
                cfg.guild_id,
                day,
                live_puzzle,
                expected_puzzle,
            )
            return
        embed = status_embed(
            self.store, cfg, day, await self.daily_reset(cfg, day, now), scheduled=True
        )
        if not prompt["attempted_at"]:
            self.store.mark_prompt_attempt(cfg.guild_id, day, now)
        message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        self.store.mark_prompt_posted(cfg.guild_id, day, message.id)
        await self.unpin_previous_prompts_locked(cfg.guild_id, day)
        log.info("Daily links posted/reconciled: server=%s day=%s", cfg.guild_id, day)

    async def publish_thread_prompt_safely(self, cfg: ServerConfig, day: date, now: datetime):
        try:
            await self.publish_thread_prompt_locked(cfg, day, now)
        except Exception:
            log.exception("Daily results thread unavailable: server=%s day=%s", cfg.guild_id, day)

    async def publish_thread_prompt_locked(self, cfg: ServerConfig, day: date, now: datetime):
        """Create the day's pinned entry point, then enrich it at the morning hour."""
        if day != active_day(now, cfg) or self.store.mode_for_day(cfg.guild_id, day) != "thread":
            return
        self.store.plan_prompt(cfg.guild_id, day, cfg.input_channel_id, cfg.announcement_style)
        prompt = self.store.prompt(cfg.guild_id, day)
        style = prompt["announcement_style"]
        presentation_cfg = replace(cfg, announcement_style=style)
        channel = self.get_channel(prompt["output_channel_id"])
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != cfg.guild_id:
            raise ValueError("Daily results parent channel is unavailable.")
        if prompt["message_id"] is None:
            found = None
            if prompt["attempted_at"]:
                attempt = datetime.fromisoformat(prompt["attempted_at"])
                async for message in channel.history(
                    limit=None, after=attempt - timedelta(seconds=5), oldest_first=True
                ):
                    if message.author.id == self.user.id and any(
                        embed.footer.text == prompt_marker(cfg.guild_id, day)
                        for embed in message.embeds
                    ):
                        found = message
                        break
            if found is None:
                if not prompt["attempted_at"]:
                    self.store.mark_prompt_attempt(cfg.guild_id, day, now)
                found = await channel.send(
                    embed=thread_prompt_embed(presentation_cfg, day),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            self.store.mark_prompt_posted(cfg.guild_id, day, found.id)
            prompt = self.store.prompt(cfg.guild_id, day)
        thread = self.get_channel(prompt["thread_id"] or prompt["message_id"])
        if thread is None:
            try:
                thread = await self.fetch_channel(prompt["thread_id"] or prompt["message_id"])
            except discord.NotFound:
                thread = await channel.create_thread(
                    name=phrase(
                        cfg.language,
                        f"Daily Games · {day} · results",
                        f"Daily Games · {day} · Ergebnisse",
                    ),
                    message=discord.Object(id=prompt["message_id"]),
                    auto_archive_duration=1440,
                    reason="Daily game results",
                )
        if (
            not isinstance(thread, discord.Thread)
            or thread.guild.id != cfg.guild_id
            or thread.parent_id != channel.id
        ):
            raise ValueError("Daily results thread does not match its parent channel.")
        if thread.locked:
            raise ValueError("Daily results thread is locked by a moderator.")
        if thread.archived:
            await thread.edit(archived=False)
        if prompt["thread_id"] is None:
            self.store.mark_prompt_thread(cfg.guild_id, day, thread.id)
            prompt = self.store.prompt(cfg.guild_id, day)
        parent_message = None
        morning_due = now >= boundary(day, cfg.timezone, "08:05").astimezone(UTC)
        # Also update a legacy opening post the first time its thread gets a guide.
        refresh_parent = not prompt["linked"] or prompt["guide_message_id"] is None
        update_header = (
            style == "compact" and morning_due and (not prompt["header_updated"] or refresh_parent)
        )
        if refresh_parent or not prompt["pinned"] or update_header:
            parent_message = await channel.fetch_message(prompt["message_id"])
        if refresh_parent and not update_header:
            await parent_message.edit(embed=thread_prompt_embed(presentation_cfg, day, thread.id))
            self.store.mark_prompt_flag(cfg.guild_id, day, "linked")
        if not prompt["pinned"]:
            try:
                if not parent_message.pinned:
                    await parent_message.pin(reason="Current Daily Games results thread")
                self.store.mark_prompt_flag(cfg.guild_id, day, "pinned")
            except discord.HTTPException:
                log.warning("Could not pin daily results prompt in server %s", cfg.guild_id)
        if self.store.prompt(cfg.guild_id, day)["pinned"]:
            await self.unpin_previous_prompts_locked(cfg.guild_id, day)
        if update_header:
            yesterday = self.store.board(cfg.guild_id, day - timedelta(days=1))
            monthly = period_snapshot(
                self.store.finalized_snapshots(
                    cfg.guild_id, day.replace(day=1), day - timedelta(days=1)
                ),
                day,
                cfg.started_day,
                "month",
            )
            await parent_message.edit(
                embed=compact_announcement_embed(
                    cfg,
                    day,
                    thread.id,
                    monthly,
                    json.loads(yesterday["snapshot"]) if yesterday else None,
                )
            )
            self.store.mark_prompt_flag(cfg.guild_id, day, "linked")
            self.store.mark_prompt_flag(cfg.guild_id, day, "header_updated")

        await self.publish_thread_guide(thread, presentation_cfg, day, now, morning_due)

    async def publish_thread_guide(
        self, thread: discord.Thread, cfg: ServerConfig, day: date, now: datetime, morning_due: bool
    ):
        prompt = self.store.prompt(cfg.guild_id, day)
        guide_id = prompt["guide_message_id"]
        guide = None
        if guide_id is None:
            # Recover an uncertain send or a guide posted by the previous version.
            async for message in thread.history(
                limit=None, after=boundary(day, cfg.timezone, cfg.cutoff), oldest_first=True
            ):
                if message.author.id == self.user.id and any(
                    item.footer.text == links_marker(cfg.guild_id, day) for item in message.embeds
                ):
                    guide = message
                    break
            if guide is None:
                guide = await thread.send(
                    embed=thread_guide_embed(self.store, cfg, day, thread.id),
                    allowed_mentions=discord.AllowedMentions.none(),
                    silent=True,
                )
            guide_id = guide.id
            self.store.mark_prompt_guide(cfg.guild_id, day, guide_id)
            prompt = self.store.prompt(cfg.guild_id, day)
        if not prompt["guide_pinned"]:
            try:
                if guide is None:
                    guide = await thread.fetch_message(guide_id)
                if not guide.pinned:
                    await guide.pin(reason="Today's game links and submission instructions")
                self.store.mark_prompt_flag(cfg.guild_id, day, "guide_pinned")
            except discord.HTTPException:
                log.warning("Could not pin daily game guide in server %s", cfg.guild_id)
        if not morning_due or prompt["morning_updated"]:
            return
        expected_puzzle = int(
            self.store.puzzle_for_day(cfg.guild_id, Game.TIMEGUESSR, day)
            or ANCHORS[Game.TIMEGUESSR] + (day - ANCHOR_DAY).days
        )
        live_puzzle = (
            await current_daily_puzzle()
            if Game.TIMEGUESSR in self.store.rotation(cfg.guild_id).games_on(day)
            else None
        )
        if (
            live_puzzle is not None
            and live_puzzle < expected_puzzle
            and now < boundary(day, cfg.timezone, "12:00").astimezone(UTC)
        ):
            return
        if guide is None:
            guide = await thread.fetch_message(guide_id)
        await guide.edit(
            embed=thread_guide_embed(
                self.store, cfg, day, thread.id, await self.daily_reset(cfg, day, now)
            )
        )
        self.store.mark_prompt_flag(cfg.guild_id, day, "morning_updated")

    async def unpin_previous_prompts_locked(self, guild_id: int, day: date):
        for old in self.store.pinned_prompts(guild_id, day):
            channel = self.get_channel(old["output_channel_id"])
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                message = await channel.fetch_message(old["message_id"])
                if message.author.id == self.user.id and message.pinned:
                    await message.unpin(reason="Replaced by today's Daily Games thread")
            except discord.NotFound:
                pass
            except discord.HTTPException:
                log.warning("Could not unpin prior prompt in server %s", guild_id)
                continue
            self.store.mark_prompt_flag(guild_id, date.fromisoformat(old["day"]), "pinned", False)

    async def publish_details_safely(self, cfg: ServerConfig, day: date, now: datetime):
        details = self.store.details(cfg.guild_id, day)
        if details is None or details["complete"]:
            return
        if details["retry_at"] and datetime.fromisoformat(details["retry_at"]) > now:
            return
        try:
            await self.publish_details_locked(cfg, day, now)
        except Exception:
            # Optional thread delivery must not prevent subsequent daily boards being posted.
            self.store.update_details(cfg.guild_id, day, retry_at=now + timedelta(minutes=15))
            log.exception(
                "Daily detail thread unavailable for server=%s day=%s; retry in 15m",
                cfg.guild_id,
                day,
            )

    async def publish_details_locked(self, cfg: ServerConfig, day: date, now: datetime):
        board = self.store.board(cfg.guild_id, day)
        details = self.store.details(cfg.guild_id, day)
        payload = json.loads(details["payload"])
        if not payload["embeds"]:
            self.store.update_details(cfg.guild_id, day, complete=True)
            return
        result_prompt = (
            self.store.prompt(cfg.guild_id, day)
            if self.store.mode_for_day(cfg.guild_id, day) == "thread"
            else None
        )
        use_results_thread = result_prompt is not None and result_prompt["thread_id"] is not None
        parent_id = (
            result_prompt["output_channel_id"] if use_results_thread else board["output_channel_id"]
        )
        channel = self.get_channel(parent_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != cfg.guild_id:
            raise ValueError("Daily board channel is unavailable.")
        permissions = channel.permissions_for(channel.guild.me)
        required = ["send_messages_in_threads", "read_message_history", "embed_links"]
        if not use_results_thread:
            required.append("create_public_threads")
        if any(not getattr(permissions, name) for name in required):
            raise ValueError(
                "Daily details need Create Public Threads, Send Messages in Threads, "
                "Read Message History, and Embed Links in the board channel."
            )
        # A message-started thread has the parent's message ID, even after an ambiguous timeout.
        thread_id = (
            result_prompt["thread_id"]
            if use_results_thread
            else details["thread_id"] or board["message_id"]
        )
        thread = self.get_channel(thread_id)
        if thread is None:
            try:
                thread = await self.fetch_channel(thread_id)
            except discord.NotFound:
                if use_results_thread:
                    raise ValueError("Daily results thread was deleted.") from None
                thread = await channel.create_thread(
                    name=phrase(
                        cfg.language,
                        f"Daily Podium · {day} · results & weekly stats",
                        f"Daily Podium · {day} · Ergebnisse & Wochenstats",
                    ),
                    message=discord.Object(id=board["message_id"]),
                    auto_archive_duration=1440,
                    reason="Daily standings details",
                )
        if (
            not isinstance(thread, discord.Thread)
            or thread.guild.id != cfg.guild_id
            or thread.parent_id != channel.id
        ):
            raise ValueError("Daily details thread does not match its parent channel.")
        if thread.locked:
            raise ValueError("Daily details thread is locked by a moderator.")
        if thread.archived:
            await thread.edit(archived=False)
        self.store.update_details(cfg.guild_id, day, thread_id=thread.id, attempted_at=now)
        found = set()
        if details["attempted_at"]:
            attempt = datetime.fromisoformat(details["attempted_at"])
            async for message in thread.history(
                limit=None, after=attempt - timedelta(seconds=5), oldest_first=True
            ):
                if message.author.id == self.user.id:
                    found.update(embed.footer.text for embed in message.embeds)
        for item in payload["embeds"]:
            embed = discord.Embed.from_dict(item)
            if embed.footer.text not in found:
                await thread.send(
                    embed=embed, allowed_mentions=discord.AllowedMentions.none(), silent=True
                )
        self.store.update_details(cfg.guild_id, day, complete=True)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return
        while isinstance(error, (commands.HybridCommandError, commands.CommandInvokeError)):
            error = error.original
        cfg = self.store.config(ctx.guild.id) if ctx.guild else None
        language = cfg.language if cfg else "en"
        if isinstance(
            error,
            (
                commands.UserInputError,
                commands.CheckFailure,
                commands.CommandOnCooldown,
                ValueError,
            ),
        ):
            await ctx.send(
                f"{command_error(language, str(error))}\n"
                + phrase(
                    language,
                    "Use `/help` or `!help` for command usage.",
                    "Mit `/help` oder `!help` findest du die Befehle.",
                ),
                ephemeral=True,
            )
        else:
            log.error("Command failed: %s", ctx.command, exc_info=error)
            await ctx.send(
                phrase(
                    language,
                    "The command failed. Check the bot logs and try again.",
                    "Der Befehl ist fehlgeschlagen. Prüfe die Bot-Logs und versuche es erneut.",
                ),
                ephemeral=True,
            )

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        cfg = self.store.config(interaction.guild_id) if interaction.guild_id else None
        language = cfg.language if cfg else "en"
        message = phrase(
            language,
            "You cannot use that command here, or an argument was invalid.",
            "Du kannst diesen Befehl hier nicht verwenden oder ein Argument ist ungültig.",
        )
        if not isinstance(error, app_commands.CheckFailure):
            log.error("Slash command failed", exc_info=error)
            message = phrase(
                language,
                "The command failed. Check the bot logs and try again.",
                "Der Befehl ist fehlgeschlagen. Prüfe die Bot-Logs und versuche es erneut.",
            )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class CompetitionCommands(commands.Cog):
    def __init__(self, bot: DailyGamesBot):
        self.bot = bot

    def config(self, ctx: commands.Context) -> ServerConfig:
        cfg = self.bot.store.config(ctx.guild.id)
        if cfg is None:
            raise commands.BadArgument("An admin must run /setup or !setup first.")
        return cfg

    def language_for(self, ctx: commands.Context) -> str:
        cfg = self.bot.store.config(ctx.guild.id) if ctx and ctx.guild else None
        return cfg.language if cfg else "en"

    @staticmethod
    def check_thread_permissions(
        channel: discord.TextChannel, member: discord.Member, language: str = "en"
    ):
        permissions = channel.permissions_for(member)
        required = (
            "view_channel",
            "send_messages",
            "embed_links",
            "create_public_threads",
            "send_messages_in_threads",
            "read_message_history",
            "add_reactions",
            "manage_messages",
            "pin_messages",
        )
        missing = [name for name in required if not getattr(permissions, name)]
        if missing:
            raise commands.BadArgument(
                phrase(
                    language,
                    f"Missing thread-mode permissions in {channel.mention}: {', '.join(missing)}",
                    f"Für den Thread-Modus fehlen in {channel.mention} Berechtigungen: "
                    f"{', '.join(missing)}",
                )
            )

    @commands.hybrid_command(name="ping", description="Check whether the bot is online.")
    async def ping(self, ctx: commands.Context):
        await ctx.send(
            phrase(
                self.language_for(ctx),
                "Pong! Daily Games is online.",
                "Pong! Daily Games ist online.",
            )
        )

    @commands.hybrid_command(name="help", description="Show Daily Games commands and scoring.")
    async def help_command(self, ctx: commands.Context):
        language = self.language_for(ctx)
        embed = discord.Embed(
            title=phrase(language, "Daily Podium · quick guide", "Daily Podium · Kurzanleitung"),
            color=MAUVE,
            description=phrase(
                language,
                "Play a game and paste its **Share** result in the results channel.\n"
                "In thread mode, paste it in today's pinned results thread.\n"
                "`!lb` · standings and browser links\n"
                "`!lb sizeitup` / `timeguessr` / `metazooa` · one game\n"
                "`!lb yesterday` / `!lb sizeitup 2026-11-13` · another day\n"
                "`!weekly [day] [game]` · weekly points from finalized days\n"
                "`!monthly [day] [game]` · monthly points from finalized days\n"
                "`!stats [@player]` · personal stats and streaks from finalized days\n"
                "`/myday` · your accepted games and streaks, privately\n"
                "`!games [day]` · play links · `!status [day]` · puzzles and deadline\n"
                "`!rotation` · active games and the next change\n"
                "`/result_mode` · admin switch between channel and thread mode\n"
                "`!language de|en` · server language (admin)\n"
                "`!announcement_style compact|standard` · posting style (admin)\n"
                "`!ping` · connection check",
                "Spiele ein Tagesrätsel und poste den **Teilen-Text** im Ergebniskanal.\n"
                "Im Thread-Modus gehört er in den heutigen angepinnten Ergebnisthread.\n"
                "`!lb` · Rangliste und Browserlinks\n"
                "`!lb sizeitup` / `timeguessr` / `metazooa` · einzelnes Spiel\n"
                "`!lb yesterday` / `!lb sizeitup 2026-11-13` · anderer Tag\n"
                "`!weekly [day] [game]` · Wochenpunkte abgeschlossener Tage\n"
                "`!monthly [day] [game]` · Monatspunkte abgeschlossener Tage\n"
                "`!stats [@player]` · persönliche Statistik und Serien\n"
                "`/myday` · deine heutigen Ergebnisse, privat\n"
                "`!games [day]` · Spiellinks · `!status [day]` · Rätsel und Frist\n"
                "`!rotation` · aktive Spiele und nächste Änderung\n"
                "`!result_mode` · zwischen Kanal und Thread wechseln (Admin)\n"
                "`!language de|en` · Sprache einstellen (Admin)\n"
                "`!announcement_style compact|standard` · Posting-Stil (Admin)\n"
                "`!ping` · Verbindung prüfen",
            ),
        )
        embed.add_field(
            name=phrase(language, "Competition", "Wettbewerb"),
            value=phrase(
                language,
                "First accepted result per player/game counts. "
                "Edits and reposts do not improve it. "
                "Failures and missing games score zero; ties share rank. "
                "Accepted single-game posts get ✅ and 🔥 streak reactions: "
                "1️⃣–9️⃣, 🔟 at 10, 💎 at 25, 🏆 at 50, 💯 at 100. "
                "Daily results post five minutes after cutoff, with details and weekly stats "
                "in a thread.",
                "Pro Spieler und Spiel zählt das erste angenommene Ergebnis. "
                "Bearbeiten oder erneut posten verbessert es nicht. "
                "Fehlversuche und fehlende Spiele geben null Punkte; Gleichstände teilen den Rang. "
                "Angenommene Einzelspiele erhalten ✅ und 🔥 Serien-Reaktionen: "
                "1️⃣–9️⃣, 🔟 ab 10, 💎 ab 25, 🏆 ab 50, 💯 ab 100. "
                "Der Tagesstand erscheint fünf Minuten nach der Frist; Details und Wochenstats "
                "stehen im Thread.",
            ),
            inline=False,
        )
        embed.add_field(
            name=phrase(language, "Admins · Manage Server", "Admins · Server verwalten"),
            value=phrase(
                language,
                "`!setup #results` · `/puzzle_date` · `/void_result`\n"
                "Slash commands appear after global sync; `!` commands work in every server.",
                "`!setup #results` · `!puzzle_date` · `!void_result`\n"
                "Alle Befehle haben auch Slash-Versionen, wenn sie global synchronisiert sind.",
            ),
            inline=False,
        )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="games", description="Links to the three daily games.")
    async def games(self, ctx: commands.Context, day: str = "today"):
        cfg = self.bot.store.config(ctx.guild.id) if ctx.guild else None
        language = self.language_for(ctx)
        lineup = DEFAULT_GAMES
        if cfg:
            selected = requested_day(day, datetime.now(UTC), cfg)
            lineup = self.bot.store.rotation(cfg.guild_id).games_on(selected)
        await ctx.send(
            embed=discord.Embed(
                title=phrase(language, "Today's games", "Heutige Spiele"),
                color=MAUVE,
                description=" · ".join(
                    f"[{GAME_TITLES[game]}]({GAME_URLS[game]})" for game in lineup
                )
                + phrase(
                    language,
                    "\nPlay, then paste your **Share** result in the results channel.",
                    "\nSpiele mit und poste danach deinen **Teilen-Text** im Ergebniskanal.",
                ),
            )
        )

    @app_commands.command(name="myday", description="Privately check your accepted games today.")
    @app_commands.guild_only()
    async def myday(self, interaction: discord.Interaction):
        cfg = self.bot.store.config(interaction.guild_id) if interaction.guild_id else None
        if cfg is None:
            await interaction.response.send_message(
                "An admin must run `/setup` before results can be recorded here.", ephemeral=True
            )
            return
        language = cfg.language
        today = active_day(datetime.now(UTC), cfg)
        rotation = self.bot.store.rotation(cfg.guild_id)
        lineup = rotation.games_on(today)
        snapshot = make_snapshot(self.bot.store.results(cfg.guild_id, today), today, cfg, lineup)
        player = next(
            (row for row in snapshot["rows"] if row["user_id"] == interaction.user.id), None
        )
        results = player["games"] if player else {}
        game_days = {
            game: self.bot.store.player_game_days(cfg.guild_id, interaction.user.id, game, today)
            for game in Game
        }
        overall = streak_lengths(set().union(*game_days.values()), today)
        deadline = int(closes_at(today, cfg).timestamp())
        embed = discord.Embed(
            title=f"{phrase(language, 'Your games', 'Deine Spiele')} · {today}",
            description=phrase(
                language,
                f"**{len(results)}/{len(lineup)} accepted** · deadline <t:{deadline}:R>\n"
                f"🔥 Any-game streak: **{overall['current']}** current / "
                f"**{overall['best']}** best\n"
                "Points are provisional until the daily board is finalized.",
                f"**{len(results)}/{len(lineup)} angenommen** · Frist <t:{deadline}:R>\n"
                f"🔥 Spielübergreifende Serie: **{overall['current']}** aktuell / "
                f"**{overall['best']}** Rekord\n"
                "Punkte bleiben vorläufig, bis die Tageswertung abgeschlossen ist.",
            ),
            color=MAUVE,
        )
        for game in lineup:
            result = results.get(game.value)
            status = (
                phrase(language, "✅ Recorded", "✅ Gespeichert")
                + f" · {score_label(game, result, language)}"
                if result
                else phrase(language, "— Missing", "— Fehlt")
                + f" · [{phrase(language, 'Play daily', 'Tagesrätsel spielen')}]({GAME_URLS[game]})"
            )
            streak = streak_lengths(game_days[game], today, rotation, game)
            embed.add_field(
                name=GAME_TITLES[game],
                value=phrase(
                    language,
                    f"{status}\n🔥 {streak['current']} current / {streak['best']} best",
                    f"{status}\n🔥 {streak['current']} aktuell / {streak['best']} Rekord",
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="setup", description="Configure this server's daily competition.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def setup(
        self,
        ctx: commands.Context,
        results_channel: discord.TextChannel,
        board_channel: discord.TextChannel | None = None,
        timezone: str | None = None,
        cutoff: str | None = None,
        scoring: ScoringMode | None = None,
        mode: ResultMode | None = None,
    ):
        old = self.bot.store.config(ctx.guild.id)
        language = old.language if old else "en"
        timezone = validate_timezone(timezone or (old.timezone if old else "Europe/Berlin"))
        cutoff = cutoff or (old.cutoff if old else "00:00")
        parse_cutoff(cutoff)
        scoring = scoring or (old.scoring if old else "players_beaten")
        board_channel = board_channel or ctx.guild.get_channel(
            old.output_channel_id if old else results_channel.id
        )
        if not isinstance(board_channel, discord.TextChannel):
            raise commands.BadArgument(
                phrase(
                    language,
                    "Choose an accessible text channel for daily boards.",
                    "Wähle einen zugänglichen Textkanal für die Tageswertung.",
                )
            )
        now = datetime.now(UTC)
        effective_day = active_day(now, old) if old else None
        selected_mode = mode or (
            self.bot.store.mode_for_day(ctx.guild.id, effective_day + timedelta(days=1))
            if old
            else "channel"
        )
        for channel in (results_channel, board_channel):
            if channel.guild.id != ctx.guild.id:
                raise commands.BadArgument(
                    phrase(
                        language,
                        "Both channels must belong to this server.",
                        "Beide Kanäle müssen zu diesem Server gehören.",
                    )
                )
            permissions = channel.permissions_for(ctx.guild.me)
            required = ["view_channel", "send_messages", "read_message_history", "embed_links"]
            if channel.id == results_channel.id:
                required.append("add_reactions")
            missing = [name for name in required if not getattr(permissions, name)]
            if missing:
                raise commands.BadArgument(
                    phrase(
                        language,
                        f"Missing permissions in {channel.mention}: {', '.join(missing)}",
                        f"In {channel.mention} fehlen Berechtigungen: {', '.join(missing)}",
                    )
                )
        if selected_mode == "thread":
            self.check_thread_permissions(results_channel, ctx.guild.me, language)
        cfg = ServerConfig(
            ctx.guild.id,
            results_channel.id,
            board_channel.id,
            timezone,
            cutoff,
            scoring,
            old.started_day if old else now.date(),
            language,
            old.announcement_style if old else "standard",
        )
        if old is None:
            cfg = ServerConfig(
                cfg.guild_id,
                cfg.input_channel_id,
                cfg.output_channel_id,
                cfg.timezone,
                cfg.cutoff,
                cfg.scoring,
                active_day(now, cfg),
                cfg.language,
                cfg.announcement_style,
            )
        mode_start = active_day(now, cfg)
        if old is not None and mode is not None:
            mode_start += timedelta(days=1)
        async with self.bot.guild_locks[cfg.guild_id]:
            self.bot.store.configure(cfg, ctx.author.id)
            if mode is not None:
                self.bot.store.set_result_mode(cfg.guild_id, mode_start, mode, ctx.author.id)
            self.bot.needs_recovery.add(cfg.guild_id)
        embed = discord.Embed(
            title=phrase(language, "Competition configured", "Wettbewerb eingerichtet"),
            color=MAUVE,
            description=phrase(
                language,
                f"**Results:** {results_channel.mention} · **Boards:** {board_channel.mention}\n"
                f"**Cutoff:** {cutoff} ({timezone}) · post five minutes later\n"
                f"**Scoring:** {scoring_label(language, scoring)} · `!lb` for standings · "
                "`!weekly` for weekly stats"
                f"\n**Results mode:** {selected_mode}"
                + (f" (from {mode_start})" if old is not None and mode is not None else ""),
                f"**Ergebnisse:** {results_channel.mention} · "
                f"**Ranglisten:** {board_channel.mention}\n"
                f"**Stichtag:** {cutoff} ({timezone}) · Posting fünf Minuten später\n"
                f"**Wertung:** {scoring_label(language, scoring)} · `!lb` für die Rangliste · "
                "`!weekly` für die Woche"
                f"\n**Ergebnismodus:** {selected_mode}"
                + (f" (ab {mode_start})" if old is not None and mode is not None else ""),
            ),
        )
        permissions = board_channel.permissions_for(ctx.guild.me)
        if selected_mode == "channel" and (
            not permissions.create_public_threads or not permissions.send_messages_in_threads
        ):
            embed.add_field(
                name=phrase(language, "Optional detail threads", "Optionale Detail-Threads"),
                value=phrase(
                    language,
                    "Allow **Create Public Threads** and **Send Messages in Threads** "
                    "in the board channel for full results and weekly summaries below daily posts.",
                    "Erlaube **Öffentliche Threads erstellen** und "
                    "**Nachrichten in Threads senden** "
                    "im Ranglistenkanal für vollständige Ergebnisse und Wochenstatistiken.",
                ),
                inline=False,
            )
        embed.set_footer(
            text=phrase(
                language,
                "Imports today's messages. Timezone, cutoff and scoring lock after "
                "competition data exists.",
                "Heutige Nachrichten werden importiert. Zeitzone, Stichtag und Wertung "
                "sind nach ersten Ergebnissen gesperrt.",
            )
        )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="result_mode", description="Choose channel or daily-thread submissions."
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def result_mode(self, ctx: commands.Context, mode: ResultMode):
        cfg = self.config(ctx)
        channel = ctx.guild.get_channel(cfg.input_channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise commands.BadArgument(
                phrase(
                    cfg.language,
                    "Configured results channel is unavailable.",
                    "Der eingestellte Ergebniskanal ist nicht verfügbar.",
                )
            )
        if mode == "thread":
            self.check_thread_permissions(channel, ctx.guild.me, cfg.language)
        day = active_day(datetime.now(UTC), cfg) + timedelta(days=1)
        async with self.bot.guild_locks[cfg.guild_id]:
            self.bot.store.set_result_mode(cfg.guild_id, day, mode, ctx.author.id)
        await ctx.send(
            phrase(
                cfg.language,
                f"Results mode will be **{mode}** from {day}. "
                "Today's submissions stay in their current mode.",
                f"Der Ergebnismodus ist ab {day} **{mode}**. "
                "Heutige Einreichungen bleiben im bisherigen Modus.",
            ),
            ephemeral=True,
        )

    @commands.hybrid_command(
        name="rotation", description="View the game lineup or schedule a server override."
    )
    @commands.guild_only()
    async def rotation(
        self,
        ctx: commands.Context,
        action: RotationAction = "show",
        game1: GameName | None = None,
        game2: GameName | None = None,
        game3: GameName | None = None,
    ):
        cfg = self.config(ctx)
        language = cfg.language
        store = self.bot.store
        values = (game1, game2, game3)
        if action == "set":
            try:
                lineup = validate_lineup(value for value in values if value is not None)
            except ValueError as exc:
                raise commands.BadArgument(
                    phrase(
                        language,
                        "Choose exactly three distinct supported games.",
                        "Wähle genau drei unterschiedliche unterstützte Spiele.",
                    )
                ) from exc
        else:
            lineup = None
            if any(value is not None for value in values):
                raise commands.BadArgument(
                    phrase(
                        language,
                        "Only set takes game names.",
                        "Nur set erwartet Spielnamen.",
                    )
                )
        if action != "show":
            if not ctx.author.guild_permissions.manage_guild:
                raise commands.BadArgument(
                    phrase(
                        language,
                        "Changing the lineup requires Manage Server.",
                        "Zum Ändern der Rotation brauchst du „Server verwalten“.",
                    )
                )
            async with self.bot.guild_locks[cfg.guild_id]:
                effective = active_day(datetime.now(UTC), cfg) + timedelta(days=1)
                store.set_rotation(cfg.guild_id, effective, lineup, ctx.author.id)
            rotation = store.rotation(cfg.guild_id)
            games = lineup_label(rotation.games_on(effective))
            await ctx.send(
                phrase(
                    language,
                    f"Lineup from {effective}: {games}.",
                    f"Rotation ab {effective}: {games}.",
                ),
                ephemeral=True,
            )
            return

        today = active_day(datetime.now(UTC), cfg)
        rotation = store.rotation(cfg.guild_id)
        games = lineup_label(rotation.games_on(today))
        source = (
            phrase(language, "global default", "globale Vorgabe")
            if rotation.inherits_on(today)
            else phrase(language, "server override", "Server-Auswahl")
        )
        message = phrase(
            language,
            f"Current lineup ({source}): {games}",
            f"Aktuelle Rotation ({source}): {games}",
        )
        upcoming = rotation.next_change(today)
        if upcoming:
            games = lineup_label(rotation.games_on(upcoming))
            message += phrase(language, f"\nFrom {upcoming}: {games}", f"\nAb {upcoming}: {games}")
        else:
            message += phrase(language, "\nNo change scheduled.", "\nKeine Änderung geplant.")
        await ctx.send(message, ephemeral=True)

    @commands.hybrid_command(
        name="rotation_global", description="Schedule the global game lineup as bot owner."
    )
    @commands.guild_only()
    @commands.is_owner()
    async def rotation_global(
        self,
        ctx: commands.Context,
        action: Literal["set"],
        game1: GameName,
        game2: GameName,
        game3: GameName,
    ):
        language = self.language_for(ctx)
        try:
            lineup = validate_lineup((game1, game2, game3))
        except ValueError as exc:
            raise commands.BadArgument(
                phrase(
                    language,
                    "Choose exactly three distinct supported games.",
                    "Wähle genau drei unterschiedliche unterstützte Spiele.",
                )
            ) from exc
        store = self.bot.store
        now = datetime.now(UTC)
        calendars = [GLOBAL_CALENDAR, *store.configs()]
        effective = max(active_day(now, cfg) for cfg in calendars) + timedelta(days=1)
        store.set_rotation(0, effective, lineup, ctx.author.id)
        games = lineup_label(lineup)
        await ctx.send(
            phrase(
                language,
                f"Global lineup from {effective}: {games}.",
                f"Globale Rotation ab {effective}: {games}.",
            ),
            ephemeral=True,
        )

    @commands.hybrid_command(name="wiki_route", description="Register a dated Wiki Game route.")
    @commands.guild_only()
    @commands.is_owner()
    async def wiki_route(self, ctx: commands.Context, day: str, start: str, target: str):
        language = self.language_for(ctx)
        try:
            selected = date.fromisoformat(day)
            self.bot.store.register_wiki_route(selected, start, target, ctx.author.id)
        except ValueError as exc:
            if language == "de":
                raise commands.BadArgument(
                    "Prüfe Datum und Artikelnamen. "
                    "Eine gespeicherte Route kann nicht ersetzt werden."
                ) from exc
            raise commands.BadArgument(str(exc)) from exc
        await ctx.send(
            phrase(
                language,
                f"Wiki Game route registered for {selected}.",
                f"Wiki-Route für den {selected} gespeichert.",
            ),
            ephemeral=True,
        )

    @commands.hybrid_command(
        name="language", description="Choose English or German for this server."
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def language(self, ctx: commands.Context, language: Language):
        cfg = self.config(ctx)
        async with self.bot.guild_locks[cfg.guild_id]:
            self.bot.store.set_presentation(cfg.guild_id, ctx.author.id, language=language)
        await ctx.send(
            phrase(
                language,
                "This server now uses English bot messages.",
                "Der Bot schreibt auf diesem Server jetzt auf Deutsch.",
            ),
            ephemeral=True,
        )

    @commands.hybrid_command(
        name="announcement_style", description="Choose standard or compact daily posting."
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def announcement_style(self, ctx: commands.Context, style: AnnouncementStyle):
        cfg = self.config(ctx)
        async with self.bot.guild_locks[cfg.guild_id]:
            self.bot.store.set_presentation(cfg.guild_id, ctx.author.id, style=style)
        await ctx.send(
            phrase(
                cfg.language,
                f"Daily posting style set to **{style}** for new daily posts. "
                "Compact posting takes effect in thread mode; existing posts stay where they are.",
                f"Täglicher Posting-Stil für neue Tagesbeiträge: **{style}**. "
                "Kompakte Posts gelten im Thread-Modus; bestehende Beiträge bleiben unverändert.",
            ),
            ephemeral=True,
        )

    @commands.hybrid_command(
        name="leaderboard",
        aliases=["lb"],
        description="Combined or single-game standings for today or another date.",
    )
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.member)
    @app_commands.choices(
        game=[app_commands.Choice(name="All games", value="all")]
        + [app_commands.Choice(name=GAME_TITLES[item], value=item.value) for item in Game]
    )
    async def leaderboard_command(
        self, ctx: commands.Context, day: str = "today", game: str = "all"
    ):
        # Keep !lb yesterday / !lb DATE and also accept !lb GAME [DAY].
        day, game = day.casefold(), game.casefold()
        if day in {item.value for item in Game}:
            day, game = ("today" if game == "all" else game), day
        try:
            selected_game = None if game == "all" else Game(game)
        except ValueError as exc:
            raise commands.BadArgument(
                phrase(
                    self.language_for(ctx),
                    "Choose all, timeguessr, sizeitup, metazooa, or wikigame.",
                    "Wähle all, timeguessr, sizeitup, metazooa oder wikigame.",
                )
            ) from exc
        cfg = self.config(ctx)
        selected = requested_day(day, datetime.now(UTC), cfg)
        await ctx.defer()
        async with self.bot.guild_locks[cfg.guild_id]:
            await self.bot.recover_locked(cfg)
            board = self.bot.store.board(cfg.guild_id, selected)
            if board:
                snapshot = json.loads(board["snapshot"])
            else:
                snapshot = make_snapshot(
                    self.bot.store.results(cfg.guild_id, selected),
                    selected,
                    cfg,
                    self.bot.store.rotation(cfg.guild_id).games_on(selected),
                )
        final = board is not None
        if not final and selected < active_day(datetime.now(UTC), cfg):
            await ctx.send(
                phrase(
                    cfg.language,
                    "Submissions are closed for that day; its final board is pending.",
                    "Einreichungen für diesen Tag sind geschlossen; "
                    "die endgültige Rangliste folgt.",
                )
            )
        database = self.bot.store.db.execute("PRAGMA database_list").fetchone()[2]
        links = (
            leaderboard_links(
                Path(database),
                cfg.guild_id,
                getattr(ctx.guild, "name", "Daily Games"),
                selected.isoformat(),
                game,
            )
            if database
            else None
        )
        embeds = compact_boards(
            snapshot,
            final=final,
            game=selected_game,
            limit=10 if links else None,
            language=cfg.language,
        )
        if links:
            server_label = phrase(cfg.language, "This server", "Dieser Server")
            global_label = phrase(cfg.language, "Global leaderboard", "Globale Rangliste")
            embeds[-1].add_field(
                name=phrase(
                    cfg.language,
                    "Full leaderboard in your browser",
                    "Vollständige Rangliste im Browser",
                ),
                value=(
                    f"[{server_label}]({links['server']}) · [{global_label}]({links['global']})"
                ),
                inline=False,
            )
        for embed in embeds:
            await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="weekly", description="This server's weekly points from finalized days."
    )
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.member)
    @app_commands.choices(
        game=[app_commands.Choice(name="All games", value="all")]
        + [app_commands.Choice(name=GAME_TITLES[item], value=item.value) for item in Game]
    )
    async def weekly(self, ctx: commands.Context, day: str = "today", game: str = "all"):
        await self.send_period(ctx, day, game, "week")

    @commands.hybrid_command(
        name="monthly", description="This server's monthly points from finalized days."
    )
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.member)
    @app_commands.choices(
        game=[app_commands.Choice(name="All games", value="all")]
        + [app_commands.Choice(name=GAME_TITLES[item], value=item.value) for item in Game]
    )
    async def monthly(self, ctx: commands.Context, day: str = "today", game: str = "all"):
        await self.send_period(ctx, day, game, "month")

    async def send_period(self, ctx: commands.Context, day: str, game: str, period: str):
        day, game = day.casefold(), game.casefold()
        if day in {item.value for item in Game}:
            day, game = ("today" if game == "all" else game), day
        try:
            selected_game = None if game == "all" else Game(game)
        except ValueError as exc:
            raise commands.BadArgument(
                phrase(
                    self.language_for(ctx),
                    "Choose all, timeguessr, sizeitup, metazooa, or wikigame.",
                    "Wähle all, timeguessr, sizeitup, metazooa oder wikigame.",
                )
            ) from exc
        cfg = self.config(ctx)
        selected = requested_day(day, datetime.now(UTC), cfg)
        summary = period_snapshot(
            self.bot.store.finalized_snapshots(
                cfg.guild_id, period_start(selected, period), selected
            ),
            selected,
            cfg.started_day,
            period,
            selected_game,
        )
        await ctx.send(embed=period_embed(summary, cfg.language))

    @commands.hybrid_command(
        name="stats", description="Personal stats from this server's finalized days."
    )
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.member)
    async def stats(self, ctx: commands.Context, member: discord.Member | None = None):
        cfg = self.config(ctx)
        target = member or ctx.author
        snapshots = self.bot.store.finalized_snapshots(cfg.guild_id, cfg.started_day, date.max)
        stats = player_stats_snapshot(
            snapshots,
            target.id,
            active_day(datetime.now(UTC), cfg),
            self.bot.store.rotation(cfg.guild_id),
        )
        await ctx.send(embed=player_stats_embed(stats, target.display_name, cfg.language))

    @commands.hybrid_command(
        name="status",
        description="Show this server's settings, daily challenge links and deadline.",
    )
    @commands.guild_only()
    async def status(self, ctx: commands.Context, day: str = "today"):
        cfg = self.config(ctx)
        now = datetime.now(UTC)
        today = active_day(now, cfg)
        selected = requested_day(day, now, cfg)
        prompt = self.bot.store.prompt(cfg.guild_id, selected)
        thread_id = (
            prompt["thread_id"]
            if self.bot.store.mode_for_day(cfg.guild_id, selected) == "thread" and prompt
            else None
        )
        presentation_cfg = (
            replace(cfg, announcement_style=prompt["announcement_style"])
            if thread_id and prompt
            else cfg
        )
        embed = status_embed(
            self.bot.store,
            presentation_cfg,
            selected,
            await self.bot.daily_reset(cfg, selected, now) if selected == today else None,
            thread_id=thread_id,
        )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="puzzle_date", description="Correct a numbered puzzle's date.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def puzzle_date_command(
        self, ctx: commands.Context, game: NumberedGame, puzzle: int, day: str
    ):
        cfg = self.config(ctx)
        if not 1 <= puzzle <= 1000000:
            raise commands.BadArgument(
                phrase(
                    cfg.language,
                    "Puzzle number is outside the supported range.",
                    "Die Rätselnummer liegt außerhalb des gültigen Bereichs.",
                )
            )
        try:
            selected = date.fromisoformat(day)
        except ValueError as exc:
            raise commands.BadArgument(
                phrase(
                    cfg.language,
                    "Use an ISO date such as 2026-11-13.",
                    "Verwende ein Datum wie 2026-11-13.",
                )
            ) from exc
        async with self.bot.guild_locks[cfg.guild_id]:
            if selected < active_day(datetime.now(UTC), cfg):
                raise commands.BadArgument(
                    phrase(
                        cfg.language,
                        "Closed competition dates cannot be remapped.",
                        "Abgeschlossene Spieltage können nicht neu zugeordnet werden.",
                    )
                )
            self.bot.store.set_override(
                cfg.guild_id, Game(game), str(puzzle), selected, ctx.author.id
            )
        await ctx.send(
            phrase(
                cfg.language,
                f"For this server: {game} #{puzzle} belongs to {selected}.",
                f"Auf diesem Server gehört {game} #{puzzle} zum {selected}.",
            ),
            ephemeral=True,
        )

    @commands.hybrid_command(name="void_result", description="Void an erroneous current-day entry.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.default_permissions(manage_guild=True)
    async def void_result(self, ctx: commands.Context, user: discord.User, game: GameName):
        cfg = self.config(ctx)
        async with self.bot.guild_locks[cfg.guild_id]:
            today = active_day(datetime.now(UTC), cfg)
            changed = self.bot.store.void(cfg.guild_id, user.id, Game(game), today, ctx.author.id)
        await ctx.send(
            phrase(
                cfg.language,
                "Result voided with an audit entry. The player may paste a fresh result today.",
                "Ergebnis mit Protokolleintrag entfernt. Der Spieler kann heute neu einreichen.",
            )
            if changed
            else phrase(
                cfg.language,
                "No accepted result found for that player/game today.",
                "Für diesen Spieler und dieses Spiel gibt es heute kein angenommenes Ergebnis.",
            ),
            ephemeral=True,
        )
