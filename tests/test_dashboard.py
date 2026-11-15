import argparse
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from daily_games import dashboard
from daily_games.calendar import active_day
from daily_games.dashboard import (
    DashboardSettings,
    create_app,
    private_host,
    read_data,
    read_database,
)
from daily_games.models import Game, Submission
from daily_games.presentation import game_snapshot, make_snapshot

USER = 9007199254740993


@pytest.fixture
def database_path(store):
    return Path(store.db.execute("PRAGMA database_list").fetchone()[2])


@pytest.fixture
def settings(database_path, monkeypatch, tmp_path):
    monkeypatch.setattr(
        dashboard,
        "process_status",
        lambda _: {
            "state": "running",
            "processes": [{"pid": "123", "uptime_seconds": 60}],
            "gateway": "not_measured",
        },
    )
    return DashboardSettings(database_path, tmp_path)


@pytest.fixture
def sample(store, config):
    now = datetime.now(UTC)
    today = active_day(now, config)
    other_config = replace(config, guild_id=101)
    store.configure(other_config, actor_id=1)
    rows = [
        Submission(
            100,
            USER,
            "<script>alert('name')</script>",
            Game.TIMEGUESSR,
            "test",
            today,
            45000,
            True,
            1,
            200,
            now,
        ),
        Submission(100, 2, "Second", Game.TIMEGUESSR, "test", today, 30000, True, 2, 200, now),
        Submission(
            100,
            USER,
            "<script>alert('name')</script>",
            Game.METAZOOA,
            "test",
            today,
            None,
            False,
            3,
            200,
            now,
        ),
        Submission(100, 3, "Zoo player", Game.METAZOOA, "test", today, 3, True, 4, 200, now),
        Submission(
            101, 9, "Other server only", Game.SIZE_IT_UP, "test", today, 500, True, 5, 200, now
        ),
    ]
    for row in rows:
        assert store.insert(row)
    return rows


@pytest.fixture
async def client(settings):
    async with TestClient(TestServer(create_app(settings.database, settings.project))) as instance:
        yield instance


async def test_dashboard_assets_and_security_headers(client):
    for path, content_type in [
        ("/", "text/html"),
        ("/assets/dashboard.js", "text/javascript"),
        ("/assets/dashboard.css", "text/css"),
    ]:
        response = await client.get(path)
        assert response.status == 200
        assert response.content_type == content_type
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    source = await (await client.get("/assets/dashboard.js")).text()
    assert "innerHTML" not in source
    assert "textContent" in source
    assert (await client.get("/assets/.env")).status == 404
    assert (await client.get("/.env")).status == 404
    assert (await client.post("/api/data")).status == 405


async def test_live_board_matches_bot_and_preserves_snowflakes(client, store, config, sample):
    response = await client.get("/api/data?server=100")
    assert response.status == 200
    data = await response.json()
    selected = data["selected"]
    assert data["bot"]["gateway"] == "not_measured"
    assert data["bot"]["scheduler_state"] == "not_measured"
    assert set(data["backup"]) == {"local_at", "offsite_at"}
    assert data["bot"]["state"] == "running"
    assert selected["server_id"] == "100"
    assert selected["today_counts"] == {
        "players": 3,
        "submissions": 4,
        "games": {"timeguessr": 2, "sizeitup": 0, "metazooa": 2},
    }
    assert selected["board_state"] == "provisional"
    expected = make_snapshot(store.results(100, sample[0].day), sample[0].day, config)
    expected["rows"] = [{**row, "user_id": str(row["user_id"])} for row in expected["rows"]]
    assert {
        **selected["board"],
        "rows": [
            {k: v for k, v in row.items() if k != "streak"} for row in selected["board"]["rows"]
        ],
    } == expected
    assert all(row["streak"]["current"] == 1 for row in selected["board"]["rows"])
    assert {row["user_id"] for row in selected["recent"]} == {str(USER), "2", "3"}
    assert "Other server only" not in json.dumps(selected)
    assert "DISCORD_BOT_TOKEN" not in json.dumps(data)
    assert selected["config"]["input_channel_id"] == "200"


@pytest.mark.parametrize("game", list(Game))
async def test_game_views_match_existing_projection(client, store, config, sample, game):
    response = await client.get(f"/api/data?server=100&game={game}")
    selected = (await response.json())["selected"]
    expected = game_snapshot(
        make_snapshot(store.results(100, sample[0].day), sample[0].day, config), game
    )
    expected["rows"] = [{**row, "user_id": str(row["user_id"])} for row in expected["rows"]]
    assert {
        **selected["board"],
        "rows": [
            {k: v for k, v in row.items() if k != "streak"} for row in selected["board"]["rows"]
        ],
    } == expected


async def test_frozen_board_and_posting_state_are_preserved(client, store, config, sample):
    day = sample[0].day
    snapshot = make_snapshot(store.results(100, day), day, config)
    store.freeze(100, day, 300, snapshot)
    store.mark_posted(100, day, USER)
    # Later live rows must not affect an already frozen board.
    store.insert(replace(sample[0], user_id=7, display_name="After freeze", message_id=77))
    response = await client.get(f"/api/data?server=100&day={day}&game=timeguessr")
    selected = (await response.json())["selected"]
    assert selected["board_state"] == "final"
    assert selected["posting"]["posted"] is True
    assert selected["posting"]["message_id"] == str(USER)
    assert selected["history"][0]["posted"] is True
    assert "After freeze" not in json.dumps(selected["board"])
    assert json.loads(store.board(100, day)["snapshot"]) == snapshot


async def test_period_board_uses_only_saved_days_and_selected_game(client, store, config, sample):
    day = sample[0].day
    snapshot = make_snapshot(store.results(100, day), day, config)
    store.freeze(100, day, 300, snapshot)
    store.insert(replace(sample[0], user_id=7, display_name="Late", message_id=77))
    response = await client.get(f"/api/data?server=100&day={day}&period=month&game=timeguessr")
    assert response.status == 200
    selected = (await response.json())["selected"]
    board = selected["period_board"]
    assert board["start"] == day.replace(day=1).isoformat()
    assert board["finalized_days"] == 1
    assert "Late" not in json.dumps(board)
    assert {row["name"]: row["points"] for row in board["rows"]} == {
        row["name"]: row["total"] for row in game_snapshot(snapshot, Game.TIMEGUESSR)["rows"]
    }
    assert all(isinstance(row["user_id"], str) for row in board["rows"])


async def test_server_switching_isolated(client, sample):
    selected = (await (await client.get("/api/data?server=101")).json())["selected"]
    assert selected["today_counts"]["submissions"] == 1
    assert [row["name"] for row in selected["board"]["rows"]] == ["Other server only"]
    assert [row["name"] for row in selected["recent"]] == ["Other server only"]


@pytest.mark.parametrize(
    "query",
    [
        "game=unknown",
        "period=year",
        "server=invalid",
        "server=-1",
        "server=9223372036854775808",
        "day=not-a-date",
        "day=9999-12-31",
        "day=0001-01-01",
        "server=100%20OR%201=1",
    ],
)
async def test_invalid_filters_return_400(client, config, query):
    response = await client.get(f"/api/data?{query}")
    assert response.status == 400
    assert "Traceback" not in await response.text()


async def test_unknown_server_returns_404(client, config):
    assert (await client.get("/api/data?server=999")).status == 404


async def test_empty_database_has_clear_state(client):
    data = await (await client.get("/api/data")).json()
    assert data["servers"] == []
    assert data["selected"] is None


async def test_missing_database_not_created_and_error_hides_path(tmp_path):
    missing = tmp_path / "private.sqlite3"
    async with TestClient(TestServer(create_app(missing))) as client:
        response = await client.get("/api/data")
        assert response.status == 503
        assert str(tmp_path) not in await response.text()
        assert not missing.exists()
        assert (await client.get("/")).status == 200


def test_reader_is_read_only_and_observes_live_wal(settings, sample, store):
    before = list(store.db.iterdump())
    first = read_data(settings, "100", "today", "all")
    assert list(store.db.iterdump()) == before
    with read_database(settings.database) as reader:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.execute("DELETE FROM submissions")
    store.insert(replace(sample[0], user_id=7, display_name="New player", message_id=77))
    second = read_data(settings, "100", "today", "all")
    assert (
        second["selected"]["today_counts"]["submissions"]
        == first["selected"]["today_counts"]["submissions"] + 1
    )


def test_voided_results_hidden(settings, sample, store):
    store.void(100, USER, Game.TIMEGUESSR, sample[0].day, actor_id=1)
    selected = read_data(settings, "100", "today", "timeguessr")["selected"]
    assert str(USER) not in {row["user_id"] for row in selected["board"]["rows"]}
    assert all(
        row["game"] != "timeguessr" or row["user_id"] != str(USER) for row in selected["recent"]
    )


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "100.118.9.35"])
def test_private_listener_addresses(host):
    assert private_host(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.88", "8.8.8.8", "example.com"])
def test_public_and_lan_bindings_rejected(host):
    with pytest.raises(argparse.ArgumentTypeError):
        private_host(host)


def test_real_process_probe_does_not_mistake_dashboard_for_bot(tmp_path):
    result = dashboard.process_status(tmp_path)
    assert result["state"] == "not_detected"
    assert result["gateway"] == "not_measured"


async def test_corrupt_snapshot_is_database_error_not_bad_filter(client, store, config, sample):
    day = sample[0].day
    store.freeze(100, day, 300, make_snapshot(store.results(100, day), day, config))
    with store.db:
        store.db.execute("UPDATE boards SET snapshot='broken-json' WHERE guild_id=100")
    response = await client.get("/api/data?server=100")
    assert response.status == 503
    assert "broken-json" not in await response.text()


async def test_uninitialized_database_returns_unavailable(tmp_path):
    path = tmp_path / "uninitialized.sqlite3"
    sqlite3.connect(path).close()
    async with TestClient(TestServer(create_app(path))) as client:
        response = await client.get("/api/data")
        assert response.status == 503


async def test_old_day_pending_and_empty_game(client, config):
    selected = (await (await client.get("/api/data?day=yesterday&game=sizeitup")).json())[
        "selected"
    ]
    assert selected["board_state"] == "closed_pending"
    assert selected["posting"] is None
    assert selected["board"]["rows"] == []


def test_placement_view_retains_full_daily_participant_value(settings, store, config, sample):
    # Use a distinct server with placement configured before its first submission.
    placement = replace(config, guild_id=102, scoring="placement")
    store.configure(placement, actor_id=1)
    for index, row in enumerate(sample[:4], start=100):
        store.insert(replace(row, guild_id=102, message_id=index))
    selected = read_data(settings, "102", "today", "timeguessr")["selected"]
    assert selected["board"]["rows"][0]["total"] == 3


def test_reader_snapshot_does_not_block_writer(settings, sample, store):
    with read_database(settings.database) as reader:
        before = reader.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        store.insert(replace(sample[0], user_id=12, message_id=120))
        # A single dashboard read sees one consistent transaction while the writer commits.
        assert reader.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == before
    with read_database(settings.database) as reader:
        assert reader.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == before + 1


async def test_api_read_remains_available_after_bad_request(client, config):
    assert (await client.get("/api/data?game=bad")).status == 400
    response = await client.get("/api/data?game=all")
    assert response.status == 200
    assert (await response.json())["selected"]["server_id"] == "100"


async def test_incompatible_schema_is_unavailable_not_missing_server(tmp_path):
    path = tmp_path / "old-schema.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE servers (guild_id INTEGER)")
        db.execute("INSERT INTO servers VALUES (100)")
    async with TestClient(TestServer(create_app(path))) as client:
        assert (await client.get("/api/data")).status == 503
