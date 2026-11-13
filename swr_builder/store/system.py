"""系统设置与审计日志。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from .schema import utcnow


def _prefix_pattern(prefix: str) -> str:
    """LIKE 前缀匹配：用 ! 转义用户输入中的通配符（避免反斜杠在不同 standard_conforming_strings 下含义不同）。"""
    escaped = prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return escaped + "%"


class SystemMixin:
    def get_settings(self) -> Dict[str, Any]:
        with self._db() as db:
            rows = db.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def save_settings(self, values: Dict[str, Any], removed: List[str], username: str) -> None:
        now = utcnow()
        with self._db() as db:
            for key, value in values.items():
                self.upsert(db, "settings", {"key": key},
                            {"value": json.dumps(value, ensure_ascii=False), "updated_at": now, "updated_by": username})
            for key in removed:
                db.execute("DELETE FROM settings WHERE key = %s", (key,))

    def add_audit(self, username: str, action: str, target: str = "", detail: str = "", ip: str = "") -> None:
        with self._db() as db:
            db.execute("INSERT INTO audit_logs(created_at, username, action, target, detail, ip) "
                       "VALUES (%s, %s, %s, %s, %s, %s)",
                       (utcnow(), username, action, target[:200], detail[:1000], ip[:64]))

    def list_audit(self, limit: int = 50, offset: int = 0, username: str = "",
                   action: str = "") -> Tuple[List[Dict[str, Any]], int]:
        where, args = [], []
        if username:
            where.append("username = %s")
            args.append(username)
        if action:
            where.append("action ILIKE %s ESCAPE '!'")
            args.append(_prefix_pattern(action))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._db() as db:
            total = db.execute(f"SELECT COUNT(*) FROM audit_logs {clause}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM audit_logs {clause} ORDER BY id DESC LIMIT %s OFFSET %s",
                              (*args, limit, offset)).fetchall()
        return [dict(r) for r in rows], total
