from datetime import date, timedelta

from daily_games.streaks import streak_lengths, streak_reactions, streak_summaries


def test_streaks_count_once_per_day_and_survive_open_day():
    first = date(2026, 11, 13)
    days = {first, first + timedelta(days=1), first + timedelta(days=3)}
    assert streak_lengths(days, first + timedelta(days=1)) == {"current": 2, "best": 2}
    assert streak_lengths(days, first + timedelta(days=2)) == {"current": 2, "best": 2}
    assert streak_lengths(days, first + timedelta(days=3)) == {"current": 1, "best": 2}
    assert streak_lengths(days, first + timedelta(days=5)) == {"current": 0, "best": 2}


def test_game_streaks_include_failures_but_not_missing_games():
    first = date(2026, 11, 13)
    snapshots = [
        {"day": first.isoformat(), "rows": [
            {"user_id": 7, "games": {"timeguessr": {"solved": True}}}
        ]},
        {"day": (first + timedelta(days=1)).isoformat(), "rows": [
            {"user_id": 7, "games": {"metazooa": {"solved": False}}}
        ]},
    ]
    summary = streak_summaries(snapshots, first + timedelta(days=2))[7]
    assert summary["all"] == {"current": 2, "best": 2}
    assert summary["timeguessr"] == {"current": 0, "best": 1}
    assert summary["metazooa"] == {"current": 1, "best": 1}
    assert summary["sizeitup"] == {"current": 0, "best": 0}


def test_streak_reaction_milestones():
    assert streak_reactions(0) == []
    assert streak_reactions(1) == ["🔥", "1️⃣"]
    assert streak_reactions(9) == ["🔥", "9️⃣"]
    assert streak_reactions(10) == streak_reactions(24) == ["🔥", "🔟"]
    assert streak_reactions(25) == streak_reactions(49) == ["🔥", "💎"]
    assert streak_reactions(50) == streak_reactions(99) == ["🔥", "🏆"]
    assert streak_reactions(100) == ["🔥", "💯"]
