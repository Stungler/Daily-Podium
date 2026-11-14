from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from daily_games.bot import CompetitionCommands, DailyGamesBot
from daily_games.discord_views import (
    compact_boards,
    daily_highlights,
    weekly_embed,
    weekly_snapshot,
)
from daily_games.models import Game, Submission
from daily_games.presentation import make_snapshot
from daily_games.storage import Store

DAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 14, 0, 5, tzinfo=UTC)


async def history(items):
    for item in items:
        yield item


@pytest.fixture
async def delivery(store, config):
    result = Submission(
        100,
        7,
        "Player",
        Game.TIMEGUESSR,
        "1262",
        DAY,
        40000,
        True,
        777,
        200,
        NOW - timedelta(hours=6),
    )
    store.insert(result)
    store.freeze(100, DAY, 300, make_snapshot([result], DAY, config))
    bot = DailyGamesBot(store)
    bot._connection.user = SimpleNamespace(id=123)
    guild = SimpleNamespace(id=100, name="Our server", me=SimpleNamespace(id=123))
    channel = MagicMock(spec=discord.TextChannel)
    channel.id, channel.guild = 300, guild
    permissions = discord.Permissions.all()
    channel.permissions_for.return_value = permissions
    thread = MagicMock(spec=discord.Thread)
    thread.id, thread.parent_id, thread.guild = 9000, 300, guild
    thread.archived, thread.locked = False, False
    thread.edit = AsyncMock()
    state = SimpleNamespace(created=False, parent_history=[], thread_history=[])

    async def send_parent(**kwargs):
        message = SimpleNamespace(id=9000, author=SimpleNamespace(id=123), embeds=[kwargs["embed"]])
        state.parent_history.append(message)
        return message

    async def create_thread(**kwargs):
        state.created = True
        return thread

    async def send_details(**kwargs):
        message = SimpleNamespace(
            id=10000 + len(state.thread_history),
            author=SimpleNamespace(id=123),
            embeds=[kwargs["embed"]],
        )
        state.thread_history.append(message)
        return message

    channel.send = AsyncMock(side_effect=send_parent)
    channel.create_thread = AsyncMock(side_effect=create_thread)
    thread.send = AsyncMock(side_effect=send_details)
    channel.history.side_effect = lambda **kwargs: history(state.parent_history)
    thread.history.side_effect = lambda **kwargs: history(state.thread_history)
    bot.get_channel = MagicMock(
        side_effect=lambda id: (
            channel if id == 300 else thread if id == 9000 and state.created else None
        )
    )
    bot.get_guild = MagicMock(return_value=guild)
    bot.fetch_channel = AsyncMock(
        side_effect=discord.NotFound(
            SimpleNamespace(status=404, reason="Not found"), "Unknown channel"
        )
    )
    yield SimpleNamespace(
        bot=bot, channel=channel, thread=thread, state=state, permissions=permissions
    )
    await bot.close()


async def test_one_compact_parent_and_one_attached_thread(delivery, store, config):
    bot, channel, thread = delivery.bot, delivery.channel, delivery.thread
    await bot.publish_locked(config, DAY, NOW)
    await bot.publish_locked(config, DAY, NOW + timedelta(seconds=30))
    channel.send.assert_awaited_once()
    channel.create_thread.assert_awaited_once()
    assert channel.create_thread.await_args.kwargs["message"].id == 9000
    assert thread.send.await_count == 3  # highlights, full results, weekly
    assert store.details(100, DAY)["complete"] == 1
    assert store.details(100, DAY)["thread_id"] == 9000
    parent = channel.send.await_args.kwargs["embed"]
    assert "`" in parent.description and "Player" in parent.description
    assert "TimeGuessr" not in parent.description  # full game data lives below
    for call in thread.send.await_args_list:
        assert call.kwargs["silent"] is True
        assert call.kwargs["allowed_mentions"].everyone is False


async def test_missing_thread_permissions_preserves_parent_and_retries(delivery, store, config):
    delivery.permissions.create_public_threads = False
    await delivery.bot.publish_locked(config, DAY, NOW)
    assert store.board(100, DAY)["message_id"] == 9000
    assert store.details(100, DAY)["complete"] == 0
    delivery.channel.create_thread.assert_not_awaited()
    delivery.permissions.create_public_threads = True
    await delivery.bot.publish_locked(config, DAY, NOW + timedelta(minutes=1))
    delivery.channel.create_thread.assert_not_awaited()
    await delivery.bot.publish_locked(config, DAY, NOW + timedelta(minutes=16))
    delivery.channel.send.assert_awaited_once()
    assert store.details(100, DAY)["complete"] == 1


async def test_create_thread_timeout_reuses_message_thread(delivery, store, config):
    async def ambiguous_create(**kwargs):
        delivery.state.created = True
        raise TimeoutError("Discord accepted thread creation but response was lost")

    delivery.channel.create_thread.side_effect = ambiguous_create
    await delivery.bot.publish_locked(config, DAY, NOW)
    assert store.details(100, DAY)["complete"] == 0
    await delivery.bot.publish_locked(config, DAY, NOW + timedelta(minutes=16))
    delivery.channel.create_thread.assert_awaited_once()
    delivery.channel.send.assert_awaited_once()
    assert store.details(100, DAY)["complete"] == 1


async def test_ambiguous_detail_send_reconciles_without_duplicate(delivery, store, config):
    original = delivery.thread.send.side_effect

    async def ambiguous_send(**kwargs):
        message = await original(**kwargs)
        if len(delivery.state.thread_history) == 2:
            raise TimeoutError("Discord accepted detail message but response was lost")
        return message

    delivery.thread.send.side_effect = ambiguous_send
    await delivery.bot.publish_locked(config, DAY, NOW)
    assert len(delivery.state.thread_history) == 2
    assert store.details(100, DAY)["complete"] == 0
    await delivery.bot.publish_locked(config, DAY, NOW + timedelta(minutes=16))
    assert len(delivery.state.thread_history) == 3
    assert store.details(100, DAY)["complete"] == 1
    delivery.channel.send.assert_awaited_once()


async def test_parent_timeout_reuses_parent_before_thread(delivery, store, config):
    original = delivery.channel.send.side_effect

    async def ambiguous_parent(**kwargs):
        await original(**kwargs)
        raise TimeoutError("Parent sent but acknowledgement lost")

    delivery.channel.send.side_effect = ambiguous_parent
    with pytest.raises(TimeoutError):
        await delivery.bot.publish_locked(config, DAY, NOW)
    await delivery.bot.publish_locked(config, DAY, NOW + timedelta(seconds=30))
    delivery.channel.send.assert_awaited_once()
    delivery.channel.create_thread.assert_awaited_once()
    assert store.details(100, DAY)["complete"] == 1


async def test_completed_thread_survives_database_reopen(delivery, store, config):
    await delivery.bot.publish_locked(config, DAY, NOW)
    path = Path(store.db.execute("PRAGMA database_list").fetchone()[2])
    reopened = Store(path)
    second = DailyGamesBot(reopened)
    second.get_channel = MagicMock(side_effect=AssertionError("No Discord calls needed"))
    try:
        await second.publish_locked(config, DAY, NOW + timedelta(days=1))
        assert reopened.details(100, DAY)["complete"] == 1
    finally:
        await second.close()
        reopened.close()


async def test_old_published_boards_are_not_retrofitted(delivery, store, config):
    store.mark_posted(100, DAY, 8000)
    await delivery.bot.publish_locked(config, DAY, NOW)
    delivery.channel.send.assert_not_awaited()
    delivery.channel.create_thread.assert_not_awaited()
    assert store.details(100, DAY) is None


async def test_empty_day_does_not_create_empty_thread(delivery, store, config):
    day = DAY + timedelta(days=1)
    store.freeze(100, day, 300, make_snapshot([], day, config))
    await delivery.bot.publish_locked(config, day, NOW + timedelta(days=1))
    delivery.channel.send.assert_awaited_once()
    delivery.channel.create_thread.assert_not_awaited()
    assert store.details(100, day)["complete"] == 1


async def test_archived_and_locked_threads(delivery, store, config):
    delivery.state.created = True
    delivery.thread.locked = True
    await delivery.bot.publish_locked(config, DAY, NOW)
    delivery.thread.send.assert_not_awaited()
    delivery.thread.locked = False
    delivery.thread.archived = True
    await delivery.bot.publish_locked(config, DAY, NOW + timedelta(minutes=16))
    delivery.thread.edit.assert_awaited_once_with(archived=False)
    assert store.details(100, DAY)["complete"] == 1


def test_weekly_scores_ties_and_partial_coverage():
    def day_snapshot(day, rows):
        return {
            "day": day,
            "rows": [
                {"user_id": uid, "name": name, "rank": rank, "total": total}
                for uid, name, rank, total in rows
            ],
        }

    snapshots = [
        day_snapshot("2026-11-08", [(1, "Old week", 1, 100)]),
        day_snapshot("2026-11-09", [(1, "A", 1, 3), (2, "B", 1, 3)]),
        day_snapshot("2026-11-11", [(1, "A", 1, 2), (2, "B", 1, 2), (3, "Failure", 3, 0)]),
        day_snapshot("2026-11-14", [(3, "Future", 1, 100)]),
    ]
    summary = weekly_snapshot(snapshots, DAY, date(2026, 11, 9))
    assert summary["finalized_days"] == 2 and summary["expected_days"] == 5
    assert [(row["rank"], row["points"], row["days"], row["wins"]) for row in summary["rows"]] == [
        (1, 5, 2, 2),
        (1, 5, 2, 2),
        (3, 0, 1, 0),
    ]
    embed = weekly_embed(summary)
    assert "2/5 days finalized" in embed.footer.text
    assert len(embed) < 6000


def test_compact_table_limits_and_safe_names(config):
    snapshot = make_snapshot([], DAY, config)
    snapshot["rows"] = [
        {
            "rank": index,
            "user_id": index,
            "name": "@everyone `name`\n" + "界" * 40,
            "total": 5,
            "games": {},
        }
        for index in range(1, 101)
    ]
    limited = compact_boards(snapshot, final=True, limit=10)
    assert len(limited) == 1
    assert "Top 10 of 100" in limited[0].description
    assert "@everyone" not in limited[0].description
    assert len(limited[0].description.splitlines()) == 12  # heading, 10 rows, overflow link hint
    full = compact_boards(snapshot, final=True)
    assert sum(len(embed.description.splitlines()) - 1 for embed in full) == 100
    assert all(len(embed) < 6000 for embed in full)


def test_highlights_include_tied_winners_and_failures(config):
    snapshot = make_snapshot([], DAY, config)
    snapshot["rows"] = [
        {
            "user_id": uid,
            "name": f"Player {uid}",
            "rank": 1,
            "total": 2,
            "games": {
                "timeguessr": {"score": 100, "solved": True, "points": 2},
                "metazooa": {"score": None, "solved": False, "points": 0},
            },
        }
        for uid in (1, 2)
    ]
    embed = daily_highlights(snapshot)
    assert "Player 1" in embed.fields[0].value and "Player 2" in embed.fields[0].value
    assert "no successful results" in embed.fields[2].value


async def test_weekly_command_reads_only_finalized_server_boards(delivery, store, config):
    cog = CompetitionCommands(delivery.bot)
    ctx = SimpleNamespace(guild=SimpleNamespace(id=100), send=AsyncMock())
    await cog.weekly.callback(cog, ctx, DAY.isoformat())
    embed = ctx.send.await_args.kwargs["embed"]
    assert "Player" in embed.description
    assert "1/1 days finalized" in embed.footer.text
    delivery.channel.create_thread.assert_not_awaited()


async def test_monthly_game_command_uses_saved_game_points(delivery, store, config):
    cog = CompetitionCommands(delivery.bot)
    ctx = SimpleNamespace(guild=SimpleNamespace(id=100), send=AsyncMock())
    await cog.monthly.callback(cog, ctx, DAY.isoformat(), "timeguessr")
    embed = ctx.send.await_args.kwargs["embed"]
    assert "This month · TimeGuessr" in embed.title
    assert "1/1 days finalized" in embed.footer.text
    assert "Player" in embed.description


def test_additive_schema_preserves_old_scores(tmp_path, config):
    path = tmp_path / "migration.sqlite3"
    old = Store(path)
    old.configure(config, actor_id=1)
    old.freeze(100, DAY, 300, make_snapshot([], DAY, config))
    with old.db:
        old.db.execute("DROP TABLE board_details")
        old.db.execute("DELETE FROM schema_version WHERE version=2")
    before = old.board(100, DAY)["snapshot"]
    old.close()
    upgraded = Store(path)
    try:
        assert upgraded.board(100, DAY)["snapshot"] == before
        assert upgraded.details(100, DAY) is None
        assert upgraded.db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 2
    finally:
        upgraded.close()
