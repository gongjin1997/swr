"""资源存储层：resources、resource_versions、service_resource_bindings 表的 CRUD。

每个方法是一个短事务。查询返回字典或字典列表，插入返回新记录。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from .db import savepoint_or_ignore
from .schema import utcnow


class ResourcesMixin:
    """资源表的数据库操作，混入 Store"""

    def create_resource(self, scope_type: str, scope_id: int, type_name: str, name: str, parent_id: Optional[int],
                        attributes: Dict[str, Any], *, created_by: Optional[int] = None,
                        description: str = "") -> Dict[str, Any]:
        """创建资源并写入第一个版本"""
        now = utcnow()
        with self._db() as db:
            # 插入资源记录
            resource_id = db.execute(
                """INSERT INTO resources(scope_type, scope_id, type, name, parent_id, description, created_by, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (scope_type, scope_id, type_name, name, parent_id, description, created_by, now, now)
            ).fetchone()[0]

            # 写入第一个版本
            attrs_json = json.dumps(attributes, ensure_ascii=False, sort_keys=True)
            checksum = hashlib.sha256(attrs_json.encode("utf-8")).hexdigest()[:16]
            version_id = db.execute(
                """INSERT INTO resource_versions(resource_id, number, attributes, checksum, created_by, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (resource_id, 1, attrs_json, checksum, created_by, now)
            ).fetchone()[0]

            # 更新 current_version_id
            db.execute("UPDATE resources SET current_version_id = %s WHERE id = %s", (version_id, resource_id))

            return self.get_resource(resource_id)

    def get_resource(self, resource_id: int) -> Optional[Dict[str, Any]]:
        """获取资源及其当前版本"""
        with self._db() as db:
            row = db.execute(
                """SELECT r.id, r.scope_type, r.scope_id, r.type, r.name, r.parent_id, r.description,
                          r.current_version_id, r.created_by, r.created_at, r.updated_at,
                          rv.number, rv.attributes, rv.checksum, rv.note as version_note,
                          rv.created_by as version_created_by, rv.created_at as version_created_at
                   FROM resources r
                   LEFT JOIN resource_versions rv ON rv.id = r.current_version_id
                   WHERE r.id = %s""",
                (resource_id,)
            ).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "scope_type": row["scope_type"],
                "scope_id": row["scope_id"],
                "type": row["type"],
                "name": row["name"],
                "parent_id": row["parent_id"],
                "description": row["description"],
                "current_version_id": row["current_version_id"],
                "current_version": row["number"],
                "attributes": json.loads(row["attributes"]) if row["attributes"] else {},
                "checksum": row["checksum"],
                "version_note": row["version_note"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def list_resources(self, scope_type: Optional[str] = None, scope_id: Optional[int] = None,
                       type_name: Optional[str] = None, limit: int = 100, offset: int = 0
                       ) -> tuple[List[Dict[str, Any]], int]:
        """列出资源，返回 (items, total)"""
        conditions = []
        params: List[Any] = []
        if scope_type:
            conditions.append("r.scope_type = %s")
            params.append(scope_type)
        if scope_id is not None:
            conditions.append("r.scope_id = %s")
            params.append(scope_id)
        if type_name:
            conditions.append("r.type = %s")
            params.append(type_name)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        with self._db() as db:
            total = db.execute(f"SELECT COUNT(*) FROM resources r {where}", params).fetchone()[0]
            rows = db.execute(
                f"""SELECT r.id, r.scope_type, r.scope_id, r.type, r.name, r.parent_id, r.description,
                           r.current_version_id, r.created_by, r.created_at, r.updated_at,
                           rv.number, rv.checksum
                    FROM resources r
                    LEFT JOIN resource_versions rv ON rv.id = r.current_version_id
                    {where}
                    ORDER BY r.id DESC
                    LIMIT %s OFFSET %s""",
                params + [limit, offset]
            ).fetchall()

            items = []
            for row in rows:
                items.append({
                    "id": row["id"],
                    "scope_type": row["scope_type"],
                    "scope_id": row["scope_id"],
                    "type": row["type"],
                    "name": row["name"],
                    "parent_id": row["parent_id"],
                    "description": row["description"],
                    "current_version_id": row["current_version_id"],
                    "current_version": row["number"],
                    "checksum": row["checksum"],
                    "created_by": row["created_by"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                })
            return items, total

    def update_resource(self, resource_id: int, attributes: Dict[str, Any], *, created_by: Optional[int] = None,
                        note: str = "", description: Optional[str] = None) -> Dict[str, Any]:
        """更新资源属性，创建新版本"""
        now = utcnow()
        with self._db() as db:
            resource = db.execute("SELECT current_version_id FROM resources WHERE id = %s", (resource_id,)).fetchone()
            if not resource:
                raise ValueError(f"资源不存在: {resource_id}")

            # 获取当前版本号
            current = db.execute("SELECT number FROM resource_versions WHERE id = %s",
                                 (resource["current_version_id"],)).fetchone()
            next_number = current["number"] + 1

            # 创建新版本
            attrs_json = json.dumps(attributes, ensure_ascii=False, sort_keys=True)
            checksum = hashlib.sha256(attrs_json.encode("utf-8")).hexdigest()[:16]
            version_id = db.execute(
                """INSERT INTO resource_versions(resource_id, number, attributes, checksum, note, created_by, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (resource_id, next_number, attrs_json, checksum, note, created_by, now)
            ).fetchone()[0]

            # 更新资源
            if description is not None:
                db.execute("UPDATE resources SET current_version_id = %s, description = %s, updated_at = %s WHERE id = %s",
                           (version_id, description, now, resource_id))
            else:
                db.execute("UPDATE resources SET current_version_id = %s, updated_at = %s WHERE id = %s",
                           (version_id, now, resource_id))

            return self.get_resource(resource_id)

    def delete_resource(self, resource_id: int) -> bool:
        """删除资源。如果被绑定则失败（外键约束 ON DELETE RESTRICT）"""
        with self._db() as db:
            result = db.execute("DELETE FROM resources WHERE id = %s", (resource_id,))
            return result.rowcount > 0

    def get_resource_version(self, resource_id: int, version_number: int) -> Optional[Dict[str, Any]]:
        """获取指定版本"""
        with self._db() as db:
            row = db.execute(
                """SELECT rv.id, rv.resource_id, rv.number, rv.attributes, rv.checksum, rv.note,
                          rv.created_by, rv.created_at,
                          r.type, r.name, r.scope_type, r.scope_id, r.parent_id
                   FROM resource_versions rv
                   JOIN resources r ON r.id = rv.resource_id
                   WHERE rv.resource_id = %s AND rv.number = %s""",
                (resource_id, version_number)
            ).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "resource_id": row["resource_id"],
                "number": row["number"],
                "attributes": json.loads(row["attributes"]),
                "checksum": row["checksum"],
                "note": row["note"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "type": row["type"],
                "name": row["name"],
                "scope_type": row["scope_type"],
                "scope_id": row["scope_id"],
                "parent_id": row["parent_id"],
            }

    def list_resource_versions(self, resource_id: int) -> List[Dict[str, Any]]:
        """列出资源的所有版本"""
        with self._db() as db:
            rows = db.execute(
                """SELECT id, number, checksum, note, created_by, created_at
                   FROM resource_versions
                   WHERE resource_id = %s
                   ORDER BY number DESC""",
                (resource_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    # === 服务资源绑定 ===

    def bind_resource(self, service_id: int, resource_id: int, alias: str = "", *,
                      created_by: Optional[int] = None) -> Dict[str, Any]:
        """绑定资源到服务"""
        now = utcnow()
        with self._db() as db:
            binding_id = db.execute(
                """INSERT INTO service_resource_bindings(service_id, resource_id, alias, created_by, created_at)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (service_id, resource_id) DO UPDATE SET alias = EXCLUDED.alias
                   RETURNING id""",
                (service_id, resource_id, alias, created_by, now)
            ).fetchone()[0]
            return self.get_binding(binding_id)

    def get_binding(self, binding_id: int) -> Optional[Dict[str, Any]]:
        """获取绑定详情"""
        with self._db() as db:
            row = db.execute(
                """SELECT b.id, b.service_id, b.resource_id, b.alias, b.created_by, b.created_at,
                          r.type, r.name, r.scope_type, r.scope_id, r.parent_id, r.current_version_id
                   FROM service_resource_bindings b
                   JOIN resources r ON r.id = b.resource_id
                   WHERE b.id = %s""",
                (binding_id,)
            ).fetchone()
            if not row:
                return None
            return dict(row)

    def list_bindings(self, service_id: int) -> List[Dict[str, Any]]:
        """列出服务的所有绑定"""
        with self._db() as db:
            rows = db.execute(
                """SELECT b.id, b.resource_id, b.alias, b.created_by, b.created_at,
                          r.type, r.name, r.scope_type, r.scope_id, r.parent_id, r.current_version_id
                   FROM service_resource_bindings b
                   JOIN resources r ON r.id = b.resource_id
                   WHERE b.service_id = %s
                   ORDER BY b.id""",
                (service_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def unbind_resource(self, service_id: int, resource_id: int) -> bool:
        """解除绑定"""
        with self._db() as db:
            result = db.execute(
                "DELETE FROM service_resource_bindings WHERE service_id = %s AND resource_id = %s",
                (service_id, resource_id)
            )
            return result.rowcount > 0

    def resource_usage(self, resource_id: int) -> List[Dict[str, Any]]:
        """查询哪些服务绑定了该资源"""
        with self._db() as db:
            rows = db.execute(
                """SELECT b.service_id, b.alias, s.name as service_name, s.namespace_id
                   FROM service_resource_bindings b
                   JOIN services s ON s.id = b.service_id
                   WHERE b.resource_id = %s
                   ORDER BY b.service_id""",
                (resource_id,)
            ).fetchall()
            return [dict(row) for row in rows]
