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


def recorded_detail(language: str, day: date | str, solved: bool) -> str:
    if solved:
        return phrase(language, f"Recorded for {day}.", f"Für den {day} gespeichert.")
    return phrase(
        language,
        f"Failure recorded for {day}: 0 points.",
        f"Fehlversuch für den {day} gespeichert: 0 Punkte.",
    )


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
    prior = store.submission_for_message(config.guild_id, user_id, result.game, message_id)
    if prior is not None and not prior["voided"]:
        # History recovery can save this exact message before its live event is handled.
        return Outcome("recorded", recorded_detail(language, prior["day"], bool(prior["solved"])))
    expected = active_day(submitted_at, config)
    if result.game not in store.rotation(config.guild_id).games_on(expected):
        return Outcome(
            "inactive",
            phrase(
                language,
                "This game is out of today's lineup.",
                "Dieses Spiel ist heute nicht in der Rotation.",
            ),
        )
    if result.game == Game.WIKI:
        day = expected
    else:
        day = puzzle_day(result, store.override(config.guild_id, result.game, result.puzzle))
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
        if result.game == Game.KRILLION and (expected - day).days == 1:
            detail += phrase(
                language,
                " Krillion's new dive opens at midnight New York time.",
                " Krillions neuer Tauchgang startet um Mitternacht in New York.",
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
    if result.game == Game.WIKI:
        if result.elapsed_seconds is None or result.elapsed_seconds < 0:
            return Outcome(
                "wrong_puzzle",
                phrase(
                    language,
                    "The Wiki Game result needs a valid time.",
                    "Für das Wiki-Ergebnis ist eine gültige Zeit nötig.",
                ),
            )
        route = store.wiki_route(day)
        if route is None:
            return Outcome(
                "unverified",
                phrase(
                    language,
                    "Today's Wiki Game route is unavailable. Try again before cutoff.",
                    "Die heutige Wiki-Route ist nicht verfügbar. Versuche es vor Fristende erneut.",
                ),
            )
        if route != result.puzzle:
            return Outcome(
                "wrong_puzzle",
                phrase(
                    language,
                    "This Wiki Game route does not match today's puzzle.",
                    "Diese Wiki-Route gehört nicht zum heutigen Rätsel.",
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
        day.isoformat() if result.game == Game.WIKI else result.puzzle,
        day,
        result.score,
        result.solved,
        message_id,
        channel_id,
        submitted_at,
        result.elapsed_seconds,
    )
    if store.insert(row):
        return Outcome("accepted", recorded_detail(language, day, result.solved))
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
