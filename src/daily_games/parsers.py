"""Parse Share text only. These functions never contact Discord or game servers."""

import re
import unicodedata
from datetime import date

from .models import Game, ParsedResult
from .wiki_daily import route_key


class ParseError(ValueError):
    pass


# Splitting on game headers allows one message to contain several pasted results.
HEADER = re.compile(
    r"(?im)^[^\w\n]*(?:TimeGuessr\s*#|(?:Magnitudle\s*[—–-]\s*)?Size It Up\b|"
    r"Animal\s*#|Krillion[ \t]+#)"
)
INTEGER = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
WIKI_SHARE = re.compile(
    r"(?P<start>[^\n]{1,300}?)\s+➡️?\s+(?P<target>[^\n]{1,300}?)\s+"
    r"⏰\s*(?P<time>[0-9]{1,4}(?::[0-9]{2}){1,2})\s+"
    r"🦶\s*(?P<steps>[0-9]{1,5})(?:\s+\((?P<path>[^\n]+)\))?"
    r"(?:\s+🔗\s*(?P<link><?https://(?:www\.)?thewikigamedaily\.com/?>?))?",
    re.IGNORECASE,
)
WIKI_ERROR = "The Wiki Game Daily: paste the complete Share result with steps and time."
KRILLION_ERROR = "Krillion: paste the daily dive number and a score from 0 to 700."


def _number(value: str) -> int:
    return int(value.replace(",", ""))


def _timeguessr(text: str) -> ParsedResult:
    match = re.search(
        rf"^TimeGuessr\s*#(\d+)\s*[—–-]\s*({INTEGER})\s*/\s*({INTEGER})\s*$",
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    if not match:
        raise ParseError("TimeGuessr: expected '#puzzle — score/50,000'.")
    puzzle, score, maximum = map(_number, match.groups())
    if not 1 <= puzzle <= 1000000 or maximum != 50000 or not 0 <= score <= maximum:
        raise ParseError("TimeGuessr: puzzle or score is outside the allowed range.")
    return ParsedResult(Game.TIMEGUESSR, str(puzzle), score)


def _sizeitup(text: str) -> ParsedResult:
    if not re.search(
        r"^(?:Magnitudle\s*[—–-]\s*)?Size It Up\s*$", text, re.IGNORECASE | re.MULTILINE
    ):
        raise ParseError("Only standard Size It Up is supported, not other variants.")
    date_match = re.search(r"^\s*(\d{4}-\d{2}-\d{2})\s*$", text, re.MULTILINE)
    number_match = re.search(r"^\s*#\s*(\d+)\s*$", text, re.MULTILINE)
    score_match = re.search(r"^\s*Score:\s*(\d+)\s*/\s*(\d+)\s*$", text, re.MULTILINE)
    if not (date_match or number_match) or not score_match:
        raise ParseError("Size It Up: include a date or #puzzle and 'Score: number/500'.")
    if date_match and number_match:
        raise ParseError("Size It Up: include only one puzzle date or number.")
    if date_match:
        try:
            puzzle = date.fromisoformat(date_match[1]).isoformat()
        except ValueError as exc:
            raise ParseError("Size It Up: invalid puzzle date.") from exc
    else:
        number = int(number_match[1])
        if not 1 <= number <= 1000000:
            raise ParseError("Size It Up: puzzle number is outside the allowed range.")
        puzzle = str(number)
    score, maximum = map(int, score_match.groups())
    if maximum != 500 or not 0 <= score <= maximum:
        raise ParseError("Size It Up: score must be between 0 and 500.")
    return ParsedResult(Game.SIZE_IT_UP, puzzle, score)


def _metazooa(text: str) -> ParsedResult:
    puzzle = re.search(r"\bAnimal\s*#(\d+)\b", text, re.IGNORECASE)
    if not puzzle or not 1 <= int(puzzle[1]) <= 1000000:
        raise ParseError("Metazooa: missing Animal puzzle number.")
    if not re.search(r"(?:https?://(?:www\.)?metazooa\.com\b|#metazooa\b)", text, re.I):
        raise ParseError("Metazooa: include the game's link or #metazooa from Share.")
    success = re.search(r"I figured it out in\s+(\d+)\s+guess(?:es)?!", text, re.I)
    failure = re.search(r"I was stumped by today['’]s game!", text, re.I)
    if success and failure:
        raise ParseError("Metazooa: result contains both a solve and a failure.")
    if failure:
        return ParsedResult(Game.METAZOOA, str(int(puzzle[1])), None, solved=False)
    if not success or not 1 <= int(success[1]) <= 20:
        raise ParseError("Metazooa: expected a solve in 1–20 guesses or the failure message.")
    return ParsedResult(Game.METAZOOA, str(int(puzzle[1])), int(success[1]))


def _krillion(text: str) -> ParsedResult:
    match = re.match(
        r"^Krillion[ \t]+#([0-9]{1,6})[ \t]*(?:🦐️?[ \t]*)?\n"
        r"(?:[ \t]*\n)*[ \t]*([0-9]{1,3})[ \t]*(?:\n|$)",
        text,
        re.IGNORECASE,
    )
    if match is None:
        raise ParseError(KRILLION_ERROR)
    puzzle, score = map(int, match.groups())
    if puzzle == 0 or score > 700:
        raise ParseError(KRILLION_ERROR)
    return ParsedResult(Game.KRILLION, str(puzzle), score)


def parse_message(content: str) -> tuple[list[ParsedResult], list[str]]:
    """Return supported results and errors; ordinary chat yields two empty lists."""
    content = unicodedata.normalize("NFC", content).replace("\r\n", "\n")
    results, errors = [], []
    other_lines = []
    for line in content.split("\n"):
        has_link = "thewikigamedaily.com" in line.lower()
        has_path_markers = "➡" in line and "🦶" in line and "(" in line
        if "⏰" not in line or not (has_link or has_path_markers):
            other_lines.append(line)
            continue
        match = WIKI_SHARE.fullmatch(line.strip().strip("`").strip())
        if match is None or not (match["path"] or match["link"]):
            errors.append(WIKI_ERROR)
            continue
        parts = [int(piece) for piece in match["time"].split(":")]
        steps = int(match["steps"])
        if not steps or any(part >= 60 for part in parts[1:]):
            errors.append(WIKI_ERROR)
            continue
        elapsed = sum(part * 60**index for index, part in enumerate(reversed(parts)))
        results.append(
            ParsedResult(
                Game.WIKI,
                route_key(match["start"], match["target"]),
                steps,
                elapsed_seconds=elapsed,
            )
        )
    content = "\n".join(other_lines)
    headers = list(HEADER.finditer(content))
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(content)
        block = content[header.start() : end].strip().strip("`").strip()
        # Header may include leading animal emoji, which the Metazooa parser handles.
        title = header.group().casefold()
        parser = (
            _timeguessr
            if "timeguessr" in title
            else _sizeitup
            if "size it up" in title
            else _krillion
            if "krillion" in title
            else _metazooa
        )
        try:
            results.append(parser(block))
        except ParseError as exc:
            errors.append(str(exc))
        except (ValueError, OverflowError):
            errors.append("Result contains an invalid or excessively large number.")
    return results, errors
