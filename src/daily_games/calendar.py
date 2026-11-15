"""Competition dates are puzzle identities, never inferred from a repost's date."""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import Game, ParsedResult, ServerConfig

ANCHOR_DAY = date(2026, 11, 13)
ANCHORS = {Game.TIMEGUESSR: 1262, Game.SIZE_IT_UP: 96, Game.METAZOOA: 1201}


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Use an IANA timezone such as Europe/Berlin or America/New_York.") from exc
    return value


def parse_cutoff(value: str) -> time:
    if not re.fullmatch(r"\d{2}:\d{2}", value):
        raise ValueError("Cutoff must use 24-hour HH:MM, such as 00:00.")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Cutoff must be a valid 24-hour time.") from exc


def boundary(day: date, timezone: str, cutoff: str) -> datetime:
    """Wall-clock boundary; first fold for ambiguous time, forward for a DST gap."""
    zone = ZoneInfo(timezone)
    local = datetime.combine(day, parse_cutoff(cutoff), zone)
    return local.astimezone(UTC).astimezone(zone)


def active_day(now: datetime, config: ServerConfig) -> date:
    if now.tzinfo is None:
        raise ValueError("An aware timestamp is required.")
    day = now.astimezone(ZoneInfo(config.timezone)).date()
    if now.astimezone(UTC) < boundary(day, config.timezone, config.cutoff).astimezone(UTC):
        day -= timedelta(days=1)
    return day


def closes_at(day: date, config: ServerConfig) -> datetime:
    return boundary(day + timedelta(days=1), config.timezone, config.cutoff)


def post_due_at(day: date, config: ServerConfig) -> datetime:
    return closes_at(day, config).astimezone(UTC) + timedelta(minutes=5)


def puzzle_day(result: ParsedResult, override: date | None = None) -> date:
    if override is not None:
        return override
    if result.game == Game.SIZE_IT_UP and not result.puzzle.isdecimal():
        return date.fromisoformat(result.puzzle)
    return ANCHOR_DAY + timedelta(days=int(result.puzzle) - ANCHORS[result.game])
