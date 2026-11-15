"""Public scores only, independently hosted behind Tailscale Funnel."""

import argparse
import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from collections import OrderedDict
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

from .calendar import active_day, closes_at
from .dashboard import STATIC, config_from_row, read_database, response_headers, submission_from_row
from .models import Game, ServerConfig
from .periods import period_snapshot, period_start
from .presentation import game_snapshot, make_snapshot
from .public_links import configure, ensure_key, read_config, server_slug
from .service import requested_day
from .streaks import (
    completed_day_anchor,
    row_streak,
    server_streak_snapshots,
    streak_summaries,
)

GLOBAL_RULES = ServerConfig(0, 0, 0, "Europe/Berlin", "00:00", "players_beaten", date(2026, 11, 13))
PUBLIC = web.AppKey("public", dict)


def safe_snapshot(snapshot: dict) -> dict:
    """An explicit allowlist; Discord IDs and arbitrary snapshot metadata stay private."""
    return {
        "day": snapshot["day"],
        "timezone": snapshot["timezone"],
        "cutoff": snapshot["cutoff"],
        "scoring": snapshot["scoring"],
        "rows": [
            {
                "rank": row["rank"],
                "name": row["name"],
                "total": row["total"],
                "streak": row["streak"],
                "games": {
                    game.value: {
                        field: row["games"][game.value][field]
                        for field in ("score", "solved", "points")
                    }
                    for game in Game
                    if game.value in row["games"]
                },
            }
            for row in snapshot["rows"]
        ],
    }


def snapshot_for(db, cfg, day):
    saved = db.execute(
        "SELECT snapshot FROM boards WHERE guild_id=? AND day=?", (cfg.guild_id, day.isoformat())
    ).fetchone()
    if saved:
        try:
            return json.loads(saved["snapshot"]), True
        except json.JSONDecodeError as exc:
            raise sqlite3.DatabaseError("Invalid saved board.") from exc
    rows = db.execute(
        "SELECT * FROM submissions WHERE guild_id=? AND day=? AND voided=0 "
        "ORDER BY submitted_at, message_id",
        (cfg.guild_id, day.isoformat()),
    )
    return make_snapshot([submission_from_row(row) for row in rows], day, cfg), False


def global_snapshot(db, configs, day):
    """Earliest accepted submission per player/game/date, regardless of server."""
    active = {cfg.guild_id for cfg in configs if cfg.started_day <= day}
    frozen = {}
    for row in db.execute("SELECT guild_id, snapshot FROM boards WHERE day=?", (day.isoformat(),)):
        try:
            snapshot = json.loads(row["snapshot"])
            frozen[row["guild_id"]] = {
                (int(player["user_id"]), game.value): (player["name"], player["games"][game.value])
                for player in snapshot["rows"]
                for game in Game
                if game.value in player["games"]
            }
        except (ValueError, KeyError, TypeError) as exc:
            raise sqlite3.DatabaseError("Invalid frozen board.") from exc
    candidates = []
    for row in db.execute("SELECT * FROM submissions WHERE day=? AND voided=0", (day.isoformat(),)):
        if row["guild_id"] not in active:
            continue
        result = submission_from_row(row)
        if result.guild_id in frozen:
            entry = frozen[result.guild_id].get((result.user_id, result.game.value))
            if entry is None:
                continue
            name, saved = entry
            result = replace(
                result, display_name=name, score=saved["score"], solved=saved["solved"]
            )
        candidates.append(result)
    candidates.sort(
        key=lambda item: (item.submitted_at.astimezone(UTC), item.message_id, item.guild_id)
    )
    unique = {}
    for result in candidates:
        unique.setdefault((result.user_id, result.game), result)
    return make_snapshot(list(unique.values()), day, GLOBAL_RULES), bool(
        active
    ) and active <= frozen.keys()


def global_streak_snapshots(db, configs, through):
    """Use only dates whose participating servers all have frozen boards."""
    boards = {}
    for row in db.execute(
        "SELECT guild_id, day, snapshot FROM boards WHERE day<=?", (through.isoformat(),)
    ):
        try:
            boards.setdefault(row["day"], {})[row["guild_id"]] = json.loads(row["snapshot"])
        except json.JSONDecodeError as exc:
            raise sqlite3.DatabaseError("Invalid saved board.") from exc
    snapshots = []
    for day_text, saved in sorted(boards.items()):
        day = date.fromisoformat(day_text)
        active = {cfg.guild_id for cfg in configs if cfg.started_day <= day}
        if not active or not active <= saved.keys():
            continue
        players = {}
        for guild_id in active:
            for row in saved[guild_id]["rows"]:
                player = players.setdefault(
                    row["user_id"], {"user_id": row["user_id"], "games": {}}
                )
                player["games"].update(row["games"])
        snapshots.append({"day": day_text, "rows": list(players.values())})
    return snapshots


def public_data(
    database: Path, key: bytes, scope: str, day: str, game: str, period: str = "day"
) -> dict:
    now = datetime.now(UTC)
    selected_game = None if game == "all" else Game(game)
    if period not in {"day", "week", "month"}:
        raise ValueError("Invalid period.")
    if scope != "global" and not re.fullmatch(r"[a-f0-9]{24}", scope):
        raise LookupError("Unknown competition.")
    local = read_config(database)
    with read_database(database) as db:
        configs = [
            config_from_row(row) for row in db.execute("SELECT * FROM servers ORDER BY guild_id")
        ]
        servers = [
            {
                "slug": server_slug(key, cfg.guild_id),
                "name": local.get("server_names", {}).get(str(cfg.guild_id), f"Community {index}"),
            }
            for index, cfg in enumerate(configs, start=1)
        ]
        cfg = GLOBAL_RULES
        name = "Global leaderboard"
        if scope != "global":
            match = next(
                (
                    (cfg, server)
                    for cfg, server in zip(configs, servers, strict=True)
                    if server["slug"] == scope
                ),
                None,
            )
            if match is None:
                raise LookupError("Unknown competition.")
            cfg, server = match
            name = server["name"]
        selected_day = requested_day(day, now, cfg)
        if selected_day in {date.min, date.max}:
            raise ValueError("Invalid date.")
        today = active_day(now, cfg)
        snapshot, final = (
            global_snapshot(db, configs, selected_day)
            if scope == "global"
            else snapshot_for(db, cfg, selected_day)
        )
        streak_boards = (
            global_streak_snapshots(db, configs, selected_day)
            if scope == "global"
            else server_streak_snapshots(db, cfg.guild_id, selected_day)
        )
        if not final:
            streak_boards.append(snapshot)
        anchor = selected_day if selected_day >= today else completed_day_anchor(selected_day)
        summaries = streak_summaries(streak_boards, anchor)
        period_board = None
        if period != "day":
            start = period_start(selected_day, period)
            if scope == "global":
                # Global points are recalculated from earliest accepted results. Only
                # fully finalized dates can contribute to a stable period board.
                saved_days = []
                current = max(start, GLOBAL_RULES.started_day)
                while current <= selected_day:
                    board, complete = global_snapshot(db, configs, current)
                    if complete:
                        saved_days.append(board)
                    current += timedelta(days=1)
                started = GLOBAL_RULES.started_day
            else:
                saved_days = []
                for row in db.execute(
                    "SELECT snapshot FROM boards WHERE guild_id=? AND day BETWEEN ? AND ? "
                    "ORDER BY day",
                    (cfg.guild_id, start.isoformat(), selected_day.isoformat()),
                ):
                    try:
                        saved_days.append(json.loads(row["snapshot"]))
                    except json.JSONDecodeError as exc:
                        raise sqlite3.DatabaseError("Invalid saved board.") from exc
                started = cfg.started_day
            period_board = period_snapshot(
                saved_days, selected_day, started, period, selected_game
            )
            period_board["rows"] = [
                {
                    **{field: row[field] for field in ("rank", "name", "points", "days", "wins")},
                    "streak": row_streak(summaries, row["user_id"], selected_game),
                }
                for row in period_board["rows"]
            ]
        player_count = len(snapshot["rows"])
        result_count = sum(len(row["games"]) for row in snapshot["rows"])
        if selected_game is not None:
            snapshot = game_snapshot(snapshot, selected_game)
        snapshot = {
            **snapshot,
            "rows": [
                {**row, "streak": row_streak(summaries, row["user_id"], selected_game)}
                for row in snapshot["rows"]
            ],
        }
        if scope == "global":
            history = [
                row[0]
                for row in db.execute("SELECT DISTINCT day FROM boards ORDER BY day DESC LIMIT 30")
            ]
        else:
            history = [
                row[0]
                for row in db.execute(
                    "SELECT day FROM boards WHERE guild_id=? ORDER BY day DESC LIMIT 30",
                    (cfg.guild_id,),
                )
            ]
        return {
            "generated_at": now.isoformat(),
            "scope": scope,
            "name": name,
            "servers": servers,
            "today": today.isoformat(),
            "closes_at": closes_at(today, cfg).isoformat(),
            "game": game,
            "period": period,
            "period_board": period_board,
            "board": safe_snapshot(snapshot),
            "state": "final"
            if final
            else ("closed_pending" if selected_day < today else "provisional"),
            "players": player_count,
            "submissions": result_count,
            "history": history,
            "rules": (
                "First accepted submission per player, game, and recorded competition date "
                "across all servers. "
                "Points are recalculated globally: 1 + players beaten. Today uses Europe/Berlin. "
                "Final only when every participating server has frozen its board."
                if scope == "global"
                else "This server's scores and scoring rules. Final boards use saved snapshots."
            ),
        }


async def public_handler(request):
    settings = request.app[PUBLIC]
    if set(request.query) - {"scope", "day", "game", "period"}:
        return web.json_response({"error": "Unsupported filter."}, status=400)
    args = (
        request.query.get("scope", "global"),
        request.query.get("day", "today"),
        request.query.get("game", "all"),
        request.query.get("period", "day"),
    )
    if sum(len(arg) for arg in args) > 100:
        return web.json_response({"error": "Invalid filter."}, status=400)
    try:
        async with settings["lock"]:
            cached = settings["cache"].get(args)
            if cached and time.monotonic() - cached[0] < 15:
                data = cached[1]
            else:
                data = await asyncio.to_thread(
                    public_data, settings["database"], settings["key"], *args
                )
                settings["cache"][args] = (time.monotonic(), data)
                while len(settings["cache"]) > 64:
                    settings["cache"].popitem(last=False)
    except (sqlite3.Error, OSError, KeyError, IndexError, TypeError):
        return web.json_response(
            {"error": "Standings are temporarily unavailable. Please try again shortly."},
            status=503,
        )
    except ValueError:
        return web.json_response(
            {"error": "Choose a supported game, period, and date."}, status=400
        )
    except LookupError:
        return web.json_response({"error": "Competition not found."}, status=404)
    return web.json_response(data)


async def public_index(request):
    if "slug" in request.match_info and not re.fullmatch(
        r"[a-f0-9]{24}", request.match_info["slug"]
    ):
        raise web.HTTPNotFound()
    return web.Response(text=(STATIC / "public.html").read_text(), content_type="text/html")


async def public_asset(request):
    name = request.match_info["name"]
    if name not in {"public.js", "dashboard.css", "public.css"}:
        raise web.HTTPNotFound()
    return web.Response(
        text=(STATIC / name).read_text(),
        content_type="text/javascript" if name.endswith(".js") else "text/css",
    )


def create_public_app(database: Path, key: bytes) -> web.Application:
    app = web.Application(middlewares=[response_headers], client_max_size=1024)
    app[PUBLIC] = {
        "database": database.resolve(),
        "key": key,
        "cache": OrderedDict(),
        "lock": asyncio.Lock(),
    }
    app.router.add_get("/", public_index)
    app.router.add_get("/global", public_index)
    app.router.add_get("/s/{slug}", public_index)
    app.router.add_get("/assets/{name}", public_asset)
    app.router.add_get("/api/leaderboard", public_handler)
    return app


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.getenv("DATABASE_PATH", "data/daily-games.sqlite3")),
    )
    parser.add_argument(
        "--base-url", help="Configure the public HTTPS origin for Discord leaderboard links."
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    if args.base_url:
        configure(args.database, args.base_url)
    key = ensure_key(args.database)
    logging.basicConfig(level=logging.INFO)
    web.run_app(
        create_public_app(args.database, key), host="127.0.0.1", port=args.port, access_log=None
    )


if __name__ == "__main__":
    main()
