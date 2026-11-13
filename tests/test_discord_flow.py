import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from daily_games.bot import CompetitionCommands, DailyGamesBot, board_embeds
from daily_games.models import Game, ParsedResult
from daily_games.presentation import make_snapshot
from daily_games.service import accept_result

from .conftest import TIMEGUESSR


async def messages(items):
    for item in items:
        yield item


def channel_mock(guild_id, history=()):
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild = SimpleNamespace(id=guild_id)
    channel.history.side_effect = lambda **kwargs: messages(history)
    channel.send = AsyncMock(return_value=SimpleNamespace(id=9999))
    return channel


def incoming(config, message_time, content=TIMEGUESSR, message_id=777):
    return SimpleNamespace(
        content=content,
        author=SimpleNamespace(id=7, display_name="Player", bot=False),
        webhook_id=None,
        id=message_id,
        channel=SimpleNamespace(id=config.input_channel_id),
        created_at=message_time,
        edited_at=None,
        add_reaction=AsyncMock(),
        reply=AsyncMock(),
    )


@pytest.fixture
async def bot(store):
    instance = DailyGamesBot(store)
    yield instance
    await instance.close()


async def test_hybrid_commands_register_without_discord_connection(bot):
    await bot.add_cog(CompetitionCommands(bot))
    assert bot.get_command("lb") is bot.get_command("leaderboard")
    names = {command.name for command in bot.tree.get_commands()}
    assert {"leaderboard", "setup", "status", "puzzle_date", "void_result"} <= names
    # Serializing slash payloads catches unsupported parameter annotations/options.
    for command in bot.tree.get_commands():
        payload = command.to_dict(bot.tree)
        assert payload["name"] == command.name


async def test_ingestion_records_before_reacting(bot, store, config, message_time):
    message = incoming(config, message_time)
    await bot.ingest(message, config, feedback=True)
    assert store.results(config.guild_id, date(2026, 11, 13))[0].score == 18419
    message.add_reaction.assert_awaited_once_with("✅")
    message.reply.assert_not_awaited()


async def test_duplicate_warns_without_replacing(bot, store, config, message_time):
    original = incoming(config, message_time)
    repost = incoming(config, message_time, TIMEGUESSR.replace("18,419", "50,000"), 778)
    await bot.ingest(original, config, feedback=True)
    await bot.ingest(repost, config, feedback=True)
    repost.add_reaction.assert_awaited_once_with("⚠️")
    assert "already submitted" in repost.reply.call_args.args[0]
    assert store.results(config.guild_id, date(2026, 11, 13))[0].score == 18419


async def test_restart_recovers_using_original_message_time(bot, store, config, message_time):
    history = [incoming(config, message_time), incoming(config, message_time, message_id=778)]
    channel = channel_mock(config.guild_id, history)
    bot.get_channel = MagicMock(return_value=channel)
    await bot.recover_locked(config)
    assert len(store.results(config.guild_id, date(2026, 11, 13))) == 1
    assert store.cursor(config.guild_id) == 778
    for message in history:
        message.add_reaction.assert_not_awaited()
        message.reply.assert_not_awaited()


async def test_webhook_and_bot_results_are_ignored(bot, store, config, message_time):
    message = incoming(config, message_time)
    message.webhook_id = 123
    await bot.ingest(message, config, feedback=True)
    assert store.results(config.guild_id, date(2026, 11, 13)) == []


async def test_edited_history_cannot_supply_a_retroactive_score(bot, store, config, message_time):
    message = incoming(config, message_time)
    message.edited_at = message_time + timedelta(hours=12)
    await bot.ingest(message, config, feedback=False)
    assert store.results(config.guild_id, date(2026, 11, 13)) == []


async def test_admin_setup_rejects_a_regular_member(bot):
    from discord.ext import commands

    await bot.add_cog(CompetitionCommands(bot))
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=100),
        author=SimpleNamespace(guild_permissions=discord.Permissions.none()),
        command=None,
        bot=bot,
        interaction=None,
    )
    with pytest.raises(commands.MissingPermissions):
        await bot.get_command("setup").can_run(ctx)


async def test_scheduled_post_reconciles_accepted_send_after_crash(
    bot, store, config, message_time
):
    day = date(2026, 11, 13)
    accept_result(
        store,
        config,
        ParsedResult(Game.TIMEGUESSR, "1262", 18419),
        user_id=7,
        display_name="Player",
        message_id=777,
        channel_id=config.input_channel_id,
        submitted_at=message_time,
    )
    snapshot = make_snapshot(store.results(config.guild_id, day), day, config)
    store.freeze(config.guild_id, day, config.output_channel_id, snapshot)
    now = datetime(2026, 11, 14, 0, 5, tzinfo=UTC)
    store.mark_attempt(config.guild_id, day, now)
    embed = board_embeds(snapshot, config.guild_id, final=True, scheduled=True)[0]
    sent = SimpleNamespace(id=8888, author=SimpleNamespace(id=123), embeds=[embed])
    channel = channel_mock(config.guild_id, [sent])
    bot.get_channel = MagicMock(return_value=channel)
    bot._connection.user = SimpleNamespace(id=123)
    await bot.publish_locked(config, day, now + timedelta(minutes=1))
    channel.send.assert_not_awaited()
    assert store.board(config.guild_id, day)["message_id"] == 8888


async def test_scheduled_post_once_after_success(bot, store, config):
    day = date(2026, 11, 13)
    snapshot = make_snapshot([], day, config)
    store.freeze(config.guild_id, day, config.output_channel_id, snapshot)
    channel = channel_mock(config.guild_id)
    bot.get_channel = MagicMock(return_value=channel)
    await bot.publish_locked(config, day, datetime(2026, 11, 14, 0, 5, tzinfo=UTC))
    await bot.publish_locked(config, day, datetime(2026, 11, 14, 0, 6, tzinfo=UTC))
    channel.send.assert_awaited_once()
    assert store.board(config.guild_id, day)["message_id"] == 9999


async def test_send_failure_keeps_frozen_board_pending_for_retry(bot, store, config):
    day = date(2026, 11, 13)
    store.freeze(config.guild_id, day, config.output_channel_id, make_snapshot([], day, config))
    channel = channel_mock(config.guild_id)
    channel.send.side_effect = TimeoutError("Simulated ambiguous network send")
    bot.get_channel = MagicMock(return_value=channel)
    with pytest.raises(TimeoutError):
        await bot.publish_locked(config, day, datetime(2026, 11, 14, 0, 5, tzinfo=UTC))
    board = store.board(config.guild_id, day)
    assert board["message_id"] is None
    assert board["attempted_at"] is not None
    assert json.loads(board["snapshot"])["rows"] == []


async def test_manually_pulled_final_view_does_not_replace_scheduled_post(bot, store, config):
    day = date(2026, 11, 13)
    snapshot = make_snapshot([], day, config)
    store.freeze(config.guild_id, day, config.output_channel_id, snapshot)
    now = datetime(2026, 11, 14, 0, 5, tzinfo=UTC)
    store.mark_attempt(config.guild_id, day, now)
    manual_view = board_embeds(snapshot, config.guild_id, final=True)[0]
    manual_message = SimpleNamespace(id=8888, author=SimpleNamespace(id=123), embeds=[manual_view])
    channel = channel_mock(config.guild_id, [manual_message])
    bot.get_channel = MagicMock(return_value=channel)
    bot._connection.user = SimpleNamespace(id=123)
    await bot.publish_locked(config, day, now + timedelta(minutes=1))
    channel.send.assert_awaited_once()
    assert store.board(config.guild_id, day)["message_id"] == 9999


def test_large_board_pages_and_neutralizes_mentions(config):
    snapshot = make_snapshot([], date(2026, 11, 13), config)
    snapshot["rows"] = [
        {
            "rank": index,
            "user_id": index,
            "name": "@everyone **a very long player name**" * 3,
            "total": 3,
            "games": {},
        }
        for index in range(1, 101)
    ]
    pages = board_embeds(snapshot, config.guild_id, final=True)
    assert len(pages) > 1
    assert all(len(embed) <= 6000 and len(embed.description) <= 4096 for embed in pages)
    assert all("@everyone" not in embed.description for embed in pages)
    assert sum(embed.description.count("pts**") for embed in pages) == 100


async def test_scheduler_waits_until_post_time_then_finalizes_once(bot, store, config, monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 11, 13, 23, 4, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)

    monkeypatch.setattr("daily_games.bot.datetime", Clock)
    bot.get_guild = MagicMock(return_value=SimpleNamespace(id=config.guild_id))
    input_channel = channel_mock(config.guild_id)
    output_channel = channel_mock(config.guild_id)
    bot.get_channel = MagicMock(
        side_effect=lambda channel_id: (
            input_channel if channel_id == config.input_channel_id else output_channel
        )
    )
    await bot.daily_tick.coro(bot)
    assert store.board(config.guild_id, date(2026, 11, 13)) is None
    Clock.current = datetime(2026, 11, 13, 23, 5, tzinfo=UTC)
    await bot.daily_tick.coro(bot)
    await bot.daily_tick.coro(bot)
    assert store.board(config.guild_id, date(2026, 11, 13))["message_id"] == 9999
    output_channel.send.assert_awaited_once()


async def test_scheduler_does_not_freeze_when_history_recovery_fails(
    bot, store, config, monkeypatch
):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 14, 8, tzinfo=UTC)

    monkeypatch.setattr("daily_games.bot.datetime", Clock)
    bot.get_guild = MagicMock(return_value=SimpleNamespace(id=config.guild_id))
    bot.recover_locked = AsyncMock(side_effect=RuntimeError("Missing history permission"))
    await bot.daily_tick.coro(bot)
    assert store.board(config.guild_id, date(2026, 11, 13)) is None


async def test_recovery_includes_pre_cutoff_message_before_scheduler_freezes(
    bot, store, config, message_time, monkeypatch
):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 14, 8, tzinfo=UTC)

    monkeypatch.setattr("daily_games.bot.datetime", Clock)
    bot.get_guild = MagicMock(return_value=SimpleNamespace(id=config.guild_id))
    input_channel = channel_mock(config.guild_id, [incoming(config, message_time)])
    output_channel = channel_mock(config.guild_id)
    bot.get_channel = MagicMock(
        side_effect=lambda channel_id: (
            input_channel if channel_id == config.input_channel_id else output_channel
        )
    )
    await bot.daily_tick.coro(bot)
    snapshot = json.loads(store.board(config.guild_id, date(2026, 11, 13))["snapshot"])
    assert snapshot["rows"][0]["games"]["timeguessr"]["score"] == 18419
    assert snapshot["rows"][0]["total"] == 1
