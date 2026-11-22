from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

from daily_games.bot import CompetitionCommands, DailyGamesBot, status_embed
from daily_games.dashboard import DashboardSettings, read_data
from daily_games.discord_views import compact_boards
from daily_games.models import DEFAULT_GAMES, Game
from daily_games.parsers import parse_message
from daily_games.presentation import game_snapshot, make_snapshot
from daily_games.public_dashboard import public_data
from daily_games.public_links import server_slug
from daily_games.service import accept_result
from daily_games.streaks import streak_lengths
from daily_games.wiki_daily import parse_route

DAY = date(2026, 11, 19)
WIKI_LINEUP = (Game.TIMEGUESSR, Game.SIZE_IT_UP, Game.WIKI)
SHARE = "Poet ➡️ Pluto ⏰ 03:15 🦶3 🔗 https://www.thewikigamedaily.com/"
KRILLION_SHARE = "Krillion #130 🦐\n245\n\n🐟🫧🐟🦑🐟⬛🏮"


def submit(store, cfg, share, day=DAY, user=1, hour=12):
    parsed, errors = parse_message(share)
    assert not errors
    return accept_result(
        store,
        cfg,
        parsed[0],
        user_id=user,
        display_name=f"Player {user}",
        message_id=user + day.toordinal() * 10,
        channel_id=cfg.input_channel_id,
        submitted_at=datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=hour),
    )


def test_rotation_inheritance_and_streaks(store, config):
    preceding, returning = DAY - timedelta(days=1), DAY + timedelta(days=2)
    store.set_rotation(0, DAY, WIKI_LINEUP, actor_id=1)
    store.set_rotation(config.guild_id, DAY, DEFAULT_GAMES, actor_id=2)
    store.set_rotation(config.guild_id, DAY + timedelta(days=1), None, actor_id=2)
    store.set_rotation(0, returning, DEFAULT_GAMES, actor_id=1)

    local, worldwide = store.rotation(config.guild_id), store.rotation()
    assert worldwide.games_on(preceding) == DEFAULT_GAMES
    assert worldwide.games_on(DAY) == WIKI_LINEUP
    assert local.games_on(DAY) == DEFAULT_GAMES
    assert local.next_change(DAY) == DAY + timedelta(days=1)
    assert local.games_on(DAY + timedelta(days=1)) == WIKI_LINEUP
    assert local.games_on(returning) == DEFAULT_GAMES
    assert local.games_between(preceding, returning) == (*DEFAULT_GAMES, Game.WIKI)
    assert streak_lengths({preceding, returning}, returning, worldwide, Game.METAZOOA) == {
        "current": 2,
        "best": 2,
    }
    assert streak_lengths({preceding, returning}, returning, local, Game.METAZOOA) == {
        "current": 1,
        "best": 1,
    }
    assert streak_lengths({preceding}, returning, worldwide, Game.METAZOOA)["current"] == 1


def test_wiki_share_boundary_and_route_validation(store, config):
    store.set_rotation(0, DAY, WIKI_LINEUP, actor_id=1)
    assert submit(store, config, SHARE, DAY - timedelta(days=1)).status == "inactive"
    assert submit(store, config, SHARE).status == "unverified"
    store.register_wiki_route(DAY, "Poet", "Pluto")
    assert submit(store, config, SHARE.replace("Pluto", "Neptune")).status == "wrong_puzzle"
    assert submit(store, config, SHARE).status == "accepted"
    assert submit(store, config, SHARE).status == "recorded"
    row = store.results(config.guild_id, DAY)[0]
    assert (row.puzzle, row.score, row.elapsed_seconds) == (DAY.isoformat(), 3, 195)
    assert parse_message(SHARE.replace("03:15", "03:80"))[1]
    assert parse_message(SHARE.replace(".com/", ".com.invalid/"))[1]
    expanded = SHARE.replace("🦶3", "🦶3 (Poet ➡️ Astronomy ➡️ Pluto)")
    assert parse_message(expanded) == parse_message(SHARE)


def test_wiki_path_without_link_records_reported_steps_and_time(store, config):
    store.set_rotation(0, DAY, WIKI_LINEUP, actor_id=1)
    store.register_wiki_route(DAY, "Poet", "Pluto")
    share = (
        "Poet ➡️ Pluto ⏰ 02:07 🦶5 (Poet ➡️ Renaissance ➡️ Leonardo da Vinci ➡️ "
        "Galilean moons ➡️ Solar System object ➡️ Pluto)"
    )
    assert submit(store, config, share.replace("Poet ➡️ Pluto", "Poet ➡️ Neptune")).status == (
        "wrong_puzzle"
    )
    assert submit(store, config, share).status == "accepted"
    saved = store.results(config.guild_id, DAY)[0]
    assert (saved.score, saved.elapsed_seconds) == (5, 127)


def test_wiki_uses_server_cutoff_with_original_timestamp(store, config):
    cfg = replace(config, cutoff="06:00")
    store.configure(cfg, actor_id=1)
    store.set_rotation(0, DAY, WIKI_LINEUP, actor_id=1)
    store.register_wiki_route(DAY, "Poet", "Pluto")
    assert submit(store, cfg, SHARE, hour=4).status == "inactive"
    assert submit(store, cfg, SHARE, hour=5).status == "accepted"


@pytest.mark.parametrize("score", ["701", "-1", "245/700", "fish"])
def test_krillion_rejects_invalid_score_lines(score):
    parsed, errors = parse_message(KRILLION_SHARE.replace("\n245\n", f"\n{score}\n"))
    assert not parsed and errors


def test_krillion_daily_rotation_ranks_scores_and_shows_game_link(store, config):
    day = date(2026, 11, 22)
    lineup = (Game.SIZE_IT_UP, Game.WIKI, Game.KRILLION)
    assert submit(store, config, KRILLION_SHARE, day).status == "inactive"
    store.set_rotation(0, day, lineup, actor_id=1)
    assert submit(store, config, KRILLION_SHARE.replace("#130", "#129"), day).status == "old"
    assert submit(store, config, KRILLION_SHARE.replace("#130", "#131"), day).status == "future"
    assert submit(store, config, KRILLION_SHARE, day).status == "accepted"
    assert submit(store, config, KRILLION_SHARE.replace("245", "700"), day, user=2).status == (
        "accepted"
    )
    assert submit(store, config, KRILLION_SHARE.replace("245", "0"), day, user=3).status == (
        "accepted"
    )

    snapshot = make_snapshot(store.results(config.guild_id, day), day, config, lineup)
    ranked = game_snapshot(snapshot, Game.KRILLION)["rows"]
    assert [row["games"]["krillion"]["score"] for row in ranked] == [700, 245, 0]
    assert ranked[2]["games"]["krillion"]["solved"]
    status = status_embed(store, config, day, None)
    assert "Krillion: `#130`" in status.description
    assert "https://krillion.io/" in status.description
    assert "midnight New York time" in status.description
    path = Path(store.db.execute("PRAGMA database_list").fetchone()[2])
    public_board = public_data(path, b"test-key", "global", day.isoformat(), "krillion")["board"]
    assert public_board["rows"][0]["games"]["krillion"]["score"] == 700


def test_wiki_ranking_and_dashboard_rotation(store, config):
    store.set_rotation(0, DAY, WIKI_LINEUP, actor_id=1)
    local_games = (Game.TIMEGUESSR, Game.METAZOOA, Game.WIKI)
    store.set_rotation(config.guild_id, DAY, local_games, actor_id=2)
    store.register_wiki_route(DAY, "Poet", "Pluto")
    for user, share in (
        (1, SHARE),
        (2, SHARE.replace("03:15", "02:00")),
        (3, SHARE.replace("03:15", "00:10").replace("🦶3", "🦶4")),
        (4, SHARE),
    ):
        assert submit(store, config, share, user=user).status == "accepted"
    zoo = "Animal #1207\nI figured it out in 2 guesses!\n#metazooa"
    assert submit(store, config, zoo, user=5).status == "accepted"
    snapshot = make_snapshot(store.results(config.guild_id, DAY), DAY, config, local_games)
    wiki = game_snapshot(snapshot, Game.WIKI)
    assert [row["user_id"] for row in wiki["rows"]] == [2, 1, 4, 3]
    assert [row["rank"] for row in wiki["rows"]] == [1, 2, 2, 4]
    assert "3 steps · 2:00" in compact_boards(snapshot, final=True, game=Game.WIKI)[0].description
    store.freeze(config.guild_id, DAY, config.output_channel_id, snapshot)

    path = Path(store.db.execute("PRAGMA database_list").fetchone()[2])
    key = b"test-key"
    global_board = public_data(path, key, "global", DAY.isoformat(), "all")["board"]
    local_board = public_data(path, key, server_slug(key, config.guild_id), DAY.isoformat(), "all")[
        "board"
    ]
    private_board = read_data(
        DashboardSettings(path, path.parent), str(config.guild_id), DAY.isoformat(), "all"
    )["selected"]["board"]
    assert list(global_board["lineup"]) == list(WIKI_LINEUP)
    assert list(local_board["lineup"]) == list(private_board["lineup"]) == list(local_games)
    assert "Player 5" not in {row["name"] for row in global_board["rows"]}
    for board in (global_board, local_board, private_board):
        winner = board["rows"][0]
        assert winner["name"] == "Player 2"
        assert winner["games"]["wikigame"]["elapsed_seconds"] == 120


def test_official_route_parser_requires_exact_date():
    asset = (
        'JSON.parse(\'{"2026-11-18":{"START_PAGE":"Archive","END_PAGE":"Old"},'
        '"2026-11-19":{"START_PAGE":"Poet","END_PAGE":"Pluto"}}\')'
    )
    assert parse_route(asset, DAY) == ("Poet", "Pluto")
    assert parse_route(asset, DAY + timedelta(days=1)) is None


def test_official_route_parser_decodes_javascript_escapes():
    script = (
        r"""JSON.parse('{"2026-11-20":{"START_PAGE":"Thomas Edison","END_PAGE":"McDonald\'s"},"""
        r""""2026-11-21":{"START_PAGE":"Ros\xe9","END_PAGE":"Caf\u00e9"}}')"""
    )
    assert parse_route(script, date(2026, 11, 20)) == ("Thomas Edison", "McDonald's")
    assert parse_route(script, date(2026, 11, 21)) == ("Rosé", "Café")


async def test_rotation_commands_scope_and_timeguessr_opt_out(store, config, monkeypatch):
    bot = DailyGamesBot(store)
    owner = AsyncMock(return_value=False)
    bot.is_owner = owner
    async with bot:
        cog = CompetitionCommands(bot)
        member = SimpleNamespace(id=8, guild_permissions=SimpleNamespace(manage_guild=False))
        ctx = SimpleNamespace(
            bot=bot, guild=SimpleNamespace(id=config.guild_id), author=member, send=AsyncMock()
        )
        with pytest.raises(commands.BadArgument, match="Manage Server"):
            await cog.rotation.callback(cog, ctx, "inherit")
        with pytest.raises(commands.NotOwner):
            await discord.utils.async_all(check(ctx) for check in cog.rotation_global.checks)
        member.guild_permissions.manage_guild = True
        await cog.rotation.callback(cog, ctx, "set", "metazooa", "sizeitup", "wikigame")
        rotation = store.rotation(config.guild_id)
        active_from = rotation.dates[-1]
        assert Game.TIMEGUESSR not in rotation.games_on(active_from)
        now = datetime.combine(active_from, datetime.min.time(), UTC)
        lookup = AsyncMock()
        monkeypatch.setattr("daily_games.bot.next_reset", lookup)
        store.register_wiki_route(active_from, "Poet", "Pluto")
        assert await bot.daily_reset(config, active_from, now) is None
        lookup.assert_not_awaited()
        assert "TimeGuessr" not in status_embed(store, config, active_from, None).description


async def test_global_change_waits_for_date_not_open_on_any_server(store, config, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = datetime(2026, 11, 19, 20, tzinfo=UTC)
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr("daily_games.bot.datetime", Clock)
    store.configure(replace(config, guild_id=101, timezone="Asia/Tokyo"), actor_id=1)
    async with DailyGamesBot(store) as bot:
        cog = CompetitionCommands(bot)
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=config.guild_id),
            author=SimpleNamespace(id=1),
            send=AsyncMock(),
        )
        await cog.rotation_global.callback(cog, ctx, "set", "timeguessr", "sizeitup", "wikigame")
        assert store.rotation().games_on(date(2026, 11, 20)) == DEFAULT_GAMES
        assert store.rotation().games_on(date(2026, 11, 21)) == WIKI_LINEUP
