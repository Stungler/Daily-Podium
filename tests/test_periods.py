from datetime import date

from daily_games.models import Game
from daily_games.periods import period_snapshot


def test_periods_sum_saved_game_points_and_count_game_participation():
    snapshots = [
        {
            "day": "2026-10-31",
            "rows": [{"user_id": 99, "name": "Old", "rank": 1, "total": 50, "games": {}}],
        },
        {
            "day": "2026-11-01",
            "rows": [
                {
                    "user_id": 1,
                    "name": "A",
                    "rank": 1,
                    "total": 5,
                    "games": {"sizeitup": {"points": 3}, "metazooa": {"points": 2}},
                },
                {
                    "user_id": 2,
                    "name": "B",
                    "rank": 2,
                    "total": 2,
                    "games": {"sizeitup": {"points": 2}},
                },
            ],
        },
        {
            "day": "2026-11-02",
            "rows": [
                {
                    "user_id": 1,
                    "name": "A",
                    "rank": 2,
                    "total": 0,
                    "games": {"sizeitup": {"points": 0}},
                },
                {
                    "user_id": 2,
                    "name": "B",
                    "rank": 1,
                    "total": 3,
                    "games": {"sizeitup": {"points": 3}},
                },
                {
                    "user_id": 3,
                    "name": "C",
                    "rank": 2,
                    "total": 1,
                    "games": {"metazooa": {"points": 1}},
                },
            ],
        },
    ]
    through = date(2026, 11, 2)
    monthly = period_snapshot(snapshots, through, date(2026, 11, 1), "month")
    assert monthly["start"] == "2026-11-01"
    assert monthly["finalized_days"] == monthly["expected_days"] == 2
    assert [(r["name"], r["points"], r["days"], r["wins"]) for r in monthly["rows"]] == [
        ("A", 5, 2, 1),
        ("B", 5, 2, 1),
        ("C", 1, 1, 0),
    ]
    assert [r["rank"] for r in monthly["rows"]] == [1, 1, 3]
    size = period_snapshot(snapshots, through, date(2026, 11, 1), "month", Game.SIZE_IT_UP)
    assert [(r["name"], r["points"], r["days"], r["wins"]) for r in size["rows"]] == [
        ("B", 5, 2, 1),
        ("A", 3, 2, 1),
    ]
    week = period_snapshot(snapshots, through, date(2026, 11, 1), "week", Game.SIZE_IT_UP)
    assert week["start"] == "2026-11-02"
    assert week["expected_days"] == week["finalized_days"] == 1
    assert [r["name"] for r in week["rows"]] == ["B", "A"]
