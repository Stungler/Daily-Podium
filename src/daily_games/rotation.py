"""Resolve the lineup for a competition date, including historical overrides."""

import json
import sqlite3
from bisect import bisect_right
from datetime import date

from .models import DEFAULT_GAMES, Game

Lineup = tuple[Game, ...]


def validate_lineup(values) -> Lineup:
    games = tuple(Game(value) for value in values)
    if len(games) != 3 or len(set(games)) != 3:
        raise ValueError("Choose exactly three distinct supported games.")
    return games


class Rotation:
    def __init__(self, db: sqlite3.Connection, guild_id: int = 0):
        self.dates: list[date] = []
        self.lineups: list[Lineup] = []
        self.inherits: list[bool] = []
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='game_rotations'"
        ).fetchone():
            return
        global_games, local_games = DEFAULT_GAMES, None
        for effective, scope, games in db.execute(
            "SELECT effective_day, guild_id, games FROM game_rotations "
            "WHERE guild_id IN (0, ?) ORDER BY effective_day, guild_id",
            (guild_id,),
        ):
            lineup = validate_lineup(json.loads(games)) if games is not None else None
            if scope == 0:
                global_games = lineup or DEFAULT_GAMES
            else:
                local_games = lineup
            day = date.fromisoformat(effective)
            if self.dates and self.dates[-1] == day:
                self.dates.pop()
                self.lineups.pop()
                self.inherits.pop()
            self.dates.append(day)
            self.lineups.append(local_games or global_games)
            self.inherits.append(local_games is None)

    def games_on(self, day: date) -> Lineup:
        index = bisect_right(self.dates, day) - 1
        return self.lineups[index] if index >= 0 else DEFAULT_GAMES

    def inherits_on(self, day: date) -> bool:
        index = bisect_right(self.dates, day) - 1
        return self.inherits[index] if index >= 0 else True

    def next_change(self, day: date) -> date | None:
        games, inherits = self.games_on(day), self.inherits_on(day)
        for index in range(bisect_right(self.dates, day), len(self.dates)):
            if (self.lineups[index], self.inherits[index]) != (games, inherits):
                return self.dates[index]
        return None

    def includes_between(self, game: Game, start: date, end: date) -> bool:
        if start > end:
            return False
        if game in self.games_on(start):
            return True
        first, stop = bisect_right(self.dates, start), bisect_right(self.dates, end)
        return any(game in lineup for lineup in self.lineups[first:stop])

    def games_between(self, start: date, end: date) -> Lineup:
        first, stop = bisect_right(self.dates, start), bisect_right(self.dates, end)
        return tuple(
            dict.fromkeys(
                game
                for lineup in (self.games_on(start), *self.lineups[first:stop])
                for game in lineup
            )
        )
