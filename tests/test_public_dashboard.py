import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from daily_games.bot import CompetitionCommands, DailyGamesBot
from daily_games.calendar import active_day
from daily_games.models import Game, Submission
from daily_games.presentation import make_snapshot
from daily_games.public_dashboard import PUBLIC, create_public_app, public_data
from daily_games.public_links import (
    configure,
    ensure_key,
    leaderboard_links,
    read_config,
    server_slug,
)

KEY = b"k" * 32


@pytest.fixture
def public_setup(store, config):
    now = datetime.now(UTC)
    day = active_day(now, config)
    store.configure(replace(config, guild_id=101), actor_id=1)
    entries = [
        (100, 1, Game.TIMEGUESSR, 10, True, 0),
        (101, 1, Game.TIMEGUESSR, 100, True, 1),
        (100, 2, Game.TIMEGUESSR, 60, True, 2),
        (101, 3, Game.TIMEGUESSR, 60, True, 3),
        (100, 1, Game.METAZOOA, None, False, 4),
        (101, 1, Game.METAZOOA, 1, True, 5),
        (101, 2, Game.METAZOOA, 4, True, 6),
    ]
    for index, (guild, user, game, score, solved, offset) in enumerate(entries, start=1):
        store.insert(
            Submission(
                guild,
                user,
                f"Player {user}",
                game,
                "test",
                day,
                score,
                solved,
                index,
                200,
                now + timedelta(seconds=offset),
            )
        )
    path = Path(store.db.execute("PRAGMA database_list").fetchone()[2])
    return path, day


@pytest.fixture
async def public_client(public_setup):
    async with TestClient(TestServer(create_public_app(public_setup[0], KEY))) as client:
        yield client


def all_keys(value):
    if isinstance(value, dict):
        return set(value).union(*(all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(all_keys(item) for item in value))
    return set()


async def test_global_earliest_per_game_and_ties(public_client):
    response = await public_client.get("/api/leaderboard")
    assert response.status == 200
    data = await response.json()
    rows = {row["name"]: row for row in data["board"]["rows"]}
    assert data["players"] == 3
    assert data["submissions"] == 5
    assert rows["Player 1"]["games"]["timeguessr"]["score"] == 10
    assert rows["Player 1"]["games"]["metazooa"]["solved"] is False
    assert rows["Player 1"]["total"] == 1
    assert rows["Player 2"]["total"] == 4
    assert rows["Player 3"]["total"] == 2
    assert data["board"]["scoring"] == "players_beaten"
    assert rows["Player 1"]["streak"] == {"current": 1, "best": 1}
    # The game filter ranks tied raw results together and preserves global points.
    data = await (await public_client.get("/api/leaderboard?game=timeguessr")).json()
    assert [row["rank"] for row in data["board"]["rows"]] == [1, 1, 3]
    assert [row["total"] for row in data["board"]["rows"]] == [2, 2, 1]
    assert all(row["streak"]["current"] == 1 for row in data["board"]["rows"])


async def test_server_link_isolated_and_ids_private(public_client):
    slug = server_slug(KEY, 100)
    response = await public_client.get(f"/api/leaderboard?scope={slug}")
    data = await response.json()
    assert data["players"] == 2
    assert "Player 3" not in json.dumps(data["board"])
    forbidden = {
        "guild_id",
        "server_id",
        "user_id",
        "channel_id",
        "input_channel_id",
        "output_channel_id",
        "message_id",
        "pid",
        "bot",
        "database",
        "project",
        "attempted_at",
    }
    assert not all_keys(data) & forbidden
    global_data = await (await public_client.get("/api/leaderboard")).json()
    assert not all_keys(global_data) & forbidden
    assert all(len(server["slug"]) == 24 for server in global_data["servers"])


async def test_public_routes_do_not_expose_private_assets(public_client):
    slug = server_slug(KEY, 100)
    for route in [
        "/",
        "/global",
        f"/s/{slug}",
        "/assets/public.js",
        "/assets/public.css",
        "/assets/dashboard.css",
    ]:
        response = await public_client.get(route)
        assert response.status == 200
        assert response.headers["X-Frame-Options"] == "DENY"
    for route in [
        "/api/data",
        "/assets/dashboard.js",
        "/assets/index.html",
        "/.env",
        "/data/public-links.key",
        "/admin",
        "/s/100",
    ]:
        assert (await public_client.get(route)).status == 404
    html = await (await public_client.get("/")).text()
    assert "Bot health" not in html and "channel ID" not in html
    assert "Global leaderboard" in html
    assert (await public_client.post("/api/leaderboard")).status == 405


@pytest.mark.parametrize(
    "query,status",
    [
        ("scope=100", 404),
        ("scope=" + "f" * 24, 404),
        ("game=nope", 400),
        ("period=year", 400),
        ("day=nope", 400),
        ("day=9999-12-31", 400),
        ("server=100", 400),
        ("private=1", 400),
    ],
)
async def test_invalid_public_filters(public_client, query, status):
    response = await public_client.get(f"/api/leaderboard?{query}")
    assert response.status == status


def test_frozen_membership_and_scores_control_global_history(public_setup, store, config):
    path, day = public_setup
    for cfg in store.configs():
        snapshot = make_snapshot(store.results(cfg.guild_id, day), day, cfg)
        store.freeze(cfg.guild_id, day, 300, snapshot)
    before = list(store.db.iterdump())
    first = public_data(path, KEY, "global", day.isoformat(), "all")
    assert first["state"] == "final"
    assert list(store.db.iterdump()) == before
    # Protect frozen content even if raw rows were changed outside the bot.
    with store.db:
        store.db.execute(
            "UPDATE submissions SET score=50000 "
            "WHERE guild_id=100 AND user_id=1 AND game='timeguessr'"
        )
    existing = store.results(100, day)[0]
    store.insert(replace(existing, user_id=99, message_id=99, display_name="Late rogue row"))
    second = public_data(path, KEY, "global", day.isoformat(), "all")
    assert second["board"] == first["board"]
    server = public_data(path, KEY, server_slug(KEY, 100), day.isoformat(), "all")
    assert "Late rogue row" not in json.dumps(server)


def test_global_waits_for_all_servers_and_keeps_server_points(public_setup, store, config):
    path, day = public_setup
    snapshot = make_snapshot(store.results(100, day), day, config)
    store.freeze(100, day, 300, snapshot)
    assert public_data(path, KEY, "global", day.isoformat(), "all")["state"] != "final"
    server = public_data(path, KEY, server_slug(KEY, 100), day.isoformat(), "all")
    assert server["state"] == "final"
    assert [(row["name"], row["total"]) for row in server["board"]["rows"]] == [
        (row["name"], row["total"]) for row in snapshot["rows"]
    ]


def test_public_period_requires_finalized_global_day_and_hides_ids(public_setup, store, config):
    path, day = public_setup
    first = make_snapshot(store.results(100, day), day, config)
    store.freeze(100, day, 300, first)
    slug = server_slug(KEY, 100)
    server = public_data(path, KEY, slug, day.isoformat(), "timeguessr", "month")
    assert server["period_board"]["finalized_days"] == 1
    assert {row["name"] for row in server["period_board"]["rows"]} == {
        "Player 1", "Player 2"
    }
    global_pending = public_data(path, KEY, "global", day.isoformat(), "all", "month")
    assert global_pending["period_board"]["finalized_days"] == 0
    assert global_pending["period_board"]["rows"] == []
    other = next(cfg for cfg in store.configs() if cfg.guild_id == 101)
    store.freeze(101, day, 300, make_snapshot(store.results(101, day), day, other))
    global_final = public_data(path, KEY, "global", day.isoformat(), "all", "month")
    assert global_final["period_board"]["finalized_days"] == 1
    assert {row["name"]: row["points"] for row in global_final["period_board"]["rows"]} == {
        row["name"]: row["total"] for row in global_final["board"]["rows"]
    }
    assert "user_id" not in all_keys(global_final)


async def test_public_missing_db_is_generic(tmp_path):
    path = tmp_path / "private-database.sqlite3"
    async with TestClient(TestServer(create_public_app(path, KEY))) as client:
        response = await client.get("/api/leaderboard")
        assert response.status == 503
        assert str(tmp_path) not in await response.text()
        assert not path.exists()


async def test_public_cache_is_bounded(public_client):
    for index in range(1, 67):
        # Distinct historical dates, even without results, exercise the bound.
        day = f"2025-{1 + (index - 1) // 28:02d}-{1 + (index - 1) % 28:02d}"
        assert (await public_client.get(f"/api/leaderboard?day={day}")).status == 200
    assert len(public_client.server.app[PUBLIC]["cache"]) == 64


def test_links_persist_and_keep_filters(public_setup):
    path, day = public_setup
    assert leaderboard_links(path, 100, "Community", str(day), "all") is None
    configure(path, "https://example.ts.net:8443")
    key = ensure_key(path)
    assert ensure_key(path) == key
    links = leaderboard_links(path, 100, "Community", str(day), "sizeitup")
    assert f"/s/{server_slug(key, 100)}?" in links["server"]
    assert "game=sizeitup" in links["server"] and f"day={day}" in links["server"]
    assert "/global?" in links["global"]
    assert read_config(path)["server_names"]["100"] == "Community"
    assert (path.parent / "public-links.key").stat().st_mode & 0o777 == 0o600


async def test_discord_leaderboard_contains_server_and_global_links(public_setup, store):
    path, day = public_setup
    configure(path, "https://example.ts.net:8443")
    bot = DailyGamesBot(store)
    bot.recover_locked = AsyncMock()
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=100, name="Our server"), defer=AsyncMock(), send=AsyncMock()
    )
    try:
        cog = CompetitionCommands(bot)
        await cog.leaderboard_command.callback(cog, ctx, str(day), "timeguessr")
        embed = ctx.send.await_args.kwargs["embed"]
        field = embed.fields[-1]
        assert "Full leaderboard" in field.name
        assert "This server" in field.value and "Global leaderboard" in field.value
        assert f"/s/{server_slug(ensure_key(path), 100)}?" in field.value
        assert f"day={day}&game=timeguessr" in field.value
        assert read_config(path)["server_names"]["100"] == "Our server"
    finally:
        await bot.close()
