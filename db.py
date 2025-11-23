# db.py
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
import json
import os
import io
import csv
from typing import Any, Dict, List, Optional

DB_PATH = os.getenv("QURYLYS_DB_PATH", "qurylys.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Создание/обновление таблиц (вызывать при старте бота)."""
    with get_conn() as conn:
        # основная таблица
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS appeals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT UNIQUE,
                chat_id INTEGER,
                user_id INTEGER,
                language TEXT,
                created_at TEXT,
                status TEXT,
                city TEXT,
                street TEXT,
                house TEXT,
                landmark TEXT,
                description TEXT,
                violation_type TEXT,
                danger_level TEXT,
                photos TEXT,
                applicant_name TEXT,
                phone TEXT,
                email TEXT,
                can_contact INTEGER,
                deadline TEXT,
                last_comment TEXT
            )
            """
        )
        # добавляем executor, если старый файл БД уже был
        try:
            conn.execute("ALTER TABLE appeals ADD COLUMN executor TEXT")
        except sqlite3.OperationalError:
            # колонка уже существует – игнорируем
            pass

        conn.commit()


# Статусы обращений
STATUS_NEW = "new"
STATUS_IN_PROGRESS = "in_progress"
STATUS_WAITING_INFO = "waiting_info"
STATUS_CLOSED_CONFIRMED = "closed_confirmed"
STATUS_CLOSED_NOT_CONFIRMED = "closed_not_confirmed"
STATUS_REJECTED = "rejected"


def create_appeal(data: Dict[str, Any]) -> str:
    """
    Создать обращение из словаря data.
    Вернёт public_id вида '25-000001'.
    """
    now = datetime.utcnow()
    # для демо – 3 дня на реакцию
    deadline = now + timedelta(days=3)
    photos_json = json.dumps(data.get("photos", []), ensure_ascii=False)

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO appeals (
                public_id, chat_id, user_id, language, created_at, status,
                city, street, house, landmark, description, violation_type,
                danger_level, photos, applicant_name, phone, email,
                can_contact, deadline, last_comment, executor
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                None,
                data.get("chat_id"),
                data.get("user_id"),
                data.get("language"),
                now.isoformat(),
                STATUS_NEW,
                "Уральск",
                data.get("street"),
                data.get("house"),
                data.get("landmark"),
                data.get("description"),
                data.get("violation_type"),
                data.get("danger_level"),
                photos_json,
                data.get("applicant_name"),
                data.get("phone"),
                data.get("email"),
                1 if data.get("can_contact") else 0,
                deadline.date().isoformat(),
                data.get("last_comment"),
                data.get("executor"),  # обычно None
            ),
        )
        appeal_id = cur.lastrowid
        public_id = f"{now.year % 100:02d}-{appeal_id:06d}"
        conn.execute(
            "UPDATE appeals SET public_id = ? WHERE id = ?",
            (public_id, appeal_id),
        )
        conn.commit()

    return public_id


def get_appeal(public_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT * FROM appeals WHERE public_id = ?", (public_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_appeals(
    status: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        if status:
            cur = conn.execute(
                """
                SELECT * FROM appeals
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (status, limit),
            )
        else:
            cur = conn.execute(
                """
                SELECT * FROM appeals
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def update_status(
    public_id: str, status: str, last_comment: Optional[str] = None
) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE appeals SET status = ?, last_comment = ? WHERE public_id = ?",
            (status, last_comment, public_id),
        )
        conn.commit()


def update_executor(public_id: str, executor: str) -> None:
    """Назначить исполнителя (строка, например: отдел / ФИО)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE appeals SET executor = ? WHERE public_id = ?",
            (executor, public_id),
        )
        conn.commit()


def export_appeals_csv(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> io.BytesIO:
    """
    Выгрузка обращений в CSV (общая).
    Делаем:
    - разделитель ';' (удобно для Excel в RU локали)
    - кодировка UTF-8 с BOM (utf-8-sig), чтобы не было кракозябр.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")

    cols = [
        "public_id",
        "created_at",
        "status",
        "city",
        "street",
        "house",
        "landmark",
        "violation_type",
        "danger_level",
        "applicant_name",
        "phone",
        "email",
        "executor",
        "deadline",
    ]
    writer.writerow(cols)

    query = "SELECT " + ",".join(cols) + " FROM appeals WHERE 1=1"
    params: List[Any] = []

    if start_date:
        query += " AND date(created_at) >= date(?)"
        params.append(start_date.date().isoformat())
    if end_date:
        query += " AND date(created_at) <= date(?)"
        params.append(end_date.date().isoformat())

    query += " ORDER BY created_at DESC"

    with get_conn() as conn:
        for row in conn.execute(query, params):
            writer.writerow([row[c] for c in cols])

    # UTF-8 с BOM, чтобы Excel понял кодировку
    data = buf.getvalue().encode("utf-8-sig")
    bio = io.BytesIO(data)
    bio.name = "appeals.csv"
    bio.seek(0)
    return bio
