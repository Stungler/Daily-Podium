from datetime import UTC, date, datetime

from daily_games.bot import status_embed
from daily_games.timeguessr import parse_current_puzzle, parse_next_reset


def test_live_countdown_timestamp_and_expired_fallback():
    page = "<script>const countdownEndsAt = 1794725999517;</script>"
    before = datetime(2026, 11, 15, 0, tzinfo=UTC)
    assert parse_next_reset(page, before) == datetime.fromtimestamp(1794725999517 / 1000, UTC)
    assert parse_next_reset(page, datetime(2026, 11, 15, 8, tzinfo=UTC)) is None
    assert parse_next_reset("No countdown on this page", before) is None


def test_current_daily_puzzle_uses_official_play_data():
    page = 'data:{mode:"daily",archive:null,playArray:[{No:"1264",DailyId:"..."}]}'
    assert parse_current_puzzle(page) == 1264
    assert parse_current_puzzle("No daily game available") is None


def test_status_uses_live_reset_and_has_safe_fallback(store, config):
    reset = datetime(2026, 11, 15, 7, tzinfo=UTC)
    day = date(2026, 11, 15)
    live = status_embed(store, config, day, reset)
    unavailable = status_embed(store, config, day, None)

    assert f"<t:{int(reset.timestamp())}:f>" in live.description
    assert "TimeGuessr: expected `1264`" in live.description
    assert "Check its timer" in unavailable.description
