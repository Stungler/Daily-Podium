from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from daily_games.calendar import active_day, boundary, closes_at, post_due_at, puzzle_day
from daily_games.models import Game, ParsedResult
from daily_games.presentation import make_snapshot
from daily_games.scoring import leaderboard
from daily_games.service import accept_result
from daily_games.storage import Store

DAY = date(2026, 11, 13)


def submit(
    store,
    config,
    timestamp,
    *,
    user=1,
    game=Game.TIMEGUESSR,
    score=10000,
    puzzle="1262",
    solved=True,
    message=1000,
):
    return accept_result(
        store,
        config,
        ParsedResult(game, puzzle, score, solved),
        user_id=user,
        display_name=f"Player {user}",
        message_id=message,
        channel_id=config.input_channel_id,
        submitted_at=timestamp,
    )


def test_confirmed_number_date_anchors():
    assert puzzle_day(ParsedResult(Game.TIMEGUESSR, "1262", 0)) == DAY
    assert puzzle_day(ParsedResult(Game.METAZOOA, "1201", 2)) == DAY
    assert puzzle_day(ParsedResult(Game.TIMEGUESSR, "1263", 0)) == DAY + timedelta(days=1)


def test_server_isolation_and_independent_points(store, config, message_time):
    other = replace(config, guild_id=101, input_channel_id=201)
    store.configure(other, actor_id=1)
    assert submit(store, config, message_time).status == "accepted"
    assert submit(store, other, message_time).status == "accepted"
    submit(store, config, message_time, user=2, score=5000)
    assert leaderboard(store.results(100, DAY))[0].total == 2
    assert leaderboard(store.results(101, DAY))[0].total == 1


def test_old_puzzle_cannot_count_next_day_even_if_never_submitted(store, config, message_time):
    outcome = submit(store, config, message_time + timedelta(days=1))
    assert outcome.status == "old"
    assert "TimeGuessr may still show the previous daily" in outcome.detail
    assert store.results(100, DAY + timedelta(days=1)) == []


def test_duplicate_does_not_replace_score(store, config, message_time):
    assert submit(store, config, message_time).status == "accepted"
    assert submit(store, config, message_time, score=50000, message=1001).status == "duplicate"
    assert store.results(100, DAY)[0].score == 10000


def test_exact_cutoff_rejects_previous_puzzle(store, config):
    close = closes_at(DAY, config)
    assert submit(store, config, close - timedelta(microseconds=1)).status == "accepted"
    assert submit(store, config, close, user=2).status == "old"


def test_future_puzzle_cannot_enter_today(store, config, message_time):
    assert submit(store, config, message_time, puzzle="1263").status == "future"


def test_new_puzzle_accepts_after_cutoff(store, config):
    assert submit(store, config, closes_at(DAY, config), puzzle="1263").status == "accepted"


def test_restart_preserves_duplicate_protection(tmp_path, message_time):
    from daily_games.models import ServerConfig

    path = tmp_path / "persistent.sqlite3"
    cfg = ServerConfig(100, 200, 300, "Europe/Berlin", "00:00", "players_beaten", DAY)
    first = Store(path)
    first.configure(cfg, 1)
    submit(first, cfg, message_time)
    first.close()
    second = Store(path)
    assert submit(second, cfg, message_time, message=1002).status == "duplicate"
    assert second.config(100) == cfg
    second.close()


def test_closed_day_history_recovers_until_frozen(store, config, message_time):
    # Processing happens after a hypothetical restart; the timestamp remains pre-cutoff.
    assert submit(store, config, message_time).status == "accepted"
    snapshot = make_snapshot(store.results(100, DAY), DAY, config)
    store.freeze(100, DAY, 300, snapshot)
    assert submit(store, config, message_time, user=2).status == "closed"
    assert len(store.results(100, DAY)) == 1


def test_points_ties_failures_and_combined_ranking(store, config, message_time):
    for user, score in [(1, 20000), (2, 20000), (3, 10000)]:
        submit(store, config, message_time, user=user, score=score)
    submit(store, config, message_time, user=1, game=Game.METAZOOA, puzzle="1201", score=2)
    submit(
        store,
        config,
        message_time,
        user=2,
        game=Game.METAZOOA,
        puzzle="1201",
        score=None,
        solved=False,
    )
    rows = leaderboard(store.results(100, DAY))
    assert [(row.user_id, row.total) for row in rows] == [(1, 4), (2, 2), (3, 1)]
    assert rows[0].points[Game.TIMEGUESSR] == rows[1].points[Game.TIMEGUESSR] == 2
    assert rows[1].points[Game.METAZOOA] == 0


def test_combined_ties_share_rank(store, config, message_time):
    for user, score in [(1, 20000), (2, 20000), (3, 10000)]:
        submit(store, config, message_time, user=user, score=score)
    assert [row.rank for row in leaderboard(store.results(100, DAY))] == [1, 1, 3]


def test_placement_mode_equal_winning_value(store, config, message_time):
    for user, score in [(1, 20000), (2, 10000), (3, 5000)]:
        submit(store, config, message_time, user=user, score=score)
    submit(store, config, message_time, user=3, game=Game.METAZOOA, puzzle="1201", score=5)
    rows = {row.user_id: row for row in leaderboard(store.results(100, DAY), "placement")}
    assert rows[1].points[Game.TIMEGUESSR] == rows[3].points[Game.METAZOOA] == 3


def test_failure_is_not_average_zero(store, config, message_time):
    submit(store, config, message_time, game=Game.METAZOOA, puzzle="1201", score=None, solved=False)
    row = leaderboard(store.results(100, DAY))[0]
    assert row.total == 0
    assert row.results[Game.METAZOOA].score is None


def test_admin_void_requires_a_new_message(store, config, message_time):
    submit(store, config, message_time)
    assert store.void(100, 1, Game.TIMEGUESSR, DAY, actor_id=9)
    assert submit(store, config, message_time).status == "duplicate"
    assert submit(store, config, message_time, message=1001, score=30000).status == "accepted"
    assert store.results(100, DAY)[0].score == 30000
    assert store.db.execute("SELECT COUNT(*) FROM audit WHERE action='void'").fetchone()[0] == 1


def test_clock_settings_lock_after_first_result(store, config, message_time):
    submit(store, config, message_time)
    with pytest.raises(ValueError, match="locked"):
        store.configure(replace(config, timezone="UTC"), 1)
    store.configure(replace(config, output_channel_id=999), 1)
    assert store.config(100).output_channel_id == 999


def test_day_mapping_override_is_per_server_and_enforces_exact_puzzle(store, config, message_time):
    store.set_override(100, Game.TIMEGUESSR, "9000", DAY, 1)
    assert submit(store, config, message_time).status == "wrong_puzzle"
    assert submit(store, config, message_time, puzzle="9000").status == "accepted"
    with pytest.raises(ValueError, match="recorded"):
        store.set_override(100, Game.TIMEGUESSR, "9000", DAY + timedelta(days=1), 1)


@pytest.mark.parametrize("day,hours", [(date(2026, 3, 28), 23), (date(2026, 10, 24), 25)])
def test_daylight_saving_wall_clock_boundaries(config, day, hours):
    start = boundary(day + timedelta(days=1), config.timezone, config.cutoff)
    end = closes_at(day + timedelta(days=1), config)
    assert (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() == hours * 3600


def test_custom_cutoff_and_five_minute_post_delay(config):
    cfg = replace(config, cutoff="03:00")
    now = datetime(2026, 11, 14, 0, 20, tzinfo=UTC)
    assert active_day(now, cfg) == DAY
    assert post_due_at(DAY, cfg) == datetime(2026, 11, 14, 2, 5, tzinfo=UTC)


def test_snapshot_and_post_markers_persist(store, config, message_time):
    submit(store, config, message_time)
    original = make_snapshot(store.results(100, DAY), DAY, config)
    store.freeze(100, DAY, 300, original)
    store.freeze(100, DAY, 300, {"wrong": "replacement"})
    import json

    assert json.loads(store.board(100, DAY)["snapshot"]) == original
    store.mark_attempt(100, DAY, message_time)
    store.mark_posted(100, DAY, 1234)
    assert store.board(100, DAY)["message_id"] == 1234
