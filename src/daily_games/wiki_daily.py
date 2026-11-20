"""Read dated routes from the assets loaded by The Wiki Game Daily."""

import json
import re
import unicodedata
from datetime import date
from html.parser import HTMLParser

import aiohttp

from .models import GAME_URLS, Game

ORIGIN = GAME_URLS[Game.WIKI].rstrip("/")
APP_SCRIPT = re.compile(r"/_next/static/chunks/pages/_app-[a-zA-Z0-9]+\.js")


def route_key(start: str, target: str) -> str:
    return json.dumps(
        [
            unicodedata.normalize("NFC", " ".join(part.split())).casefold()
            for part in (start, target)
        ],
        ensure_ascii=False,
    )


class Scripts(HTMLParser):
    app: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            source = dict(attrs).get("src")
            if source and APP_SCRIPT.fullmatch(source):
                self.app = source


def parse_route(script: str, day: date) -> tuple[str, str] | None:
    for match in re.finditer(rf'"{day.isoformat()}"\s*:\s*(?=\{{)', script):
        try:
            item, _ = json.JSONDecoder().raw_decode(script, match.end())
        except ValueError:
            continue
        if isinstance(item, dict):
            start, target = item.get("START_PAGE"), item.get("END_PAGE")
            if all(isinstance(value, str) and value.strip() for value in (start, target)):
                return start, target
    return None


async def fetch_route(day: date) -> tuple[str, str] | None:
    async def read(session, path, limit):
        chunks = []
        async with session.get(ORIGIN + path, allow_redirects=False) as response:
            if response.status != 200:
                return ""
            size = 0
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                if size > limit:
                    return ""
                chunks.append(chunk)
        return b"".join(chunks).decode()

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as session:
            visited = set()
            for path in ("/", f"/archive/{day.isoformat()}"):
                parser = Scripts()
                parser.feed(await read(session, path, 256_000))
                if parser.app and parser.app not in visited:
                    visited.add(parser.app)
                    route = parse_route(await read(session, parser.app, 3_000_000), day)
                    if route:
                        return route
    except (aiohttp.ClientError, TimeoutError, ValueError):
        pass
    return None
