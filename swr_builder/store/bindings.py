"""角色授权（role_bindings）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .schema import utcnow


class BindingsMixin:
    def list_bindings(self, user_id: Optional[int] = None, scope_type: Optional[str] = None,
                      scope_id: Optional[int] = None) -> List[Dict[str, Any]]:
        where, args = ["subject_type = 'user'", "revoked_at IS NULL"], []
        if user_id is not None:
            where.append("subject_id = ?")
            args.append(user_id)
        if scope_type is not None:
            where.append("scope_type = ?")
            args.append(scope_type)
        if scope_id is not None:
            where.append("scope_id = ?")
            args.append(scope_id)
        now = utcnow()
        where.append("(expires_at IS NULL OR expires_at > ?)")
        args.append(now)
        with self._db() as db:
            rows = db.execute(f"SELECT * FROM role_bindings WHERE {' AND '.join(where)} ORDER BY id", args).fetchall()
        return [dict(row) for row in rows]

    def all_bindings(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM role_bindings WHERE revoked_at IS NULL ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def create_binding(self, subject_type: str, subject_id: int, role: str, scope_type: str, scope_id: int,
                       created_by: int, expires_at: Optional[str] = None, note: str = "") -> int:
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO role_bindings(subject_type, subject_id, role, scope_type, scope_id, expires_at, created_by, created_at, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (subject_type, subject_id, role, scope_type, scope_id, expires_at, created_by, utcnow(), note),
            )
            return int(cur.lastrowid)

    def delete_binding(self, binding_id: int) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM role_bindings WHERE id = ?", (binding_id,)).rowcount > 0

    def delete_user_bindings(self, user_id: int) -> None:
        with self._db() as db:
            db.execute("DELETE FROM role_bindings WHERE subject_type = 'user' AND subject_id = ?", (user_id,))

    def get_binding(self, binding_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM role_bindings WHERE id = ? AND revoked_at IS NULL", (binding_id,)).fetchone()
        return dict(row) if row else None

    def project_ids_for_user(self, user_id: int, permission_roles: Dict[str, set],
                             permission: str) -> List[int]:
        bindings = self.list_bindings(user_id=user_id)
        if any(b["scope_type"] == "global" and (
            permission in permission_roles.get(b["role"], set()) or "*" in permission_roles.get(b["role"], set())
        ) for b in bindings):
            with self._db() as db:
                return [r["id"] for r in db.execute("SELECT id FROM projects ORDER BY id")]
        return sorted({b["scope_id"] for b in bindings if b["scope_type"] == "project" and (
            permission in permission_roles.get(b["role"], set()) or "*" in permission_roles.get(b["role"], set())
        )})

    def namespace_ids_for_user(self,user_id: int,permission_roles: Dict[str,set],permission: str) -> List[int]:
        bindings=self.list_bindings(user_id=user_id,scope_type='namespace')
        return sorted({b['scope_id'] for b in bindings if permission in permission_roles.get(b['role'],set()) or '*' in permission_roles.get(b['role'],set())})

    def user_has_scope_permission(self, user_id: int, permission_roles: Dict[str, set], permission: str,
                                  scope_type: str, scope_id: int) -> bool:
        user = self.get_user(user_id)
        if not user or not user["enabled"]:
            return False
        for binding in self.list_bindings(user_id=user_id):
            if binding["scope_type"] == "global" or (
                binding["scope_type"] == scope_type and binding["scope_id"] == scope_id
            ):
                grants = permission_roles.get(binding["role"], set())
                if permission in grants or "*" in grants:
                    return True
        return False

    def bind_user_role(self, user_id: int, role: str, scope_type: str, scope_id: int,
                       created_by: Optional[int] = None, note: str = "") -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR IGNORE INTO role_bindings(subject_type, subject_id, role, scope_type, scope_id, created_by, created_at, note) "
                "VALUES ('user', ?, ?, ?, ?, ?, ?, ?)",
                (user_id, role, scope_type, scope_id, created_by, utcnow(), note),
            )
