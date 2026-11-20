from collections import defaultdict
from dataclasses import dataclass

from .models import Game, Submission


@dataclass(frozen=True)
class Standing:
    rank: int
    user_id: int
    display_name: str
    total: int
    results: dict[Game, Submission]
    points: dict[Game, int]


def better(left: Submission, right: Submission) -> bool:
    if left.solved != right.solved:
        return left.solved
    if not left.solved:
        return False
    assert left.score is not None and right.score is not None
    if left.game == Game.WIKI:
        assert left.elapsed_seconds is not None and right.elapsed_seconds is not None
        return (left.score, left.elapsed_seconds) < (right.score, right.elapsed_seconds)
    return left.score < right.score if left.game == Game.METAZOOA else left.score > right.score


def leaderboard(results: list[Submission], mode: str = "players_beaten") -> list[Standing]:
    if mode not in {"players_beaten", "placement"}:
        raise ValueError("Unknown scoring mode.")
    by_game: dict[Game, list[Submission]] = defaultdict(list)
    by_player: dict[int, dict[Game, Submission]] = defaultdict(dict)
    names = {}
    points: dict[int, dict[Game, int]] = defaultdict(dict)
    for result in results:
        by_game[result.game].append(result)
        by_player[result.user_id][result.game] = result
        names[result.user_id] = result.display_name
    for game, entries in by_game.items():
        for entry in entries:
            if not entry.solved:
                value = 0
            elif mode == "players_beaten":
                value = 1 + sum(better(entry, other) for other in entries)
            else:
                value = len(by_player) - sum(better(other, entry) for other in entries)
            points[entry.user_id][game] = value
    totals = {uid: sum(points[uid].values()) for uid in by_player}
    ordered = sorted(by_player, key=lambda uid: (-totals[uid], names[uid].casefold(), uid))
    standings = []
    rank = 0
    for position, uid in enumerate(ordered, start=1):
        if position == 1 or totals[uid] != totals[ordered[position - 2]]:
            rank = position
        standings.append(Standing(rank, uid, names[uid], totals[uid], by_player[uid], points[uid]))
    return standings
