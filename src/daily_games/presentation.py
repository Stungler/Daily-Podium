"""Plain-data board snapshots: identical content for prefix/slash/scheduled views."""

from datetime import date

from .models import Game, ServerConfig, Submission
from .scoring import leaderboard


def make_snapshot(results: list[Submission], day: date, config: ServerConfig) -> dict:
    rows = []
    for player in leaderboard(results, config.scoring):
        scores = {}
        for game, result in player.results.items():
            scores[game.value] = {
                "score": result.score,
                "solved": result.solved,
                "puzzle": result.puzzle,
                "points": player.points[game],
            }
        rows.append(
            {
                "rank": player.rank,
                "user_id": player.user_id,
                "name": player.display_name,
                "total": player.total,
                "games": scores,
            }
        )
    return {
        "day": day.isoformat(),
        "timezone": config.timezone,
        "cutoff": config.cutoff,
        "scoring": config.scoring,
        "rows": rows,
    }


def score_label(game: Game, result: dict | None) -> str:
    if result is None:
        return "—"
    if not result["solved"]:
        return "❌ Failed (+0)"
    score = result["score"]
    label = f"{score} guesses" if game == Game.METAZOOA else f"{score:,}"
    return f"{label} (+{result['points']})"
