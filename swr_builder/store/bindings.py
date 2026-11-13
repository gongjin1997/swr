"""角色授权（role_bindings）。

"有效授权"指未撤销且未过期的授权；权限计算只使用有效授权（见 access/principal.py）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..errors import Conflict
from .db import is_unique_violation
from .schema import utcnow

PLATFORM_ADMIN = ("platform_admin", "global", 0)
GRANT_CONSTRAINT = "uq_role_bindings_grant"


class BindingsMixin:
    def list_bindings(self, user_id: Optional[int] = None, scope_type: Optional[str] = None,
                      scope_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """有效授权（未撤销、未过期）。"""
        where, args = ["subject_type = 'user'", "revoked_at IS NULL", "(expires_at IS NULL OR expires_at > %s)"], [utcnow()]
        for column, value in (("subject_id", user_id), ("scope_type", scope_type), ("scope_id", scope_id)):
            if value is not None:
                where.append(f"{column} = %s")
                args.append(value)
        with self._db() as db:
            rows = db.execute(f"SELECT * FROM role_bindings WHERE {' AND '.join(where)} ORDER BY id", args).fetchall()
        return [dict(row) for row in rows]

    def all_bindings(self) -> List[Dict[str, Any]]:
        """未撤销的授权（含已过期）。"""
        with self._db() as db:
            rows = db.execute("SELECT * FROM role_bindings WHERE revoked_at IS NULL ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def list_binding_rows(self, user_id: Optional[int] = None, scope_type: Optional[str] = None,
                          scope_id: Optional[int] = None, role: Optional[str] = None) -> List[Dict[str, Any]]:
        """授权列表（含已过期），附带账号、范围与授予人的名称，供管理页面展示。"""
        where, args = ["b.revoked_at IS NULL", "b.subject_type = 'user'"], []
        for column, value in (("b.subject_id", user_id), ("b.scope_type", scope_type), ("b.scope_id", scope_id),
                              ("b.role", role)):
            if value is not None:
                where.append(f"{column} = %s")
                args.append(value)
        sql = ("SELECT b.*, u.username AS subject_username, u.display_name AS subject_display_name, "
               "u.enabled AS subject_enabled, p.name AS project_name, n.name AS namespace_name, n.tier AS namespace_tier, "
               "c.name AS cluster_name, creator.username AS created_by_username "
               "FROM role_bindings b "
               "LEFT JOIN users u ON u.id = b.subject_id "
               "LEFT JOIN projects p ON b.scope_type = 'project' AND p.id = b.scope_id "
               "LEFT JOIN namespaces n ON b.scope_type = 'namespace' AND n.id = b.scope_id "
               "LEFT JOIN clusters c ON c.id = n.cluster_id "
               "LEFT JOIN users creator ON creator.id = b.created_by "
               f"WHERE {' AND '.join(where)} ORDER BY b.id")
        with self._db() as db:
            rows = db.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def _insert_binding(self, db: Any, subject_id: int, role: str, scope_type: str, scope_id: int,
                        created_by: Optional[int], expires_at: Optional[str] = None, note: str = "") -> int:
        try:
            row = db.execute(
                "INSERT INTO role_bindings(subject_type, subject_id, role, scope_type, scope_id, expires_at, created_by, "
                "created_at, note) VALUES ('user', %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (subject_id, role, scope_type, scope_id, expires_at, created_by, utcnow(), note),
            ).fetchone()
        except Exception as exc:
            if is_unique_violation(exc, GRANT_CONSTRAINT):
                raise Conflict("该用户已拥有相同范围和角色的授权") from None
            raise
        return int(row["id"])

    def create_binding(self, subject_type: str, subject_id: int, role: str, scope_type: str, scope_id: int,
                       created_by: Optional[int], expires_at: Optional[str] = None, note: str = "") -> int:
        if subject_type != "user":
            raise ValueError("当前只支持直接绑定用户")
        with self._db() as db:
            binding_id = self._insert_binding(db, subject_id, role, scope_type, scope_id, created_by, expires_at, note)
            if (role, scope_type, scope_id) == PLATFORM_ADMIN:
                db.execute("UPDATE users SET role = 'admin' WHERE id = %s", (subject_id,))
        return binding_id

    def delete_binding(self, binding_id: int) -> bool:
        with self._db() as db:
            row = db.execute("SELECT * FROM role_bindings WHERE id = %s", (binding_id,)).fetchone()
            if not row:
                return False
            db.execute("DELETE FROM role_bindings WHERE id = %s", (binding_id,))
            if (row["role"], row["scope_type"], row["scope_id"]) == PLATFORM_ADMIN:
                # 旧版本按 users.role = 'admin' 判定管理员，撤销后同步该字段，避免回退版本后权限复活
                db.execute("UPDATE users SET role = 'member' WHERE id = %s AND role = 'admin'", (row["subject_id"],))
        return True

    def delete_user_bindings(self, user_id: int) -> None:
        with self._db() as db:
            db.execute("DELETE FROM role_bindings WHERE subject_type = 'user' AND subject_id = %s", (user_id,))

    def get_binding(self, binding_id: int) -> Optional[Dict[str, Any]]:
        """未撤销的授权（含已过期）。"""
        with self._db() as db:
            row = db.execute("SELECT * FROM role_bindings WHERE id = %s AND revoked_at IS NULL", (binding_id,)).fetchone()
        return dict(row) if row else None

    def bind_user_role(self, user_id: int, role: str, scope_type: str, scope_id: int,
                       created_by: Optional[int] = None, note: str = "") -> None:
        """幂等授权（已存在相同授权时忽略）。"""
        with self._db() as db:
            self.insert_ignore(db, "role_bindings", {
                "subject_type": "user", "subject_id": user_id, "role": role, "scope_type": scope_type,
                "scope_id": scope_id, "created_by": created_by, "created_at": utcnow(), "note": note})
            if (role, scope_type, scope_id) == PLATFORM_ADMIN:
                db.execute("UPDATE users SET role = 'admin' WHERE id = %s", (user_id,))

    def count_role_bindings(self) -> Dict[str, int]:
        """每个角色的有效授权数。"""
        with self._db() as db:
            rows = db.execute("SELECT role, COUNT(*) AS n FROM role_bindings WHERE revoked_at IS NULL "
                              "AND (expires_at IS NULL OR expires_at > %s) GROUP BY role", (utcnow(),)).fetchall()
        return {row["role"]: row["n"] for row in rows}
