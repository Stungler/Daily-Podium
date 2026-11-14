import io
import os
import subprocess
import tarfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from daily_games.backup import make_backup, verify_archive
from daily_games.presentation import make_snapshot
from daily_games.storage import Store


def archive_members(path: Path, password: Path) -> dict[str, bytes]:
    result = subprocess.run(
        [
            "openssl",
            "enc",
            "-d",
            "-aes-256-cbc",
            "-pbkdf2",
            "-iter",
            "200000",
            "-pass",
            f"file:{password}",
            "-in",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:gz") as archive:
        return {item.name: archive.extractfile(item).read() for item in archive}


def test_backup_live_wal_and_restore_with_public_link_key(tmp_path, config):
    root = tmp_path / "checkout"
    data = root / "data"
    data.mkdir(parents=True)
    path = data / "daily-games.sqlite3"
    store = Store(path)
    store.configure(config, actor_id=1)
    day = date(2026, 11, 13)
    store.freeze(100, day, 300, make_snapshot([], day, config))
    (root / ".env").write_text("DISCORD_BOT_TOKEN=private-example\n")
    (data / "public-links.key").write_bytes(b"k" * 32)
    (data / "public-dashboard.json").write_text('{"base_url":"https://example.test"}')
    password = data / ".backup-password"
    password.write_bytes(b"p" * 48)
    backup_dir = tmp_path / "backups"

    archive = make_backup(path, backup_dir, password)
    assert archive.stat().st_mode & 0o777 == 0o600
    assert b"DISCORD_BOT_TOKEN" not in archive.read_bytes()
    assert b"public-links.key" not in archive.read_bytes()
    members = archive_members(archive, password)
    assert set(members) == {
        "daily-games.sqlite3",
        ".env",
        "data/public-links.key",
        "data/public-dashboard.json",
        "manifest.json",
    }
    assert members["data/public-links.key"] == b"k" * 32
    restored = tmp_path / "restored.sqlite3"
    restored.write_bytes(members["daily-games.sqlite3"])
    reopened = Store(restored)
    try:
        assert reopened.board(100, day) is not None
    finally:
        reopened.close()
    store.close()


def test_retention_only_removes_old_matching_archives(tmp_path, config):
    root = tmp_path / "checkout" / "data"
    root.mkdir(parents=True)
    database = root / "daily-games.sqlite3"
    store = Store(database)
    try:
        store.configure(config, actor_id=1)
    finally:
        store.close()
    password = root / ".backup-password"
    password.write_bytes(b"p" * 48)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old = backup_dir / "daily-podium-old.tar.gz.enc"
    unrelated = backup_dir / "manual-backup.enc"
    old.write_bytes(b"old")
    unrelated.write_bytes(b"unrelated")
    timestamp = (datetime.now(UTC) - timedelta(days=40)).timestamp()
    os.utime(old, (timestamp, timestamp))
    os.utime(unrelated, (timestamp, timestamp))

    new = make_backup(database, backup_dir, password, keep_days=30)
    assert new.exists()
    assert not old.exists()
    assert unrelated.exists()


def test_corrupt_archive_fails_verification(tmp_path, config):
    root = tmp_path / "checkout" / "data"
    root.mkdir(parents=True)
    database = root / "daily-games.sqlite3"
    store = Store(database)
    try:
        store.configure(config, actor_id=1)
    finally:
        store.close()
    password = root / ".backup-password"
    password.write_bytes(b"p" * 48)
    archive = make_backup(database, tmp_path / "backups", password)
    archive.write_bytes(archive.read_bytes()[:100])
    with pytest.raises((ValueError, tarfile.TarError, EOFError, OSError)):
        verify_archive(archive, password)
