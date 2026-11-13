from dataclasses import dataclass
from datetime import date, datetime

from .calendar import active_day, puzzle_day
from .models import ParsedResult, ServerConfig, Submission
from .storage import Store


@dataclass(frozen=True)
class Outcome:
    status: str
    detail: str


def accept_result(
    store: Store,
    config: ServerConfig,
    result: ParsedResult,
    *,
    user_id: int,
    display_name: str,
    message_id: int,
    channel_id: int,
    submitted_at: datetime,
) -> Outcome:
    """Apply identical rules to live messages and recovered history timestamps."""
    if channel_id != config.input_channel_id:
        return Outcome("ignored", "Results belong in this server's configured results channel.")
    day = puzzle_day(result, store.override(config.guild_id, result.game, result.puzzle))
    expected = active_day(submitted_at, config)
    if day < config.started_day or day < expected:
        return Outcome("old", f"This puzzle belongs to {day}; that competition day is closed.")
    if day > expected:
        return Outcome("future", f"This puzzle belongs to {day}; that competition day is not open.")
    assigned = store.puzzle_for_day(config.guild_id, result.game, day)
    if assigned is not None and assigned != result.puzzle:
        return Outcome("wrong_puzzle", f"This server assigned puzzle {assigned} to {day}.")
    if store.board(config.guild_id, day):
        return Outcome("closed", f"The final board for {day} is already frozen.")
    row = Submission(
        config.guild_id,
        user_id,
        display_name,
        result.game,
        result.puzzle,
        day,
        result.score,
        result.solved,
        message_id,
        channel_id,
        submitted_at,
    )
    if store.insert(row):
        detail = (
            f"Recorded for {day}." if result.solved else f"Failure recorded for {day}: 0 points."
        )
        return Outcome("accepted", detail)
    return Outcome("duplicate", "You already submitted this game/puzzle in this server.")


def requested_day(value: str, now: datetime, config: ServerConfig) -> date:
    today = active_day(now, config)
    if value.casefold() == "today":
        return today
    if value.casefold() == "yesterday":
        return date.fromordinal(today.toordinal() - 1)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Choose today, yesterday, or an ISO date such as 2026-11-13.") from exc
