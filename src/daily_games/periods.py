"""Aggregate saved daily points into week-to-date and month-to-date boards."""

from datetime import date, timedelta

from .models import Game
from .presentation import game_snapshot


def period_start(through: date, period: str) -> date:
    if period == "week":
        return through - timedelta(days=through.weekday())
    if period == "month":
        return through.replace(day=1)
    raise ValueError("Choose week or month.")


def period_snapshot(
    snapshots: list[dict], through: date, started: date, period: str, game: Game | None = None
) -> dict:
    start = period_start(through, period)
    first = max(start, started)
    days = sorted(
        (
            snapshot
            for snapshot in snapshots
            if first.isoformat() <= snapshot["day"] <= through.isoformat()
        ),
        key=lambda snapshot: snapshot["day"],
    )
    players: dict[int, dict] = {}
    for snapshot in days:
        view = game_snapshot(snapshot, game) if game else snapshot
        for row in view["rows"]:
            player = players.setdefault(
                row["user_id"],
                {"user_id": row["user_id"], "points": 0, "days": 0, "wins": 0},
            )
            player["name"] = row["name"]
            player["points"] += row["total"]
            player["days"] += 1
            player["wins"] += int(row["rank"] == 1 and row["total"] > 0)
    ordered = sorted(
        players.values(), key=lambda row: (-row["points"], row["name"].casefold(), row["user_id"])
    )
    rank = 0
    for index, row in enumerate(ordered, start=1):
        if index == 1 or row["points"] != ordered[index - 2]["points"]:
            rank = index
        row["rank"] = rank
    return {
        "period": period,
        "game": game.value if game else "all",
        "start": start.isoformat(),
        "through": through.isoformat(),
        "finalized_days": len(days),
        "expected_days": max(0, (through - first).days + 1),
        "rows": ordered,
    }
