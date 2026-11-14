import asyncio
import json
import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .calendar import active_day, boundary, closes_at, parse_cutoff, post_due_at, validate_timezone
from .discord_views import MAUVE, compact_boards, daily_highlights, weekly_embed, weekly_snapshot
from .models import GAME_TITLES, GAME_URLS, Game, ServerConfig
from .parsers import parse_message
from .presentation import game_snapshot, make_snapshot, score_label
from .public_links import leaderboard_links
from .service import accept_result, requested_day
from .storage import Store

log = logging.getLogger(__name__)
ScoringMode = Literal["players_beaten", "placement"]
NumberedGame = Literal["timeguessr", "metazooa"]
GameName = Literal["timeguessr", "sizeitup", "metazooa"]


class CommandSyncError(RuntimeError):
    """Discord denied access while registering commands during startup."""


def board_marker(guild_id: int, day: date) -> str:
    return f"daily-games:{guild_id}:{day.isoformat()}"


def board_embeds(
    snapshot: dict, guild_id: int, *, final: bool, scheduled: bool = False, game: Game | None = None
) -> list[discord.Embed]:
    """Page embeds to respect Discord limits without silently truncating players."""
    day = date.fromisoformat(snapshot["day"])
    heading = "Final standings" if final else "Current standings · provisional"
    title = "Daily Games"
    games = [(Game.TIMEGUESSR, "TG"), (Game.SIZE_IT_UP, "Size"), (Game.METAZOOA, "Zoo")]
    if game is not None:
        snapshot = game_snapshot(snapshot, game)
        title = GAME_TITLES[game]
        games = [(game, title)]
    lines = []
    for row in snapshot["rows"]:
        name = discord.utils.escape_markdown(discord.utils.escape_mentions(row["name"]))[:80]
        scores = " · ".join(
            f"{label}: {score_label(game, row['games'].get(game.value))}" for game, label in games
        )
        lines.append(f"**{row['rank']}. {name} — {row['total']} pts**\n{scores}")
    pages, page = [], ""
    for line in lines or ["No results for this competition day."]:
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
        mode = "1 + players beaten" if snapshot["scoring"] == "players_beaten" else "placement"
        embed.add_field(
            name="Competition rules",
            value=(
                f"{snapshot['timezone']} · cutoff {snapshot['cutoff']} · {mode}\n"
                "TG = TimeGuessr · Size = Size It Up · Zoo = Metazooa\n"
                "Missing: — · Failed: 0 points · Ties share rank"
            ),
            inline=False,
        )
        marker = (
            board_marker(guild_id, day) if scheduled else "Final view" if final else "Live view"
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
        self.dev_guild_id = dev_guild_id
        self.guild_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.needs_recovery = {cfg.guild_id for cfg in store.configs()}
        self.tree.on_error = self.on_app_command_error

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
        await super().close()

    async def on_ready(self):
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
        self.needs_recovery.update(cfg.guild_id for cfg in self.store.configs())

    async def on_resumed(self):
        await self.recover_all()

    async def recover_all(self):
        for cfg in self.store.configs():
            if self.get_guild(cfg.guild_id):
                try:
                    async with self.guild_locks[cfg.guild_id]:
                        await self.recover_locked(cfg)
                except Exception:
                    log.exception("Recovery failed for server %s", cfg.guild_id)

    async def recover_locked(self, cfg: ServerConfig):
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
        async for message in channel.history(limit=None, after=after, oldest_first=True):
            await self.ingest(message, cfg, feedback=False)
            self.store.advance_cursor(cfg.guild_id, message.id)
        self.needs_recovery.discard(cfg.guild_id)

    async def ingest(self, message: discord.Message, cfg: ServerConfig, *, feedback: bool):
        if message.author.bot or message.webhook_id:
            return
        parsed, errors = parse_message(message.content)
        if not parsed and not errors:
            return
        if message.edited_at is not None:
            # History exposes only the edited content, so we cannot reconstruct the
            # original submission or establish whether its score changed after cutoff.
            if feedback:
                await message.reply(
                    "Edited results are not imported. Paste a fresh Share result instead.",
                    mention_author=False,
                )
            return
        accepted, rejected = [], []
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
            )
            label = GAME_TITLES[result.game]
            if outcome.status == "accepted":
                accepted.append(f"{label}: {outcome.detail}")
            elif outcome.status != "ignored":
                rejected.append(f"{label}: {outcome.detail}")
        if not feedback:
            return
        try:
            await message.add_reaction("✅" if accepted else "⚠️")
            if errors or rejected:
                await message.reply(
                    "\n".join([*accepted, *rejected, *errors])[:1800],
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except discord.HTTPException:
            # A missing reaction permission must never undo a stored submission.
            log.warning("Could not acknowledge result in server %s", cfg.guild_id)

    async def on_message(self, message: discord.Message):
        if message.author.bot or message.webhook_id or message.guild is None:
            return
        cfg = self.store.config(message.guild.id)
        if cfg and message.channel.id == cfg.input_channel_id:
            async with self.guild_locks[cfg.guild_id]:
                try:
                    if cfg.guild_id in self.needs_recovery:
                        # Leave this live event out of history so it still gets feedback.
                        await self.recover_locked_before(cfg, message)
                    await self.ingest(message, cfg, feedback=True)
                    self.store.advance_cursor(cfg.guild_id, message.id)
                except Exception:
                    self.needs_recovery.add(cfg.guild_id)
                    log.exception("Submission processing failed for server %s", cfg.guild_id)
                    await message.reply(
                        "Could not save this result. Please try again shortly.",
                        mention_author=False,
                    )
        await self.process_commands(message)

    async def recover_locked_before(self, cfg: ServerConfig, current: discord.Message):
        channel = current.channel
        cursor = self.store.cursor(cfg.guild_id)
        after = (
            discord.Object(id=cursor)
            if cursor
            else boundary(cfg.started_day, cfg.timezone, cfg.cutoff).astimezone(UTC)
        )
        async for message in channel.history(
            limit=None, after=after, before=current, oldest_first=True
        ):
            await self.ingest(message, cfg, feedback=False)
            self.store.advance_cursor(cfg.guild_id, message.id)
        self.needs_recovery.discard(cfg.guild_id)

    @tasks.loop(seconds=30, reconnect=True)
    async def daily_tick(self):
        now = datetime.now(UTC)
        for cfg in self.store.configs():
            if not self.get_guild(cfg.guild_id):
                continue
            try:
                async with self.guild_locks[cfg.guild_id]:
                    # Always recover before freezing to include pre-cutoff messages missed offline.
                    await self.recover_locked(cfg)
                    day = cfg.started_day
                    while post_due_at(day, cfg) <= now:
                        board = self.store.board(cfg.guild_id, day)
                        if board is None:
                            snapshot = make_snapshot(
                                self.store.results(cfg.guild_id, day), day, cfg
                            )
                            self.store.freeze(cfg.guild_id, day, cfg.output_channel_id, snapshot)
                        await self.publish_locked(cfg, day, now)
                        day += timedelta(days=1)
            except Exception:
                log.exception("Daily scheduling failed for server %s; will retry", cfg.guild_id)

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
        channel = self.get_channel(board["output_channel_id"])
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != cfg.guild_id:
            raise ValueError("Daily board channel is unavailable.")
        snapshot = json.loads(board["snapshot"])
        if details is None and board["attempted_at"] is None:
            summary = compact_boards(snapshot, final=True, scheduled=True, limit=10)[0]
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
                    "full standings: `!lb`", "full standings in the browser"
                )
                summary.add_field(
                    name="Full leaderboard in your browser",
                    value=(
                        f"[This server]({links['server']}) · "
                        f"[Global leaderboard]({links['global']})"
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
                    daily_highlights(snapshot),
                    *board_embeds(
                        snapshot,
                        cfg.guild_id,
                        final=True,
                    ),
                    weekly_embed(weekly),
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
                        + (f" · {prior_footer}" if embed.title.startswith("This week") else "")
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
        embeds = (
            [discord.Embed.from_dict(json.loads(details["payload"])["summary"])]
            if details
            else (board_embeds(snapshot, cfg.guild_id, final=True, scheduled=True))
        )
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
                    if footer.startswith(marker + " · page ") or any(
                        footer == planned.footer.text for planned in embeds
                    ):
                        found[footer] = message.id
        if not board["attempted_at"]:
            self.store.mark_attempt(cfg.guild_id, day, now)
        last_id = None
        for embed in embeds:
            footer = embed.footer.text
            if footer in found:
                last_id = found[footer]
                continue
            message = await channel.send(
                embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
            last_id = message.id
        self.store.mark_posted(cfg.guild_id, day, last_id)
        log.info("Daily board posted/reconciled: server=%s day=%s", cfg.guild_id, day)
        if details:
            await self.publish_details_safely(cfg, day, now)

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
        channel = self.get_channel(board["output_channel_id"])
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != cfg.guild_id:
            raise ValueError("Daily board channel is unavailable.")
        permissions = channel.permissions_for(channel.guild.me)
        required = (
            "create_public_threads",
            "send_messages_in_threads",
            "read_message_history",
            "embed_links",
        )
        if any(not getattr(permissions, name) for name in required):
            raise ValueError(
                "Daily details need Create Public Threads, Send Messages in Threads, "
                "Read Message History, and Embed Links in the board channel."
            )
        # A message-started thread has the parent's message ID, even after an ambiguous timeout.
        thread_id = details["thread_id"] or board["message_id"]
        thread = self.get_channel(thread_id)
        if thread is None:
            try:
                thread = await self.fetch_channel(thread_id)
            except discord.NotFound:
                thread = await channel.create_thread(
                    name=f"Daily Podium · {day} · results & weekly stats",
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
        if isinstance(
            error,
            (
                commands.UserInputError,
                commands.CheckFailure,
                commands.CommandOnCooldown,
                ValueError,
            ),
        ):
            await ctx.send(f"{error}\nUse `/help` or `!help` for command usage.", ephemeral=True)
        else:
            log.error("Command failed: %s", ctx.command, exc_info=error)
            await ctx.send("The command failed. Check the bot logs and try again.", ephemeral=True)

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        message = "You cannot use that command here, or an argument was invalid."
        if not isinstance(error, app_commands.CheckFailure):
            log.error("Slash command failed", exc_info=error)
            message = "The command failed. Check the bot logs and try again."
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

    @commands.hybrid_command(name="ping", description="Check whether the bot is online.")
    async def ping(self, ctx: commands.Context):
        await ctx.send("Pong! Daily Games is online.")

    @commands.hybrid_command(name="help", description="Show Daily Games commands and scoring.")
    async def help_command(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Daily Podium · quick guide",
            color=MAUVE,
            description=(
                "Play a game and paste its **Share** result in the results channel.\n"
                "`!lb` · standings and browser links\n"
                "`!lb sizeitup` / `timeguessr` / `metazooa` · one game\n"
                "`!lb yesterday` / `!lb sizeitup 2026-11-13` · another day\n"
                "`!weekly [day]` · weekly points from finalized days\n"
                "`!games` · play links · `!status` · puzzles and deadline\n"
                "`!ping` · connection check"
            ),
        )
        embed.add_field(
            name="Competition",
            value=(
                "First accepted result per player/game counts. "
                "Edits and reposts do not improve it. "
                "Failures and missing games score zero; ties share rank. "
                "Daily results post five minutes after cutoff, with details and weekly stats "
                "in a thread."
            ),
            inline=False,
        )
        embed.add_field(
            name="Admins · Manage Server",
            value=(
                "`!setup #results` · `/puzzle_date` · `/void_result`\n"
                "All commands also have slash versions; `/leaderboard` has day and game selectors."
            ),
            inline=False,
        )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="games", description="Links to the three daily games.")
    async def games(self, ctx: commands.Context):
        await ctx.send(
            embed=discord.Embed(
                title="Today's games",
                color=MAUVE,
                description=" · ".join(f"[{GAME_TITLES[game]}]({GAME_URLS[game]})" for game in Game)
                + "\nPlay, then paste your **Share** result in the results channel.",
            )
        )

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
    ):
        old = self.bot.store.config(ctx.guild.id)
        timezone = validate_timezone(timezone or (old.timezone if old else "Europe/Berlin"))
        cutoff = cutoff or (old.cutoff if old else "00:00")
        parse_cutoff(cutoff)
        scoring = scoring or (old.scoring if old else "players_beaten")
        board_channel = board_channel or ctx.guild.get_channel(
            old.output_channel_id if old else results_channel.id
        )
        if not isinstance(board_channel, discord.TextChannel):
            raise commands.BadArgument("Choose an accessible text channel for daily boards.")
        for channel in (results_channel, board_channel):
            if channel.guild.id != ctx.guild.id:
                raise commands.BadArgument("Both channels must belong to this server.")
            permissions = channel.permissions_for(ctx.guild.me)
            required = ["view_channel", "send_messages", "read_message_history", "embed_links"]
            if channel.id == results_channel.id:
                required.append("add_reactions")
            missing = [name for name in required if not getattr(permissions, name)]
            if missing:
                raise commands.BadArgument(
                    f"Missing permissions in {channel.mention}: {', '.join(missing)}"
                )
        now = datetime.now(UTC)
        cfg = ServerConfig(
            ctx.guild.id,
            results_channel.id,
            board_channel.id,
            timezone,
            cutoff,
            scoring,
            old.started_day if old else now.date(),
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
            )
        async with self.bot.guild_locks[cfg.guild_id]:
            self.bot.store.configure(cfg, ctx.author.id)
            self.bot.needs_recovery.add(cfg.guild_id)
        embed = discord.Embed(
            title="Competition configured",
            color=MAUVE,
            description=(
                f"**Results:** {results_channel.mention} · **Boards:** {board_channel.mention}\n"
                f"**Cutoff:** {cutoff} ({timezone}) · post five minutes later\n"
                f"**Scoring:** {scoring} · `!lb` for standings · `!weekly` for weekly stats"
            ),
        )
        permissions = board_channel.permissions_for(ctx.guild.me)
        if not permissions.create_public_threads or not permissions.send_messages_in_threads:
            embed.add_field(
                name="Optional detail threads",
                value=(
                    "Allow **Create Public Threads** and **Send Messages in Threads** "
                    "in the board channel for full results and weekly summaries below daily posts."
                ),
                inline=False,
            )
        embed.set_footer(
            text=(
                "Imports today's messages. Timezone, cutoff and scoring lock after "
                "competition data exists."
            )
        )
        await ctx.send(embed=embed, ephemeral=True)

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
                "Choose all, timeguessr, sizeitup, or metazooa for the game."
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
                    self.bot.store.results(cfg.guild_id, selected), selected, cfg
                )
        final = board is not None
        if not final and selected < active_day(datetime.now(UTC), cfg):
            await ctx.send("Submissions are closed for that day; its final board is pending.")
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
            snapshot, final=final, game=selected_game, limit=10 if links else None
        )
        if links:
            embeds[-1].add_field(
                name="Full leaderboard in your browser",
                value=f"[This server]({links['server']}) · [Global leaderboard]({links['global']})",
                inline=False,
            )
        for embed in embeds:
            await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="weekly", description="This server's weekly points from finalized days."
    )
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.member)
    async def weekly(self, ctx: commands.Context, day: str = "today"):
        cfg = self.config(ctx)
        selected = requested_day(day, datetime.now(UTC), cfg)
        monday = selected - timedelta(days=selected.weekday())
        summary = weekly_snapshot(
            self.bot.store.finalized_snapshots(cfg.guild_id, monday, selected),
            selected,
            cfg.started_day,
        )
        await ctx.send(embed=weekly_embed(summary))

    @commands.hybrid_command(
        name="status",
        description="Show this server's settings, daily challenge links and deadline.",
    )
    @commands.guild_only()
    async def status(self, ctx: commands.Context):
        from .calendar import ANCHOR_DAY, ANCHORS

        cfg = self.config(ctx)
        today = active_day(datetime.now(UTC), cfg)
        expected = []
        for game in Game:
            override = self.bot.store.puzzle_for_day(cfg.guild_id, game, today)
            puzzle = override or (
                today.isoformat()
                if game == Game.SIZE_IT_UP
                else str(ANCHORS[game] + (today - ANCHOR_DAY).days)
            )
            expected.append(f"{GAME_TITLES[game]}: `{puzzle}` · [Play daily](<{GAME_URLS[game]}>)")
        deadline = int(closes_at(today, cfg).timestamp())
        embed = discord.Embed(
            title=f"Competition · {today}",
            color=MAUVE,
            description=(
                f"**Results:** <#{cfg.input_channel_id}> · **Boards:** <#{cfg.output_channel_id}>\n"
                f"**Deadline:** <t:{deadline}:f> (<t:{deadline}:R>)\n" + "\n".join(expected)
            ),
        )
        embed.set_footer(
            text=f"{cfg.timezone} · {cfg.cutoff} · {cfg.scoring} · !weekly for weekly stats"
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
            raise commands.BadArgument("Puzzle number is outside the supported range.")
        try:
            selected = date.fromisoformat(day)
        except ValueError as exc:
            raise commands.BadArgument("Use an ISO date such as 2026-11-13.") from exc
        async with self.bot.guild_locks[cfg.guild_id]:
            if selected < active_day(datetime.now(UTC), cfg):
                raise commands.BadArgument("Closed competition dates cannot be remapped.")
            self.bot.store.set_override(
                cfg.guild_id, Game(game), str(puzzle), selected, ctx.author.id
            )
        await ctx.send(f"For this server: {game} #{puzzle} belongs to {selected}.", ephemeral=True)

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
            "Result voided with an audit entry. The player may paste a fresh result today."
            if changed
            else "No accepted result found for that player/game today.",
            ephemeral=True,
        )
