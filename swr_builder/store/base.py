"""数据库连接、锁与通用插入/更新。"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Tuple

from .migrations import apply_migrations
from .schema import SCHEMA, SCHEMA_EXT


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.executescript(SCHEMA_EXT)
            # 1.0 → 1.1 升级：构建记录增加提交账号
            cols = {r["name"] for r in db.execute("PRAGMA table_info(jobs)").fetchall()}
            if "created_by" not in cols:
                db.execute("ALTER TABLE jobs ADD COLUMN created_by TEXT")
            apply_migrations(db)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(str(self.path), timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _insert(self, table: str, allowed: Tuple[str, ...], fields: Dict[str, Any]) -> int:
        unknown = set(fields) - set(allowed)
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self._db() as db:
            return db.execute(f"INSERT INTO {table}({cols}) VALUES ({marks})", tuple(fields.values())).lastrowid

    def _update(self, table: str, allowed: Tuple[str, ...], row_id: int, fields: Dict[str, Any]) -> bool:
        if not fields:
            return True
        unknown = set(fields) - set(allowed)
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._db() as db:
            return db.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*fields.values(), row_id)).rowcount > 0

    @staticmethod
    def _table_exists(db: sqlite3.Connection, table: str) -> bool:
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() is not None
