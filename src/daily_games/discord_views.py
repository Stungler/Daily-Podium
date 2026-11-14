"""Compact Discord views and summaries derived from immutable daily snapshots."""

import unicodedata
from datetime import date, timedelta

import discord

from .models import GAME_TITLES, Game
from .presentation import game_snapshot, score_label

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


def compact_boards(snapshot, *, final, game=None, limit=None, scheduled=False):
    view = game_snapshot(snapshot, game) if game else snapshot
    title = GAME_TITLES[game] if game else "Daily Games"
    state = "Final standings" if final else "Current standings · provisional"
    rows = view["rows"][:limit] if limit else view["rows"]
    lines = []
    header = f"{'#':>3} {'Player':18} {'Pts':>4}"
    if game:
        header += f" {'Guesses' if game == Game.METAZOOA else 'Score':>10}"
    for row in rows:
        line = f"{row['rank']:>3} {name_column(row['name'])} {row['total']:>4}"
        if game:
            result = row["games"][game.value]
            raw = f"{result['score']:,}" if result["solved"] else "Failed"
            line += f" {raw:>10}"
        lines.append(f"`{line}`")
    pages = [lines[index : index + 15] for index in range(0, len(lines), 15)] or [[]]
    embeds = []
    count = len(view["rows"])
    for index, page in enumerate(pages, start=1):
        description = (
            "\n".join([f"`{header}`", *page]) if page else "No results for this competition day."
        )
        if limit and count > limit:
            description += f"\n*Top {limit} of {count} players · full standings in the browser.*"
        if scheduled:
            # Threads may be unavailable; !lb still works without a public dashboard.
            description = description.replace(
                "full standings in the browser", "full standings: `!lb`"
            )
            description = (
                f"**{count} players · {sum(len(row['games']) for row in view['rows'])} results**\n"
                + description
            )
        embed = discord.Embed(
            title=f"{title} · {snapshot['day']} · {state}",
            description=description,
            color=GREEN if final else MAUVE,
        )
        entries = sum(len(row["games"]) for row in view["rows"])
        if game:
            entries = count
        mode = "1 + players beaten" if snapshot["scoring"] == "players_beaten" else "placement"
        embed.set_footer(
            text=(
                f"{count} players · {entries} results · {mode} · "
                f"{snapshot['timezone']} {snapshot['cutoff']} · {index}/{len(pages)}"
            )
        )
        if scheduled:
            embed.set_footer(text=f"Daily Podium · {snapshot['day']} · Final")
        embeds.append(embed)
    return embeds


def daily_highlights(snapshot):
    embed = discord.Embed(title=f"Game highlights · {snapshot['day']}", color=MAUVE)
    for game in Game:
        rows = game_snapshot(snapshot, game)["rows"]
        solved = [row for row in rows if row["games"][game.value]["solved"]]
        if solved:
            winners = [row for row in solved if row["rank"] == solved[0]["rank"]]
            names = ", ".join(player_name(row["name"]) for row in winners[:5])
            if len(winners) > 5:
                names += f" +{len(winners) - 5} tied"
            score = score_label(game, winners[0]["games"][game.value])
            value = f"**{names}** · {score}\n{len(rows)} played · {len(solved)} solved"
        else:
            value = f"{len(rows)} played · no successful results"
        embed.add_field(name=GAME_TITLES[game], value=value, inline=False)
    return embed


def weekly_snapshot(snapshots, through: date, started: date):
    monday = through - timedelta(days=through.weekday())
    first = max(monday, started)
    days = sorted(
        (
            snapshot
            for snapshot in snapshots
            if first.isoformat() <= snapshot["day"] <= through.isoformat()
        ),
        key=lambda snapshot: snapshot["day"],
    )
    players = {}
    for snapshot in days:
        for row in snapshot["rows"]:
            player = players.setdefault(
                row["user_id"], {"user_id": row["user_id"], "points": 0, "days": 0, "wins": 0}
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
        "start": monday.isoformat(),
        "through": through.isoformat(),
        "finalized_days": len(days),
        "expected_days": max(0, (through - first).days + 1),
        "rows": ordered,
    }


def weekly_embed(summary):
    lines = [
        f"`{row['rank']:>3} {name_column(row['name'])} {row['points']:>4} "
        f"{row['days']:>2} {row['wins']:>3}`"
        for row in summary["rows"][:10]
    ]
    header = f"`{'#':>3} {'Player':18} {'Pts':>4} {'D':>2} {'Win':>3}`"
    description = (
        "\n".join([header, *lines])
        if lines
        else "No player results in finalized days for this week yet."
    )
    if len(summary["rows"]) > 10:
        description += f"\n*Top 10 of {len(summary['rows'])} players.*"
    embed = discord.Embed(
        title=f"This week · {summary['start']} → {summary['through']}",
        description=description,
        color=MAUVE,
    )
    embed.set_footer(
        text=(
            f"{summary['finalized_days']}/{summary['expected_days']} days finalized · "
            "daily points added · d = days played · wins include ties"
        )
    )
    return embed


def player_stats_snapshot(snapshots: list[dict], user_id: int) -> dict:
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
            if better_score:
                game_stats["best"] = score
            projected = game_snapshot(snapshot, game)
            game_stats["wins"] += int(
                any(row["user_id"] == user_id and row["rank"] == 1 for row in projected["rows"])
            )
    return result


def player_stats_embed(stats: dict, display_name: str) -> discord.Embed:
    if stats["days_played"]:
        average = stats["points"] / stats["days_played"]
        description = (
            f"**{stats['days_played']}/{stats['finalized_days']}** days played · "
            f"**{stats['daily_wins']}** daily wins\n"
            f"**{stats['points']}** total points · **{average:.1f}** points per played day\n"
            f"Last played: {stats['last_day']}"
        )
    else:
        description = "No results in finalized days for this player yet."
    embed = discord.Embed(
        title=f"Player stats · {player_name(display_name)}",
        description=description,
        color=MAUVE,
    )
    for game in Game:
        data = stats["games"][game.value]
        if not data["played"]:
            value = "No finalized results yet."
        else:
            if data["solved"]:
                unit = " guesses" if game == Game.METAZOOA else ""
                average = f"{data['score_sum'] / data['solved']:,.1f}{unit}"
                best = f"{data['best']:,}{unit}"
            else:
                average = best = "—"
            value = (
                f"**{data['played']}** played · **{data['solved']}** solved · "
                f"**{data['wins']}** wins\n"
                f"{data['points']} pts · avg {average} · best {best}"
            )
        embed.add_field(name=GAME_TITLES[game], value=value, inline=False)
    embed.set_footer(
        text="This server · finalized days only · ties count as wins · score averages use solves"
    )
    return embed
