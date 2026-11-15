from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from daily_games.bot import CompetitionCommands, DailyGamesBot
from daily_games.calendar import active_day
from daily_games.models import Game, Submission


async def test_myday_is_private_and_reports_accepted_missing_and_streaks(store, config):
    now = datetime.now(UTC)
    today = active_day(now, config)
    yesterday = today - timedelta(days=1)
    for message_id, day, game, score, solved in [
        (1, yesterday, Game.TIMEGUESSR, 100, True),
        (2, today, Game.TIMEGUESSR, 200, True),
        (3, today, Game.METAZOOA, None, False),
    ]:
        assert store.insert(
            Submission(
                config.guild_id,
                7,
                "Player",
                game,
                day.isoformat(),
                day,
                score,
                solved,
                message_id,
                config.input_channel_id,
                now,
            )
        )
    bot = DailyGamesBot(store)
    cog = CompetitionCommands(bot)
    interaction = SimpleNamespace(
        guild_id=config.guild_id,
        user=SimpleNamespace(id=7),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    try:
        await cog.myday.callback(cog, interaction)
        kwargs = interaction.response.send_message.await_args.kwargs
        assert kwargs["ephemeral"] is True
        embed = kwargs["embed"]
        assert "2/3 accepted" in embed.description
        assert "Any-game streak: **2**" in embed.description
        assert "Recorded" in embed.fields[0].value
        assert "2 current / 2 best" in embed.fields[0].value
        assert "Missing" in embed.fields[1].value
        assert "Failed (+0)" in embed.fields[2].value
        assert "1 current / 1 best" in embed.fields[2].value
    finally:
        await bot.close()


async def test_myday_unconfigured_server_is_private(store):
    bot = DailyGamesBot(store)
    cog = CompetitionCommands(bot)
    interaction = SimpleNamespace(
        guild_id=999,
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    try:
        await cog.myday.callback(cog, interaction)
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
        assert "setup" in interaction.response.send_message.await_args.args[0]
    finally:
        await bot.close()
