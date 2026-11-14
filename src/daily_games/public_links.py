"""Stable public URLs and local display names; no Discord IDs in public URLs."""

import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from filelock import FileLock

log = logging.getLogger(__name__)


def ensure_key(database: Path) -> bytes:
    path = database.resolve().parent / "public-links.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        key = path.read_bytes()
    else:
        key = secrets.token_bytes(32)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
    if len(key) != 32:
        raise ValueError("Invalid public link key; restore its backup rather than replacing it.")
    return key


def server_slug(key: bytes, guild_id: int) -> str:
    return hmac.new(key, str(guild_id).encode(), hashlib.sha256).hexdigest()[:24]


def read_config(database: Path) -> dict:
    try:
        data = json.loads((database.resolve().parent / "public-dashboard.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def update_config(database: Path, **updates):
    path = database.resolve().parent / "public-dashboard.json"
    with FileLock(str(path) + ".lock", timeout=2):
        config = read_config(database)
        names = updates.pop("server_names", {})
        config.update(updates)
        config["server_names"] = {**config.get("server_names", {}), **names}
        descriptor, temporary = tempfile.mkstemp(prefix=".public-dashboard-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(config, handle)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def configure(database: Path, base_url: str):
    parts = urlsplit(base_url)
    if parts.scheme != "https" or not parts.netloc or parts.path not in {"", "/"}:
        raise ValueError("Public base URL must be an HTTPS origin without a path.")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError("Public base URL must not contain credentials, query, or fragment.")
    ensure_key(database)
    update_config(database, base_url=base_url.rstrip("/"))


def remember_server(database: Path, guild_id: int, name: str):
    config = read_config(database)
    if config.get("base_url") and config.get("server_names", {}).get(str(guild_id)) != name:
        update_config(database, server_names={str(guild_id): name})


def leaderboard_links(database: Path, guild_id: int, name: str, day: str, game: str) -> dict | None:
    """Fail open for Discord commands if optional dashboard configuration is missing."""
    try:
        config = read_config(database)
        base = config.get("base_url")
        key_path = database.resolve().parent / "public-links.key"
        if not base or not key_path.is_file():
            return None
        key = key_path.read_bytes()
        if len(key) != 32:
            return None
        remember_server(database, guild_id, name)
        query = urlencode({"day": day, "game": game})
        return {
            "server": f"{base}/s/{server_slug(key, guild_id)}?{query}",
            "global": f"{base}/global?{query}",
        }
    except (OSError, ValueError, TimeoutError):
        log.warning("Public dashboard link unavailable; leaderboard remains available.")
        return None
