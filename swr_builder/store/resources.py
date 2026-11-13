"""资源与资源绑定的数据访问（每个方法一个事务）。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ..errors import Conflict, RevisionConflict
from .db import Database
from .schema import utcnow

EDIT_CONFLICT = "资源已被他人修改，请刷新后重试"


class ResourcesMixin:
    @staticmethod
    def _resource(row: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(row)
        if "attributes" in item and item["attributes"]:
            item["attributes"] = json.loads(item["attributes"])
        return item

    @staticmethod
    def _resource_version(row: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(row)
        if "attributes" in item and item["attributes"]:
            item["attributes"] = json.loads(item["attributes"])
        return item

    def create_resource(self: Database, scope_type: str, scope_id: int, type_name: str, name: str, description: str,
                       attributes: Dict[str, Any], checksum: str, note: str, actor_id: int,
                       parent_id: Optional[int] = None) -> int:
        now = utcnow()
        attrs_json = json.dumps(attributes, ensure_ascii=False)
        with self._db() as db, self._unique("同一层级下已有同名资源"):
            resource_id = int(db.execute(
                "INSERT INTO resources(scope_type, scope_id, type, name, description, parent_id, created_by, "
                "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (scope_type, scope_id, type_name, name, description, parent_id, actor_id, now, now)
            ).fetchone()["id"])
            version_id = int(db.execute(
                "INSERT INTO resource_versions(resource_id, number, attributes, checksum, note, created_by, "
                "created_at) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (resource_id, 1, attrs_json, checksum, note, actor_id, now)
            ).fetchone()["id"])
            db.execute("UPDATE resources SET current_version_id = %s WHERE id = %s", (version_id, resource_id))
        return resource_id

    def list_resources(self: Database, scope_type: Optional[str] = None, scope_id: Optional[int] = None,
                      type_name: Optional[str] = None, limit: int = 100, offset: int = 0
                      ) -> Tuple[List[Dict[str, Any]], int]:
        limit = min(max(limit, 1), 100)
        offset = max(offset, 0)
        where: List[str] = ["r.deleted_at IS NULL"]
        params: List[Any] = []
        if scope_type:
            where.append("r.scope_type = %s")
            params.append(scope_type)
        if scope_id is not None:
            where.append("r.scope_id = %s")
            params.append(scope_id)
        if type_name:
            where.append("r.type = %s")
            params.append(type_name)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self._db() as db:
            total = db.execute(f"SELECT COUNT(*) AS cnt FROM resources r{clause}", params).fetchone()["cnt"]
            rows = db.execute(
                "SELECT r.*, v.number AS version, v.attributes, v.checksum, v.created_at AS version_created_at, "
                "v.created_by AS version_created_by, v.note AS version_note, "
                "(SELECT COUNT(*) FROM service_resource_bindings b WHERE b.resource_id = r.id) AS binding_count, "
                "(SELECT COUNT(*) FROM resources c WHERE c.parent_id = r.id) AS child_count "
                f"FROM resources r LEFT JOIN resource_versions v ON v.id = r.current_version_id{clause} "
                "ORDER BY r.scope_type, r.scope_id, r.type, r.name LIMIT %s OFFSET %s",
                params + [limit, offset]
            ).fetchall()
        return [self._resource(r) for r in rows], total

    def get_resource(self: Database, resource_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute(
                "SELECT r.*, v.number AS version, v.attributes, v.checksum, v.created_at AS version_created_at, "
                "v.created_by AS version_created_by, v.note AS version_note, "
                "(SELECT COUNT(*) FROM service_resource_bindings b WHERE b.resource_id = r.id) AS binding_count, "
                "(SELECT COUNT(*) FROM resources c WHERE c.parent_id = r.id) AS child_count "
                "FROM resources r LEFT JOIN resource_versions v ON v.id = r.current_version_id "
                "WHERE r.id = %s AND r.deleted_at IS NULL", (resource_id,)
            ).fetchone()
        return self._resource(row) if row else None

    def find_resource(self: Database, scope_type: str, scope_id: int, name: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute(
                "SELECT r.*, v.number AS version, v.attributes, v.checksum "
                "FROM resources r LEFT JOIN resource_versions v ON v.id = r.current_version_id "
                "WHERE r.scope_type = %s AND r.scope_id = %s AND r.name = %s AND r.deleted_at IS NULL",
                (scope_type, scope_id, name)
            ).fetchone()
        return self._resource(row) if row else None

    def add_resource_version(self: Database, resource_id: int, base_version: int, attributes: Dict[str, Any],
                            checksum: str, note: str, actor_id: int, description: Optional[str] = None
                            ) -> Optional[int]:
        now = utcnow()
        attrs_json = json.dumps(attributes, ensure_ascii=False)
        with self._db() as db:
            row = db.execute(
                "SELECT r.id, v.number, v.checksum FROM resources r "
                "LEFT JOIN resource_versions v ON v.id = r.current_version_id WHERE r.id = %s AND r.deleted_at IS NULL "
                "FOR UPDATE OF r", (resource_id,)
            ).fetchone()
            if not row:
                return None
            current = row["number"] or 0
            if current != base_version:
                raise RevisionConflict(EDIT_CONFLICT)
            if row["checksum"] == checksum:
                return current
            new_number = current + 1
            version_id = int(db.execute(
                "INSERT INTO resource_versions(resource_id, number, attributes, checksum, note, created_by, "
                "created_at) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (resource_id, new_number, attrs_json, checksum, note, actor_id, now)
            ).fetchone()["id"])
            update_fields = "current_version_id = %s, updated_at = %s"
            update_params = [version_id, now]
            if description is not None:
                update_fields += ", description = %s"
                update_params.append(description)
            update_params.append(resource_id)
            db.execute(f"UPDATE resources SET {update_fields} WHERE id = %s", update_params)
        return new_number

    def list_resource_versions(self: Database, resource_id: int) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT v.id, v.resource_id, v.number, v.checksum, v.note, v.created_by, v.created_at, "
                "u.username AS created_by_username "
                "FROM resource_versions v LEFT JOIN users u ON u.id = v.created_by "
                "WHERE v.resource_id = %s ORDER BY v.number DESC",
                (resource_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_resource_version(self: Database, resource_id: int, version: Optional[int] = None
                            ) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            if version is None:
                row = db.execute(
                    "SELECT v.* FROM resources r JOIN resource_versions v ON v.id = r.current_version_id "
                    "WHERE r.id = %s", (resource_id,)
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM resource_versions WHERE resource_id = %s AND number = %s",
                    (resource_id, version)
                ).fetchone()
        return self._resource_version(row) if row else None

    def delete_resource(self: Database, resource_id: int) -> bool:
        with self._db() as db:
            if db.execute("SELECT 1 FROM resources WHERE parent_id = %s AND deleted_at IS NULL LIMIT 1",
                          (resource_id,)).fetchone():
                raise Conflict("该资源有子资源，不能删除")
            return db.execute("UPDATE resources SET deleted_at = %s WHERE id = %s AND deleted_at IS NULL",
                              (utcnow(), resource_id)).rowcount > 0

    def count_service_bindings(self: Database, resource_id: int) -> int:
        with self._db() as db:
            row = db.execute("SELECT COUNT(*) AS cnt FROM service_resource_bindings WHERE resource_id = %s",
                            (resource_id,)).fetchone()
        return row["cnt"] if row else 0

    def list_service_resource_bindings(self: Database, service_id: int) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT b.service_id, b.resource_id, b.alias, b.created_by, b.created_at, "
                "r.scope_type, r.scope_id, r.type, r.name, r.description, r.parent_id, "
                "v.number AS version, v.attributes, v.checksum "
                "FROM service_resource_bindings b "
                "JOIN resources r ON r.id = b.resource_id "
                "LEFT JOIN resource_versions v ON v.id = r.current_version_id "
                "WHERE b.service_id = %s ORDER BY r.type, r.name",
                (service_id,)
            ).fetchall()
        result = []
        for row in rows:
            result.append({
                "service_id": row["service_id"],
                "resource_id": row["resource_id"],
                "alias": row["alias"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "resource": {
                    "id": row["resource_id"],
                    "scope_type": row["scope_type"],
                    "scope_id": row["scope_id"],
                    "type": row["type"],
                    "name": row["name"],
                    "description": row["description"],
                    "parent_id": row["parent_id"],
                    "version": row["version"],
                    "attributes": json.loads(row["attributes"]) if row["attributes"] else {},
                    "checksum": row["checksum"],
                }
            })
        return result

    def create_service_resource_binding(self: Database, service_id: int, resource_id: int, alias: str,
                                       actor_id: int) -> None:
        with self._db() as db, self._unique("该服务已绑定该资源", "pk_service_resource_bindings"):
            db.execute(
                "INSERT INTO service_resource_bindings(service_id, resource_id, alias, created_by, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (service_id, resource_id, alias, actor_id, utcnow())
            )

    def delete_service_resource_binding(self: Database, service_id: int, resource_id: int) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM service_resource_bindings WHERE service_id = %s AND resource_id = %s",
                            (service_id, resource_id)).rowcount > 0

    def delete_all_service_resource_bindings(self: Database, service_id: int) -> int:
        with self._db() as db:
            return db.execute("DELETE FROM service_resource_bindings WHERE service_id = %s", (service_id,)).rowcount
