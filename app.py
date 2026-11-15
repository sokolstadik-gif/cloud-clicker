from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import re
import secrets
import sqlite3

import psycopg
import time
import uuid
import urllib.error
import urllib.request
from urllib.parse import parse_qsl
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, g, jsonify, render_template, request, session

from new_year_rules import (
    ERA_NAMES, era_unlock_progress, winter_calendar_status, winter_ticket_reward,
)
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DB_PATH = Path(os.environ.get("DATABASE_PATH", str(BASE_DIR / "game.db")))
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
REQUIRE_TELEGRAM_AUTH = os.environ.get("REQUIRE_TELEGRAM_AUTH", "0") == "1"
TELEGRAM_INIT_DATA_MAX_AGE = int(os.environ.get("TELEGRAM_INIT_DATA_MAX_AGE", "86400"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")

# Telegram Stars (XTR) are the supported in-app payment currency for digital goods.
STAR_PACKAGES = {
    "starter": {"title": "Cloud Starter", "description": "10 000 Cloudcoin для Cloud Clicker", "stars": 25, "coins": 10_000},
    "pro": {"title": "Cloud Pro", "description": "60 000 Cloudcoin для Cloud Clicker", "stars": 100, "coins": 60_000},
    "vip": {"title": "Cloud VIP", "description": "200 000 Cloudcoin для Cloud Clicker", "stars": 250, "coins": 200_000},
}

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-this-secret-before-deploying"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
    MAX_CONTENT_LENGTH=16 * 1024,
)

# Original Cloud Clicker mechanics inspired by the broad clicker loop of
# tap -> buy upgrade cards -> earn passively -> complete daily challenges.
UPGRADES = {
    "tap": {
        "title": "Импульсный кликер",
        "description": "+1 Cloudcoin за каждое нажатие",
        "base_cost": 25,
        "growth": 1.60,
        "icon": "👆",
        "category": "Облачные технологии",
    },
    "auto": {
        "title": "Ветряная турбина",
        "description": "+1 Cloudcoin в секунду",
        "base_cost": 80,
        "growth": 1.72,
        "icon": "🌬️",
        "category": "Энергетика",
    },
    "energy": {
        "title": "Конденсатор грозы",
        "description": "+100 к энергии и +0,5 к восстановлению в секунду",
        "base_cost": 60,
        "growth": 1.55,
        "icon": "🔋",
        "category": "Энергетика",
    },
    "lab": {
        "title": "Лаборатория облаков",
        "description": "+2 Cloudcoin в секунду за уровень",
        "base_cost": 220,
        "growth": 1.78,
        "icon": "🧪",
        "category": "Исследования",
    },
    "radar": {
        "title": "Погодный радар",
        "description": "+5 Cloudcoin в секунду за уровень",
        "base_cost": 500,
        "growth": 1.84,
        "icon": "📡",
        "category": "Исследования",
    },
    "network": {
        "title": "Сеть высотных станций",
        "description": "+2 Cloudcoin за тап за уровень",
        "base_cost": 750,
        "growth": 1.90,
        "icon": "🛰️",
        "category": "Инфраструктура",
    },
}

LEVEL_COLUMNS = {
    "tap": "tap_level",
    "auto": "auto_level",
    "energy": "energy_level",
    "lab": "lab_level",
    "radar": "radar_level",
    "network": "network_level",
}

START_COINS = 0
START_ENERGY = 100
OFFLINE_CAP_SECONDS = 8 * 60 * 60
DAILY_REWARD = 250
TURBO_COST = 500
TURBO_SECONDS = 30
ENERGY_REFILL_COST = 250
COMBO_REWARD = 3_000
CIPHER_REWARD = 2_000

TAP_REWARDS = [
    {"taps": 50, "reward": 200},
    {"taps": 100, "reward": 400},
    {"taps": 250, "reward": 1_000},
    {"taps": 500, "reward": 2_000},
    {"taps": 1_000, "reward": 5_000},
    {"taps": 2_500, "reward": 15_000},
    {"taps": 5_000, "reward": 35_000},
    {"taps": 10_000, "reward": 80_000},
]

DAILY_QUESTS = {
    "tap_100": {"title": "Облачная разминка", "description": "Сделай 100 нажатий сегодня", "target": 100, "reward": 300, "icon": "☁️"},
    "upgrade_2": {"title": "Техно-пилот", "description": "Купи 2 карточки улучшений сегодня", "target": 2, "reward": 500, "icon": "🛠️"},
    "daily_bonus": {"title": "На связи", "description": "Забери ежедневный бонус", "target": 1, "reward": 250, "icon": "🎁"},
}

# A short rotating Morse puzzle. The page shows only the Morse sequence;
# the answer is checked on the server.
CIPHER_WORDS = [
    ("CLOUD", "-.-. .-.. --- ..- -.."),
    ("STORM", "... - --- .-. --"),
    ("WIND", ".-- .. -. -.."),
    ("RAIN", ".-. .- .. -."),
    ("SKY", "... -.- -.--"),
    ("SUN", "... ..- -."),
    ("AIR", ".- .. .-."),
]


class HybridRow(dict):
    """PostgreSQL row with both SQLite-style name and numeric indexing."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def hybrid_row_factory(cursor):
    def make_row(values):
        # Description is populated after execute(), not when the cursor is created.
        names = [column.name for column in cursor.description]
        return HybridRow(zip(names, values))

    return make_row


class PostgresCompatConnection:
    """Small SQLite-compatible adapter for the SQL used by this application."""

    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self.connection.__exit__(exc_type, exc_value, traceback)

    def close(self):
        self.connection.close()

    @staticmethod
    def translate(sql):
        statement = sql.strip()

        if re.match(r"(?is)^PRAGMA\s+(journal_mode|foreign_keys)", statement):
            return "SELECT 1"
        table_info = re.match(r"(?is)^PRAGMA\s+table_info\((\w+)\)", statement)
        if table_info:
            return (
                "SELECT ordinal_position - 1 AS cid, column_name AS name "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s "
                "ORDER BY ordinal_position"
            ), (table_info.group(1),)
        if re.match(r"(?is)^BEGIN\s+IMMEDIATE$", statement):
            # SQLite serialized writers globally. Lock one shared row to retain
            # that protection on PostgreSQL and prevent double-spending races.
            return "SELECT id FROM app_write_lock WHERE id = 1 FOR UPDATE"

        ignore_insert = bool(re.match(r"(?is)^INSERT\s+OR\s+IGNORE\s+INTO\b", statement))
        if ignore_insert:
            statement = re.sub(r"(?i)^INSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", statement)
            statement = statement.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

        statement = re.sub(
            r"(?i)MIN\(\s*\?\s*,\s*progress\s*\+\s*\?\s*\)",
            "LEAST(?, progress + ?)",
            statement,
        )
        return statement.replace("?", "%s")

    def execute(self, sql, params=()):
        translated = self.translate(sql)
        if isinstance(translated, tuple):
            query, query_params = translated
            return self.connection.execute(query, query_params)
        return self.connection.execute(translated, params or ())

    def executemany(self, sql, params_seq):
        translated = self.translate(sql)
        if isinstance(translated, tuple):
            raise ValueError("PRAGMA table_info does not support executemany")
        return self.connection.cursor().executemany(translated, params_seq)


def get_db():
    if DATABASE_URL:
        dsn = DATABASE_URL
        if dsn.startswith("postgres://"):
            dsn = "postgresql://" + dsn[len("postgres://"):]
        connection = psycopg.connect(
            dsn,
            row_factory=hybrid_row_factory,
            connect_timeout=10,
        )
        return PostgresCompatConnection(connection)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_connection():
    conn = get_db()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with db_connection() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                id TEXT PRIMARY KEY,
                nickname TEXT NOT NULL DEFAULT 'Cloud',
                coins INTEGER NOT NULL DEFAULT 0,
                total_earned INTEGER NOT NULL DEFAULT 0,
                total_taps INTEGER NOT NULL DEFAULT 0,
                tap_level INTEGER NOT NULL DEFAULT 0,
                auto_level INTEGER NOT NULL DEFAULT 0,
                energy_level INTEGER NOT NULL DEFAULT 0,
                lab_level INTEGER NOT NULL DEFAULT 0,
                radar_level INTEGER NOT NULL DEFAULT 0,
                network_level INTEGER NOT NULL DEFAULT 0,
                energy REAL NOT NULL DEFAULT 100,
                passive_fraction REAL NOT NULL DEFAULT 0,
                turbo_until REAL NOT NULL DEFAULT 0,
                last_energy_at REAL NOT NULL,
                last_active_at REAL NOT NULL,
                last_daily_claim TEXT,
                created_at REAL NOT NULL
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(players)").fetchall()}
        migrations = {
            "passive_fraction": "REAL NOT NULL DEFAULT 0",
            "lab_level": "INTEGER NOT NULL DEFAULT 0",
            "radar_level": "INTEGER NOT NULL DEFAULT 0",
            "network_level": "INTEGER NOT NULL DEFAULT 0",
            "turbo_until": "REAL NOT NULL DEFAULT 0",
            "active_era": "INTEGER NOT NULL DEFAULT 1",
        }
        for column, definition in migrations.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE players ADD COLUMN {column} {definition}")
        conn.execute("""CREATE TABLE IF NOT EXISTS player_era_progress (
            player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            era INTEGER NOT NULL,
            balance INTEGER NOT NULL DEFAULT 0,
            earned INTEGER NOT NULL DEFAULT 0,
            taps INTEGER NOT NULL DEFAULT 0,
            upgrade_purchases INTEGER NOT NULL DEFAULT 0,
            levels_json TEXT NOT NULL DEFAULT '{}',
            last_balance INTEGER NOT NULL DEFAULT 0,
            last_taps INTEGER NOT NULL DEFAULT 0,
            last_upgrade_levels INTEGER NOT NULL DEFAULT 0,
            opened_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (player_id, era)
        )""")
        for player in conn.execute("SELECT * FROM players").fetchall():
            levels = {key: int(player[column]) for key, column in LEVEL_COLUMNS.items()}
            conn.execute(
                """INSERT OR IGNORE INTO player_era_progress
                   (player_id, era, balance, earned, taps, upgrade_purchases, levels_json,
                    last_balance, last_taps, last_upgrade_levels, opened_at, updated_at)
                   VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (player["id"], int(player["coins"]), int(player["total_earned"]),
                 int(player["total_taps"]), sum(levels.values()), json.dumps(levels),
                 int(player["coins"]), int(player["total_taps"]), sum(levels.values()),
                 float(player["created_at"]), time.time()),
            )
        conn.execute("""CREATE TABLE IF NOT EXISTS winter_event_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            event_start TEXT NOT NULL
        )""")
        conn.execute("INSERT OR IGNORE INTO winter_event_settings (id, event_start) VALUES (1, ?)", (current_utc_date(),))
        conn.execute("""CREATE TABLE IF NOT EXISTS winter_event_players (
            player_id TEXT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
            tickets INTEGER NOT NULL DEFAULT 0
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS winter_calendar_claims (
            player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            day_number INTEGER NOT NULL,
            reward INTEGER NOT NULL,
            claimed_at REAL NOT NULL,
            PRIMARY KEY (player_id, day_number)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS player_achievement_claims (
            player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            achievement_id TEXT NOT NULL,
            reward INTEGER NOT NULL,
            claimed_at REAL NOT NULL,
            PRIMARY KEY (player_id, achievement_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS referral_codes (
            player_id TEXT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
            code TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS referrals (
            invitee_id TEXT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
            inviter_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending',
            reward INTEGER NOT NULL DEFAULT 1000,
            created_at REAL NOT NULL,
            activated_at REAL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS player_gifts (
            id TEXT PRIMARY KEY,
            sender_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            receiver_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            gift_code TEXT NOT NULL,
            kind TEXT NOT NULL,
            rarity TEXT NOT NULL,
            effect TEXT,
            duration_seconds INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            activated_at REAL,
            expires_at REAL,
            status TEXT NOT NULL DEFAULT 'inventory',
            note TEXT NOT NULL DEFAULT ''
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_player_gifts_receiver ON player_gifts(receiver_id, status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_player_gifts_sender_date ON player_gifts(sender_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_inviter ON referrals(inviter_id, status)")
        conn.execute("UPDATE players SET nickname = 'Cloud' WHERE nickname = 'Хомяк'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_players_leaderboard ON players(total_earned DESC)")

        conn.execute(
            """CREATE TABLE IF NOT EXISTS tap_reward_claims (
                player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                milestone INTEGER NOT NULL,
                reward INTEGER NOT NULL,
                claimed_at REAL NOT NULL,
                PRIMARY KEY (player_id, milestone)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS daily_combo_progress (
                player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                combo_date TEXT NOT NULL,
                upgrade_id TEXT NOT NULL,
                purchased_at REAL NOT NULL,
                PRIMARY KEY (player_id, combo_date, upgrade_id)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS daily_combo_claims (
                player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                combo_date TEXT NOT NULL,
                claimed_at REAL NOT NULL,
                PRIMARY KEY (player_id, combo_date)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS daily_cipher_claims (
                player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                cipher_date TEXT NOT NULL,
                claimed_at REAL NOT NULL,
                PRIMARY KEY (player_id, cipher_date)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS daily_quest_progress (
                player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                quest_date TEXT NOT NULL,
                quest_id TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                claimed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (player_id, quest_date, quest_id)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS telegram_accounts (
                telegram_id TEXT PRIMARY KEY,
                player_id TEXT NOT NULL UNIQUE REFERENCES players(id) ON DELETE CASCADE,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at REAL NOT NULL,
                last_seen_at REAL NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS star_payments (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL UNIQUE,
                telegram_id TEXT NOT NULL,
                player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                package_id TEXT NOT NULL,
                stars_amount INTEGER NOT NULL,
                coins_amount INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                telegram_charge_id TEXT UNIQUE,
                created_at REAL NOT NULL,
                paid_at REAL
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_star_payments_status ON star_payments(status, created_at)")
        conn.execute("CREATE TABLE IF NOT EXISTS app_write_lock (id INTEGER PRIMARY KEY CHECK (id = 1))")
        conn.execute("INSERT OR IGNORE INTO app_write_lock (id) VALUES (1)")


def get_player_id() -> str:
    telegram_player_id = getattr(g, "telegram_player_id", None)
    if isinstance(telegram_player_id, str):
        return telegram_player_id
    player_id = session.get("player_id")
    if not isinstance(player_id, str) or len(player_id) != 32:
        player_id = uuid.uuid4().hex
        session["player_id"] = player_id
    return player_id


def validate_telegram_init_data(raw_init_data: str) -> dict[str, Any]:
    """Verify Telegram Mini App initData using the bot-token HMAC scheme."""
    if not BOT_TOKEN:
        raise ValueError("На сервере не задан BOT_TOKEN.")
    if not raw_init_data or len(raw_init_data) > 16_384:
        raise ValueError("Telegram initData отсутствует или имеет неверный размер.")
    fields = dict(parse_qsl(raw_init_data, keep_blank_values=True, strict_parsing=False))
    received_hash = fields.pop("hash", "")
    if not received_hash:
        raise ValueError("В initData отсутствует hash.")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(expected_hash, received_hash):
        raise ValueError("Подпись Telegram initData не прошла проверку.")
    try:
        auth_date = int(fields.get("auth_date", "0"))
        user = json.loads(fields.get("user", "{}"))
        telegram_id = int(user["id"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        raise ValueError("В initData нет корректного пользователя Telegram.")
    now = int(time.time())
    if auth_date <= 0 or auth_date > now + 60 or now - auth_date > TELEGRAM_INIT_DATA_MAX_AGE:
        raise ValueError("Данные авторизации Telegram устарели. Закрой и снова открой Mini App.")
    return {
        "id": telegram_id,
        "username": str(user.get("username") or "")[:64],
        "first_name": str(user.get("first_name") or "")[:128],
        "last_name": str(user.get("last_name") or "")[:128],
    }


def bind_telegram_user(user: dict[str, Any], guest_player_id: str | None = None) -> str:
    """Bind a verified Telegram ID to a persistent player row (preserving guest progress when possible)."""
    telegram_id = str(int(user["id"]))
    now = time.time()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT player_id FROM telegram_accounts WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if existing:
            player_id = str(existing["player_id"])
            ensure_player(conn, player_id)
            conn.execute(
                "UPDATE telegram_accounts SET username = ?, first_name = ?, last_name = ?, last_seen_at = ? WHERE telegram_id = ?",
                (user.get("username") or None, user.get("first_name") or None, user.get("last_name") or None, now, telegram_id),
            )
        else:
            candidate = None
            if isinstance(guest_player_id, str) and len(guest_player_id) == 32:
                player_exists = conn.execute("SELECT id FROM players WHERE id = ?", (guest_player_id,)).fetchone()
                already_linked = conn.execute("SELECT 1 FROM telegram_accounts WHERE player_id = ?", (guest_player_id,)).fetchone()
                if player_exists and not already_linked:
                    candidate = guest_player_id
            player_id = candidate or uuid.uuid4().hex
            ensure_player(conn, player_id)
            nickname = user.get("username") or user.get("first_name") or "Cloud"
            conn.execute("UPDATE players SET nickname = ? WHERE id = ? AND nickname = 'Cloud'", (str(nickname)[:18], player_id))
            conn.execute(
                "INSERT INTO telegram_accounts (telegram_id, player_id, username, first_name, last_name, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (telegram_id, player_id, user.get("username") or None, user.get("first_name") or None, user.get("last_name") or None, now, now),
            )
    return player_id


def telegram_api(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не настроен на сервере.")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Telegram Bot API ответил HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Не удалось связаться с Telegram Bot API: {exc}") from exc
    if not result.get("ok"):
        raise RuntimeError(str(result.get("description", "Неизвестная ошибка Telegram Bot API")))
    return result


def current_utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def ensure_player(conn: sqlite3.Connection, player_id: str) -> sqlite3.Row:
    now = time.time()
    conn.execute(
        """INSERT OR IGNORE INTO players
            (id, nickname, coins, total_earned, total_taps, tap_level, auto_level,
             energy_level, lab_level, radar_level, network_level, energy,
             passive_fraction, turbo_until, last_energy_at, last_active_at, created_at)
            VALUES (?, 'Cloud', ?, 0, 0, 0, 0, 0, 0, 0, 0, ?, 0, 0, ?, ?, ?)""",
        (player_id, START_COINS, START_ENERGY, now, now, now),
    )
    row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
    assert row is not None
    return row


def ensure_daily_quests(conn: sqlite3.Connection, player_id: str, day: str | None = None) -> None:
    day = day or current_utc_date()
    conn.executemany(
        "INSERT OR IGNORE INTO daily_quest_progress (player_id, quest_date, quest_id, progress, claimed) VALUES (?, ?, ?, 0, 0)",
        [(player_id, day, quest_id) for quest_id in DAILY_QUESTS],
    )


def bump_quest(conn: sqlite3.Connection, player_id: str, quest_id: str, amount: int, day: str | None = None) -> None:
    day = day or current_utc_date()
    ensure_daily_quests(conn, player_id, day)
    target = DAILY_QUESTS[quest_id]["target"]
    conn.execute(
        "UPDATE daily_quest_progress SET progress = MIN(?, progress + ?) WHERE player_id = ? AND quest_date = ? AND quest_id = ?",
        (target, max(0, int(amount)), player_id, day, quest_id),
    )


def combo_for_day(day: str | None = None) -> list[str]:
    day = day or current_utc_date()
    seed = int.from_bytes(hashlib.sha256(("cloud-combo:" + day).encode()).digest()[:8], "big")
    return random.Random(seed).sample(list(UPGRADES.keys()), 3)


def cipher_for_day(day: str | None = None) -> tuple[str, str]:
    day = day or current_utc_date()
    seed = int.from_bytes(hashlib.sha256(("cloud-cipher:" + day).encode()).digest()[:8], "big")
    return CIPHER_WORDS[seed % len(CIPHER_WORDS)]


def max_energy(row: sqlite3.Row) -> int:
    return START_ENERGY + int(row["energy_level"]) * 100


def energy_regen(row: sqlite3.Row) -> float:
    return 2.0 + int(row["energy_level"]) * 0.5


def tap_power(row: sqlite3.Row) -> int:
    return 1 + int(row["tap_level"]) + int(row["network_level"]) * 2


def passive_income(row: sqlite3.Row) -> int:
    return int(row["auto_level"]) + int(row["lab_level"]) * 2 + int(row["radar_level"]) * 5


def upgrade_cost(upgrade_id: str, level: int) -> int:
    item = UPGRADES[upgrade_id]
    return int(round(item["base_cost"] * (item["growth"] ** level)))


def sync_era_progress(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """Accumulate server-observed per-era deltas without altering lifetime totals."""
    era = int(row["active_era"])
    levels = {key: int(row[column]) for key, column in LEVEL_COLUMNS.items()}
    level_total = sum(levels.values())
    saved = conn.execute(
        "SELECT * FROM player_era_progress WHERE player_id = ? AND era = ?",
        (row["id"], era),
    ).fetchone()
    now = time.time()
    if saved is None:
        conn.execute(
            """INSERT INTO player_era_progress
               (player_id, era, balance, earned, taps, upgrade_purchases, levels_json,
                last_balance, last_taps, last_upgrade_levels, opened_at, updated_at)
               VALUES (?, ?, ?, 0, 0, 0, ?, ?, ?, ?, ?, ?)""",
            (row["id"], era, int(row["coins"]), json.dumps(levels), int(row["coins"]),
             int(row["total_taps"]), level_total, now, now),
        )
        saved = conn.execute(
            "SELECT * FROM player_era_progress WHERE player_id = ? AND era = ?",
            (row["id"], era),
        ).fetchone()
    balance = int(row["coins"])
    taps = int(row["total_taps"])
    earned = int(saved["earned"]) + max(0, balance - int(saved["last_balance"]))
    era_taps = int(saved["taps"]) + max(0, taps - int(saved["last_taps"]))
    purchases = int(saved["upgrade_purchases"]) + max(0, level_total - int(saved["last_upgrade_levels"]))
    conn.execute(
        """UPDATE player_era_progress SET balance = ?, earned = ?, taps = ?,
           upgrade_purchases = ?, levels_json = ?, last_balance = ?, last_taps = ?,
           last_upgrade_levels = ?, updated_at = ? WHERE player_id = ? AND era = ?""",
        (balance, earned, era_taps, purchases, json.dumps(levels), balance, taps,
         level_total, now, row["id"], era),
    )
    return {"era": era, "balance": balance, "earned": earned, "taps": era_taps,
            "upgrade_purchases": purchases, "levels": levels}


def get_era_overview(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    current = sync_era_progress(conn, row)
    saved = {}
    for item in conn.execute(
        "SELECT era, balance, earned, taps, upgrade_purchases, levels_json FROM player_era_progress WHERE player_id = ?",
        (row["id"],),
    ).fetchall():
        saved[int(item["era"])] = item
    unlock = era_unlock_progress(
        current["era"],
        era1_taps=int(saved.get(1)["taps"]) if saved.get(1) else 0,
        era2_upgrade_purchases=int(saved.get(2)["upgrade_purchases"]) if saved.get(2) else 0,
        era2_earned=int(saved.get(2)["earned"]) if saved.get(2) else 0,
        era3_earned=int(saved.get(3)["earned"]) if saved.get(3) else 0,
    )
    return {
        "current": current["era"],
        "name": ERA_NAMES[current["era"]][0],
        "currency": ERA_NAMES[current["era"]][1],
        "progress": unlock,
        "archive": [
            {"era": era, "name": ERA_NAMES[era][0], "currency": ERA_NAMES[era][1],
             "balance": int(saved[era]["balance"]), "earned": int(saved[era]["earned"]),
             "taps": int(saved[era]["taps"]),
             "upgrade_purchases": int(saved[era]["upgrade_purchases"]),
             "levels": json.loads(saved[era]["levels_json"]),
             "completed": era < current["era"]}
            for era in sorted(saved)
        ],
    }


def advance_player(conn: sqlite3.Connection, row: sqlite3.Row, now: float | None = None) -> sqlite3.Row:
    """Apply passive income and energy regeneration before each game action."""
    now = now if now is not None else time.time()
    gift_effects = active_gift_effects(conn, row["id"], now)
    energy_elapsed = max(0.0, min(now - float(row["last_energy_at"]), 24 * 60 * 60))
    energy = min(max_energy(row), float(row["energy"]) + energy_elapsed * energy_regen(row))
    active_elapsed = max(0.0, min(now - float(row["last_active_at"]), OFFLINE_CAP_SECONDS))
    passive_rate = passive_income(row) * (2 if gift_effects["passive_double"] else 1)
    passive_total = active_elapsed * passive_rate + float(row["passive_fraction"])
    passive_added = int(passive_total)
    passive_fraction = passive_total - passive_added
    conn.execute(
        """UPDATE players
           SET energy = ?, passive_fraction = ?, last_energy_at = ?, last_active_at = ?,
               coins = coins + ?, total_earned = total_earned + ?
           WHERE id = ?""",
        (energy, passive_fraction, now, now, passive_added, passive_added, row["id"]),
    )
    updated = conn.execute("SELECT * FROM players WHERE id = ?", (row["id"],)).fetchone()
    sync_era_progress(conn, updated)
    return updated


def winter_event_state(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    conn.execute("INSERT OR IGNORE INTO winter_event_players (player_id, tickets) VALUES (?, 0)", (row["id"],))
    settings = conn.execute("SELECT event_start FROM winter_event_settings WHERE id = 1").fetchone()
    event_start = date.fromisoformat(str(settings["event_start"]))
    today = date.fromisoformat(current_utc_date())
    calendar = winter_calendar_status(event_start, today)
    day_number = int(calendar["day"])
    claim = conn.execute(
        "SELECT reward FROM winter_calendar_claims WHERE player_id = ? AND day_number = ?",
        (row["id"], day_number),
    ).fetchone() if calendar["active"] else None
    player = conn.execute("SELECT tickets FROM winter_event_players WHERE player_id = ?", (row["id"],)).fetchone()
    return {
        "start_date": event_start.isoformat(),
        "calendar": {
            **calendar,
            "claimed_today": claim is not None,
            "today_reward": winter_ticket_reward(day_number) if calendar["active"] else 0,
        },
        "tickets": int(player["tickets"]),
        "claimed_days": [
            int(item["day_number"])
            for item in conn.execute(
                "SELECT day_number FROM winter_calendar_claims WHERE player_id = ? ORDER BY day_number",
                (row["id"],),
            ).fetchall()
        ],
    }


GIFT_CATALOG = {
    "snowflake_charm": {"title": "Снежинка", "kind": "decor", "rarity": "common", "icon": "❄️", "description": "Памятный зимний сувенир"},
    "cloud_bell": {"title": "Облачный колокольчик", "kind": "decor", "rarity": "rare", "icon": "🔔", "description": "Коллекционный праздничный декор"},
    "golden_ornament": {"title": "Золотая игрушка", "kind": "decor", "rarity": "epic", "icon": "🌟", "description": "Редкое украшение облака"},
    "winter_wings": {"title": "Крылья зимнего света", "kind": "decor", "rarity": "legendary", "icon": "🪽", "description": "Легендарный декоративный подарок"},
    "warm_glow": {"title": "Тёплое сияние", "kind": "boost", "rarity": "common", "icon": "✨", "description": "+10% монет за тап на 1 час", "effect": "income_10", "duration": 3600},
    "double_cloud": {"title": "Двойное облако", "kind": "boost", "rarity": "rare", "icon": "☁️", "description": "x2 монет за тап на 30 минут", "effect": "tap_double", "duration": 1800},
    "tap_spark": {"title": "Искра силы", "kind": "boost", "rarity": "epic", "icon": "⚡", "description": "+5 к силе тапа на 1 час", "effect": "tap_plus_5", "duration": 3600},
    "tailwind": {"title": "Попутный ветер", "kind": "boost", "rarity": "legendary", "icon": "🌬️", "description": "x2 пассивный доход на 1 час", "effect": "passive_double", "duration": 3600},
}
GIFT_DAILY_LIMITS = {1: 5, 2: 10, 3: 15, 4: 20}


def active_gift_effects(conn: sqlite3.Connection, player_id: str, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    rows = conn.execute(
        """SELECT effect FROM player_gifts
           WHERE receiver_id = ? AND kind = 'boost' AND status = 'active'
             AND expires_at > ? ORDER BY activated_at""",
        (player_id, now),
    ).fetchall()
    effects = [str(item["effect"]) for item in rows]
    return {
        "active_count": len(effects),
        "tap_double": "tap_double" in effects,
        "tap_plus_5": 5 if "tap_plus_5" in effects else 0,
        "income_10": 1.10 if "income_10" in effects else 1.0,
        "passive_double": "passive_double" in effects,
        "effects": effects,
    }


def gift_state(conn: sqlite3.Connection, player_id: str, era: int) -> dict[str, Any]:
    now = time.time()
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    limit = GIFT_DAILY_LIMITS.get(era, 20)
    sent_today = conn.execute(
        "SELECT COUNT(*) AS total FROM player_gifts WHERE sender_id = ? AND created_at >= ?",
        (player_id, day_start),
    ).fetchone()
    active_gift_effects(conn, player_id, now)
    conn.execute(
        "UPDATE player_gifts SET status = 'expired' WHERE receiver_id = ? AND status = 'active' AND expires_at <= ?",
        (player_id, now),
    )
    items = conn.execute(
        """SELECT g.*, p.nickname AS sender_nickname
           FROM player_gifts g JOIN players p ON p.id = g.sender_id
           WHERE g.receiver_id = ? ORDER BY g.created_at DESC LIMIT 60""",
        (player_id,),
    ).fetchall()
    inventory = []
    for item in items:
        catalog = GIFT_CATALOG.get(str(item["gift_code"]), {})
        expires = float(item["expires_at"] or 0)
        inventory.append({
            "id": str(item["id"]), "code": str(item["gift_code"]),
            "title": catalog.get("title", str(item["gift_code"])),
            "icon": catalog.get("icon", "🎁"), "description": catalog.get("description", ""),
            "kind": str(item["kind"]), "rarity": str(item["rarity"]),
            "status": str(item["status"]), "sender": str(item["sender_nickname"]),
            "note": str(item["note"] or ""),
            "remaining_seconds": max(0, int(expires - now)) if item["status"] == "active" else 0,
            "can_activate": item["kind"] == "boost" and item["status"] == "inventory",
        })
    active = active_gift_effects(conn, player_id, now)
    return {
        "daily_limit": limit, "sent_today": int(sent_today["total"] or 0),
        "remaining_today": max(0, limit - int(sent_today["total"] or 0)),
        "active_boosts": active["active_count"], "max_active_boosts": 2,
        "effects": active["effects"], "inventory": inventory,
        "catalog": [
            {"code": code, **{key: value for key, value in item.items() if key not in {"effect", "duration"}}}
            for code, item in GIFT_CATALOG.items()
        ],
    }


ACHIEVEMENTS = [
    {"id": "taps_1000", "title": "Облачный кликер", "description": "Сделай 1 000 нажатий", "metric": "taps", "target": 1000, "reward": 500, "badge": "☁"},
    {"id": "earned_100k", "title": "Копилка растёт", "description": "Заработай 100 000 монет за всё время", "metric": "earned", "target": 100000, "reward": 1000, "badge": "💰"},
    {"id": "upgrades_50", "title": "Инженер облаков", "description": "Купи 50 уровней улучшений", "metric": "upgrades", "target": 50, "reward": 2500, "badge": "🛠️"},
    {"id": "era_2", "title": "Космический путь", "description": "Открой эпоху Космических облаков", "metric": "era", "target": 2, "reward": 5000, "badge": "🚀"},
    {"id": "era_3", "title": "Небесный правитель", "description": "Открой Небесную империю", "metric": "era", "target": 3, "reward": 10000, "badge": "👑"},
    {"id": "era_4", "title": "За гранью небес", "description": "Достигни Божественного измерения", "metric": "era", "target": 4, "reward": 20000, "badge": "✨"},
]


def achievement_state(conn: sqlite3.Connection, row: sqlite3.Row) -> list[dict[str, Any]]:
    metrics = {
        "taps": int(row["total_taps"]),
        "earned": int(row["total_earned"]),
        "upgrades": sum(int(row[column]) for column in LEVEL_COLUMNS.values()),
        "era": int(row["active_era"]),
    }
    claimed = {
        item["achievement_id"]: item
        for item in conn.execute(
            "SELECT achievement_id, reward FROM player_achievement_claims WHERE player_id = ?",
            (row["id"],),
        ).fetchall()
    }
    result = []
    for item in ACHIEVEMENTS:
        progress = min(item["target"], metrics[item["metric"]])
        is_claimed = item["id"] in claimed
        result.append({
            **item, "progress": progress,
            "percent": min(100, int(progress * 100 / item["target"])),
            "unlocked": progress >= item["target"],
            "claimed": is_claimed,
        })
    return result


def register_referral(conn: sqlite3.Connection, invitee_id: str, code: str | None) -> None:
    if not code or len(code) > 64:
        return
    invitee = conn.execute("SELECT created_at FROM players WHERE id = ?", (invitee_id,)).fetchone()
    if not invitee or time.time() - float(invitee["created_at"]) > 600:
        return
    inviter = conn.execute("SELECT player_id FROM referral_codes WHERE code = ?", (code,)).fetchone()
    if not inviter:
        return
    inviter_id = str(inviter["player_id"])
    if inviter_id == invitee_id:
        return
    conn.execute(
        """INSERT OR IGNORE INTO referrals
           (invitee_id, inviter_id, status, reward, created_at)
           VALUES (?, ?, 'pending', 1000, ?)""",
        (invitee_id, inviter_id, time.time()),
    )


def referral_state(conn: sqlite3.Connection, player_id: str) -> dict[str, Any]:
    saved = conn.execute("SELECT code FROM referral_codes WHERE player_id = ?", (player_id,)).fetchone()
    if saved is None:
        code = secrets.token_urlsafe(9)
        conn.execute(
            "INSERT OR IGNORE INTO referral_codes (player_id, code, created_at) VALUES (?, ?, ?)",
            (player_id, code, time.time()),
        )
        saved = conn.execute("SELECT code FROM referral_codes WHERE player_id = ?", (player_id,)).fetchone()
    code = str(saved["code"])
    stats = conn.execute(
        """SELECT COUNT(*) AS total,
           SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
           SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
           SUM(CASE WHEN status = 'active' THEN reward ELSE 0 END) AS rewards
           FROM referrals WHERE inviter_id = ?""",
        (player_id,),
    ).fetchone()
    base = PUBLIC_BASE_URL or ""
    return {
        "code": code,
        "link": f"{base}/?ref={code}",
        "invited": int(stats["total"] or 0),
        "active": int(stats["active"] or 0),
        "pending": int(stats["pending"] or 0),
        "rewards": int(stats["rewards"] or 0),
        "activation_taps": 100,
    }


def make_state(row: sqlite3.Row, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    levels = {key: int(row[column]) for key, column in LEVEL_COLUMNS.items()}
    era_overview = get_era_overview(conn, row) if conn is not None else None
    winter_state = winter_event_state(conn, row) if conn is not None else None
    achievements = achievement_state(conn, row) if conn is not None else []
    referrals = referral_state(conn, row["id"]) if conn is not None else None
    gifts = gift_state(conn, row["id"], int(row["active_era"])) if conn is not None else None
    upgrades = []
    for upgrade_id, data in UPGRADES.items():
        level = levels[upgrade_id]
        upgrades.append({
            "id": upgrade_id,
            "title": data["title"],
            "description": data["description"],
            "icon": data["icon"],
            "category": data["category"],
            "level": level,
            "cost": upgrade_cost(upgrade_id, level),
        })

    claimed_tap = set()
    combo_progress: set[str] = set()
    combo_claimed = False
    cipher_claimed = False
    quests = []
    if conn is not None:
        claimed_tap = {
            int(item["milestone"])
            for item in conn.execute("SELECT milestone FROM tap_reward_claims WHERE player_id = ?", (row["id"],)).fetchall()
        }
        day = current_utc_date()
        ensure_daily_quests(conn, row["id"], day)
        combo_progress = {
            item["upgrade_id"]
            for item in conn.execute("SELECT upgrade_id FROM daily_combo_progress WHERE player_id = ? AND combo_date = ?", (row["id"], day)).fetchall()
        }
        combo_claimed = conn.execute("SELECT 1 FROM daily_combo_claims WHERE player_id = ? AND combo_date = ?", (row["id"], day)).fetchone() is not None
        cipher_claimed = conn.execute("SELECT 1 FROM daily_cipher_claims WHERE player_id = ? AND cipher_date = ?", (row["id"], day)).fetchone() is not None
        quest_rows = conn.execute("SELECT quest_id, progress, claimed FROM daily_quest_progress WHERE player_id = ? AND quest_date = ?", (row["id"], day)).fetchall()
        quest_map = {item["quest_id"]: item for item in quest_rows}
        for quest_id, item in DAILY_QUESTS.items():
            saved = quest_map.get(quest_id)
            progress = int(saved["progress"]) if saved else 0
            quests.append({
                "id": quest_id,
                **item,
                "progress": progress,
                "claimed": bool(saved["claimed"]) if saved else False,
                "unlocked": progress >= item["target"],
                "percent": min(100, int(progress * 100 / item["target"])),
            })

    total_taps = int(row["total_taps"])
    tap_rewards = [{
        "taps": item["taps"],
        "reward": item["reward"],
        "claimed": item["taps"] in claimed_tap,
        "unlocked": total_taps >= item["taps"],
        "progress": min(100, int(total_taps * 100 / item["taps"])),
    } for item in TAP_REWARDS]

    day = current_utc_date()
    combo_ids = combo_for_day(day)
    combo_cards = [{
        "id": key,
        "title": UPGRADES[key]["title"],
        "icon": UPGRADES[key]["icon"],
        "done": key in combo_progress,
    } for key in combo_ids]
    cipher_word, cipher_morse = cipher_for_day(day)
    now = time.time()
    turbo_until = float(row["turbo_until"])
    turbo_remaining = max(0, int(turbo_until - now))

    return {
        "nickname": row["nickname"],
        "era": era_overview,
        "winter_event": winter_state,
        "achievements": achievements,
        "referrals": referrals,
        "gifts": gifts,
        "coins": int(row["coins"]),
        "total_earned": int(row["total_earned"]),
        "total_taps": total_taps,
        "tap_power": tap_power(row),
        "passive_per_sec": passive_income(row),
        "energy": round(float(row["energy"]), 1),
        "max_energy": max_energy(row),
        "energy_regen": energy_regen(row),
        "daily_available": row["last_daily_claim"] != day,
        "daily_reward": DAILY_REWARD,
        "upgrades": upgrades,
        "total_upgrade_levels": sum(levels.values()),
        "tap_rewards": tap_rewards,
        "daily_combo": {"cards": combo_cards, "reward": COMBO_REWARD, "claimed": combo_claimed,
                        "completed": all(card["done"] for card in combo_cards), "date": day},
        "daily_cipher": {"morse": cipher_morse, "reward": CIPHER_REWARD, "claimed": cipher_claimed, "date": day},
        "daily_quests": quests,
        "boosts": {"turbo_active": turbo_remaining > 0, "turbo_remaining": turbo_remaining,
                   "turbo_cost": TURBO_COST, "turbo_seconds": TURBO_SECONDS,
                   "energy_cost": ENERGY_REFILL_COST, "energy_full": float(row["energy"]) >= max_energy(row) - 0.5},
    }


def api_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def check_csrf() -> bool:
    sent = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")
    return bool(sent and expected and secrets.compare_digest(sent, expected))


@app.before_request
def prepare_session():
    session.setdefault("csrf_token", secrets.token_urlsafe(24))
    incoming_ref = request.args.get("ref", "")
    if incoming_ref and len(incoming_ref) <= 64:
        session["referral_code"] = incoming_ref
    guest_player_id = get_player_id()
    raw_init_data = request.headers.get("X-Telegram-Init-Data", "")
    if raw_init_data:
        try:
            telegram_user = validate_telegram_init_data(raw_init_data)
            player_id = bind_telegram_user(telegram_user, guest_player_id)
            g.telegram_user = telegram_user
            g.telegram_player_id = player_id
            session["player_id"] = player_id
        except ValueError as exc:
            if request.path.startswith("/api/"):
                return api_error(str(exc), 401)
            g.telegram_auth_error = str(exc)
    elif REQUIRE_TELEGRAM_AUTH and request.path.startswith("/api/"):
        return api_error("Открой Cloud Clicker через Telegram Mini App.", 401)


@app.get("/")
def index():
    return render_template("index.html", csrf_token=session["csrf_token"])


@app.get("/api/state")
def state():
    player_id = get_player_id()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = ensure_player(conn, player_id)
        if getattr(g, "telegram_user", None):
            register_referral(conn, player_id, session.get("referral_code"))
        row = advance_player(conn, row)
        result = make_state(row, conn)
    return jsonify({"ok": True, "state": result})


@app.post("/api/gifts/send")
def send_gift():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    if not getattr(g, "telegram_user", None):
        return api_error("Отправка подарков доступна после входа через Telegram.", 401)
    payload = request.get_json(silent=True) or {}
    recipient_code = str(payload.get("recipient_code", "")).strip()
    gift_code = str(payload.get("gift_code", "")).strip()
    note = " ".join(str(payload.get("note", "")).split())[:120]
    gift = GIFT_CATALOG.get(gift_code)
    if len(recipient_code) > 64 or not recipient_code or not gift:
        return api_error("Проверь код друга и выбери подарок.")
    player_id = get_player_id()
    now = time.time()
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        sender = ensure_player(conn, player_id)
        recipient = conn.execute(
            """SELECT p.id FROM referral_codes r
               JOIN players p ON p.id = r.player_id
               JOIN telegram_accounts t ON t.player_id = p.id
               WHERE r.code = ?""",
            (recipient_code,),
        ).fetchone()
        if recipient is None:
            return jsonify({"ok": False, "error": "Друг не найден. Нужен код игрока, который уже открыл игру через Telegram.", "state": make_state(sender, conn)}), 404
        receiver_id = str(recipient["id"])
        if receiver_id == player_id:
            return jsonify({"ok": False, "error": "Нельзя отправить подарок самому себе.", "state": make_state(sender, conn)}), 409
        limit = GIFT_DAILY_LIMITS.get(int(sender["active_era"]), 20)
        sent = conn.execute(
            "SELECT COUNT(*) AS total FROM player_gifts WHERE sender_id = ? AND created_at >= ?",
            (player_id, day_start),
        ).fetchone()
        if int(sent["total"] or 0) >= limit:
            return jsonify({"ok": False, "error": f"Лимит эпохи: {limit} подарков в сутки.", "state": make_state(sender, conn)}), 429
        gift_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO player_gifts
               (id, sender_id, receiver_id, gift_code, kind, rarity, effect,
                duration_seconds, created_at, status, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (gift_id, player_id, receiver_id, gift_code, gift["kind"], gift["rarity"],
             gift.get("effect"), int(gift.get("duration", 0)), now,
             "collected" if gift["kind"] == "decor" else "inventory", note),
        )
        updated = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        result = make_state(updated, conn)
    return jsonify({"ok": True, "state": result, "message": "Подарок отправлен!"})


@app.post("/api/gifts/activate")
def activate_gift():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    gift_id = str((request.get_json(silent=True) or {}).get("gift_id", ""))
    if not gift_id or len(gift_id) > 64:
        return api_error("Не выбран подарок.")
    player_id = get_player_id()
    now = time.time()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = ensure_player(conn, player_id)
        gift = conn.execute(
            "SELECT * FROM player_gifts WHERE id = ? AND receiver_id = ?",
            (gift_id, player_id),
        ).fetchone()
        if gift is None or gift["kind"] != "boost" or gift["status"] != "inventory":
            return jsonify({"ok": False, "error": "Этот подарок нельзя активировать.", "state": make_state(row, conn)}), 409
        active = active_gift_effects(conn, player_id, now)
        if active["active_count"] >= 2:
            return jsonify({"ok": False, "error": "Уже активны два подарочных буста. Дождись окончания одного.", "state": make_state(row, conn)}), 409
        duration = max(60, int(gift["duration_seconds"]))
        conn.execute(
            "UPDATE player_gifts SET status = 'active', activated_at = ?, expires_at = ? WHERE id = ? AND receiver_id = ? AND status = 'inventory'",
            (now, now + duration, gift_id, player_id),
        )
        updated = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        result = make_state(updated, conn)
    return jsonify({"ok": True, "state": result, "message": "Подарочный буст активирован!"})


@app.post("/api/achievement/claim")
def claim_achievement():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    payload = request.get_json(silent=True) or {}
    achievement_id = str(payload.get("id", ""))
    item = next((entry for entry in ACHIEVEMENTS if entry["id"] == achievement_id), None)
    if item is None:
        return api_error("Такого достижения нет.")
    player_id = get_player_id()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = ensure_player(conn, player_id)
        row = advance_player(conn, row)
        achievements = achievement_state(conn, row)
        status = next(entry for entry in achievements if entry["id"] == achievement_id)
        if status["claimed"]:
            return jsonify({"ok": False, "error": "Награда за достижение уже получена.", "state": make_state(row, conn)}), 409
        if not status["unlocked"]:
            return jsonify({"ok": False, "error": "Достижение ещё не открыто.", "state": make_state(row, conn)}), 409
        reward = int(item["reward"])
        conn.execute(
            "INSERT INTO player_achievement_claims (player_id, achievement_id, reward, claimed_at) VALUES (?, ?, ?, ?)",
            (player_id, achievement_id, reward, time.time()),
        )
        conn.execute("UPDATE players SET coins = coins + ?, total_earned = total_earned + ? WHERE id = ?", (reward, reward, player_id))
        updated = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        result = make_state(updated, conn)
    return jsonify({"ok": True, "reward": reward, "state": result})


@app.post("/api/winter/calendar/claim")
def claim_winter_calendar():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    player_id = get_player_id()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = ensure_player(conn, player_id)
        event = winter_event_state(conn, row)
        calendar = event["calendar"]
        if not calendar["active"]:
            return jsonify({"ok": False, "error": "Новогодний календарь сейчас не активен.", "state": make_state(row, conn)}), 409
        day_number = int(calendar["day"])
        if calendar["claimed_today"]:
            return jsonify({"ok": False, "error": "Подарок за сегодняшний день уже получен.", "state": make_state(row, conn)}), 409
        reward = winter_ticket_reward(day_number)
        conn.execute(
            "INSERT INTO winter_calendar_claims (player_id, day_number, reward, claimed_at) VALUES (?, ?, ?, ?)",
            (player_id, day_number, reward, time.time()),
        )
        conn.execute("UPDATE winter_event_players SET tickets = tickets + ? WHERE player_id = ?", (reward, player_id))
        result = make_state(row, conn)
    return jsonify({"ok": True, "reward": reward, "state": result})


@app.post("/api/era/transition")
def transition_era():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    player_id = get_player_id()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = ensure_player(conn, player_id)
        row = advance_player(conn, row)
        overview = get_era_overview(conn, row)
        requirement = overview["progress"]
        if requirement.get("next_era") is None:
            return jsonify({"ok": False, "error": "Ты уже достиг последней эпохи.", "state": make_state(row, conn)}), 409
        if not requirement.get("unlocked"):
            return jsonify({"ok": False, "error": "Условие перехода ещё не выполнено.", "state": make_state(row, conn)}), 409
        next_era = int(requirement["next_era"])
        now = time.time()
        conn.execute(
            """UPDATE players SET active_era = ?, coins = 0,
               tap_level = 0, auto_level = 0, energy_level = 0, lab_level = 0,
               radar_level = 0, network_level = 0, energy = ?, passive_fraction = 0,
               turbo_until = 0, last_energy_at = ?, last_active_at = ? WHERE id = ?""",
            (next_era, START_ENERGY, now, now, player_id),
        )
        conn.execute(
            """INSERT OR IGNORE INTO player_era_progress
               (player_id, era, balance, earned, taps, upgrade_purchases, levels_json,
                last_balance, last_taps, last_upgrade_levels, opened_at, updated_at)
               VALUES (?, ?, 0, 0, 0, 0, '{}', 0, ?, 0, ?, ?)""",
            (player_id, next_era, int(row["total_taps"]), now, now),
        )
        updated = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        result = make_state(updated, conn)
    return jsonify({"ok": True, "era_changed": True, "state": result})


@app.post("/api/click")
def click():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    payload = request.get_json(silent=True) or {}
    try:
        requested_count = int(payload.get("count", 1))
    except (TypeError, ValueError):
        requested_count = 1
    requested_count = max(1, min(requested_count, 10))
    player_id = get_player_id()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = ensure_player(conn, player_id)
        row = advance_player(conn, row)
        accepted_count = min(requested_count, int(float(row["energy"])))
        if accepted_count < 1:
            return jsonify({"ok": False, "error": "Энергия закончилась", "state": make_state(row, conn)}), 409
        gift_effects = active_gift_effects(conn, player_id)
        multiplier = (2 if float(row["turbo_until"]) > time.time() else 1) * (2 if gift_effects["tap_double"] else 1)
        reward = int(round((tap_power(row) + gift_effects["tap_plus_5"]) * accepted_count * multiplier * gift_effects["income_10"]))
        conn.execute(
            """UPDATE players SET coins = coins + ?, total_earned = total_earned + ?,
               total_taps = total_taps + ?, energy = energy - ? WHERE id = ?""",
            (reward, reward, accepted_count, accepted_count, player_id),
        )
        invitee = conn.execute("SELECT total_taps FROM players WHERE id = ?", (player_id,)).fetchone()
        if int(invitee["total_taps"]) >= 100:
            referral = conn.execute(
                "SELECT inviter_id, reward FROM referrals WHERE invitee_id = ? AND status = 'pending'",
                (player_id,),
            ).fetchone()
            if referral:
                conn.execute(
                    "UPDATE referrals SET status = 'active', activated_at = ? WHERE invitee_id = ? AND status = 'pending'",
                    (time.time(), player_id),
                )
                conn.execute(
                    "UPDATE players SET coins = coins + ?, total_earned = total_earned + ? WHERE id = ?",
                    (int(referral["reward"]), int(referral["reward"]), referral["inviter_id"]),
                )
                inviter_row = conn.execute("SELECT * FROM players WHERE id = ?", (referral["inviter_id"],)).fetchone()
                sync_era_progress(conn, inviter_row)
        bump_quest(conn, player_id, "tap_100", accepted_count)
        row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        result = make_state(row, conn)
    return jsonify({"ok": True, "earned": reward, "accepted_count": accepted_count, "state": result})


@app.post("/api/upgrade")
def buy_upgrade():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    payload = request.get_json(silent=True) or {}
    upgrade_id = payload.get("id")
    if upgrade_id not in UPGRADES:
        return api_error("Такого улучшения нет.")
    player_id = get_player_id()
    day = current_utc_date()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = ensure_player(conn, player_id)
        row = advance_player(conn, row)
        level_column = LEVEL_COLUMNS[upgrade_id]
        level = int(row[level_column])
        cost = upgrade_cost(upgrade_id, level)
        if int(row["coins"]) < cost:
            return jsonify({"ok": False, "error": "Пока не хватает Cloudcoin", "state": make_state(row, conn)}), 409
        conn.execute(f"UPDATE players SET coins = coins - ?, {level_column} = {level_column} + 1 WHERE id = ?", (cost, player_id))
        if upgrade_id in combo_for_day(day):
            conn.execute("INSERT OR IGNORE INTO daily_combo_progress (player_id, combo_date, upgrade_id, purchased_at) VALUES (?, ?, ?, ?)", (player_id, day, upgrade_id, time.time()))
        bump_quest(conn, player_id, "upgrade_2", 1, day)
        row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        result = make_state(row, conn)
    return jsonify({"ok": True, "state": result})


@app.post("/api/daily")
def claim_daily():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    player_id = get_player_id()
    today = current_utc_date()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = ensure_player(conn, player_id)
        row = advance_player(conn, row)
        if row["last_daily_claim"] == today:
            return jsonify({"ok": False, "error": "Сегодня награда уже получена", "state": make_state(row, conn)}), 409
        conn.execute("UPDATE players SET coins = coins + ?, total_earned = total_earned + ?, last_daily_claim = ? WHERE id = ?", (DAILY_REWARD, DAILY_REWARD, today, player_id))
        bump_quest(conn, player_id, "daily_bonus", 1, today)
        row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        result = make_state(row, conn)
    return jsonify({"ok": True, "state": result})


@app.post("/api/tap-reward/claim")
def claim_tap_reward():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    payload = request.get_json(silent=True) or {}
    try:
        milestone = int(payload.get("milestone", 0))
    except (TypeError, ValueError):
        return api_error("Неверная награда.")
    reward_item = next((item for item in TAP_REWARDS if item["taps"] == milestone), None)
    if reward_item is None:
        return api_error("Такой награды нет.")
    player_id = get_player_id()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = ensure_player(conn, player_id)
        row = advance_player(conn, row)
        if int(row["total_taps"]) < milestone:
            return jsonify({"ok": False, "error": "Сначала набери нужное количество тапов.", "state": make_state(row, conn)}), 409
        already_claimed = conn.execute("SELECT 1 FROM tap_reward_claims WHERE player_id = ? AND milestone = ?", (player_id, milestone)).fetchone()
        if already_claimed:
            return jsonify({"ok": False, "error": "Эта награда уже получена.", "state": make_state(row, conn)}), 409
        reward = int(reward_item["reward"])
        conn.execute("INSERT INTO tap_reward_claims (player_id, milestone, reward, claimed_at) VALUES (?, ?, ?, ?)", (player_id, milestone, reward, time.time()))
        conn.execute("UPDATE players SET coins = coins + ?, total_earned = total_earned + ? WHERE id = ?", (reward, reward, player_id))
        row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        result = make_state(row, conn)
    return jsonify({"ok": True, "reward": reward, "state": result})


@app.post("/api/combo/claim")
def claim_combo():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    player_id = get_player_id()
    day = current_utc_date()
    selected = combo_for_day(day)
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = ensure_player(conn, player_id)
        row = advance_player(conn, row)
        bought = {r["upgrade_id"] for r in conn.execute("SELECT upgrade_id FROM daily_combo_progress WHERE player_id = ? AND combo_date = ?", (player_id, day)).fetchall()}
        if not set(selected).issubset(bought):
            return jsonify({"ok": False, "error": "Сначала улучши все три карточки дня.", "state": make_state(row, conn)}), 409
        already = conn.execute("SELECT 1 FROM daily_combo_claims WHERE player_id = ? AND combo_date = ?", (player_id, day)).fetchone()
        if already:
            return jsonify({"ok": False, "error": "Комбо уже получено сегодня.", "state": make_state(row, conn)}), 409
        conn.execute("INSERT INTO daily_combo_claims (player_id, combo_date, claimed_at) VALUES (?, ?, ?)", (player_id, day, time.time()))
        conn.execute("UPDATE players SET coins = coins + ?, total_earned = total_earned + ? WHERE id = ?", (COMBO_REWARD, COMBO_REWARD, player_id))
        row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        result = make_state(row, conn)
    return jsonify({"ok": True, "reward": COMBO_REWARD, "state": result})


@app.post("/api/cipher/claim")
def claim_cipher():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    payload = request.get_json(silent=True) or {}
    answer = "".join(ch for ch in str(payload.get("answer", "")).upper() if ch.isalpha())[:12]
    word, _morse = cipher_for_day()
    if answer != word:
        return api_error("Код неверный. Попробуй ещё раз.")
    player_id = get_player_id()
    day = current_utc_date()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = ensure_player(conn, player_id)
        row = advance_player(conn, row)
        already = conn.execute("SELECT 1 FROM daily_cipher_claims WHERE player_id = ? AND cipher_date = ?", (player_id, day)).fetchone()
        if already:
            return jsonify({"ok": False, "error": "Секретный код уже активирован сегодня.", "state": make_state(row, conn)}), 409
        conn.execute("INSERT INTO daily_cipher_claims (player_id, cipher_date, claimed_at) VALUES (?, ?, ?)", (player_id, day, time.time()))
        conn.execute("UPDATE players SET coins = coins + ?, total_earned = total_earned + ? WHERE id = ?", (CIPHER_REWARD, CIPHER_REWARD, player_id))
        row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        result = make_state(row, conn)
    return jsonify({"ok": True, "reward": CIPHER_REWARD, "state": result})


@app.post("/api/quest/claim")
def claim_quest():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    payload = request.get_json(silent=True) or {}
    quest_id = payload.get("id")
    if quest_id not in DAILY_QUESTS:
        return api_error("Такого задания нет.")
    player_id = get_player_id()
    day = current_utc_date()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = ensure_player(conn, player_id)
        row = advance_player(conn, row)
        ensure_daily_quests(conn, player_id, day)
        progress = conn.execute("SELECT progress, claimed FROM daily_quest_progress WHERE player_id = ? AND quest_date = ? AND quest_id = ?", (player_id, day, quest_id)).fetchone()
        quest = DAILY_QUESTS[quest_id]
        if progress["claimed"]:
            return jsonify({"ok": False, "error": "Награда за это задание уже получена.", "state": make_state(row, conn)}), 409
        if int(progress["progress"]) < quest["target"]:
            return jsonify({"ok": False, "error": "Задание ещё не выполнено.", "state": make_state(row, conn)}), 409
        reward = int(quest["reward"])
        conn.execute("UPDATE daily_quest_progress SET claimed = 1 WHERE player_id = ? AND quest_date = ? AND quest_id = ?", (player_id, day, quest_id))
        conn.execute("UPDATE players SET coins = coins + ?, total_earned = total_earned + ? WHERE id = ?", (reward, reward, player_id))
        row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        result = make_state(row, conn)
    return jsonify({"ok": True, "reward": reward, "state": result})


@app.post("/api/boost/turbo")
def activate_turbo():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    player_id = get_player_id()
    now = time.time()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = ensure_player(conn, player_id)
        row = advance_player(conn, row, now)
        if int(row["coins"]) < TURBO_COST:
            return jsonify({"ok": False, "error": "Не хватает Cloudcoin на турбо-буст.", "state": make_state(row, conn)}), 409
        new_until = min(max(float(row["turbo_until"]), now) + TURBO_SECONDS, now + 180)
        conn.execute("UPDATE players SET coins = coins - ?, turbo_until = ? WHERE id = ?", (TURBO_COST, new_until, player_id))
        row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        result = make_state(row, conn)
    return jsonify({"ok": True, "state": result})


@app.post("/api/boost/energy")
def activate_energy_boost():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    player_id = get_player_id()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = ensure_player(conn, player_id)
        row = advance_player(conn, row)
        if float(row["energy"]) >= max_energy(row) - 0.5:
            return jsonify({"ok": False, "error": "Энергия уже полная.", "state": make_state(row, conn)}), 409
        if int(row["coins"]) < ENERGY_REFILL_COST:
            return jsonify({"ok": False, "error": "Не хватает Cloudcoin на восстановление энергии.", "state": make_state(row, conn)}), 409
        conn.execute("UPDATE players SET coins = coins - ?, energy = ? WHERE id = ?", (ENERGY_REFILL_COST, max_energy(row), player_id))
        row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        result = make_state(row, conn)
    return jsonify({"ok": True, "state": result})


@app.post("/api/nickname")
def set_nickname():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    payload = request.get_json(silent=True) or {}
    nickname = " ".join(str(payload.get("nickname", "")).strip().split())[:18]
    if len(nickname) < 2:
        return api_error("Имя должно содержать хотя бы 2 символа.")
    player_id = get_player_id()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ensure_player(conn, player_id)
        conn.execute("UPDATE players SET nickname = ? WHERE id = ?", (nickname, player_id))
        row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        result = make_state(row, conn)
    return jsonify({"ok": True, "state": result})


@app.get("/api/leaderboard")
def leaderboard():
    player_id = get_player_id()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ensure_player(conn, player_id)
        rows = conn.execute("SELECT id, nickname, total_earned, total_taps FROM players ORDER BY total_earned DESC, total_taps DESC LIMIT 10").fetchall()
        my_rank = conn.execute("SELECT COUNT(*) + 1 AS rank FROM players WHERE total_earned > (SELECT total_earned FROM players WHERE id = ?)", (player_id,)).fetchone()["rank"]
    return jsonify({"ok": True,
        "leaders": [{"rank": i + 1, "nickname": row["nickname"], "total_earned": int(row["total_earned"]), "total_taps": int(row["total_taps"]), "you": row["id"] == player_id} for i, row in enumerate(rows)],
        "my_rank": int(my_rank)})


@app.get("/api/shop/packages")
def shop_packages():
    return jsonify({
        "ok": True,
        "currency": "XTR",
        "payments_enabled": bool(BOT_TOKEN),
        "packages": [
            {"id": package_id, **package}
            for package_id, package in STAR_PACKAGES.items()
        ],
    })


@app.post("/api/shop/create-invoice")
def create_star_invoice():
    if not check_csrf():
        return api_error("Обнови страницу и попробуй ещё раз.", 403)
    telegram_user = getattr(g, "telegram_user", None)
    if not telegram_user:
        return api_error("Покупки доступны после запуска игры через Telegram.", 401)
    if not BOT_TOKEN:
        return api_error("Магазин пока не настроен: администратор должен добавить BOT_TOKEN.", 503)
    payload_data = request.get_json(silent=True) or {}
    package_id = str(payload_data.get("package_id", ""))
    package = STAR_PACKAGES.get(package_id)
    if not package:
        return api_error("Такого набора нет.")

    player_id = get_player_id()
    invoice_payload = "cloudclicker_" + secrets.token_urlsafe(24)
    payment_id = uuid.uuid4().hex
    created_at = time.time()
    with db_connection() as conn:
        conn.execute(
            "INSERT INTO star_payments (id, payload, telegram_id, player_id, package_id, stars_amount, coins_amount, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'creating', ?)",
            (payment_id, invoice_payload, str(telegram_user["id"]), player_id, package_id, int(package["stars"]), int(package["coins"]), created_at),
        )
    try:
        result = telegram_api("createInvoiceLink", {
            "title": package["title"],
            "description": package["description"],
            "payload": invoice_payload,
            "currency": "XTR",
            "prices": [{"label": f"{package['coins']:,} Cloudcoin", "amount": int(package["stars"])}],
        })
        invoice_url = result.get("result")
        if not isinstance(invoice_url, str) or not invoice_url.startswith("https://"):
            raise RuntimeError("Telegram не вернул корректную ссылку на оплату.")
        with db_connection() as conn:
            conn.execute("UPDATE star_payments SET status = 'pending' WHERE id = ? AND status = 'creating'", (payment_id,))
        return jsonify({"ok": True, "invoice_url": invoice_url, "package": package_id})
    except Exception as exc:
        with db_connection() as conn:
            conn.execute("UPDATE star_payments SET status = 'failed' WHERE id = ?", (payment_id,))
        app.logger.exception("Could not create Telegram Stars invoice")
        return api_error(f"Не удалось создать счёт Telegram: {exc}", 502)


def process_pre_checkout(query: dict[str, Any]) -> None:
    query_id = str(query.get("id", ""))
    from_user = query.get("from") or {}
    invoice_payload = str(query.get("invoice_payload", ""))
    currency = str(query.get("currency", ""))
    total_amount = int(query.get("total_amount", 0))
    with db_connection() as conn:
        payment = conn.execute("SELECT * FROM star_payments WHERE payload = ?", (invoice_payload,)).fetchone()
        valid = bool(
            payment
            and payment["status"] == "pending"
            and payment["telegram_id"] == str(from_user.get("id", ""))
            and currency == "XTR"
            and total_amount == int(payment["stars_amount"])
        )
    answer = {"pre_checkout_query_id": query_id, "ok": valid}
    if not valid:
        answer["error_message"] = "Счёт не найден, устарел или не соответствует покупке."
    telegram_api("answerPreCheckoutQuery", answer)


def process_successful_payment(message: dict[str, Any]) -> None:
    from_user = message.get("from") or {}
    payment_info = message.get("successful_payment") or {}
    invoice_payload = str(payment_info.get("invoice_payload", ""))
    telegram_id = str(from_user.get("id", ""))
    currency = str(payment_info.get("currency", ""))
    total_amount = int(payment_info.get("total_amount", 0))
    charge_id = str(payment_info.get("telegram_payment_charge_id", ""))
    if not invoice_payload or not charge_id or currency != "XTR":
        app.logger.warning("Ignored malformed Telegram successful_payment update")
        return
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        payment = conn.execute("SELECT * FROM star_payments WHERE payload = ?", (invoice_payload,)).fetchone()
        if not payment:
            app.logger.error("Successful payment has unknown invoice payload")
            return
        if payment["status"] == "paid":
            return  # Idempotent webhook retry; do not credit twice.
        if payment["status"] != "pending" or payment["telegram_id"] != telegram_id or total_amount != int(payment["stars_amount"]):
            app.logger.error("Successful payment does not match pending invoice")
            return
        conn.execute(
            "UPDATE players SET coins = coins + ?, total_earned = total_earned + ? WHERE id = ?",
            (int(payment["coins_amount"]), int(payment["coins_amount"]), payment["player_id"]),
        )
        conn.execute(
            "UPDATE star_payments SET status = 'paid', telegram_charge_id = ?, paid_at = ? WHERE id = ?",
            (charge_id, time.time(), payment["id"]),
        )


def process_start_message(message: dict[str, Any]) -> None:
    if not PUBLIC_BASE_URL.startswith("https://"):
        app.logger.warning("PUBLIC_BASE_URL is not configured; skipping /start Mini App button")
        return
    chat = message.get("chat") or {}
    if chat.get("type") != "private" or not chat.get("id"):
        return
    telegram_api("sendMessage", {
        "chat_id": chat["id"],
        "text": "☁ Добро пожаловать в Cloud Clicker! Нажимай на облако, собирай Cloudcoin и открывай улучшения.\n\nКоманды: /terms — условия покупок, /support — поддержка.",
        "reply_markup": {
            "inline_keyboard": [[{
                "text": "☁ Открыть Cloud Clicker",
                "web_app": {"url": PUBLIC_BASE_URL},
            }]]
        },
    })


def process_bot_command(message: dict[str, Any]) -> None:
    chat = message.get("chat") or {}
    if chat.get("type") != "private" or not chat.get("id"):
        return
    command = str(message.get("text", "")).strip().split(" ", 1)[0].split("@", 1)[0]
    if command == "/terms":
        text = (PUBLIC_BASE_URL + "/terms") if PUBLIC_BASE_URL.startswith("https://") else "Условия покупки доступны на сайте Cloud Clicker."
        telegram_api("sendMessage", {"chat_id": chat["id"], "text": "Условия покупки цифровых товаров Cloud Clicker: " + text})
    elif command == "/support":
        telegram_api("sendMessage", {"chat_id": chat["id"], "text": "По вопросам платежей и начисления Cloudcoin напиши создателю: @deIaywork. Сохрани квитанцию Telegram Stars и укажи дату и пакет покупки."})


@app.get("/terms")
def terms_page():
    return """<!doctype html><html lang='ru'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Условия Cloud Clicker</title><body style='font:16px/1.6 system-ui;max-width:760px;margin:40px auto;padding:0 18px;background:#080b10;color:#e6f2ff'><h1>Условия покупок Cloud Clicker</h1><p>Покупки в Telegram Mini App проводятся в Telegram Stars (XTR). Счёт и оплата обрабатываются Telegram.</p><p>Cloudcoin — внутриигровая цифровая валюта Cloud Clicker. Она предназначена только для использования в игре, не является криптовалютой, электронными деньгами или денежным балансом, не имеет гарантированной денежной стоимости и не подлежит выводу или обмену на реальные деньги.</p><p>Cloudcoin начисляется на игровой аккаунт после получения сервером подтверждения успешного платежа Telegram. Не закрывай приложение до завершения платёжного окна. Если покупка оплачена, но Cloudcoin не появились, обратись в поддержку и приложи информацию из квитанции.</p><p>Покупка связана с Telegram ID аккаунта, с которого была открыта Mini App. Не передавай другим людям доступ к аккаунту.</p><p>Поддержка по покупкам: <a style='color:#9edaff' href='https://t.me/deIaywork'>@deIaywork</a>. Условия могут обновляться; перед покупкой проверь актуальную версию этой страницы.</p><p><a style='color:#9edaff' href='/'>Вернуться в игру</a></p></body></html>"""


@app.post("/telegram/webhook")
def telegram_webhook():
    if not TELEGRAM_WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "Webhook secret is not configured"}), 503
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secrets.compare_digest(supplied, TELEGRAM_WEBHOOK_SECRET):
        return jsonify({"ok": False}), 403
    update = request.get_json(silent=True) or {}
    try:
        if "pre_checkout_query" in update:
            process_pre_checkout(update["pre_checkout_query"])
        elif "message" in update:
            message = update["message"] or {}
            if "successful_payment" in message:
                process_successful_payment(message)
            else:
                command = str(message.get("text", "")).strip().split(" ", 1)[0].split("@", 1)[0]
                if command == "/start":
                    process_start_message(message)
                elif command in {"/terms", "/support"}:
                    process_bot_command(message)
    except Exception:
        app.logger.exception("Telegram webhook processing failed")
        return jsonify({"ok": False}), 500
    return jsonify({"ok": True})


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)