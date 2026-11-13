from unittest.mock import MagicMock

import pytest

from daily_games import __main__ as entrypoint
from daily_games.bot import CommandSyncError


def test_command_sync_failure_exits_cleanly_and_closes_database(monkeypatch, tmp_path):
    monkeypatch.setattr(entrypoint, "load_dotenv", lambda: None)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DEV_GUILD_ID", "100")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    store = MagicMock()
    bot = MagicMock()
    bot.run.side_effect = CommandSyncError("Check DEV_GUILD_ID and install the bot.")
    monkeypatch.setattr(entrypoint, "Store", MagicMock(return_value=store))
    monkeypatch.setattr(entrypoint, "DailyGamesBot", MagicMock(return_value=bot))

    with pytest.raises(SystemExit, match="Check DEV_GUILD_ID") as caught:
        entrypoint.main()

    assert caught.value.__suppress_context__
    store.close.assert_called_once_with()
