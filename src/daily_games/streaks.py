"""Participation streaks from accepted competition dates, independent of scoring."""

import json
import sqlite3
from datetime import date, timedelta

from .models import Game

STREAK_DIGITS = ("0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣")


def streak_reactions(length: int) -> list[str]:
    if length < 1:
        return []
    if length < 10:
        marker = STREAK_DIGITS[length]
    elif length < 25:
        marker = "🔟"
    elif length < 50:
        marker = "💎"
    elif length < 100:
        marker = "🏆"
    else:
        marker = "💯"
    return ["🔥", marker]


def streak_lengths(days: set[date], as_of: date) -> dict[str, int]:
    """Keep yesterday's run current while today's competition is still open."""
    relevant = sorted(day for day in days if day <= as_of)
    best = run = 0
    previous = None
    for day in relevant:
        if day == previous:
            continue
        run = run + 1 if previous and (day - previous).days == 1 else 1
        best = max(best, run)
        previous = day
    current = run if previous and (as_of - previous).days <= 1 else 0
    return {"current": current, "best": best}


def streak_summaries(snapshots: list[dict], as_of: date) -> dict[int, dict[str, dict[str, int]]]:
    """Summarize combined and per-game days from immutable or provisional boards."""
    days: dict[int, dict[str, set[date]]] = {}
    for snapshot in snapshots:
        day = date.fromisoformat(snapshot["day"])
        if day > as_of:
            continue
        for row in snapshot["rows"]:
            player = days.setdefault(row["user_id"], {"all": set()})
            player["all"].add(day)
            for game in Game:
                if game.value in row["games"]:
                    player.setdefault(game.value, set()).add(day)
    return {
        user_id: {
            key: streak_lengths(player.get(key, set()), as_of)
            for key in ("all", *(game.value for game in Game))
        }
        for user_id, player in days.items()
    }


def completed_day_anchor(day: date) -> date:
    """The day after a final board, capped at the date type's upper bound."""
    return day + timedelta(days=1) if day < date.max else day


def server_streak_snapshots(db: sqlite3.Connection, guild_id: int, through: date) -> list[dict]:
    """Read all frozen boards for one server without changing stored snapshots."""
    snapshots = []
    for row in db.execute(
        "SELECT snapshot FROM boards WHERE guild_id=? AND day<=? ORDER BY day",
        (guild_id, through.isoformat()),
    ):
        try:
            snapshots.append(json.loads(row["snapshot"]))
        except json.JSONDecodeError as exc:
            raise sqlite3.DatabaseError("Invalid frozen board.") from exc
    return snapshots


def row_streak(summaries: dict, user_id: int, game: Game | None) -> dict[str, int]:
    key = game.value if game else "all"
    return summaries.get(user_id, {}).get(key, {"current": 0, "best": 0})
