from dataclasses import dataclass
from datetime import date, datetime

from .calendar import active_day, puzzle_day
from .i18n import phrase
from .models import Game, ParsedResult, ServerConfig, Submission
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
    allowed_channel_id: int | None = None,
    language: str = "en",
) -> Outcome:
    """Apply identical rules to live messages and recovered history timestamps."""
    if channel_id != (allowed_channel_id or config.input_channel_id):
        return Outcome(
            "ignored",
            phrase(
                language,
                "Results belong in this server's configured results channel.",
                "Ergebnisse gehören in den eingestellten Ergebniskanal dieses Servers.",
            ),
        )
    day = puzzle_day(result, store.override(config.guild_id, result.game, result.puzzle))
    expected = active_day(submitted_at, config)
    if day < config.started_day or day < expected:
        detail = phrase(
            language,
            f"This puzzle belongs to {day}; that competition day is closed.",
            f"Dieses Rätsel gehört zum {day}; dieser Spieltag ist abgeschlossen.",
        )
        if result.game == Game.TIMEGUESSR and (expected - day).days == 1:
            detail += phrase(
                language,
                " TimeGuessr may still show the previous daily after this server's cutoff."
                " Wait for its next daily puzzle before submitting for today.",
                " TimeGuessr zeigt nach dem Tageswechsel möglicherweise noch das alte Rätsel."
                " Warte auf das neue Tagesrätsel, bevor du für heute einreichst.",
            )
        return Outcome("old", detail)
    if day > expected:
        return Outcome(
            "future",
            phrase(
                language,
                f"This puzzle belongs to {day}; that competition day is not open.",
                f"Dieses Rätsel gehört zum {day}; dieser Spieltag hat noch nicht begonnen.",
            ),
        )
    assigned = store.puzzle_for_day(config.guild_id, result.game, day)
    # A dated Size It Up Share identifies the day directly, even if an admin
    # assigned that day's numbered Share format through /puzzle_date.
    dated_size_result = result.game == Game.SIZE_IT_UP and result.puzzle == day.isoformat()
    if assigned is not None and assigned != result.puzzle and not dated_size_result:
        return Outcome(
            "wrong_puzzle",
            phrase(
                language,
                f"This server assigned puzzle {assigned} to {day}.",
                f"Für den {day} wurde auf diesem Server Rätsel {assigned} festgelegt.",
            ),
        )
    if store.board(config.guild_id, day):
        return Outcome(
            "closed",
            phrase(
                language,
                f"The final board for {day} is already frozen.",
                f"Die endgültige Rangliste für den {day} ist bereits abgeschlossen.",
            ),
        )
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
            phrase(language, f"Recorded for {day}.", f"Für den {day} gespeichert.")
            if result.solved
            else phrase(
                language,
                f"Failure recorded for {day}: 0 points.",
                f"Fehlversuch für den {day} gespeichert: 0 Punkte.",
            )
        )
        return Outcome("accepted", detail)
    return Outcome(
        "duplicate",
        phrase(
            language,
            "You already submitted this game/puzzle in this server.",
            "Du hast dieses Spiel/Rätsel auf diesem Server bereits eingereicht.",
        ),
    )


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
