"""Independent, read-only Tailscale dashboard. Never starts or controls the bot."""

import argparse
import asyncio
import ipaddress
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NamedTuple

from aiohttp import web
from dotenv import load_dotenv

from .calendar import active_day, closes_at, post_due_at
from .models import GAME_TITLES, Game, ServerConfig, Submission
from .periods import period_snapshot, period_start
from .presentation import game_snapshot, make_snapshot
from .service import requested_day
from .streaks import completed_day_anchor, row_streak, server_streak_snapshots, streak_summaries

STATIC = Path(__file__).with_name("dashboard_static")
logger = logging.getLogger(__name__)


class DashboardSettings(NamedTuple):
    database: Path
    project: Path


class ServerNotFound(LookupError):
    """The requested server is absent from the configured competitions."""


SETTINGS = web.AppKey("settings", DashboardSettings)
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self'; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
}


@contextmanager
def read_database(path: Path):
    """Read the live WAL consistently, without schema creation or checkpoints."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.close()


def process_status(project: Path) -> dict:
    """Observe this checkout's Python bot processes, not their Discord readiness."""
    processes = []
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return {"state": "unknown", "processes": [], "gateway": "not_measured"}
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            module = any(
                argv[index : index + 2] == [b"-m", b"daily_games"] for index in range(len(argv) - 1)
            )
            script = any(Path(os.fsdecode(arg)).name == "daily-games-bot" for arg in argv[:2])
            if not (module or script) or (entry / "cwd").resolve() != project.resolve():
                continue
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                continue
            uptime = float(Path("/proc/uptime").read_text().split()[0])
            age = uptime - int(fields[19]) / os.sysconf("SC_CLK_TCK")
            processes.append({"pid": entry.name, "uptime_seconds": max(0, int(age))})
        except (OSError, ValueError, IndexError):
            continue
    return {
        "state": "running" if processes else "not_detected",
        "processes": processes,
        "gateway": "not_measured",
    }


def config_from_row(row: sqlite3.Row) -> ServerConfig:
    return ServerConfig(
        row["guild_id"],
        row["input_channel_id"],
        row["output_channel_id"],
        row["timezone"],
        row["cutoff"],
        row["scoring"],
        date.fromisoformat(row["started_day"]),
    )


def submission_from_row(row: sqlite3.Row) -> Submission:
    return Submission(
        row["guild_id"],
        row["user_id"],
        row["display_name"],
        Game(row["game"]),
        row["puzzle"],
        date.fromisoformat(row["day"]),
        row["score"],
        bool(row["solved"]),
        row["message_id"],
        row["channel_id"],
        datetime.fromisoformat(row["submitted_at"]),
    )


def board_record(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "day": row["day"],
        "posted": row["message_id"] is not None,
        "message_id": str(row["message_id"]) if row["message_id"] is not None else None,
        "attempted_at": row["attempted_at"],
    }


def read_data(
    settings: DashboardSettings, server: str, day: str, game: str, period: str = "day"
) -> dict:
    now = datetime.now(UTC)
    selected_game = None if game == "all" else Game(game)
    if period not in {"day", "week", "month"}:
        raise ValueError("Choose day, week, or month.")
    if server and (not server.isdecimal() or not 0 < int(server) < 2**63):
        raise ValueError("Choose a configured server.")
    with read_database(settings.database) as db:
        configs = [
            config_from_row(row) for row in db.execute("SELECT * FROM servers ORDER BY guild_id")
        ]
        response = {
            "generated_at": now.isoformat(),
            "database": "available",
            "bot": process_status(settings.project),
            "games": [{"id": item.value, "name": GAME_TITLES[item]} for item in Game],
            "servers": [{"id": str(cfg.guild_id)} for cfg in configs],
            "selected": None,
        }
        cfg = (
            next((cfg for cfg in configs if str(cfg.guild_id) == server), None)
            if server
            else (configs[0] if configs else None)
        )
        if cfg is None:
            if server:
                raise ServerNotFound("Server is not configured.")
            return response
        today = active_day(now, cfg)
        selected_day = requested_day(day, now, cfg)
        # These helpers add/subtract a day; exclude date extremes up front.
        if selected_day in {date.min, date.max}:
            raise ValueError("Choose a date between 0001-01-02 and 9999-12-30.")
        rows = db.execute(
            "SELECT * FROM submissions WHERE guild_id=? AND day=? AND voided=0 "
            "ORDER BY submitted_at, message_id",
            (cfg.guild_id, selected_day.isoformat()),
        ).fetchall()
        saved_board = db.execute(
            "SELECT * FROM boards WHERE guild_id=? AND day=?",
            (cfg.guild_id, selected_day.isoformat()),
        ).fetchone()
        if saved_board:
            try:
                snapshot = json.loads(saved_board["snapshot"])
            except json.JSONDecodeError as exc:
                raise sqlite3.DatabaseError("Invalid frozen board.") from exc
        else:
            snapshot = make_snapshot([submission_from_row(row) for row in rows], selected_day, cfg)
        full_snapshot = snapshot
        if selected_game is not None:
            snapshot = game_snapshot(snapshot, selected_game)
        streak_boards = server_streak_snapshots(db, cfg.guild_id, selected_day)
        if not saved_board:
            streak_boards.append(full_snapshot)
        anchor = selected_day if selected_day >= today else completed_day_anchor(selected_day)
        summaries = streak_summaries(streak_boards, anchor)
        period_board = None
        if period != "day":
            saved_days = db.execute(
                "SELECT snapshot FROM boards WHERE guild_id=? AND day BETWEEN ? AND ? "
                "ORDER BY day",
                (
                    cfg.guild_id,
                    period_start(selected_day, period).isoformat(),
                    selected_day.isoformat(),
                ),
            ).fetchall()
            try:
                period_board = period_snapshot(
                    [json.loads(row["snapshot"]) for row in saved_days],
                    selected_day, cfg.started_day, period, selected_game,
                )
            except json.JSONDecodeError as exc:
                raise sqlite3.DatabaseError("Invalid frozen board.") from exc
            period_board["rows"] = [
                {
                    **row,
                    "user_id": str(row["user_id"]),
                    "streak": row_streak(summaries, row["user_id"], selected_game),
                }
                for row in period_board["rows"]
            ]
        # Copy before serialization so frozen SQLite snapshots are never modified.
        snapshot["rows"] = [
            {
                **row,
                "user_id": str(row["user_id"]),
                "streak": row_streak(summaries, row["user_id"], selected_game),
            }
            for row in snapshot["rows"]
        ]
        counts = db.execute(
            "SELECT game, COUNT(*) AS count FROM submissions "
            "WHERE guild_id=? AND day=? AND voided=0 GROUP BY game",
            (cfg.guild_id, today.isoformat()),
        ).fetchall()
        players = db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM submissions "
            "WHERE guild_id=? AND day=? AND voided=0",
            (cfg.guild_id, today.isoformat()),
        ).fetchone()[0]
        recent = db.execute(
            "SELECT * FROM submissions WHERE guild_id=? AND voided=0 "
            "ORDER BY submitted_at DESC, message_id DESC LIMIT 20",
            (cfg.guild_id,),
        ).fetchall()
        history = db.execute(
            "SELECT * FROM boards WHERE guild_id=? ORDER BY day DESC LIMIT 30",
            (cfg.guild_id,),
        ).fetchall()
        per_game = {item.value: 0 for item in Game}
        per_game.update({row["game"]: row["count"] for row in counts})
        response["selected"] = {
            "server_id": str(cfg.guild_id),
            "config": {
                "timezone": cfg.timezone,
                "cutoff": cfg.cutoff,
                "scoring": cfg.scoring,
                "input_channel_id": str(cfg.input_channel_id),
                "output_channel_id": str(cfg.output_channel_id),
                "started_day": cfg.started_day.isoformat(),
            },
            "today": today.isoformat(),
            "closes_at": closes_at(today, cfg).isoformat(),
            "post_due_at": post_due_at(today, cfg).isoformat(),
            "today_counts": {
                "players": players,
                "submissions": sum(per_game.values()),
                "games": per_game,
            },
            "game": game,
            "period": period,
            "period_board": period_board,
            "board": snapshot,
            "board_state": "final"
            if saved_board
            else ("closed_pending" if selected_day < today else "provisional"),
            "posting": board_record(saved_board),
            "recent": [
                {
                    "user_id": str(row["user_id"]),
                    "name": row["display_name"],
                    "game": row["game"],
                    "score": row["score"],
                    "solved": bool(row["solved"]),
                    "day": row["day"],
                    "puzzle": row["puzzle"],
                    "submitted_at": row["submitted_at"],
                }
                for row in recent
            ],
            "history": [board_record(row) for row in history],
        }
        return response


@web.middleware
async def response_headers(request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        exc.headers.update(SECURITY_HEADERS)
        raise
    response.headers.update(SECURITY_HEADERS)
    return response


async def data_handler(request: web.Request) -> web.Response:
    try:
        data = await asyncio.to_thread(
            read_data,
            request.app[SETTINGS],
            request.query.get("server", ""),
            request.query.get("day", "today"),
            request.query.get("game", "all"),
            request.query.get("period", "day"),
        )
    except (ValueError, OverflowError):
        return web.json_response(
            {"error": "Choose a valid server, game, period, and day "
             "(today, yesterday, or YYYY-MM-DD)."},
            status=400,
        )
    except ServerNotFound:
        return web.json_response({"error": "Server is not configured."}, status=404)
    except (sqlite3.Error, OSError, KeyError, TypeError, IndexError):
        logger.warning("Dashboard database is unavailable or incompatible.")
        return web.json_response(
            {"error": "Database unavailable. Check its location and permissions, then retry."},
            status=503,
        )
    return web.json_response(data)


async def index_handler(request: web.Request) -> web.Response:
    return web.Response(text=(STATIC / "index.html").read_text(), content_type="text/html")


async def asset_handler(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if name not in {"dashboard.css", "dashboard.js"}:
        raise web.HTTPNotFound()
    return web.Response(
        text=(STATIC / name).read_text(),
        content_type="text/css" if name.endswith(".css") else "text/javascript",
    )


def create_app(database: Path, project: Path | None = None) -> web.Application:
    app = web.Application(middlewares=[response_headers], client_max_size=1024)
    app[SETTINGS] = DashboardSettings(database.resolve(), (project or Path.cwd()).resolve())
    app.router.add_get("/", index_handler)
    app.router.add_get("/assets/{name}", asset_handler)
    app.router.add_get("/api/data", data_handler)
    return app


def private_host(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a Tailscale IPv4 or loopback address.") from exc
    if not address.is_loopback and address not in ipaddress.ip_network("100.64.0.0/10"):
        raise argparse.ArgumentTypeError("Bind only to loopback or a Tailscale IPv4 address.")
    return value


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host", type=private_host, default=os.getenv("DASHBOARD_HOST", "127.0.0.1")
    )
    parser.add_argument("--port", type=int, default=os.getenv("DASHBOARD_PORT", "8765"))
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.getenv("DATABASE_PATH", "data/daily-games.sqlite3")),
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(args.database), host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
