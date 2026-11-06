"""项目、SWR 组织授权、命名空间分配。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..images import registry_path_parts
from .schema import utcnow


class ProjectsMixin:
    def list_projects(self) -> List[Dict[str, Any]]:
        return self._load_projects()

    def _load_projects(self, project_id: Optional[int] = None) -> List[Dict[str, Any]]:
        where, args = ("", ()) if project_id is None else (" WHERE project_id = ?", (project_id,))
        with self._db() as db:
            if project_id is None:
                rows = db.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE").fetchall()
            else:
                rows = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchall()
            orgs = db.execute("SELECT * FROM project_registry_orgs" + where + " ORDER BY namespace", args).fetchall()
            allocations = db.execute("SELECT project_id,namespace_id FROM project_namespaces" + where
                                     + " ORDER BY namespace_id", args).fetchall()
        by_project: Dict[int, List[Dict[str, Any]]] = {}
        for row in orgs:
            by_project.setdefault(row["project_id"], []).append({
                "namespace": row["namespace"], "base_prefixes": json.loads(row["base_prefixes_json"])
            })
        namespace_ids: Dict[int,List[int]]={}
        for row in allocations:namespace_ids.setdefault(row['project_id'],[]).append(row['namespace_id'])
        result = []
        for row in rows:
            item = dict(row)
            item["registry_orgs"] = by_project.get(item["id"], [])
            item["namespace_ids"] = namespace_ids.get(item["id"],[])
            result.append(item)
        return result

    def get_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        rows = self._load_projects(project_id)
        return rows[0] if rows else None

    def create_project(self, slug: str, name: str, registry_orgs: List[Dict[str, Any]]) -> Dict[str, Any]:
        now = utcnow()
        with self._db() as db:
            project_id = db.execute(
                "INSERT INTO projects(slug, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (slug, name, now, now),
            ).lastrowid
            for item in registry_orgs:
                db.execute("INSERT INTO project_registry_orgs(project_id, namespace, base_prefixes_json) VALUES (?, ?, ?)",
                           (project_id, item["namespace"], json.dumps(item["base_prefixes"], ensure_ascii=False)))
        return self.get_project(project_id)  # type: ignore[return-value]

    def add_project_registry_org(self, project_id: int, namespace: str, prefixes: List[str]) -> None:
        with self._db() as db:
            row = db.execute("SELECT base_prefixes_json FROM project_registry_orgs WHERE project_id = ? AND namespace = ?",
                             (project_id, namespace)).fetchone()
            current = set(json.loads(row["base_prefixes_json"])) if row else set()
            current.update(prefixes)
            db.execute(
                "INSERT INTO project_registry_orgs(project_id, namespace, base_prefixes_json) VALUES (?, ?, ?) "
                "ON CONFLICT(project_id, namespace) DO UPDATE SET base_prefixes_json = excluded.base_prefixes_json",
                (project_id, namespace, json.dumps(sorted(current), ensure_ascii=False)),
            )

    def ensure_default_project_registry_orgs(self, default_namespace: str) -> None:
        if not default_namespace:
            return
        project_id = self.default_project_id()
        with self._db() as db:
            rows = db.execute("SELECT data FROM apps WHERE project_id = ?", (project_id,)).fetchall()
        if not rows:
            self.add_project_registry_org(project_id, default_namespace, [])
            return
        grouped: Dict[str, set] = {}
        for row in rows:
            app = json.loads(row["data"])
            namespace = app.get("organization") or default_namespace
            repository = app.get("repository")
            if namespace and repository:
                grouped.setdefault(namespace, set())
            parts = registry_path_parts(app.get("base_image", ""))
            if parts:
                grouped.setdefault(parts[0], set()).add(parts[1])
        for namespace, prefixes in grouped.items():
            self.add_project_registry_org(project_id, namespace, sorted(prefixes))

    def update_project(self, project_id: int, slug: str, name: str,
                       registry_orgs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        now = utcnow()
        with self._db() as db:
            changed = db.execute("UPDATE projects SET slug = ?, name = ?, updated_at = ? WHERE id = ?",
                                 (slug, name, now, project_id)).rowcount
            if not changed:
                return None
            db.execute("DELETE FROM project_registry_orgs WHERE project_id = ?", (project_id,))
            for item in registry_orgs:
                db.execute("INSERT INTO project_registry_orgs(project_id, namespace, base_prefixes_json) VALUES (?, ?, ?)",
                           (project_id, item["namespace"], json.dumps(item["base_prefixes"], ensure_ascii=False)))
        return self.get_project(project_id)

    def delete_project(self, project_id: int) -> bool:
        with self._db() as db:
            project=db.execute("SELECT slug FROM projects WHERE id=?",(project_id,)).fetchone()
            if not project or project['slug']=='default':return False
            referenced = db.execute(
                "SELECT EXISTS(SELECT 1 FROM apps WHERE project_id = ?) OR "
                "EXISTS(SELECT 1 FROM jobs WHERE project_id = ?) OR "
                "EXISTS(SELECT 1 FROM deployments WHERE project_id = ?) OR "
                "EXISTS(SELECT 1 FROM role_bindings WHERE scope_type='project' AND scope_id=?)",
                (project_id, project_id, project_id,project_id),
            ).fetchone()[0]
            if referenced:
                return False
            return db.execute("DELETE FROM projects WHERE id = ?", (project_id,)).rowcount > 0

    def default_project_id(self) -> int:
        with self._db() as db:
            row = db.execute("SELECT id FROM projects WHERE slug = 'default'").fetchone()
        if not row:
            raise RuntimeError("default project migration is missing")
        return int(row["id"])

    def project_namespace_ids(self, project_id: int) -> List[int]:
        with self._db() as db:
            return [row['namespace_id'] for row in db.execute("SELECT namespace_id FROM project_namespaces WHERE project_id=? ORDER BY namespace_id",(project_id,))]

    def assign_namespace(self, project_id: int, namespace_id: int) -> None:
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO project_namespaces(project_id,namespace_id,created_at) VALUES(?,?,?)",
                (project_id,namespace_id,utcnow()))

    def unassign_namespace(self, project_id: int, namespace_id: int) -> bool:
        with self._db() as db:
            linked=db.execute("SELECT EXISTS(SELECT 1 FROM services WHERE namespace_id=?)",(namespace_id,)).fetchone()[0] if self._table_exists(db,'services') else 0
            if linked:return False
            return db.execute("DELETE FROM project_namespaces WHERE project_id=? AND namespace_id=?",(project_id,namespace_id)).rowcount>0
