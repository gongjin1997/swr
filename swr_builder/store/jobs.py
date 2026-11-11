"""构建记录。"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from .schema import (JOB_FIELDS, JSON_FIELDS, LIST_FIELDS, utcnow)


class JobsMixin:
    @staticmethod
    def _encode(fields: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        for key, value in fields.items():
            if key not in JOB_FIELDS:
                raise KeyError(key)
            out[key] = json.dumps(value, ensure_ascii=False) if key in JSON_FIELDS and value is not None else value
        return out

    @staticmethod
    def _job(row: sqlite3.Row) -> Dict[str, Any]:
        job = dict(row)
        for key in JSON_FIELDS:
            if key in job and job[key]:
                job[key] = json.loads(job[key])
        return job

    def create_job(self, **fields: Any) -> int:
        if fields.get("project_id") is None:
            fields["project_id"] = self.default_project_id()
        data = self._encode(fields)
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        with self._db() as db:
            return db.execute(f"INSERT INTO jobs({cols}) VALUES ({marks})", tuple(data.values())).lastrowid

    def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        data = self._encode(fields)
        sets = ", ".join(f"{k} = ?" for k in data)
        with self._db() as db:
            db.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*data.values(), job_id))

    def delete_job(self, job_id: int) -> None:
        with self._db() as db:
            db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def list_jobs(self, app_id: Optional[int] = None, status: Optional[str] = None,
                  limit: int = 30, offset: int = 0,
                  project_ids: Optional[List[int]] = None) -> Tuple[List[Dict[str, Any]], int]:
        where, args = [], []
        if project_ids is not None:
            if not project_ids:
                return [], 0
            where.append(f"project_id IN ({','.join('?' for _ in project_ids)})")
            args.extend(project_ids)
        if app_id:
            where.append("app_id = ?")
            args.append(app_id)
        if status:
            where.append("status = ?")
            args.append(status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._db() as db:
            total = db.execute(f"SELECT COUNT(*) FROM jobs {clause}", args).fetchone()[0]
            rows = db.execute(f"SELECT {', '.join(LIST_FIELDS)} FROM jobs {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                              (*args, limit, offset)).fetchall()
        return [dict(r) for r in rows], total

    def fail_unfinished_jobs(self, reason: str) -> int:
        with self._db() as db:
            return db.execute("UPDATE jobs SET status = 'failed', error = ?, finished_at = ? "
                              "WHERE status IN ('queued', 'running')", (reason, utcnow())).rowcount

    def count_jobs(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
