import os
from datetime import UTC, datetime, timedelta

from daily_games.dashboard import DashboardSettings, backup_status, observed_health
from daily_games.health import HealthReporter, health_path, read_health


def test_health_record_matches_current_process_and_rejects_stale_pid(tmp_path):
    database = tmp_path / "test.sqlite3"
    reporter = HealthReporter(database)
    now = datetime.now(UTC)
    reporter.record(
        gateway="connected",
        gateway_at=now.isoformat(),
        scheduler_at=now.isoformat(),
        scheduler_errors=0,
        last_final_post_at=now.isoformat(),
        last_final_post_day="2026-11-15",
        last_final_post_server_id="100",
    )
    path = health_path(database)
    assert path.stat().st_mode & 0o777 == 0o600
    assert read_health(path)["gateway"] == "connected"
    settings = DashboardSettings(database, tmp_path)
    running = {"state": "running", "processes": [{"pid": str(os.getpid())}]}
    data = observed_health(settings, running, now)
    assert data["gateway"] == "connected"
    assert data["scheduler_state"] == "recent"
    assert data["last_final_post_day"] == "2026-11-15"
    old_process = {"state": "running", "processes": [{"pid": "999999999"}]}
    assert observed_health(settings, old_process, now)["gateway"] == "not_measured"
    assert (
        observed_health(settings, {"state": "not_detected", "processes": []}, now)["gateway"]
        == "unavailable"
    )
    reporter.state["updated_at"] = (now - timedelta(minutes=3)).isoformat()
    reporter.state["scheduler_at"] = (now - timedelta(minutes=3)).isoformat()
    from daily_games.health import write_health

    write_health(path, reporter.state)
    stale = observed_health(settings, running, now)
    assert stale["gateway"] == "stale"
    assert stale["scheduler_state"] == "stale"


def test_backup_times_come_only_from_verified_archive_and_success_marker(tmp_path):
    database = tmp_path / "data" / "test.sqlite3"
    database.parent.mkdir()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    settings = DashboardSettings(database, tmp_path, backup_dir)
    assert backup_status(settings) == {"local_at": None, "offsite_at": None}
    (backup_dir / "partial.tar.gz.enc").write_bytes(b"ignored")
    archive = backup_dir / "daily-podium-20261115-000000.tar.gz.enc"
    archive.write_bytes(b"fixture")
    (database.parent / "offsite-last-success.txt").write_text("2026-11-15T04:00:00Z\n")
    status = backup_status(settings)
    assert status["local_at"] is not None
    assert status["offsite_at"] == "2026-11-15T04:00:00+00:00"
