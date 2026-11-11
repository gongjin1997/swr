"""项目、SWR 组织授权、命名空间分配。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..images import registry_path_parts
from .schema import utcnow

PROJECT_UNIQUE = "项目 slug 或名称已存在"


class ProjectsMixin:
    def list_projects(self) -> List[Dict[str, Any]]:
        return self._load_projects()

    def _load_projects(self, project_id: Optional[int] = None) -> List[Dict[str, Any]]:
        where, args = ("", ()) if project_id is None else (" WHERE project_id = %s", (project_id,))
        with self._db() as db:
            if project_id is None:
                rows = db.execute("SELECT * FROM projects ORDER BY lower(name), id").fetchall()
            else:
                rows = db.execute("SELECT * FROM projects WHERE id = %s", (project_id,)).fetchall()
            orgs = db.execute("SELECT * FROM project_registry_orgs" + where + " ORDER BY namespace", args).fetchall()
            allocations = db.execute("SELECT project_id, namespace_id FROM project_namespaces" + where
                                     + " ORDER BY namespace_id", args).fetchall()
        registry: Dict[int, List[Dict[str, Any]]] = {}
        for row in orgs:
            registry.setdefault(row["project_id"], []).append(
                {"namespace": row["namespace"], "base_prefixes": json.loads(row["base_prefixes_json"])})
        namespaces: Dict[int, List[int]] = {}
        for row in allocations:
            namespaces.setdefault(row["project_id"], []).append(row["namespace_id"])
        return [dict(row, registry_orgs=registry.get(row["id"], []), namespace_ids=namespaces.get(row["id"], []))
                for row in rows]

    def get_project(self, project_id: Optional[int]) -> Optional[Dict[str, Any]]:
        if not project_id:
            return None
        rows = self._load_projects(project_id)
        return rows[0] if rows else None

    @staticmethod
    def _write_registry_orgs(db: Any, project_id: int, registry_orgs: List[Dict[str, Any]]) -> None:
        db.execute("DELETE FROM project_registry_orgs WHERE project_id = %s", (project_id,))
        for item in registry_orgs:
            db.execute("INSERT INTO project_registry_orgs(project_id, namespace, base_prefixes_json) VALUES (%s, %s, %s)",
                       (project_id, item["namespace"], json.dumps(item["base_prefixes"], ensure_ascii=False)))

    def create_project(self, slug: str, name: str, registry_orgs: List[Dict[str, Any]]) -> Dict[str, Any]:
        now = utcnow()
        with self._db() as db, self._unique(PROJECT_UNIQUE):
            project_id = int(db.execute("INSERT INTO projects(slug, name, created_at, updated_at) "
                                        "VALUES (%s, %s, %s, %s) RETURNING id", (slug, name, now, now)).fetchone()["id"])
            self._write_registry_orgs(db, project_id, registry_orgs)
        return self.get_project(project_id)  # type: ignore[return-value]

    def update_project(self, project_id: int, slug: str, name: str,
                       registry_orgs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        with self._db() as db, self._unique(PROJECT_UNIQUE):
            changed = db.execute("UPDATE projects SET slug = %s, name = %s, updated_at = %s WHERE id = %s",
                                 (slug, name, utcnow(), project_id)).rowcount
            if not changed:
                return None
            self._write_registry_orgs(db, project_id, registry_orgs)
        return self.get_project(project_id)

    def add_project_registry_org(self, project_id: int, namespace: str, prefixes: List[str]) -> None:
        """合并（不覆盖）项目的 SWR 组织授权：已有前缀保留，新前缀追加。"""
        with self._db() as db:
            # 锁住项目行，避免并发合并时丢失前缀
            if not db.execute("SELECT id FROM projects WHERE id = %s FOR UPDATE", (project_id,)).fetchone():
                return
            row = db.execute("SELECT base_prefixes_json FROM project_registry_orgs WHERE project_id = %s "
                             "AND namespace = %s", (project_id, namespace)).fetchone()
            current = set(json.loads(row["base_prefixes_json"])) if row else set()
            current.update(prefixes)
            self.upsert(db, "project_registry_orgs", {"project_id": project_id, "namespace": namespace},
                        {"base_prefixes_json": json.dumps(sorted(current), ensure_ascii=False)})

    def ensure_default_project_registry_orgs(self, default_namespace: str) -> None:
        """默认项目兼容 v1.1：按已有应用补齐输出仓库组织与基础镜像组织的授权。"""
        if not default_namespace:
            return
        project_id = self.default_project_id()
        with self._db() as db:
            rows = db.execute("SELECT data FROM apps WHERE project_id = %s", (project_id,)).fetchall()
        if not rows:
            self.add_project_registry_org(project_id, default_namespace, [])
            return
        grouped: Dict[str, set] = {}
        for row in rows:
            app = json.loads(row["data"])
            namespace = app.get("organization") or default_namespace
            if namespace and app.get("repository"):
                grouped.setdefault(namespace, set())
            parts = registry_path_parts(app.get("base_image", ""))
            if parts:
                grouped.setdefault(parts[0], set()).add(parts[1])
        for namespace, prefixes in grouped.items():
            self.add_project_registry_org(project_id, namespace, sorted(prefixes))

    def delete_project(self, project_id: int) -> bool:
        """默认项目，以及仍有应用、构建、发布记录或成员授权的项目不能删除。"""
        with self._db() as db:
            project = db.execute("SELECT slug FROM projects WHERE id = %s FOR UPDATE", (project_id,)).fetchone()
            if not project or project["slug"] == "default":
                return False
            referenced = db.execute(
                "SELECT EXISTS(SELECT 1 FROM apps WHERE project_id = %s) OR "
                "EXISTS(SELECT 1 FROM jobs WHERE project_id = %s) OR "
                "EXISTS(SELECT 1 FROM deployments WHERE project_id = %s) OR "
                "EXISTS(SELECT 1 FROM services WHERE project_id = %s) OR "
                "EXISTS(SELECT 1 FROM role_bindings WHERE scope_type = 'project' AND scope_id = %s)",
                (project_id, project_id, project_id, project_id, project_id),
            ).fetchone()[0]
            if referenced:
                return False
            return db.execute("DELETE FROM projects WHERE id = %s", (project_id,)).rowcount > 0

    def default_project_id(self) -> int:
        with self._db() as db:
            row = db.execute("SELECT id FROM projects WHERE slug = 'default'").fetchone()
        if not row:
            raise RuntimeError("default project migration is missing")
        return int(row["id"])

    def project_namespace_ids(self, project_id: int) -> List[int]:
        with self._db() as db:
            rows = db.execute("SELECT namespace_id FROM project_namespaces WHERE project_id = %s ORDER BY namespace_id",
                              (project_id,)).fetchall()
        return [row["namespace_id"] for row in rows]

    def assign_namespace(self, project_id: int, namespace_id: int) -> None:
        with self._db() as db:
            self.insert_ignore(db, "project_namespaces",
                               {"project_id": project_id, "namespace_id": namespace_id, "created_at": utcnow()})

    def unassign_namespace(self, project_id: int, namespace_id: int) -> bool:
        """项目在该命名空间仍有服务时不能取消分配。"""
        with self._db() as db:
            linked = db.execute("SELECT EXISTS(SELECT 1 FROM services WHERE project_id = %s AND namespace_id = %s)",
                                (project_id, namespace_id)).fetchone()[0]
            if linked:
                return False
            return db.execute("DELETE FROM project_namespaces WHERE project_id = %s AND namespace_id = %s",
                              (project_id, namespace_id)).rowcount > 0
