import json
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from discord.ext import commands

from daily_games.bot import CompetitionCommands, DailyGamesBot, board_embeds
from daily_games.models import Game, Submission
from daily_games.presentation import game_snapshot, make_snapshot
from daily_games.storage import Store

DAY = date(2026, 11, 13)


def submission(user, game, score, solved=True):
    return Submission(
        100,
        user,
        f"Player {user}",
        game,
        "test",
        DAY,
        score,
        solved,
        user,
        200,
        datetime(2026, 11, 13, 18, tzinfo=UTC),
    )


@pytest.fixture
def results():
    return [
        submission(1, Game.TIMEGUESSR, 100),
        submission(2, Game.TIMEGUESSR, 100),
        submission(3, Game.TIMEGUESSR, 50),
        submission(1, Game.METAZOOA, None, False),
        submission(3, Game.METAZOOA, 4),
        submission(4, Game.METAZOOA, 8),
        submission(1, Game.SIZE_IT_UP, 350),
        submission(2, Game.SIZE_IT_UP, 368),
    ]


@pytest.mark.parametrize("mode", ["players_beaten", "placement"])
def test_game_views_preserve_points_ties_and_source(config, results, mode):
    snapshot = make_snapshot(results, DAY, replace(config, scoring=mode))
    original = json.dumps(snapshot, sort_keys=True)
    tg = game_snapshot(snapshot, Game.TIMEGUESSR)
    assert [row["user_id"] for row in tg["rows"]] == [1, 2, 3]
    assert [row["rank"] for row in tg["rows"]] == [1, 1, 3]
    # Placement points still count all four daily participants, not only TG players.
    assert tg["rows"][0]["total"] == (4 if mode == "placement" else 2)
    zoo = game_snapshot(snapshot, Game.METAZOOA)
    assert [row["user_id"] for row in zoo["rows"]] == [3, 4, 1]
    assert zoo["rows"][-1]["total"] == 0
    size = game_snapshot(snapshot, Game.SIZE_IT_UP)
    assert [row["user_id"] for row in size["rows"]] == [2, 1]
    for game in Game:
        for row in game_snapshot(snapshot, game)["rows"]:
            assert row["total"] == row["games"][game.value]["points"]
    assert json.dumps(snapshot, sort_keys=True) == original


def test_filtered_embeds_and_empty_game(config, results):
    snapshot = make_snapshot(results, DAY, config)
    combined = board_embeds(snapshot, 100, final=False)[0]
    assert combined.title.startswith("Daily Games")
    assert "TG:" in combined.description and "Zoo:" in combined.description
    size = board_embeds(snapshot, 100, final=False, game=Game.SIZE_IT_UP)[0]
    assert size.title.startswith("Size It Up")
    assert "368 (+2)" in size.description
    assert "TG:" not in size.description and "Zoo:" not in size.description
    assert "Player 3" not in size.description
    empty = make_snapshot([], DAY, config)
    assert "No results" in board_embeds(empty, 100, final=True, game=Game.METAZOOA)[0].description


def test_game_views_survive_database_reopen(tmp_path, config, results):
    path = tmp_path / "persisted.sqlite3"
    snapshot = make_snapshot(results, DAY, config)
    store = Store(path)
    store.configure(config, actor_id=1)
    store.freeze(100, DAY, 300, snapshot)
    store.close()
    reopened = Store(path)
    try:
        saved = json.loads(reopened.board(100, DAY)["snapshot"])
        for game in Game:
            assert game_snapshot(saved, game) == game_snapshot(snapshot, game)
        assert saved == snapshot
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "day,game,expected",
    [
        ("2026-11-13", "all", "Daily Games"),
        ("2026-11-13", "sizeitup", "Size It Up"),
        ("sizeitup", "2026-11-13", "Size It Up"),
        ("timeguessr", "2026-11-13", "TimeGuessr"),
        ("metazooa", "2026-11-13", "Metazooa"),
    ],
)
async def test_commands_use_frozen_snapshot(store, config, results, day, game, expected):
    snapshot = make_snapshot(results, DAY, config)
    store.freeze(100, DAY, 300, snapshot)
    bot = DailyGamesBot(store)
    bot.recover_locked = AsyncMock()
    ctx = SimpleNamespace(guild=SimpleNamespace(id=100), defer=AsyncMock(), send=AsyncMock())
    try:
        cog = CompetitionCommands(bot)
        await cog.leaderboard_command.callback(cog, ctx, day, game)
        embed = ctx.send.await_args.kwargs["embed"]
        assert embed.title.startswith(expected)
        assert "Final standings" in embed.title
        assert json.loads(store.board(100, DAY)["snapshot"]) == snapshot
    finally:
        await bot.close()


@pytest.mark.parametrize("day,game", [("today", "all"), ("yesterday", "all"), ("sizeitup", "all")])
async def test_live_and_relative_day_commands(store, config, day, game):
    bot = DailyGamesBot(store)
    bot.recover_locked = AsyncMock()
    ctx = SimpleNamespace(guild=SimpleNamespace(id=100), defer=AsyncMock(), send=AsyncMock())
    try:
        cog = CompetitionCommands(bot)
        await cog.leaderboard_command.callback(cog, ctx, day, game)
        embed = ctx.send.await_args.kwargs["embed"]
        assert embed.title.startswith("Size It Up" if day == "sizeitup" else "Daily Games")
        assert "provisional" in embed.title
    finally:
        await bot.close()


async def test_slash_game_choices_and_invalid_prefix_game(store):
    bot = DailyGamesBot(store)
    try:
        await bot.add_cog(CompetitionCommands(bot))
        payload = bot.tree.get_command("leaderboard").to_dict(bot.tree)
        options = {option["name"]: option for option in payload["options"]}
        assert {item["value"] for item in options["game"]["choices"]} == {
            "all",
            "timeguessr",
            "sizeitup",
            "metazooa",
            "wikigame",
        }
        cog = bot.get_cog("CompetitionCommands")
        with pytest.raises(commands.BadArgument, match="Choose all"):
            await cog.leaderboard_command.callback(cog, None, "today", "invalid")
    finally:
        await bot.close()
