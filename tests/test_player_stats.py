from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from daily_games.bot import CompetitionCommands, DailyGamesBot
from daily_games.discord_views import player_stats_embed, player_stats_snapshot
from daily_games.models import Game, ServerConfig, Submission
from daily_games.presentation import make_snapshot

DAY = date(2026, 11, 13)
WHEN = datetime(2026, 11, 13, 18, tzinfo=UTC)


def submission(user_id, game, score, day, *, solved=True):
    return Submission(
        100,
        user_id,
        f"Player {user_id}",
        game,
        day.isoformat(),
        day,
        score,
        solved,
        user_id * 100 + list(Game).index(game),
        200,
        WHEN + timedelta(days=(day - DAY).days),
    )


def test_stats_count_ties_failures_and_solved_zero(config):
    first = make_snapshot(
        [
            submission(7, Game.TIMEGUESSR, 40000, DAY),
            submission(7, Game.SIZE_IT_UP, 350, DAY),
            submission(7, Game.METAZOOA, None, DAY, solved=False),
            submission(8, Game.TIMEGUESSR, 30000, DAY),
            submission(8, Game.SIZE_IT_UP, 250, DAY),
            submission(8, Game.METAZOOA, 4, DAY),
        ],
        DAY,
        config,
    )
    next_day = DAY + timedelta(days=1)
    second = make_snapshot(
        [
            submission(7, Game.TIMEGUESSR, 0, next_day),
            submission(7, Game.METAZOOA, 2, next_day),
            submission(8, Game.TIMEGUESSR, 0, next_day),
            submission(8, Game.METAZOOA, 2, next_day),
        ],
        next_day,
        config,
    )
    third_day = DAY + timedelta(days=2)
    third = make_snapshot(
        [submission(7, Game.METAZOOA, None, third_day, solved=False)],
        third_day,
        config,
    )
    empty_day = DAY + timedelta(days=3)
    empty = make_snapshot([], empty_day, config)

    stats = player_stats_snapshot([third, empty, first, second], 7)
    assert (stats["days_played"], stats["finalized_days"]) == (3, 4)
    assert stats["daily_wins"] == 2  # Tied first places count; a zero-point day does not.
    assert stats["points"] == 6
    assert stats["last_day"] == third_day.isoformat()
    assert stats["games"]["timeguessr"] == {
        "played": 2,
        "solved": 2,
        "wins": 2,
        "points": 3,
        "score_sum": 40000,
        "best": 40000,
    }
    assert stats["games"]["sizeitup"]["wins"] == 1
    assert stats["games"]["metazooa"] == {
        "played": 3,
        "solved": 1,
        "wins": 1,
        "points": 1,
        "score_sum": 2,
        "best": 2,
    }
    embed = player_stats_embed(stats, "Player 7")
    assert "3/4" in embed.description
    assert "2.0" in embed.description  # Six points over three played days.
    assert "avg 20,000.0" in embed.fields[0].value
    assert "best 40,000" in embed.fields[0].value
    assert "best 2 guesses" in embed.fields[2].value
    assert len(embed) < 6000


def test_stats_empty_player_and_safe_name(config):
    stats = player_stats_snapshot([make_snapshot([], DAY, config)], 999)
    embed = player_stats_embed(stats, "@everyone `hi`")
    assert "No results" in embed.description
    assert "@everyone" not in embed.title
    assert all("No finalized results" in field.value for field in embed.fields)


async def test_stats_command_uses_only_this_servers_finalized_days(store, config):
    result = submission(7, Game.TIMEGUESSR, 40000, DAY)
    store.freeze(100, DAY, 300, make_snapshot([result], DAY, config))
    # A current-day submission has no frozen board and must not affect stats.
    store.insert(submission(7, Game.TIMEGUESSR, 50000, DAY + timedelta(days=1)))
    other = ServerConfig(101, 201, 301, "Europe/Berlin", "00:00", "players_beaten", DAY)
    store.configure(other, actor_id=1)
    other_result = replace(submission(7, Game.TIMEGUESSR, 50000, DAY), guild_id=101)
    store.freeze(101, DAY, 301, make_snapshot([other_result], DAY, other))

    bot = DailyGamesBot(store)
    cog = CompetitionCommands(bot)
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=100),
        author=SimpleNamespace(id=7, display_name="Current name"),
        send=AsyncMock(),
    )
    try:
        await cog.stats.callback(cog, ctx, None)
        embed = ctx.send.await_args.kwargs["embed"]
        assert "Current name" in embed.title
        assert "1/1" in embed.description
        assert "40,000" in embed.fields[0].value
        assert "50,000" not in embed.fields[0].value

        target = SimpleNamespace(id=8, display_name="No results")
        await cog.stats.callback(cog, ctx, target)
        assert "No results in finalized days" in ctx.send.await_args.kwargs["embed"].description
    finally:
        await bot.close()
