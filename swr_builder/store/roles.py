"""Custom role definitions; effective permissions are read on every request."""
import json

from .schema import utcnow


class RolesMixin:
    def list_custom_roles(self):
        with self._db() as db:
            rows = db.execute("SELECT * FROM custom_roles ORDER BY name").fetchall()
        return [dict(row, permissions=json.loads(row["permissions"])) for row in rows]

    def save_custom_role(self, code, name, scope_type, permissions, description, enabled=True):
        with self._db() as db:
            db.execute("INSERT INTO custom_roles(code,name,scope_type,permissions,description,enabled,updated_at) "
                       "VALUES(?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,"
                       "permissions=excluded.permissions,description=excluded.description,"
                       "enabled=excluded.enabled,updated_at=excluded.updated_at",
                       (code, name, scope_type, json.dumps(permissions), description, int(enabled), utcnow()))

    def delete_custom_role(self, code):
        with self._db() as db:
            used = db.execute("SELECT 1 FROM role_bindings WHERE role=? AND revoked_at IS NULL "
                              "AND (expires_at IS NULL OR expires_at>?)", (code, utcnow())).fetchone()
            if used:
                return False
            return db.execute("DELETE FROM custom_roles WHERE code=?", (code,)).rowcount > 0
