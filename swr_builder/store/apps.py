"""应用。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .schema import utcnow


class AppsMixin:
    @staticmethod
    def _app(row: Dict[str, Any]) -> Dict[str, Any]:
        data = json.loads(row["data"])
        data.update(id=row["id"], created_at=row["created_at"], updated_at=row["updated_at"])
        if "project_id" in row:
            data["project_id"] = row["project_id"]
        return data

    @staticmethod
    def _app_data(data: Dict[str, Any]) -> str:
        clean = {k: v for k, v in data.items() if k not in ("id", "created_at", "updated_at")}
        return json.dumps(clean, ensure_ascii=False)

    def list_apps(self, project_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        if project_ids is not None and not project_ids:
            return []
        with self._db() as db:
            if project_ids is None:
                rows = db.execute("SELECT * FROM apps WHERE deleted_at IS NULL ORDER BY lower(name), id").fetchall()
            else:
                rows = db.execute(f"SELECT * FROM apps WHERE project_id IN ({self.marks(project_ids)}) "
                                  "AND deleted_at IS NULL ORDER BY lower(name), id", project_ids).fetchall()
        return [self._app(r) for r in rows]

    def get_app(self, app_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM apps WHERE id = %s AND deleted_at IS NULL", (app_id,)).fetchone()
        return self._app(row) if row else None

    def find_app_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM apps WHERE name = %s AND deleted_at IS NULL", (name,)).fetchone()
        return self._app(row) if row else None

    def create_app(self, data: Dict[str, Any], project_id: Optional[int] = None) -> Dict[str, Any]:
        now = utcnow()
        project_id = project_id or self.default_project_id()
        with self._db() as db, self._unique("应用名称已存在"):
            app_id = int(db.execute("INSERT INTO apps(name, data, created_at, updated_at, project_id) "
                                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                                    (data["name"], self._app_data(data), now, now, project_id)).fetchone()["id"])
        return self.get_app(app_id)  # type: ignore[return-value]

    def update_app(self, app_id: int, data: Dict[str, Any], project_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        with self._db() as db, self._unique("应用名称已存在"):
            cur = db.execute("UPDATE apps SET name = %s, data = %s, updated_at = %s, "
                             "project_id = COALESCE(%s, project_id) WHERE id = %s AND deleted_at IS NULL",
                             (data["name"], self._app_data(data), utcnow(), project_id, app_id))
            if cur.rowcount == 0:
                return None
        return self.get_app(app_id)

    def delete_app(self, app_id: int) -> bool:
        with self._db() as db:
            return db.execute("UPDATE apps SET deleted_at = %s WHERE id = %s AND deleted_at IS NULL",
                              (utcnow(), app_id)).rowcount > 0
