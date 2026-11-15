"""Read TimeGuessr's own daily countdown without assuming a fixed reset hour."""

import re
from datetime import UTC, datetime

import aiohttp

COUNTDOWN_URL = "https://timeguessr.com/finalscoredaily"
PLAY_URL = "https://timeguessr.com/play?mode=daily"
COUNTDOWN_END = re.compile(r"countdownEndsAt\s*=\s*(\d{13})")
DAILY_PUZZLE = re.compile(r'mode:"daily",archive:null,playArray:\[\{No:"(\d+)"')


def parse_next_reset(page: str, now: datetime) -> datetime | None:
    match = COUNTDOWN_END.search(page)
    if match is None:
        return None
    reset = datetime.fromtimestamp(int(match[1]) / 1000, UTC)
    return reset if reset > now else None


def parse_current_puzzle(page: str) -> int | None:
    match = DAILY_PUZZLE.search(page)
    return int(match[1]) if match else None


async def _fetch_page(url: str) -> str | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; DailyPodium/0.1)"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                response.raise_for_status()
                return await response.text()
    except (aiohttp.ClientError, TimeoutError, ValueError, OverflowError):
        return None


async def next_reset(now: datetime) -> datetime | None:
    """Return the site's live next-daily timestamp, or None if unavailable."""
    page = await _fetch_page(COUNTDOWN_URL)
    return parse_next_reset(page, now) if page else None


async def current_daily_puzzle() -> int | None:
    """Return the puzzle number currently served by the official daily page."""
    page = await _fetch_page(PLAY_URL)
    return parse_current_puzzle(page) if page else None
