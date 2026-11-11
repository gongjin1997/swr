"""集群与命名空间。"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from .schema import utcnow

CLUSTER_COLUMNS = ("id, name, auth_type, server, cce_cluster_id, credential_expires_at, cert_duration_days, status, "
                   "created_at, updated_at")
CLUSTER_WRITE_FIELDS = ("name", "auth_type", "server", "credential_encrypted", "cce_cluster_id",
                        "credential_expires_at", "cert_duration_days", "status")


class ClustersMixin:
    # ------------------------------------------------------------------ 集群
    def list_clusters(self) -> List[Dict[str, Any]]:
        """集群列表（不含加密凭据）。"""
        with self._db() as db:
            rows = db.execute(f"SELECT {CLUSTER_COLUMNS} FROM clusters ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(row) for row in rows]

    def get_cluster(self, cluster_id: int) -> Optional[Dict[str, Any]]:
        """集群（不含加密凭据）。"""
        with self._db() as db:
            row = db.execute(f"SELECT {CLUSTER_COLUMNS} FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
        return dict(row) if row else None

    def get_cluster_record(self, cluster_id: int) -> Optional[Dict[str, Any]]:
        """集群完整记录（含加密凭据），只在需要连接集群时使用。"""
        with self._db() as db:
            row = db.execute("SELECT * FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
        return dict(row) if row else None

    def create_cluster(self, data: Dict[str, Any]) -> int:
        now = utcnow()
        values = (data["name"], data["auth_type"], data["server"], data["credential_encrypted"],
                  data.get("cce_cluster_id"), data.get("credential_expires_at"), data.get("cert_duration_days", 30),
                  data.get("status", "unverified"), now, now)
        with self._db() as db, self._unique("集群名称已存在"):
            cur = db.execute(f"INSERT INTO clusters({', '.join(CLUSTER_WRITE_FIELDS)}, created_at, updated_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
            return int(cur.lastrowid)

    def update_cluster(self, cluster_id: int, data: Dict[str, Any]) -> bool:
        assignments = ", ".join(f"{key} = ?" for key in CLUSTER_WRITE_FIELDS)
        values = [data.get(key) for key in CLUSTER_WRITE_FIELDS]
        with self._db() as db, self._unique("集群名称已存在"):
            return db.execute(f"UPDATE clusters SET {assignments}, updated_at = ? WHERE id = ?",
                              (*values, utcnow(), cluster_id)).rowcount > 0

    def set_cluster_status(self, cluster_id: int, status: str) -> None:
        with self._db() as db:
            db.execute("UPDATE clusters SET status = ?, updated_at = ? WHERE id = ?", (status, utcnow(), cluster_id))

    # ------------------------------------------------------------------ 命名空间
    @staticmethod
    def _namespace(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        item["policy"] = json.loads(item.pop("policy_json"))
        return item

    def list_namespaces(self, namespace_ids: Optional[List[int]] = None, cluster_id: Optional[int] = None,
                        tier: Optional[str] = None) -> List[Dict[str, Any]]:
        """namespace_ids 为 None 表示不限，空列表表示没有可见的命名空间。"""
        where: List[str] = []
        args: List[Any] = []
        if namespace_ids is not None:
            if not namespace_ids:
                return []
            where.append(f"n.id IN ({','.join('?' for _ in namespace_ids)})")
            args.extend(namespace_ids)
        if cluster_id is not None:
            where.append("n.cluster_id = ?")
            args.append(cluster_id)
        if tier is not None:
            where.append("n.tier = ?")
            args.append(tier)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self._db() as db:
            rows = db.execute("SELECT n.*, c.name AS cluster_name FROM namespaces n JOIN clusters c ON c.id = n.cluster_id"
                              + clause + " ORDER BY c.name, n.name", args).fetchall()
            allocations = db.execute("SELECT namespace_id, project_id FROM project_namespaces ORDER BY project_id").fetchall()
        projects: Dict[int, List[int]] = {}
        for row in allocations:
            projects.setdefault(row["namespace_id"], []).append(row["project_id"])
        return [dict(self._namespace(row), project_ids=projects.get(row["id"], [])) for row in rows]

    def get_namespace(self, namespace_id: Optional[int]) -> Optional[Dict[str, Any]]:
        if not namespace_id:
            return None
        rows = self.list_namespaces([namespace_id])
        return rows[0] if rows else None

    def create_namespace(self, cluster_id: int, name: str, tier: str, policy: Dict[str, Any],
                         project_id: Optional[int] = None) -> int:
        """纳管命名空间，可同时分配给一个项目（同一事务）。"""
        now = utcnow()
        with self._db() as db, self._unique("此命名空间已纳管，请刷新列表"):
            cur = db.execute("INSERT INTO namespaces(cluster_id, name, tier, policy_json, created_at, updated_at) "
                             "VALUES (?, ?, ?, ?, ?, ?)",
                             (cluster_id, name, tier, json.dumps(policy, ensure_ascii=False), now, now))
            if project_id is not None:
                db.execute("INSERT INTO project_namespaces(project_id, namespace_id, created_at) VALUES (?, ?, ?)",
                           (project_id, cur.lastrowid, now))
            return int(cur.lastrowid)

    def update_namespace(self, namespace_id: int, tier: str, policy: Dict[str, Any]) -> bool:
        with self._db() as db:
            return db.execute("UPDATE namespaces SET tier = ?, policy_json = ?, updated_at = ? WHERE id = ?",
                              (tier, json.dumps(policy, ensure_ascii=False), utcnow(), namespace_id)).rowcount > 0

    def delete_namespace(self, namespace_id: int) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM namespaces WHERE id = ?", (namespace_id,)).rowcount > 0
