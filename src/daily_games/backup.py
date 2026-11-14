"""Encrypted, verified backups of a live Daily Podium SQLite database."""

import argparse
import fcntl
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ITERATIONS = "200000"
ARCHIVE_PREFIX = "daily-podium-"
ARCHIVE_SUFFIX = ".tar.gz.enc"
OPTIONAL_FILES = (".env", "data/public-links.key", "data/public-dashboard.json")


def digest(path: Path) -> str:
    hash_value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hash_value.update(block)
    return hash_value.hexdigest()


def check_database(path: Path):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as database:
        result = database.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise ValueError(f"SQLite integrity check failed: {result}")
        database.execute("SELECT 1 FROM boards LIMIT 1").fetchone()


def add_bytes(archive: tarfile.TarFile, name: str, data: bytes):
    item = tarfile.TarInfo(name)
    item.size = len(data)
    item.mode = 0o600
    archive.addfile(item, io.BytesIO(data))


def openssl_args(password_file: Path, *, decrypt: bool, path: Path) -> list[str]:
    args = [
        "openssl",
        "enc",
        "-d" if decrypt else "-e",
        "-aes-256-cbc",
        "-pbkdf2",
        "-iter",
        ITERATIONS,
        "-pass",
        f"file:{password_file}",
    ]
    args.extend(["-in" if decrypt else "-out", str(path)])
    return args


def verify_archive(archive_path: Path, password_file: Path):
    """Decrypt and restore into scratch space, then verify every file and SQLite."""
    with tempfile.TemporaryDirectory(prefix="daily-podium-restore-") as scratch_name:
        scratch = Path(scratch_name)
        process = subprocess.Popen(
            openssl_args(password_file, decrypt=True, path=archive_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        files = {}
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|gz") as archive:
                for item in archive:
                    if (
                        not item.isfile()
                        or item.name
                        not in {"manifest.json", "daily-games.sqlite3", *OPTIONAL_FILES}
                        or item.name in files
                    ):
                        raise ValueError("Unexpected or duplicate backup archive member.")
                    destination = scratch / item.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(item)
                    assert source is not None
                    with destination.open("wb") as output:
                        while block := source.read(1024 * 1024):
                            output.write(block)
                    files[item.name] = destination
        finally:
            process.stdout.close()
            errors = process.stderr.read()
            code = process.wait()
            process.stderr.close()
        if code != 0:
            raise ValueError(f"Backup decryption failed: {errors.decode(errors='replace').strip()}")
        if "manifest.json" not in files or "daily-games.sqlite3" not in files:
            raise ValueError("Backup archive is incomplete.")
        manifest = json.loads(files.pop("manifest.json").read_text())
        if manifest.get("version") != 1 or set(manifest.get("sha256", {})) != set(files):
            raise ValueError("Backup manifest does not match archive contents.")
        for name, path in files.items():
            if digest(path) != manifest["sha256"][name]:
                raise ValueError(f"Backup checksum mismatch: {name}")
        check_database(files["daily-games.sqlite3"])


def make_backup(database_path: Path, backup_dir: Path, password_file: Path, keep_days: int = 30):
    database_path = database_path.resolve()
    backup_dir = backup_dir.resolve()
    password_file = password_file.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {database_path}")
    if not password_file.is_file() or password_file.stat().st_size < 32:
        raise ValueError("Backup password file is missing or too short.")
    if keep_days < 1:
        raise ValueError("Backup retention must be at least one day.")
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_dir, 0o700)
    lock_path = backup_dir / ".backup.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix="daily-podium-snapshot-") as scratch_name:
            snapshot = Path(scratch_name) / "daily-games.sqlite3"
            with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as source:
                with sqlite3.connect(snapshot) as target:
                    source.backup(target)
            snapshot.chmod(0o600)
            check_database(snapshot)
            root = database_path.parent.parent
            paths = {"daily-games.sqlite3": snapshot}
            extra_content = {}
            for name in OPTIONAL_FILES:
                candidate = root / name
                if candidate.is_file():
                    extra_content[name] = candidate.read_bytes()
            manifest = {
                "version": 1,
                "sha256": {
                    "daily-games.sqlite3": digest(snapshot),
                    **{
                        name: hashlib.sha256(data).hexdigest()
                        for name, data in extra_content.items()
                    },
                },
            }
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            archive_path = backup_dir / f"{ARCHIVE_PREFIX}{stamp}{ARCHIVE_SUFFIX}"
            if archive_path.exists():
                raise FileExistsError(f"A backup already exists for this second: {archive_path}")
            partial = backup_dir / f".{archive_path.name}.{os.getpid()}.partial"
            try:
                process = subprocess.Popen(
                    openssl_args(password_file, decrypt=False, path=partial),
                    stdin=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                try:
                    with tarfile.open(fileobj=process.stdin, mode="w|gz") as archive:
                        for name, path in paths.items():
                            archive.add(path, arcname=name, recursive=False)
                        for name, data in extra_content.items():
                            add_bytes(archive, name, data)
                        add_bytes(archive, "manifest.json", json.dumps(manifest).encode())
                finally:
                    process.stdin.close()
                    errors = process.stderr.read()
                    code = process.wait()
                    process.stderr.close()
                if code != 0:
                    detail = errors.decode(errors="replace")
                    raise RuntimeError(f"Backup encryption failed: {detail}")
                verify_archive(partial, password_file)
                os.chmod(partial, 0o600)
                partial.replace(archive_path)
            finally:
                partial.unlink(missing_ok=True)
            cutoff = datetime.now() - timedelta(days=keep_days)
            for old in backup_dir.glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}"):
                if old != archive_path and datetime.fromtimestamp(old.stat().st_mtime) < cutoff:
                    old.unlink()
            return archive_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--keep-days", type=int, default=30)
    args = parser.parse_args()
    path = make_backup(args.database, args.backup_dir, args.password_file, args.keep_days)
    print(f"Verified encrypted backup: {path}")


if __name__ == "__main__":
    main()
