import pytest

from daily_games.models import Game
from daily_games.parsers import parse_message

from .conftest import FAILED_METAZOOA, METAZOOA, SIZEITUP, SIZEITUP_NUMBERED, TIMEGUESSR


@pytest.mark.parametrize(
    "text,game,puzzle,score,solved",
    [
        (TIMEGUESSR, Game.TIMEGUESSR, "1262", 18419, True),
        (SIZEITUP, Game.SIZE_IT_UP, "2026-11-13", 298, True),
        (SIZEITUP_NUMBERED, Game.SIZE_IT_UP, "98", 108, True),
        (METAZOOA, Game.METAZOOA, "1201", 2, True),
        (FAILED_METAZOOA, Game.METAZOOA, "1201", None, False),
    ],
)
def test_user_supplied_share_formats(text, game, puzzle, score, solved):
    results, errors = parse_message(text)
    assert errors == []
    assert len(results) == 1
    result = results[0]
    assert (result.game, result.puzzle, result.score, result.solved) == (
        game,
        puzzle,
        score,
        solved,
    )


def test_multiple_games_in_one_message():
    results, errors = parse_message("\n\n".join([TIMEGUESSR, SIZEITUP, METAZOOA]))
    assert len(results) == 3
    assert not errors


@pytest.mark.parametrize(
    "text",
    [
        TIMEGUESSR.replace("18,419", "50,001"),
        TIMEGUESSR.replace("18,419", "18,41"),
        TIMEGUESSR.replace("50,000", "5,000"),
        TIMEGUESSR.replace("#1262", "#9999999999999999999999999"),
        SIZEITUP.replace("298/500", "501/500"),
        SIZEITUP.replace("2026-11-13", "2026-02-30"),
        SIZEITUP.replace("Size It Up", "Size It Up: Pop Culture"),
        SIZEITUP_NUMBERED.replace("#98", "#0"),
        SIZEITUP_NUMBERED.replace("#98", "#1000001"),
        SIZEITUP_NUMBERED.replace("#98", "#98\n2026-11-15"),
        METAZOOA.replace("2 guesses", "21 guesses"),
        METAZOOA.replace("I figured it out in 2 guesses!", ""),
        METAZOOA + "\nI was stumped by today's game!",
    ],
)
def test_malformed_results_are_not_accepted(text):
    results, errors = parse_message(text)
    assert not results
    assert errors


def test_ordinary_chat_and_commands_are_ignored():
    for text in ("hello everyone", "!lb", "I beat yesterday's score", "https://metazooa.com"):
        assert parse_message(text) == ([], [])


def test_singular_guess_and_windows_line_endings():
    text = METAZOOA.replace("2 guesses", "1 guess").replace("\n", "\r\n")
    assert parse_message(text)[0][0].score == 1


def test_extremely_long_number_is_rejected_without_crashing_recovery():
    results, errors = parse_message(TIMEGUESSR.replace("18,419", "9" * 10000))
    assert not results
    assert errors
