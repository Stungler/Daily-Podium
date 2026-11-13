import logging
import os
from pathlib import Path

import discord
from dotenv import load_dotenv
from filelock import FileLock, Timeout

from .bot import DailyGamesBot
from .storage import Store


def main():
    load_dotenv()
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Set DISCORD_BOT_TOKEN in your local .env file before starting the bot.")
    database = Path(os.getenv("DATABASE_PATH", "data/daily-games.sqlite3")).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    guild = os.getenv("DEV_GUILD_ID", "").strip()
    try:
        guild_id = int(guild) if guild else None
    except ValueError as exc:
        raise SystemExit("DEV_GUILD_ID must be a numeric Discord server ID or empty.") from exc
    try:
        with FileLock(str(database) + ".lock", timeout=0):
            store = Store(database)
            try:
                bot = DailyGamesBot(store, guild_id)
                bot.run(token, log_handler=None)
            finally:
                store.close()
    except Timeout:
        raise SystemExit("Another bot process is already using this database.") from None
    except discord.LoginFailure:
        raise SystemExit(
            "Discord rejected the bot token. Check or regenerate it in the portal."
        ) from None
    except discord.PrivilegedIntentsRequired:
        raise SystemExit("Enable Message Content Intent under Developer Portal > Bot.") from None


if __name__ == "__main__":
    main()
