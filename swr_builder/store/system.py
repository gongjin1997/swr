"""系统设置与审计日志。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from .schema import utcnow


class SystemMixin:
    def get_settings(self) -> Dict[str, Any]:
        with self._db() as db:
            rows = db.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def save_settings(self, values: Dict[str, Any], removed: List[str], username: str) -> None:
        now = utcnow()
        with self._db() as db:
            for key, value in values.items():
                db.execute("INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                           "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                           "updated_by = excluded.updated_by",
                           (key, json.dumps(value, ensure_ascii=False), now, username))
            for key in removed:
                db.execute("DELETE FROM settings WHERE key = ?", (key,))

    def add_audit(self, username: str, action: str, target: str = "", detail: str = "", ip: str = "") -> None:
        with self._db() as db:
            db.execute("INSERT INTO audit_logs(created_at, username, action, target, detail, ip) VALUES (?, ?, ?, ?, ?, ?)",
                       (utcnow(), username, action, target[:200], detail[:1000], ip[:64]))

    def list_audit(self, limit: int = 50, offset: int = 0, username: str = "",
                   action: str = "") -> Tuple[List[Dict[str, Any]], int]:
        where, args = [], []
        if username:
            where.append("username = ?")
            args.append(username)
        if action:
            where.append("action LIKE ?")
            args.append(action + "%")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._db() as db:
            total = db.execute(f"SELECT COUNT(*) FROM audit_logs {clause}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM audit_logs {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                              (*args, limit, offset)).fetchall()
        return [dict(r) for r in rows], total
