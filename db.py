import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from config import settings


def get_conn():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def local_now():
    return datetime.now(ZoneInfo(settings.timezone))


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                daily_calorie_target INTEGER NOT NULL DEFAULT 1232,
                created_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meal_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                meal_name TEXT,
                image_path TEXT,
                user_note TEXT,
                total_calories_kcal REAL NOT NULL DEFAULT 0,
                carbs_g REAL NOT NULL DEFAULT 0,
                protein_g REAL NOT NULL DEFAULT 0,
                fat_g REAL NOT NULL DEFAULT 0,
                raw_ai_json TEXT,
                created_at TEXT NOT NULL,
                created_date TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_meal_records_chat_date
            ON meal_records(chat_id, created_date)
            """
        )

        conn.commit()


def upsert_user(chat_id: int, username: str = "", first_name: str = ""):
    now = local_now().isoformat()

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (chat_id, username, first_name, daily_calorie_target, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
            """,
            (
                chat_id,
                username or "",
                first_name or "",
                settings.default_daily_calorie_target,
                now,
            ),
        )
        conn.commit()


def get_user(chat_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        return dict(row) if row else None


def set_goal(chat_id: int, goal_kcal: int):
    user = get_user(chat_id)
    now = local_now().isoformat()

    with get_conn() as conn:
        if user:
            conn.execute(
                "UPDATE users SET daily_calorie_target = ? WHERE chat_id = ?",
                (goal_kcal, chat_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO users (chat_id, username, first_name, daily_calorie_target, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (chat_id, "", "", goal_kcal, now),
            )
        conn.commit()


def save_meal(chat_id: int, image_path: str, user_note: str, result: dict):
    now = local_now()
    created_at = now.isoformat()
    created_date = now.date().isoformat()

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO meal_records (
                chat_id,
                meal_name,
                image_path,
                user_note,
                total_calories_kcal,
                carbs_g,
                protein_g,
                fat_g,
                raw_ai_json,
                created_at,
                created_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                result.get("meal_name", "本次餐點"),
                image_path,
                user_note or "",
                float(result.get("total_calories_kcal", 0)),
                float(result.get("macros", {}).get("carbs_g", 0)),
                float(result.get("macros", {}).get("protein_g", 0)),
                float(result.get("macros", {}).get("fat_g", 0)),
                json.dumps(result, ensure_ascii=False),
                created_at,
                created_date,
            ),
        )
        conn.commit()


def get_today_summary(chat_id: int):
    today = local_now().date().isoformat()

    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS meal_count,
                COALESCE(SUM(total_calories_kcal), 0) AS total_calories_kcal,
                COALESCE(SUM(carbs_g), 0) AS carbs_g,
                COALESCE(SUM(protein_g), 0) AS protein_g,
                COALESCE(SUM(fat_g), 0) AS fat_g
            FROM meal_records
            WHERE chat_id = ? AND created_date = ?
            """,
            (chat_id, today),
        ).fetchone()

        return dict(row) if row else {
            "meal_count": 0,
            "total_calories_kcal": 0,
            "carbs_g": 0,
            "protein_g": 0,
            "fat_g": 0,
        }


def get_today_meals(chat_id: int, limit: int = 20):
    today = local_now().date().isoformat()

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                meal_name,
                total_calories_kcal,
                carbs_g,
                protein_g,
                fat_g,
                created_at
            FROM meal_records
            WHERE chat_id = ? AND created_date = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (chat_id, today, limit),
        ).fetchall()

        return [dict(r) for r in rows]


def get_meal_by_id(meal_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM meal_records WHERE id = ?",
            (meal_id,),
        ).fetchone()
        return dict(row) if row else None
