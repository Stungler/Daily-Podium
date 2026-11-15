"""Small local status records for the private operations dashboard."""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def health_path(database: Path) -> Path:
    return database.resolve().parent / "bot-health.json"


def write_health(path: Path, state: dict) -> None:
    """Replace the status atomically so a concurrent dashboard read stays valid."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".bot-health-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(state, handle)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class HealthReporter:
    def __init__(self, database: Path):
        self.path = health_path(database)
        self.state = {"pid": os.getpid(), "gateway": "starting", "scheduler_errors": 0}
        try:
            previous = json.loads(self.path.read_text())
            if isinstance(previous, dict):
                for field in (
                    "last_final_post_at",
                    "last_final_post_day",
                    "last_final_post_server_id",
                ):
                    if field in previous:
                        self.state[field] = previous[field]
        except (OSError, ValueError):
            pass

    def record(self, **changes) -> None:
        self.state.update(changes)
        self.state["updated_at"] = datetime.now(UTC).isoformat()
        write_health(self.path, self.state)


def read_health(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None
