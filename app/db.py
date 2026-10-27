"""SQLite 存储层：建表、连接、几个小的 get_or_create 辅助函数。"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS subjects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stem            TEXT NOT NULL,
    stem_hash       TEXT NOT NULL UNIQUE,
    qtype           TEXT NOT NULL DEFAULT 'unknown',
    options         TEXT NOT NULL DEFAULT '[]',
    my_answer       TEXT NOT NULL DEFAULT '',
    correct_answer  TEXT NOT NULL DEFAULT '',
    analysis        TEXT NOT NULL DEFAULT '',
    subject_id      INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    chapter         TEXT NOT NULL DEFAULT '',
    difficulty      INTEGER NOT NULL DEFAULT 2,
    source_url      TEXT NOT NULL DEFAULT '',
    source_title    TEXT NOT NULL DEFAULT '',
    source_site     TEXT NOT NULL DEFAULT '',
    raw_text        TEXT NOT NULL DEFAULT '',
    image_path      TEXT NOT NULL DEFAULT '',
    llm_status      TEXT NOT NULL DEFAULT 'none',
    seen_count      INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_points (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    subject_id  INTEGER REFERENCES subjects(id) ON DELETE CASCADE,
    parent_id   INTEGER REFERENCES knowledge_points(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (name, subject_id)
);

CREATE TABLE IF NOT EXISTS question_kp (
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    kp_id       INTEGER NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
    PRIMARY KEY (question_id, kp_id)
);

CREATE TABLE IF NOT EXISTS attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    answer      TEXT NOT NULL DEFAULT '',
    is_correct  INTEGER,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    due_at      TEXT NOT NULL,
    interval    REAL NOT NULL DEFAULT 0,
    ease        REAL NOT NULL DEFAULT 2.5,
    reps        INTEGER NOT NULL DEFAULT 0,
    lapses      INTEGER NOT NULL DEFAULT 0,
    last_grade  INTEGER,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_questions_subject ON questions(subject_id);
CREATE INDEX IF NOT EXISTS idx_questions_status  ON questions(llm_status);
CREATE INDEX IF NOT EXISTS idx_qkp_kp            ON question_kp(kp_id);
CREATE INDEX IF NOT EXISTS idx_reviews_due       ON reviews(due_at);
CREATE INDEX IF NOT EXISTS idx_attempts_q        ON attempts(question_id);
"""


def utcnow() -> str:
    """统一用带时区的 ISO 字符串，字典序即时间序。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# 老库升级：新加的列在这里补，init_db() 每次启动都会检查
_MIGRATIONS = [
    ("questions", "image_path", "TEXT NOT NULL DEFAULT ''"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, ddl in _MIGRATIONS:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        if column not in columns:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, ddl))


def init_db() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def get_or_create_subject(conn: sqlite3.Connection, name: str) -> Optional[int]:
    name = (name or "").strip()
    if not name:
        return None
    row = conn.execute("SELECT id FROM subjects WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO subjects(name, created_at) VALUES(?, ?)", (name, utcnow())
    )
    return cur.lastrowid


def get_or_create_kp(
    conn: sqlite3.Connection,
    name: str,
    subject_id: Optional[int] = None,
    parent_id: Optional[int] = None,
) -> Optional[int]:
    name = (name or "").strip()
    if not name:
        return None
    row = conn.execute(
        "SELECT id FROM knowledge_points WHERE name = ? AND subject_id IS ?",
        (name, subject_id),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO knowledge_points(name, subject_id, parent_id, created_at)"
        " VALUES(?, ?, ?, ?)",
        (name, subject_id, parent_id, utcnow()),
    )
    return cur.lastrowid
