"""Plain-data board snapshots: identical content for prefix/slash/scheduled views."""

from datetime import date

from .i18n import phrase
from .models import DEFAULT_GAMES, Game, ServerConfig, Submission
from .rotation import Lineup
from .scoring import leaderboard


def make_snapshot(
    results: list[Submission], day: date, config: ServerConfig, lineup: Lineup = DEFAULT_GAMES
) -> dict:
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
            if game == Game.WIKI:
                scores[game.value]["elapsed_seconds"] = result.elapsed_seconds
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
        "lineup": list(lineup),
        "rows": rows,
    }


def game_snapshot(snapshot: dict, game: Game) -> dict:
    """Project a live or frozen board without changing its stored points or content."""
    rows = [
        {**row, "total": row["games"][game.value]["points"]}
        for row in snapshot["rows"]
        if game.value in row["games"]
    ]
    rows.sort(key=lambda row: (-row["total"], row["name"].casefold(), row["user_id"]))
    rank = 0
    for position, row in enumerate(rows, start=1):
        if position == 1 or row["total"] != rows[position - 2]["total"]:
            rank = position
        row["rank"] = rank
    return {**snapshot, "rows": rows}


def raw_score_label(game: Game, result: dict, language: str = "en") -> str:
    score = result["score"]
    if game == Game.WIKI:
        steps = phrase(language, "steps", "Schritte")
        elapsed = result.get("elapsed_seconds")
        if elapsed is None:
            return f"{score} {steps}"
        minutes, seconds = divmod(elapsed, 60)
        return f"{score} {steps} · {minutes}:{seconds:02d}"
    if game == Game.METAZOOA:
        return phrase(language, f"{score} guesses", f"{score} Versuche")
    return f"{score:,}"


def score_label(game: Game, result: dict | None, language: str = "en") -> str:
    if result is None:
        return "—"
    if not result["solved"]:
        return phrase(language, "❌ Failed (+0)", "❌ Nicht geschafft (+0)")
    return f"{raw_score_label(game, result, language)} (+{result['points']})"
