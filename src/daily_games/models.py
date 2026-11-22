from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class Game(StrEnum):
    TIMEGUESSR = "timeguessr"
    SIZE_IT_UP = "sizeitup"
    METAZOOA = "metazooa"
    WIKI = "wikigame"
    KRILLION = "krillion"


DEFAULT_GAMES = (Game.TIMEGUESSR, Game.SIZE_IT_UP, Game.METAZOOA)


GAME_TITLES = {
    Game.TIMEGUESSR: "TimeGuessr",
    Game.SIZE_IT_UP: "Size It Up",
    Game.METAZOOA: "Metazooa",
    Game.WIKI: "The Wiki Game Daily",
    Game.KRILLION: "Krillion",
}

GAME_URLS = {
    Game.TIMEGUESSR: "https://timeguessr.com/play?mode=daily",
    Game.SIZE_IT_UP: "https://magnitudle.com/size-it-up",
    Game.METAZOOA: "https://metazooa.com/play/daily",
    Game.WIKI: "https://www.thewikigamedaily.com/",
    Game.KRILLION: "https://krillion.io/",
}


@dataclass(frozen=True)
class ParsedResult:
    game: Game
    puzzle: str
    score: int | None
    solved: bool = True
    elapsed_seconds: int | None = None


@dataclass(frozen=True)
class Submission:
    guild_id: int
    user_id: int
    display_name: str
    game: Game
    puzzle: str
    day: date
    score: int | None
    solved: bool
    message_id: int
    channel_id: int
    submitted_at: datetime
    elapsed_seconds: int | None = None


@dataclass(frozen=True)
class ServerConfig:
    guild_id: int
    input_channel_id: int
    output_channel_id: int
    timezone: str
    cutoff: str
    scoring: str
    started_day: date
    language: str = "en"
    announcement_style: str = "standard"
