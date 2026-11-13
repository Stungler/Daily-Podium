from datetime import UTC, date, datetime

import pytest

from daily_games.models import ServerConfig
from daily_games.storage import Store

TIMEGUESSR = """TimeGuessr #1262 — 18,419/50,000

1️⃣ 🏆6,291 · 📅 1y · 🌍 2476.7km
2️⃣ 🏆257 · 📅 62y · 🌍 6433.3km
3️⃣ 🏆3,696 · 📅 9y · 🌍 3991.3km
4️⃣ 🏆719 · 📅 30y · 🌍 7562.7km
5️⃣ 🏆7,456 · 📅 3y · 🌍 1153.1km

https://timeguessr.com"""

SIZEITUP = """Magnitudle — Size It Up
2026-11-13

Score: 298/500

🟥⬜⬜⬜⬜ 20
🟥🟥🟥🟥🟥 100
🟥🟥🟥⬜⬜ 62
🟥🟥🟥⬜⬜ 67
🟥🟥⬜⬜⬜ 49

https://magnitudle.com/size-it-up"""

METAZOOA = """🦍 Animal #1201 🐹
I figured it out in 2 guesses!
🟩🟩
🔥 2 | Avg. Guesses: 7

https://metazooa.com
#metazooa"""

FAILED_METAZOOA = """🦉 Animal #1201 🦟
I was stumped by today's game!
🟨🟨🟨🟨🟧🟥🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨🟨🟥🟨
🔥 0 | Avg. Guesses: 0

https://metazooa.com
#metazooa"""


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "test.sqlite3")
    yield instance
    instance.close()


@pytest.fixture
def config(store):
    cfg = ServerConfig(
        100, 200, 300, "Europe/Berlin", "00:00", "players_beaten", date(2026, 11, 13)
    )
    store.configure(cfg, actor_id=1)
    return cfg


@pytest.fixture
def message_time():
    return datetime(2026, 11, 13, 18, tzinfo=UTC)
