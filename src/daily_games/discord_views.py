"""Compact Discord views and summaries derived from immutable daily snapshots."""

import unicodedata
from datetime import date

import discord

from .i18n import phrase, scoring_label
from .models import DEFAULT_GAMES, GAME_TITLES, Game
from .periods import period_snapshot
from .presentation import game_snapshot, raw_score_label, score_label
from .rotation import Rotation
from .streaks import completed_day_anchor, streak_summaries

MAUVE = 0xCBA6F7
GREEN = 0xA6E3A1


def player_name(value: str) -> str:
    clean = " ".join(value.split())[:40]
    return discord.utils.escape_markdown(discord.utils.escape_mentions(clean))


def name_column(value: str, width: int = 18) -> str:
    """Keep names inside inline code and pad common wide characters correctly."""
    value = " ".join(value.split()).replace("`", "'")
    result, used = "", 0
    for char in value:
        if unicodedata.category(char).startswith("C"):
            continue
        size = (
            0
            if unicodedata.combining(char)
            else (2 if unicodedata.east_asian_width(char) in "WF" else 1)
        )
        if used + size > width:
            break
        result += char
        used += size
    return discord.utils.escape_mentions(result) + " " * (width - used)


def compact_boards(snapshot, *, final, game=None, limit=None, scheduled=False, language="en"):
    view = game_snapshot(snapshot, game) if game else snapshot
    title = GAME_TITLES[game] if game else "Daily Games"
    state = (
        phrase(language, "Final standings", "Endstand")
        if final
        else phrase(language, "Current standings · provisional", "Aktueller Stand · vorläufig")
    )
    rows = view["rows"][:limit] if limit else view["rows"]
    lines = []
    player_heading = phrase(language, "Player", "Spieler")
    points_heading = phrase(language, "Pts", "Pkt")
    header = f"{'#':>3} {player_heading:18} {points_heading:>4}"
    if game:
        score_heading = (
            phrase(language, "Guesses", "Versuche")
            if game == Game.METAZOOA
            else phrase(language, "Score", "Punkte")
        )
        if game == Game.WIKI:
            score_heading = phrase(language, "Steps · Time", "Schritte · Zeit")
        width = 26 if game == Game.WIKI else 10
        header += f" {score_heading:>{width}}"
    for row in rows:
        line = f"{row['rank']:>3} {name_column(row['name'])} {row['total']:>4}"
        if game:
            result = row["games"][game.value]
            raw = (
                (
                    raw_score_label(game, result, language)
                    if game == Game.WIKI
                    else f"{result['score']:,}"
                )
                if result["solved"]
                else phrase(language, "Failed", "Fehlversuch")
            )
            line += f" {raw:>{width}}"
        lines.append(f"`{line}`")
    pages = [lines[index : index + 15] for index in range(0, len(lines), 15)] or [[]]
    embeds = []
    count = len(view["rows"])
    for index, page in enumerate(pages, start=1):
        description = (
            "\n".join([f"`{header}`", *page])
            if page
            else phrase(
                language,
                "No results for this competition day.",
                "Für diesen Spieltag gibt es noch keine Ergebnisse.",
            )
        )
        if limit and count > limit:
            description += phrase(
                language,
                f"\n*Top {limit} of {count} players · full standings in the browser.*",
                f"\n*Top {limit} von {count} Spielern · vollständige Rangliste im Browser.*",
            )
        if scheduled:
            # Threads may be unavailable; !lb still works without a public dashboard.
            description = description.replace(
                phrase(
                    language, "full standings in the browser", "vollständige Rangliste im Browser"
                ),
                phrase(language, "full standings: `!lb`", "vollständige Rangliste: `!lb`"),
            )
            result_count = sum(len(row["games"]) for row in view["rows"])
            description = (
                f"**{count} {phrase(language, 'players', 'Spieler')} · "
                f"{result_count} {phrase(language, 'results', 'Ergebnisse')}**\n" + description
            )
        embed = discord.Embed(
            title=f"{title} · {snapshot['day']} · {state}",
            description=description,
            color=GREEN if final else MAUVE,
        )
        entries = sum(len(row["games"]) for row in view["rows"])
        if game:
            entries = count
        mode = scoring_label(language, snapshot["scoring"])
        embed.set_footer(
            text=(
                f"{count} {phrase(language, 'players', 'Spieler')} · "
                f"{entries} {phrase(language, 'results', 'Ergebnisse')} · {mode} · "
                f"{snapshot['timezone']} {snapshot['cutoff']} · {index}/{len(pages)}"
            )
        )
        if scheduled:
            embed.set_footer(text=f"Daily Podium · {snapshot['day']} · Final")
        embeds.append(embed)
    return embeds


def daily_highlights(snapshot, language="en"):
    embed = discord.Embed(
        title=f"{phrase(language, 'Game highlights', 'Spiel-Highlights')} · {snapshot['day']}",
        color=MAUVE,
    )
    for game in map(Game, snapshot.get("lineup", DEFAULT_GAMES)):
        rows = game_snapshot(snapshot, game)["rows"]
        solved = [row for row in rows if row["games"][game.value]["solved"]]
        if solved:
            winners = [row for row in solved if row["rank"] == solved[0]["rank"]]
            names = ", ".join(player_name(row["name"]) for row in winners[:5])
            if len(winners) > 5:
                names += phrase(
                    language, f" +{len(winners) - 5} tied", f" +{len(winners) - 5} gleichauf"
                )
            score = score_label(game, winners[0]["games"][game.value], language)
            value = phrase(
                language,
                f"**{names}** · {score}\n{len(rows)} played · {len(solved)} solved",
                f"**{names}** · {score}\n{len(rows)} gespielt · {len(solved)} geschafft",
            )
        else:
            value = phrase(
                language,
                f"{len(rows)} played · no successful results",
                f"{len(rows)} gespielt · keine erfolgreichen Ergebnisse",
            )
        embed.add_field(name=GAME_TITLES[game], value=value, inline=False)
    return embed


def weekly_snapshot(snapshots, through: date, started: date, game: Game | None = None):
    return period_snapshot(snapshots, through, started, "week", game)


def weekly_embed(summary, language="en"):
    return period_embed(summary, language)


def period_embed(summary, language="en"):
    win_width = 4 if language == "de" else 3
    lines = [
        f"`{row['rank']:>3} {name_column(row['name'])} {row['points']:>4} "
        f"{row['days']:>2} {row['wins']:>{win_width}}`"
        for row in summary["rows"][:10]
    ]
    player_heading = phrase(language, "Player", "Spieler")
    points_heading = phrase(language, "Pts", "Pkt")
    win_heading = phrase(language, "Win", "Sieg")
    header = (
        f"`{'#':>3} {player_heading:18} {points_heading:>4} {'D':>2} {win_heading:>{win_width}}`"
    )
    description = (
        "\n".join([header, *lines])
        if lines
        else phrase(
            language,
            f"No player results in finalized days for this {summary['period']} yet.",
            "Für diesen Zeitraum gibt es noch keine abgeschlossenen Spielergebnisse.",
        )
    )
    if len(summary["rows"]) > 10:
        description += phrase(
            language,
            f"\n*Top 10 of {len(summary['rows'])} players.*",
            f"\n*Top 10 von {len(summary['rows'])} Spielern.*",
        )
    period_heading = (
        phrase(language, "This week", "Diese Woche")
        if summary["period"] == "week"
        else phrase(language, "This month", "Dieser Monat")
    )
    game_heading = (
        GAME_TITLES[Game(summary["game"])]
        if summary["game"] != "all"
        else phrase(language, "Combined", "Gesamt")
    )
    embed = discord.Embed(
        title=(f"{period_heading} · {game_heading} · {summary['start']} → {summary['through']}"),
        description=description,
        color=MAUVE,
    )
    embed.set_footer(
        text=(
            phrase(
                language,
                f"{summary['finalized_days']}/{summary['expected_days']} days finalized · "
                "saved daily points added · d = days played · wins include ties",
                f"{summary['finalized_days']}/{summary['expected_days']} Tage abgeschlossen · "
                "gespeicherte Tagespunkte addiert · D = Spieltage · Gleichstände zählen als Sieg",
            )
        )
    )
    return embed


def player_stats_snapshot(
    snapshots: list[dict],
    user_id: int,
    as_of: date | None = None,
    rotation: Rotation | None = None,
) -> dict:
    """Summarize one player's finalized results in one Discord server."""
    games = {
        game.value: {"played": 0, "solved": 0, "wins": 0, "points": 0, "score_sum": 0, "best": None}
        for game in Game
    }
    result = {
        "finalized_days": len(snapshots),
        "days_played": 0,
        "daily_wins": 0,
        "points": 0,
        "last_day": None,
        "games": games,
    }
    for snapshot in sorted(snapshots, key=lambda item: item["day"]):
        player = next((row for row in snapshot["rows"] if row["user_id"] == user_id), None)
        if player is None:
            continue
        result["days_played"] += 1
        result["daily_wins"] += int(player["rank"] == 1 and player["total"] > 0)
        result["points"] += player["total"]
        result["last_day"] = snapshot["day"]
        for game in Game:
            entry = player["games"].get(game.value)
            if entry is None:
                continue
            game_stats = games[game.value]
            game_stats["played"] += 1
            game_stats["points"] += entry["points"]
            if not entry["solved"]:
                continue
            score = entry["score"]
            game_stats["solved"] += 1
            game_stats["score_sum"] += score
            best = game_stats["best"]
            better_score = best is None or (score < best if game == Game.METAZOOA else score > best)
            if game == Game.WIKI:
                elapsed = entry["elapsed_seconds"]
                better_score = best is None or (score, elapsed) < (
                    best,
                    game_stats["best_elapsed_seconds"],
                )
                if better_score:
                    game_stats["best_elapsed_seconds"] = elapsed
            if better_score:
                game_stats["best"] = score
            projected = game_snapshot(snapshot, game)
            game_stats["wins"] += int(
                any(row["user_id"] == user_id and row["rank"] == 1 for row in projected["rows"])
            )
    anchor = as_of or (
        completed_day_anchor(date.fromisoformat(max(item["day"] for item in snapshots)))
        if snapshots
        else date.min
    )
    streaks = streak_summaries(snapshots, anchor, rotation).get(user_id, {})
    result["streak"] = streaks.get("all", {"current": 0, "best": 0})
    for game in Game:
        games[game.value]["streak"] = streaks.get(game.value, {"current": 0, "best": 0})
    return result


def player_stats_embed(stats: dict, display_name: str, language="en") -> discord.Embed:
    if stats["days_played"]:
        average = stats["points"] / stats["days_played"]
        description = phrase(
            language,
            f"**{stats['days_played']}/{stats['finalized_days']}** days played · "
            f"**{stats['daily_wins']}** daily wins\n"
            f"**{stats['points']}** total points · **{average:.1f}** points per played day\n"
            f"🔥 **{stats['streak']['current']}** current streak · "
            f"**{stats['streak']['best']}** best\n"
            f"Last played: {stats['last_day']}",
            f"**{stats['days_played']}/{stats['finalized_days']}** Tage gespielt · "
            f"**{stats['daily_wins']}** Tagessiege\n"
            f"**{stats['points']}** Punkte gesamt · **{average:.1f}** Punkte je Spieltag\n"
            f"🔥 **{stats['streak']['current']}** aktuelle Serie · "
            f"**{stats['streak']['best']}** Rekord\n"
            f"Zuletzt gespielt: {stats['last_day']}",
        )
    else:
        description = phrase(
            language,
            "No results in finalized days for this player yet.",
            "Für diesen Spieler gibt es noch keine Ergebnisse aus abgeschlossenen Tagen.",
        )
    embed = discord.Embed(
        title=(
            f"{phrase(language, 'Player stats', 'Spielerstatistik')} · {player_name(display_name)}"
        ),
        description=description,
        color=MAUVE,
    )
    for game in Game:
        data = stats["games"][game.value]
        if game not in DEFAULT_GAMES and not data["played"]:
            continue
        if not data["played"]:
            value = phrase(
                language, "No finalized results yet.", "Noch keine abgeschlossenen Ergebnisse."
            )
        else:
            if data["solved"]:
                unit = phrase(language, " guesses", " Versuche") if game == Game.METAZOOA else ""
                average = f"{data['score_sum'] / data['solved']:,.1f}{unit}"
                best = f"{data['best']:,}{unit}"
                if game == Game.WIKI:
                    unit = phrase(language, " steps", " Schritte")
                    average = f"{data['score_sum'] / data['solved']:,.1f}{unit}"
                    best = raw_score_label(
                        game,
                        {"score": data["best"], "elapsed_seconds": data["best_elapsed_seconds"]},
                        language,
                    )
            else:
                average = best = "—"
            value = phrase(
                language,
                f"**{data['played']}** played · **{data['solved']}** solved · "
                f"**{data['wins']}** wins\n"
                f"🔥 {data['streak']['current']} current · {data['streak']['best']} best\n"
                f"{data['points']} pts · avg {average} · best {best}",
                f"**{data['played']}** gespielt · **{data['solved']}** geschafft · "
                f"**{data['wins']}** Siege\n"
                f"🔥 {data['streak']['current']} aktuell · {data['streak']['best']} Rekord\n"
                f"{data['points']} Pkt. · Ø {average} · Bestwert {best}",
            )
        embed.add_field(name=GAME_TITLES[game], value=value, inline=False)
    embed.set_footer(
        text=phrase(
            language,
            "This server · finalized days only · ties count as wins · score averages use solves",
            "Dieser Server · nur abgeschlossene Tage · Gleichstände zählen als Sieg · "
            "Ø nur aus Erfolgen",
        )
    )
    return embed
