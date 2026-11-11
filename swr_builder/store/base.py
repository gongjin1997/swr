"""数据库连接、锁与通用插入/更新。

约定：数据层只抛出两类错误给上层——
- 约束冲突转换为 errors.Conflict（409），消息可直接展示；
- 参数或程序错误保持原样（ValueError 由全局处理器转换为 400，其他为 500）。
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Tuple

from ..errors import Conflict
from .migrations import apply_migrations
from .schema import SCHEMA, SCHEMA_EXT


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._local = threading.local()
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
        # 锁不可重入：同一线程嵌套调用会死锁，这里直接报错，便于发现问题
        if getattr(self._local, "active", False):
            raise RuntimeError("Store._db() 不能嵌套调用；请在同一个连接内完成查询")
        with self._lock:
            self._local.active = True
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
                self._local.active = False

    @staticmethod
    @contextmanager
    def _unique(message: str) -> Iterator[None]:
        """把唯一约束冲突转换为 Conflict(message)。"""
        try:
            yield
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise Conflict(message) from None
            raise

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
