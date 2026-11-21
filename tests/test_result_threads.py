from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from daily_games.bot import CompetitionCommands, DailyGamesBot, compact_announcement_embed
from daily_games.calendar import active_day
from daily_games.models import Game
from daily_games.presentation import make_snapshot

from .conftest import TIMEGUESSR
from .test_discord_flow import incoming

DAY = date(2026, 11, 13)


async def history(items, **kwargs):
    after = kwargs.get("after")
    after_id = (
        after.id
        if hasattr(after, "id")
        else discord.utils.time_snowflake(after)
        if after is not None
        else 0
    )
    before = kwargs.get("before")
    before_id = before.id if before is not None else None
    for item in items:
        if item.id > after_id and (before_id is None or item.id < before_id):
            yield item


@pytest.fixture
async def thread_server(store, config):
    bot = DailyGamesBot(store)
    bot._connection.user = SimpleNamespace(id=123)
    guild = SimpleNamespace(id=config.guild_id, me=SimpleNamespace(id=123), name="Test")
    parent = MagicMock(spec=discord.TextChannel)
    parent.id, parent.guild = config.input_channel_id, guild
    parent.permissions_for.return_value = discord.Permissions.all()
    board = MagicMock(spec=discord.TextChannel)
    board.id, board.guild = config.output_channel_id, guild
    board.permissions_for.return_value = discord.Permissions.all()
    state = SimpleNamespace(messages={}, threads={}, thread_messages={}, board_messages=[])

    async def send_parent(**kwargs):
        embed = kwargs["embed"]
        day = date.fromisoformat(embed.footer.text.rsplit(":", 1)[1])
        message_id = discord.utils.time_snowflake(datetime.combine(day, datetime.min.time(), UTC))
        message = SimpleNamespace(
            id=message_id,
            author=SimpleNamespace(id=123, bot=True),
            webhook_id=None,
            embeds=[embed],
            pinned=False,
            channel=parent,
            guild=guild,
            edit=AsyncMock(),
            pin=AsyncMock(),
            unpin=AsyncMock(),
        )

        async def edit(**edit_kwargs):
            message.embeds = [edit_kwargs["embed"]]

        async def pin(**_kwargs):
            message.pinned = True

        async def unpin(**_kwargs):
            message.pinned = False

        message.edit.side_effect = edit
        message.pin.side_effect = pin
        message.unpin.side_effect = unpin
        state.messages[message_id] = message
        return message

    async def create_thread(**kwargs):
        thread_id = kwargs["message"].id
        thread = MagicMock(spec=discord.Thread)
        thread.id, thread.parent_id, thread.guild = thread_id, parent.id, guild
        thread.archived, thread.locked = False, False
        thread.permissions_for.return_value = discord.Permissions.all()
        thread.history.side_effect = lambda **kwargs: history(
            state.thread_messages[thread_id], **kwargs
        )
        thread.send = AsyncMock(return_value=SimpleNamespace(id=thread_id + 1))
        state.threads[thread_id] = thread
        state.thread_messages[thread_id] = []
        return thread

    async def fetch_message(message_id):
        return state.messages[message_id]

    parent.send = AsyncMock(side_effect=send_parent)
    parent.create_thread = AsyncMock(side_effect=create_thread)
    parent.fetch_message = AsyncMock(side_effect=fetch_message)
    parent.history.side_effect = lambda **kwargs: history(state.messages.values(), **kwargs)
    board.send = AsyncMock(return_value=SimpleNamespace(id=9999))
    board.history.side_effect = lambda **kwargs: history(state.board_messages, **kwargs)
    bot.get_channel = MagicMock(
        side_effect=lambda channel_id: (
            parent
            if channel_id == parent.id
            else board
            if channel_id == board.id
            else state.threads.get(channel_id)
        )
    )
    bot.get_guild = MagicMock(return_value=guild)
    bot.fetch_channel = AsyncMock(
        side_effect=discord.NotFound(
            SimpleNamespace(status=404, reason="Not found"), "Unknown channel"
        )
    )
    yield SimpleNamespace(bot=bot, parent=parent, board=board, state=state, guild=guild)
    await bot.close()


def test_mode_changes_are_effective_by_day(store, config):
    store.set_result_mode(config.guild_id, DAY + timedelta(days=1), "thread", actor_id=1)
    assert store.mode_for_day(config.guild_id, DAY) == "channel"
    assert store.mode_for_day(config.guild_id, DAY + timedelta(days=1)) == "thread"
    store.set_result_mode(config.guild_id, DAY + timedelta(days=2), "channel", actor_id=1)
    assert store.mode_for_day(config.guild_id, DAY + timedelta(days=2)) == "channel"


async def test_result_mode_command_schedules_next_day(thread_server, store, config):
    cog = CompetitionCommands(thread_server.bot)
    thread_server.guild.get_channel = lambda channel_id: (
        thread_server.parent if channel_id == config.input_channel_id else None
    )
    ctx = SimpleNamespace(
        guild=thread_server.guild,
        author=SimpleNamespace(id=1),
        send=AsyncMock(),
    )
    today = active_day(datetime.now(UTC), config)
    await cog.result_mode.callback(cog, ctx, "thread")
    assert store.mode_for_day(config.guild_id, today) == "channel"
    assert store.mode_for_day(config.guild_id, today + timedelta(days=1)) == "thread"
    assert "from" in ctx.send.await_args.args[0]


async def test_admin_can_set_german_and_compact_style(thread_server, store, config):
    cog = CompetitionCommands(thread_server.bot)
    ctx = SimpleNamespace(
        guild=thread_server.guild,
        author=SimpleNamespace(id=1),
        send=AsyncMock(),
    )
    await cog.language.callback(cog, ctx, "de")
    assert store.config(config.guild_id).language == "de"
    await cog.announcement_style.callback(cog, ctx, "compact")
    assert store.config(config.guild_id).announcement_style == "compact"
    assert "Kompakte Posts" in ctx.send.await_args.args[0]


async def test_initial_setup_can_start_in_thread_mode(thread_server, store):
    cog = CompetitionCommands(thread_server.bot)
    guild = SimpleNamespace(id=101, me=SimpleNamespace(id=123))
    parent = MagicMock(spec=discord.TextChannel)
    parent.id, parent.guild, parent.mention = 201, guild, "<#201>"
    parent.permissions_for.return_value = discord.Permissions.all()
    guild.get_channel = lambda channel_id: parent if channel_id == parent.id else None
    ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=1), send=AsyncMock())
    await cog.setup.callback(cog, ctx, parent, None, None, None, None, "thread")
    cfg = store.config(guild.id)
    assert cfg is not None
    assert store.mode_for_day(guild.id, cfg.started_day) == "thread"
    assert "Results mode:** thread" in ctx.send.await_args.kwargs["embed"].description


async def test_scheduler_opens_thread_before_morning(thread_server, store, config, monkeypatch):
    store.set_result_mode(config.guild_id, DAY, "thread", actor_id=1)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = datetime(2026, 11, 13, 0, tzinfo=UTC)
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr("daily_games.bot.datetime", Clock)
    await thread_server.bot.daily_tick.coro(thread_server.bot)
    prompt = store.prompt(config.guild_id, DAY)
    assert prompt["thread_id"] == prompt["message_id"]
    assert prompt["pinned"] == 1
    assert thread_server.board.send.await_count == 0


async def test_thread_prompt_is_pinned_once_and_morning_links_edit_it(
    thread_server, store, config, monkeypatch
):
    store.set_result_mode(config.guild_id, DAY, "thread", actor_id=1)
    bot = thread_server.bot
    now = datetime(2026, 11, 13, 0, tzinfo=UTC)
    await bot.publish_thread_prompt_locked(config, DAY, now)
    await bot.publish_thread_prompt_locked(config, DAY, now + timedelta(minutes=1))
    prompt = store.prompt(config.guild_id, DAY)
    assert prompt["thread_id"] == prompt["message_id"]
    assert prompt["pinned"] == prompt["linked"] == 1
    assert thread_server.parent.send.await_count == 1
    assert thread_server.parent.create_thread.await_count == 1
    parent_message = thread_server.state.messages[prompt["message_id"]]
    parent_message.pin.assert_awaited_once()
    monkeypatch.setattr("daily_games.bot.current_daily_puzzle", AsyncMock(return_value=1262))
    monkeypatch.setattr("daily_games.bot.next_reset", AsyncMock(return_value=None))
    await bot.publish_thread_prompt_locked(config, DAY, datetime(2026, 11, 13, 8, tzinfo=UTC))
    assert store.prompt(config.guild_id, DAY)["morning_updated"] == 1
    assert f"<#{prompt['thread_id']}>" in parent_message.embeds[0].description
    assert parent_message.edit.await_count == 2


def test_compact_german_announcement_shows_month_and_yesterday(config):
    from dataclasses import replace

    cfg = replace(config, language="de", announcement_style="compact")
    monthly = {
        "rows": [
            {"rank": 1, "name": "Anna", "points": 17},
            {"rank": 2, "name": "Ben", "points": 14},
            {"rank": 3, "name": "Cara", "points": 10},
            {"rank": 3, "name": "Dora", "points": 10},
        ]
    }
    yesterday = {"rows": [{"rank": 1, "name": "Anna", "total": 5}]}
    embed = compact_announcement_embed(cfg, DAY, 900, monthly, yesterday)
    assert "gehen wieder los" in embed.title
    assert "Gestern gewonnen" in embed.description
    assert "Monats-Top-3" in embed.description
    assert "1. Anna · 17 Pkt." in embed.description
    assert "+1 gleichauf auf Platz 3" in embed.description
    assert "<#900>" in embed.description


async def test_compact_mode_keeps_final_board_and_morning_links_in_threads(
    thread_server, store, config, monkeypatch
):
    store.set_result_mode(config.guild_id, DAY, "thread", actor_id=1)
    store.set_presentation(config.guild_id, actor_id=1, language="de", style="compact")
    cfg = store.config(config.guild_id)
    midnight = datetime(2026, 11, 13, 0, tzinfo=UTC)
    await thread_server.bot.publish_thread_prompt_locked(cfg, DAY, midnight)
    prompt = store.prompt(cfg.guild_id, DAY)
    thread = thread_server.state.threads[prompt["thread_id"]]
    monkeypatch.setattr("daily_games.bot.current_daily_puzzle", AsyncMock(return_value=1262))
    monkeypatch.setattr("daily_games.bot.next_reset", AsyncMock(return_value=None))
    await thread_server.bot.publish_thread_prompt_locked(
        cfg, DAY, midnight + timedelta(hours=8, minutes=5)
    )
    parent_message = thread_server.state.messages[prompt["message_id"]]
    assert "Monats-Top-3" in parent_message.embeds[0].description
    assert "Tagesrätsel spielen" not in parent_message.embeds[0].description
    assert "Tagesrätsel spielen" in thread.send.await_args.kwargs["embed"].description
    assert store.prompt(cfg.guild_id, DAY)["header_updated"] == 1
    assert store.prompt(cfg.guild_id, DAY)["morning_updated"] == 1
    store.freeze(cfg.guild_id, DAY, cfg.output_channel_id, make_snapshot([], DAY, cfg))
    await thread_server.bot.publish_locked(cfg, DAY, midnight + timedelta(days=1, minutes=5))
    assert store.board(cfg.guild_id, DAY)["message_id"] is not None
    assert thread_server.board.send.await_count == 0
    assert thread.send.await_count == 2


async def test_compact_morning_header_appears_while_timeguessr_is_delayed(
    thread_server, store, config, monkeypatch
):
    store.set_result_mode(config.guild_id, DAY, "thread", actor_id=1)
    store.set_presentation(config.guild_id, actor_id=1, language="de", style="compact")
    cfg = store.config(config.guild_id)
    midnight = datetime(2026, 11, 13, 0, tzinfo=UTC)
    await thread_server.bot.publish_thread_prompt_locked(cfg, DAY, midnight)
    monkeypatch.setattr("daily_games.bot.current_daily_puzzle", AsyncMock(return_value=1261))
    await thread_server.bot.publish_thread_prompt_locked(
        cfg, DAY, midnight + timedelta(hours=8, minutes=5)
    )
    prompt = store.prompt(cfg.guild_id, DAY)
    assert prompt["header_updated"] == 1
    assert prompt["morning_updated"] == 0
    parent = thread_server.state.messages[prompt["message_id"]]
    assert "Monats-Top-3" in parent.embeds[0].description
    thread = thread_server.state.threads[prompt["thread_id"]]
    thread.send.assert_not_awaited()
    monkeypatch.setattr("daily_games.bot.current_daily_puzzle", AsyncMock(return_value=1262))
    monkeypatch.setattr("daily_games.bot.next_reset", AsyncMock(return_value=None))
    await thread_server.bot.publish_thread_prompt_locked(
        cfg, DAY, midnight + timedelta(hours=8, minutes=10)
    )
    assert store.prompt(cfg.guild_id, DAY)["morning_updated"] == 1
    thread.send.assert_awaited_once()


async def test_compact_morning_links_reconcile_after_ambiguous_send(
    thread_server, store, config, monkeypatch
):
    store.set_result_mode(config.guild_id, DAY, "thread", actor_id=1)
    store.set_presentation(config.guild_id, actor_id=1, style="compact")
    cfg = store.config(config.guild_id)
    midnight = datetime(2026, 11, 13, 0, tzinfo=UTC)
    await thread_server.bot.publish_thread_prompt_locked(cfg, DAY, midnight)
    monkeypatch.setattr("daily_games.bot.current_daily_puzzle", AsyncMock(return_value=1262))
    monkeypatch.setattr("daily_games.bot.next_reset", AsyncMock(return_value=None))
    await thread_server.bot.publish_thread_prompt_locked(
        cfg, DAY, midnight + timedelta(hours=8, minutes=5)
    )
    prompt = store.prompt(cfg.guild_id, DAY)
    thread = thread_server.state.threads[prompt["thread_id"]]
    sent_embed = thread.send.await_args.kwargs["embed"]
    thread_server.state.thread_messages[thread.id].append(
        SimpleNamespace(
            id=discord.utils.time_snowflake(midnight + timedelta(hours=8, minutes=5)) + 1,
            author=SimpleNamespace(id=123),
            embeds=[sent_embed],
        )
    )
    with store.db:
        store.db.execute(
            "UPDATE daily_prompts SET morning_updated=0 WHERE guild_id=? AND day=?",
            (cfg.guild_id, DAY.isoformat()),
        )
    await thread_server.bot.publish_thread_prompt_locked(
        cfg, DAY, midnight + timedelta(hours=8, minutes=6)
    )
    thread.send.assert_awaited_once()
    assert store.prompt(cfg.guild_id, DAY)["morning_updated"] == 1


async def test_style_change_keeps_existing_daily_prompt_layout(
    thread_server, store, config, monkeypatch
):
    store.set_result_mode(config.guild_id, DAY, "thread", actor_id=1)
    midnight = datetime(2026, 11, 13, 0, tzinfo=UTC)
    await thread_server.bot.publish_thread_prompt_locked(config, DAY, midnight)
    store.set_presentation(config.guild_id, actor_id=1, style="compact")
    cfg = store.config(config.guild_id)
    assert store.prompt(cfg.guild_id, DAY)["announcement_style"] == "standard"
    monkeypatch.setattr("daily_games.bot.current_daily_puzzle", AsyncMock(return_value=1262))
    monkeypatch.setattr("daily_games.bot.next_reset", AsyncMock(return_value=None))
    await thread_server.bot.publish_thread_prompt_locked(
        cfg, DAY, midnight + timedelta(hours=8, minutes=5)
    )
    prompt = store.prompt(cfg.guild_id, DAY)
    parent = thread_server.state.messages[prompt["message_id"]]
    assert "Play today" in parent.embeds[0].title
    assert "Play daily" in parent.embeds[0].description
    thread_server.state.threads[prompt["thread_id"]].send.assert_not_awaited()


async def test_new_daily_pin_unpins_only_previous_bot_prompt(thread_server, store, config):
    store.set_result_mode(config.guild_id, DAY, "thread", actor_id=1)
    await thread_server.bot.publish_thread_prompt_locked(
        config, DAY, datetime(2026, 11, 13, 0, tzinfo=UTC)
    )
    first = store.prompt(config.guild_id, DAY)
    first_message = thread_server.state.messages[first["message_id"]]
    next_day = DAY + timedelta(days=1)
    await thread_server.bot.publish_thread_prompt_locked(
        config, next_day, datetime(2026, 11, 14, 0, tzinfo=UTC)
    )
    assert store.prompt(config.guild_id, DAY)["pinned"] == 0
    assert store.prompt(config.guild_id, next_day)["pinned"] == 1
    first_message.unpin.assert_awaited_once()


async def test_switch_back_to_channel_mode_clears_old_daily_pin(
    thread_server, store, config, monkeypatch
):
    store.set_result_mode(config.guild_id, DAY, "thread", actor_id=1)
    await thread_server.bot.publish_thread_prompt_locked(
        config, DAY, datetime(2026, 11, 13, 0, tzinfo=UTC)
    )
    old_prompt = store.prompt(config.guild_id, DAY)
    old_message = thread_server.state.messages[old_prompt["message_id"]]
    next_day = DAY + timedelta(days=1)
    store.set_result_mode(config.guild_id, next_day, "channel", actor_id=1)
    store.freeze(config.guild_id, DAY, config.output_channel_id, make_snapshot([], DAY, config))
    store.mark_posted(config.guild_id, DAY, 8000)
    monkeypatch.setattr("daily_games.bot.current_daily_puzzle", AsyncMock(return_value=1263))
    monkeypatch.setattr("daily_games.bot.next_reset", AsyncMock(return_value=None))
    await thread_server.bot.publish_prompt_locked(
        config, next_day, datetime(2026, 11, 14, 8, tzinfo=UTC)
    )
    assert store.prompt(config.guild_id, DAY)["pinned"] == 0
    old_message.unpin.assert_awaited_once()
    assert thread_server.board.send.await_count == 1


async def test_thread_results_are_accepted_and_parent_is_redirected(thread_server, store, config):
    store.set_result_mode(config.guild_id, DAY, "thread", actor_id=1)
    await thread_server.bot.publish_thread_prompt_locked(
        config, DAY, datetime(2026, 11, 13, 0, tzinfo=UTC)
    )
    thread_id = store.prompt(config.guild_id, DAY)["thread_id"]
    when = datetime(2026, 11, 13, 18, tzinfo=UTC)
    message = incoming(config, when, TIMEGUESSR)
    message.channel = thread_server.state.threads[thread_id]
    message.guild = thread_server.guild
    await thread_server.bot.ingest(message, config, feedback=True)
    assert store.results(config.guild_id, DAY)[0].score == 18419
    message.add_reaction.assert_any_await("✅")
    wrong_place = incoming(config, when, TIMEGUESSR, 778)
    wrong_place.guild = thread_server.guild
    await thread_server.bot.ingest(wrong_place, config, feedback=True)
    assert f"<#{thread_id}>" in wrong_place.reply.await_args.args[0]
    wrong_place.edit.assert_awaited_once_with(suppress=True)
    assert len(store.results(config.guild_id, DAY)) == 1


async def test_parent_gap_results_recover_and_thread_has_own_cursor(thread_server, store, config):
    store.set_result_mode(config.guild_id, DAY, "thread", actor_id=1)
    await thread_server.bot.publish_thread_prompt_locked(
        config, DAY, datetime(2026, 11, 13, 0, tzinfo=UTC)
    )
    thread_id = store.prompt(config.guild_id, DAY)["thread_id"]
    early = incoming(config, datetime(2026, 11, 12, 23, 30, tzinfo=UTC), TIMEGUESSR)
    early.guild = thread_server.guild
    await thread_server.bot.ingest(early, config, feedback=False)
    assert len(store.results(config.guild_id, DAY)) == 1
    second_time = datetime(2026, 11, 13, 18, tzinfo=UTC)
    second_id = discord.utils.time_snowflake(second_time) + 8
    second = incoming(config, second_time, TIMEGUESSR, second_id)
    second.author.id = 8
    second.channel = thread_server.state.threads[thread_id]
    second.guild = thread_server.guild
    thread_server.state.thread_messages[thread_id].append(second)
    await thread_server.bot.recover_locked(config)
    assert len(store.results(config.guild_id, DAY)) == 2
    assert store.prompt(config.guild_id, DAY)["thread_cursor"] == second_id
    await thread_server.bot.recover_locked(config)
    assert len(store.results(config.guild_id, DAY)) == 2


async def test_live_thread_message_recovers_earlier_thread_result(thread_server, store, config):
    store.set_result_mode(config.guild_id, DAY, "thread", actor_id=1)
    await thread_server.bot.publish_thread_prompt_locked(
        config, DAY, datetime(2026, 11, 13, 0, tzinfo=UTC)
    )
    thread_id = store.prompt(config.guild_id, DAY)["thread_id"]
    thread = thread_server.state.threads[thread_id]
    when = datetime(2026, 11, 13, 18, tzinfo=UTC)
    first = incoming(config, when, TIMEGUESSR, discord.utils.time_snowflake(when) + 1)
    first.channel, first.guild = thread, thread_server.guild
    current = incoming(config, when, TIMEGUESSR, discord.utils.time_snowflake(when) + 2)
    current.author.id = 8
    current.channel, current.guild = thread, thread_server.guild
    thread_server.state.thread_messages[thread_id].extend([first, current])
    thread_server.bot.needs_recovery.add(config.guild_id)
    thread_server.bot.process_commands = AsyncMock()
    await thread_server.bot.on_message(current)
    assert {row.user_id for row in store.results(config.guild_id, DAY)} == {7, 8}
    assert store.prompt(config.guild_id, DAY)["thread_cursor"] == current.id
    first.add_reaction.assert_not_awaited()
    current.add_reaction.assert_any_await("✅")


@pytest.mark.parametrize(
    ("game", "share", "score"),
    [
        (Game.SIZE_IT_UP, "Size It Up\n#103\n\nScore: 273/500", 273),
        (
            Game.WIKI,
            "Thomas Edison ➡️ McDonald's ⏰ 02:10 🦶5 🔗 https://www.thewikigamedaily.com/",
            5,
        ),
    ],
)
async def test_thread_live_acknowledges_original_post_already_saved_by_recovery(
    thread_server, store, config, game, share, score
):
    bot = thread_server.bot
    day = date(2026, 11, 20)
    store.set_result_mode(config.guild_id, day, "thread", actor_id=1)
    store.set_rotation(0, day, (Game.TIMEGUESSR, Game.SIZE_IT_UP, Game.WIKI), actor_id=1)
    store.register_wiki_route(day, "Thomas Edison", "McDonald's")
    await bot.publish_thread_prompt_locked(config, day, datetime(2026, 11, 20, 0, tzinfo=UTC))
    thread_id = store.prompt(config.guild_id, day)["thread_id"]
    thread = thread_server.state.threads[thread_id]
    when = datetime(2026, 11, 20, 15, tzinfo=UTC)
    snowflake = discord.utils.time_snowflake(when)
    bot.process_commands = AsyncMock()

    def make_post(offset, player):
        post = incoming(config, when, share, snowflake + offset)
        post.channel, post.guild = thread, thread_server.guild
        post.author.id = player
        thread_server.state.thread_messages[thread_id].append(post)
        return post

    first = make_post(1, 7)
    await bot.on_message(first)
    first.add_reaction.assert_any_await("✅")

    second = make_post(2, 8)
    # The scheduled history scan sees the post before its live event takes the lock.
    async with bot.guild_locks[config.guild_id]:
        await bot.recover_locked(config)
    second.add_reaction.assert_not_awaited()
    await bot.on_message(second)
    assert [call.args[0] for call in second.add_reaction.await_args_list] == ["✅", "🔥", "1️⃣"]
    second.reply.assert_not_awaited()

    repost = make_post(3, 8)
    await bot.on_message(repost)
    repost.add_reaction.assert_awaited_once_with("⚠️")
    assert "already submitted" in repost.reply.await_args.args[0]
    assert {row.user_id: (row.game, row.score) for row in store.results(config.guild_id, day)} == {
        7: (game, score),
        8: (game, score),
    }


async def test_final_details_use_results_thread(thread_server, store, config):
    store.set_result_mode(config.guild_id, DAY, "thread", actor_id=1)
    await thread_server.bot.publish_thread_prompt_locked(
        config, DAY, datetime(2026, 11, 13, 0, tzinfo=UTC)
    )
    thread_id = store.prompt(config.guild_id, DAY)["thread_id"]
    when = datetime(2026, 11, 13, 18, tzinfo=UTC)
    message = incoming(config, when, TIMEGUESSR)
    message.channel = thread_server.state.threads[thread_id]
    message.guild = thread_server.guild
    await thread_server.bot.ingest(message, config, feedback=False)
    store.freeze(
        config.guild_id,
        DAY,
        config.output_channel_id,
        make_snapshot(store.results(config.guild_id, DAY), DAY, config),
    )
    await thread_server.bot.publish_locked(config, DAY, datetime(2026, 11, 13, 23, 5, tzinfo=UTC))
    assert store.details(config.guild_id, DAY)["thread_id"] == thread_id
    assert store.details(config.guild_id, DAY)["complete"] == 1
    thread_server.board.create_thread.assert_not_awaited()
    assert thread_server.state.threads[thread_id].send.await_count == 3


def test_thread_mode_requires_pin_permission(thread_server):
    thread_server.parent.permissions_for.return_value.pin_messages = False
    from discord.ext import commands

    with pytest.raises(commands.BadArgument, match="pin_messages"):
        CompetitionCommands.check_thread_permissions(thread_server.parent, thread_server.guild.me)
