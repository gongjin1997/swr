"""资源管理的存储层（基于新的领域模型）。

提供资源和资源绑定的 CRUD 操作。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ..core.resource_types import get_resource_type
from .schema import utcnow


class ResourceStore:
    """资源存储层（Mixin）"""

    def create_resource(
        self,
        scope_type: str,
        scope_id: int,
        resource_type: str,
        name: str,
        attributes: Dict[str, Any],
        *,
        parent_id: Optional[int] = None,
        description: str = "",
        created_by: Optional[int] = None,
        note: str = "",
    ) -> Dict[str, Any]:
        """创建资源并生成第一个版本"""
        # 验证资源类型
        type_def = get_resource_type(resource_type)
        if not type_def:
            raise ValueError(f"未知的资源类型: {resource_type}")

        # 验证父子关系
        if parent_id:
            parent = self.get_resource(parent_id)
            if not parent:
                raise ValueError(f"父资源不存在: {parent_id}")
            if type_def.parent_type != parent["type"]:
                raise ValueError(f"资源类型 {resource_type} 的父类型应为 {type_def.parent_type}，实际为 {parent['type']}")

        now = utcnow()
        attributes_json = json.dumps(attributes, ensure_ascii=False)

        with self._db() as db:
            # 创建资源
            resource_id = db.execute(
                """INSERT INTO resources(scope_type, scope_id, type, name, parent_id, description, created_by, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (scope_type, scope_id, resource_type, name, parent_id, description, created_by, now, now)
            ).fetchone()[0]

            # 创建第一个版本
            import hashlib
            checksum = hashlib.sha256(attributes_json.encode()).hexdigest()

            version_id = db.execute(
                """INSERT INTO resource_versions(resource_id, number, attributes, checksum, note, created_by, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (resource_id, 1, attributes_json, checksum, note, created_by, now)
            ).fetchone()[0]

            # 更新 current_version_id
            db.execute(
                "UPDATE resources SET current_version_id = %s WHERE id = %s",
                (version_id, resource_id)
            )

            return self.get_resource(resource_id)

    def get_resource(self, resource_id: int) -> Optional[Dict[str, Any]]:
        """获取资源详情（包含当前版本的属性）"""
        with self._db() as db:
            row = db.execute(
                """SELECT r.id, r.scope_type, r.scope_id, r.type, r.name, r.parent_id, r.description,
                          r.current_version_id, r.created_by, r.created_at, r.updated_at,
                          rv.number as current_version, rv.attributes
                   FROM resources r
                   LEFT JOIN resource_versions rv ON r.current_version_id = rv.id
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
                "current_version": row["current_version"],
                "attributes": json.loads(row["attributes"]) if row["attributes"] else {},
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def list_resources(
        self,
        scope_type: Optional[str] = None,
        scope_id: Optional[int] = None,
        resource_type: Optional[str] = None,
        parent_id: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """列出资源"""
        with self._db() as db:
            where_clauses = []
            params = []

            if scope_type:
                where_clauses.append("r.scope_type = %s")
                params.append(scope_type)
            if scope_id is not None:
                where_clauses.append("r.scope_id = %s")
                params.append(scope_id)
            if resource_type:
                where_clauses.append("r.type = %s")
                params.append(resource_type)
            if parent_id is not None:
                where_clauses.append("r.parent_id = %s")
                params.append(parent_id)

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            # 获取总数
            total = db.execute(f"SELECT COUNT(*) FROM resources r {where_sql}", params).fetchone()[0]

            # 获取列表
            params.extend([limit, offset])
            rows = db.execute(
                f"""SELECT r.id, r.scope_type, r.scope_id, r.type, r.name, r.parent_id, r.description,
                           r.current_version_id, rv.number as current_version,
                           r.created_by, r.created_at, r.updated_at
                    FROM resources r
                    LEFT JOIN resource_versions rv ON r.current_version_id = rv.id
                    {where_sql}
                    ORDER BY r.id
                    LIMIT %s OFFSET %s""",
                params
            ).fetchall()

            items = [dict(row) for row in rows]
            return items, total

    def update_resource_attributes(
        self,
        resource_id: int,
        attributes: Dict[str, Any],
        *,
        created_by: Optional[int] = None,
        note: str = "",
    ) -> Dict[str, Any]:
        """更新资源属性（创建新版本）"""
        resource = self.get_resource(resource_id)
        if not resource:
            raise ValueError(f"资源不存在: {resource_id}")

        now = utcnow()
        attributes_json = json.dumps(attributes, ensure_ascii=False)

        with self._db() as db:
            # 获取当前最大版本号
            current_version = resource["current_version"] or 0
            new_version = current_version + 1

            # 创建新版本
            import hashlib
            checksum = hashlib.sha256(attributes_json.encode()).hexdigest()

            version_id = db.execute(
                """INSERT INTO resource_versions(resource_id, number, attributes, checksum, note, created_by, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (resource_id, new_version, attributes_json, checksum, note, created_by, now)
            ).fetchone()[0]

            # 更新资源的 current_version_id
            db.execute(
                """UPDATE resources
                   SET current_version_id = %s, updated_at = %s
                   WHERE id = %s""",
                (version_id, now, resource_id)
            )

            return self.get_resource(resource_id)

    def delete_resource(self, resource_id: int) -> bool:
        """删除资源（级联删除版本和子资源）"""
        with self._db() as db:
            result = db.execute("DELETE FROM resources WHERE id = %s", (resource_id,))
            return result.rowcount > 0

    def bind_resource(
        self,
        service_id: int,
        resource_id: int,
        alias: str = "",
    ) -> Dict[str, Any]:
        """绑定资源到服务"""
        now = utcnow()
        with self._db() as db:
            binding_id = db.execute(
                """INSERT INTO service_resource_bindings(service_id, resource_id, alias, created_at)
                   VALUES (%s, %s, %s, %s)
                   RETURNING id""",
                (service_id, resource_id, alias, now)
            ).fetchone()[0]

            return self.get_binding(binding_id)

    def get_binding(self, binding_id: int) -> Optional[Dict[str, Any]]:
        """获取绑定详情"""
        with self._db() as db:
            row = db.execute(
                """SELECT id, service_id, resource_id, alias, created_at
                   FROM service_resource_bindings
                   WHERE id = %s""",
                (binding_id,)
            ).fetchone()

            if not row:
                return None

            return dict(row)

    def list_bindings(self, service_id: int) -> List[Dict[str, Any]]:
        """列出服务的资源绑定"""
        with self._db() as db:
            rows = db.execute(
                """SELECT srb.id, srb.service_id, srb.resource_id, srb.alias, srb.created_at,
                          r.type as resource_type, r.name as resource_name
                   FROM service_resource_bindings srb
                   JOIN resources r ON srb.resource_id = r.id
                   WHERE srb.service_id = %s
                   ORDER BY srb.id""",
                (service_id,)
            ).fetchall()

            return [dict(row) for row in rows]

    def unbind_resource(self, binding_id: int) -> bool:
        """解绑资源"""
        with self._db() as db:
            result = db.execute("DELETE FROM service_resource_bindings WHERE id = %s", (binding_id,))
            return result.rowcount > 0
