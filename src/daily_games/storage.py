"""Small, indexed SQLite operations for a single bot process."""

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from .models import Game, ServerConfig, Submission


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_version VALUES (1);
            CREATE TABLE IF NOT EXISTS servers (
                guild_id INTEGER PRIMARY KEY,
                input_channel_id INTEGER NOT NULL,
                output_channel_id INTEGER NOT NULL,
                timezone TEXT NOT NULL,
                cutoff TEXT NOT NULL,
                scoring TEXT NOT NULL,
                started_day TEXT NOT NULL,
                cursor INTEGER
            );
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL REFERENCES servers(guild_id),
                user_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                game TEXT NOT NULL,
                puzzle TEXT NOT NULL,
                day TEXT NOT NULL,
                score INTEGER,
                solved INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                submitted_at TEXT NOT NULL,
                voided INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS submission_puzzle
                ON submissions(guild_id, user_id, game, puzzle) WHERE voided=0;
            CREATE UNIQUE INDEX IF NOT EXISTS submission_day
                ON submissions(guild_id, user_id, game, day) WHERE voided=0;
            CREATE INDEX IF NOT EXISTS submissions_board ON submissions(guild_id, day);
            CREATE TABLE IF NOT EXISTS boards (
                guild_id INTEGER NOT NULL REFERENCES servers(guild_id),
                day TEXT NOT NULL,
                output_channel_id INTEGER NOT NULL,
                snapshot TEXT NOT NULL,
                attempted_at TEXT,
                message_id INTEGER,
                PRIMARY KEY(guild_id, day)
            );
            CREATE TABLE IF NOT EXISTS puzzle_dates (
                guild_id INTEGER NOT NULL REFERENCES servers(guild_id),
                game TEXT NOT NULL,
                puzzle TEXT NOT NULL,
                day TEXT NOT NULL,
                PRIMARY KEY(guild_id, game, puzzle),
                UNIQUE(guild_id, game, day)
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                actor_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    @staticmethod
    def _config(row: sqlite3.Row) -> ServerConfig:
        return ServerConfig(
            row["guild_id"],
            row["input_channel_id"],
            row["output_channel_id"],
            row["timezone"],
            row["cutoff"],
            row["scoring"],
            date.fromisoformat(row["started_day"]),
        )

    def config(self, guild_id: int) -> ServerConfig | None:
        row = self.db.execute("SELECT * FROM servers WHERE guild_id=?", (guild_id,)).fetchone()
        return self._config(row) if row else None

    def configs(self) -> list[ServerConfig]:
        return [self._config(row) for row in self.db.execute("SELECT * FROM servers")]

    def configure(self, config: ServerConfig, actor_id: int):
        old = self.config(config.guild_id)
        if old:
            timing_changed = (old.timezone, old.cutoff, old.scoring) != (
                config.timezone,
                config.cutoff,
                config.scoring,
            )
            used = self.db.execute(
                "SELECT 1 FROM submissions WHERE guild_id=? LIMIT 1", (config.guild_id,)
            ).fetchone()
            if timing_changed and (used or self.has_boards(config.guild_id)):
                raise ValueError(
                    "Timezone, cutoff and scoring are locked after competition data exists. "
                    "This protects existing standings; channel settings can still change."
                )
        with self.db:
            self.db.execute(
                """
                INSERT INTO servers (
                    guild_id, input_channel_id, output_channel_id, timezone, cutoff, scoring,
                    started_day
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    cursor=CASE WHEN input_channel_id!=excluded.input_channel_id
                        THEN NULL ELSE cursor END,
                    input_channel_id=excluded.input_channel_id,
                    output_channel_id=excluded.output_channel_id,
                    timezone=excluded.timezone, cutoff=excluded.cutoff, scoring=excluded.scoring,
                    started_day=excluded.started_day
            """,
                (
                    config.guild_id,
                    config.input_channel_id,
                    config.output_channel_id,
                    config.timezone,
                    config.cutoff,
                    config.scoring,
                    (old.started_day if old else config.started_day).isoformat(),
                ),
            )
            self._audit(config.guild_id, actor_id, "configure", str(config))

    def has_boards(self, guild_id: int) -> bool:
        return (
            self.db.execute("SELECT 1 FROM boards WHERE guild_id=? LIMIT 1", (guild_id,)).fetchone()
            is not None
        )

    def insert(self, result: Submission) -> bool:
        # A voided message must not resurrect when history is replayed.
        known = self.db.execute(
            "SELECT 1 FROM submissions WHERE guild_id=? AND user_id=? AND game=? AND message_id=?",
            (result.guild_id, result.user_id, result.game.value, result.message_id),
        ).fetchone()
        if known:
            return False
        with self.db:
            cursor = self.db.execute(
                """
                INSERT OR IGNORE INTO submissions (
                    guild_id,user_id,display_name,game,puzzle,day,score,solved,message_id,
                    channel_id,submitted_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
                (
                    result.guild_id,
                    result.user_id,
                    result.display_name,
                    result.game.value,
                    result.puzzle,
                    result.day.isoformat(),
                    result.score,
                    int(result.solved),
                    result.message_id,
                    result.channel_id,
                    result.submitted_at.isoformat(),
                ),
            )
        return cursor.rowcount == 1

    def results(self, guild_id: int, day: date) -> list[Submission]:
        rows = self.db.execute(
            "SELECT * FROM submissions WHERE guild_id=? AND day=? AND voided=0 "
            "ORDER BY submitted_at, message_id",
            (guild_id, day.isoformat()),
        )
        return [
            Submission(
                row["guild_id"],
                row["user_id"],
                row["display_name"],
                Game(row["game"]),
                row["puzzle"],
                date.fromisoformat(row["day"]),
                row["score"],
                bool(row["solved"]),
                row["message_id"],
                row["channel_id"],
                datetime.fromisoformat(row["submitted_at"]),
            )
            for row in rows
        ]

    def board(self, guild_id: int, day: date) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM boards WHERE guild_id=? AND day=?", (guild_id, day.isoformat())
        ).fetchone()

    def freeze(self, guild_id: int, day: date, channel_id: int, snapshot: dict):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO boards(guild_id,day,output_channel_id,snapshot) "
                "VALUES (?,?,?,?)",
                (guild_id, day.isoformat(), channel_id, json.dumps(snapshot)),
            )

    def mark_attempt(self, guild_id: int, day: date, now: datetime):
        with self.db:
            self.db.execute(
                "UPDATE boards SET attempted_at=? WHERE guild_id=? AND day=?",
                (now.isoformat(), guild_id, day.isoformat()),
            )

    def mark_posted(self, guild_id: int, day: date, message_id: int):
        with self.db:
            self.db.execute(
                "UPDATE boards SET message_id=? WHERE guild_id=? AND day=?",
                (message_id, guild_id, day.isoformat()),
            )

    def cursor(self, guild_id: int) -> int | None:
        row = self.db.execute("SELECT cursor FROM servers WHERE guild_id=?", (guild_id,)).fetchone()
        return row[0] if row else None

    def advance_cursor(self, guild_id: int, message_id: int):
        with self.db:
            self.db.execute(
                "UPDATE servers SET cursor=MAX(COALESCE(cursor,0),?) WHERE guild_id=?",
                (message_id, guild_id),
            )

    def override(self, guild_id: int, game: Game, puzzle: str) -> date | None:
        row = self.db.execute(
            "SELECT day FROM puzzle_dates WHERE guild_id=? AND game=? AND puzzle=?",
            (guild_id, game.value, puzzle),
        ).fetchone()
        return date.fromisoformat(row[0]) if row else None

    def puzzle_for_day(self, guild_id: int, game: Game, day: date) -> str | None:
        row = self.db.execute(
            "SELECT puzzle FROM puzzle_dates WHERE guild_id=? AND game=? AND day=?",
            (guild_id, game.value, day.isoformat()),
        ).fetchone()
        return row[0] if row else None

    def set_override(self, guild_id: int, game: Game, puzzle: str, day: date, actor_id: int):
        existing = self.override(guild_id, game, puzzle)
        if existing == day:
            return
        if self.board(guild_id, day) or (existing and self.board(guild_id, existing)):
            raise ValueError("A finalized day's puzzle mapping cannot change.")
        used = self.db.execute(
            "SELECT 1 FROM submissions WHERE guild_id=? AND game=? AND (puzzle=? OR day=?) LIMIT 1",
            (guild_id, game.value, puzzle, day.isoformat()),
        ).fetchone()
        if used:
            raise ValueError("Cannot remap a puzzle or day with recorded submissions.")
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO puzzle_dates VALUES (?,?,?,?) "
                    "ON CONFLICT(guild_id,game,puzzle) DO UPDATE SET day=excluded.day",
                    (guild_id, game.value, puzzle, day.isoformat()),
                )
                self._audit(guild_id, actor_id, "puzzle_date", f"{game}:{puzzle} -> {day}")
        except sqlite3.IntegrityError as exc:
            raise ValueError("Another puzzle is already explicitly assigned to that day.") from exc

    def void(self, guild_id: int, user_id: int, game: Game, day: date, actor_id: int) -> bool:
        if self.board(guild_id, day):
            raise ValueError("A finalized day cannot be changed.")
        with self.db:
            cursor = self.db.execute(
                "UPDATE submissions SET voided=1 WHERE guild_id=? AND user_id=? "
                "AND game=? AND day=? AND voided=0",
                (guild_id, user_id, game.value, day.isoformat()),
            )
            if cursor.rowcount:
                self._audit(guild_id, actor_id, "void", f"user={user_id} game={game} day={day}")
        return cursor.rowcount > 0

    def _audit(self, guild_id: int, actor_id: int, action: str, detail: str):
        self.db.execute(
            "INSERT INTO audit(guild_id,actor_id,action,detail,created_at) VALUES (?,?,?,?,?)",
            (guild_id, actor_id, action, detail, datetime.now(UTC).isoformat()),
        )
