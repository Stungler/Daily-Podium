from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class Game(StrEnum):
    TIMEGUESSR = "timeguessr"
    SIZE_IT_UP = "sizeitup"
    METAZOOA = "metazooa"


GAME_TITLES = {
    Game.TIMEGUESSR: "TimeGuessr",
    Game.SIZE_IT_UP: "Size It Up",
    Game.METAZOOA: "Metazooa",
}


@dataclass(frozen=True)
class ParsedResult:
    game: Game
    puzzle: str
    score: int | None
    solved: bool = True


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


@dataclass(frozen=True)
class ServerConfig:
    guild_id: int
    input_channel_id: int
    output_channel_id: int
    timezone: str
    cutoff: str
    scoring: str
    started_day: date
