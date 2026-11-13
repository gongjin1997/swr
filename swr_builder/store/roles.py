"""自定义角色定义；有效权限在每次请求时读取。"""
import json

from .schema import utcnow


class RolesMixin:
    def list_custom_roles(self):
        with self._db() as db:
            rows = db.execute("SELECT * FROM custom_roles WHERE deleted_at IS NULL ORDER BY name").fetchall()
        return [dict(row, permissions=json.loads(row["permissions"])) for row in rows]

    def save_custom_role(self, code, name, scope_type, permissions, description, enabled=True):
        """新建或更新角色；已有角色的范围不变。"""
        values = {"name": name, "permissions": json.dumps(permissions), "description": description,
                  "enabled": bool(enabled), "updated_at": utcnow()}
        with self._db() as db:
            sets = ", ".join(f"{column} = %s" for column in values)
            # 已软删除的同名角色在这里复活
            if db.execute(f"UPDATE custom_roles SET {sets}, deleted_at = NULL WHERE code = %s",
                          (*values.values(), code)).rowcount:
                return
            if not self.insert_ignore(db, "custom_roles", {"code": code, "scope_type": scope_type, **values}):
                db.execute(f"UPDATE custom_roles SET {sets}, deleted_at = NULL WHERE code = %s",
                           (*values.values(), code))

    def delete_custom_role(self, code):
        with self._db() as db:
            used = db.execute("SELECT 1 FROM role_bindings WHERE role = %s AND revoked_at IS NULL "
                              "AND (expires_at IS NULL OR expires_at > %s)", (code, utcnow())).fetchone()
            if used:
                return False
            return db.execute("UPDATE custom_roles SET deleted_at = %s WHERE code = %s AND deleted_at IS NULL",
                              (utcnow(), code)).rowcount > 0
