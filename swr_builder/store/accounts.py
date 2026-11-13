"""账号、会话与 API 令牌。

权限只来自 role_bindings。users.role 是 v1.3 及更早版本的"基础角色"字段：
- 新账号写入 'admin'（持有平台管理员授权）或 'member'；
- 以旧值 admin/developer/viewer 创建账号时，按 LEGACY_ROLE_BINDINGS 自动补一条授权（兼容旧接口与首次启动）；
- 授予/撤销平台管理员授权时同步为 'admin'/'member'，回退到旧版本时权限保持一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .schema import USER_FIELDS, utcnow

# 旧基础角色 → 授权（role, scope_type, 是否默认项目, 备注）。
LEGACY_ROLE_BINDINGS = {
    "admin": ("platform_admin", "global", False, "legacy administrator role"),
    "developer": ("developer", "project", True, "default project membership"),
    "viewer": ("project_viewer", "project", True, "default project membership"),
}
_ACTIVE_ADMIN_SQL = ("SELECT u.id FROM role_bindings b JOIN users u ON u.id = b.subject_id "
                     "WHERE b.subject_type = 'user' AND b.role = 'platform_admin' AND b.scope_type = 'global' "
                     "AND b.revoked_at IS NULL AND (b.expires_at IS NULL OR b.expires_at > %s) "
                     "AND u.deleted_at IS NULL")


class AccountsMixin:
    def _bind_legacy_role(self, db, user_id: int, role: str, created_by: Optional[int]) -> None:
        mapping = LEGACY_ROLE_BINDINGS.get(role)
        if not mapping:
            return
        binding_role, scope_type, default_project, note = mapping
        scope_id = 0
        if default_project:
            row = db.execute("SELECT id FROM projects WHERE slug = 'default'").fetchone()
            if not row:
                raise RuntimeError("default project migration is missing")
            scope_id = int(row["id"])
        self.insert_ignore(db, "role_bindings", {
            "subject_type": "user", "subject_id": user_id, "role": binding_role, "scope_type": scope_type,
            "scope_id": scope_id, "created_by": created_by, "created_at": utcnow(), "note": note})

    def list_users(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM users WHERE deleted_at IS NULL ORDER BY id").fetchall()]

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE id = %s AND deleted_at IS NULL", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_name(self, username: str) -> Optional[Dict[str, Any]]:
        """用户名不区分大小写（与 v1.x 行为一致）。"""
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE lower(username) = lower(%s) AND deleted_at IS NULL", (username,)).fetchone()
        return dict(row) if row else None

    def create_user(self, *, grants: Sequence[Dict[str, Any]] = (), created_by: Optional[int] = None,
                    **fields: Any) -> Dict[str, Any]:
        """创建账号，并在同一事务内写入初始授权（grants 项：role/scope_type/scope_id/expires_at/note）。"""
        now = utcnow()
        fields.setdefault("created_at", now)
        fields.setdefault("updated_at", now)
        unknown = set(fields) - set(USER_FIELDS)
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        fields = self.coerce(fields)
        with self._db() as db, self._unique("用户名已存在", "uq_users_username"):
            user_id = int(db.execute(f"INSERT INTO users({', '.join(fields)}) VALUES ({self.marks(fields)}) RETURNING id",
                                     tuple(fields.values())).fetchone()["id"])
            self._bind_legacy_role(db, user_id, fields.get("role", ""), created_by)
            for grant in grants:
                self._insert_binding(db, user_id, grant["role"], grant["scope_type"], grant["scope_id"], created_by,
                                     grant.get("expires_at"), grant.get("note", ""))
        return self.get_user(user_id)  # type: ignore[return-value]

    def set_platform_admin(self, user_id: int, enabled: bool, actor_id: Optional[int]) -> None:
        """授予或撤销平台管理员授权，并同步旧的 users.role 字段（单事务）。"""
        with self._db() as db:
            if enabled:
                existing = db.execute("SELECT id FROM role_bindings WHERE subject_type = 'user' AND subject_id = %s "
                                      "AND role = 'platform_admin' AND scope_type = 'global' AND scope_id = 0",
                                      (user_id,)).fetchone()
                if existing:
                    db.execute("UPDATE role_bindings SET revoked_at = NULL, expires_at = NULL WHERE id = %s",
                               (existing["id"],))
                else:
                    self._insert_binding(db, user_id, "platform_admin", "global", 0, actor_id, None, "platform administrator")
                db.execute("UPDATE users SET role = 'admin', updated_at = %s WHERE id = %s", (utcnow(), user_id))
            else:
                db.execute("DELETE FROM role_bindings WHERE subject_type = 'user' AND subject_id = %s "
                           "AND role = 'platform_admin' AND scope_type = 'global'", (user_id,))
                db.execute("UPDATE users SET role = 'member', updated_at = %s WHERE id = %s AND role = 'admin'",
                           (utcnow(), user_id))

    def update_user(self, user_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
        fields.setdefault("updated_at", utcnow())
        self._update("users", USER_FIELDS, user_id, fields)
        return self.get_user(user_id)

    def delete_user(self, user_id: int) -> bool:
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
            db.execute("DELETE FROM api_tokens WHERE user_id = %s", (user_id,))
            db.execute("DELETE FROM role_bindings WHERE subject_type = 'user' AND subject_id = %s", (user_id,))
            # 软删除：账号记录保留（审计与历史引用），会话与令牌已经失效
            return db.execute("UPDATE users SET deleted_at = %s, enabled = FALSE, updated_at = %s "
                              "WHERE id = %s AND deleted_at IS NULL",
                              (utcnow(), utcnow(), user_id)).rowcount > 0

    def count_users(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM users WHERE deleted_at IS NULL").fetchone()[0]

    def is_platform_admin(self, user_id: int) -> bool:
        """启用且持有有效平台管理员授权的账号。"""
        with self._db() as db:
            row = db.execute(_ACTIVE_ADMIN_SQL + " AND u.enabled AND u.id = %s", (utcnow(), user_id)).fetchone()
        return row is not None

    def count_platform_admins(self, exclude_id: Optional[int] = None) -> int:
        """启用且持有有效平台管理员授权的账号数（可排除一个账号）。"""
        with self._db() as db:
            rows = db.execute(_ACTIVE_ADMIN_SQL + " AND u.enabled", (utcnow(),)).fetchall()
        return len({row["id"] for row in rows} - {exclude_id})

    def create_session(self, token_hash: str, user_id: int, expires_at: str, ip: str, user_agent: str) -> None:
        now = utcnow()
        with self._db() as db:
            db.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at, last_seen_at, ip, user_agent) "
                       "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                       (token_hash, user_id, now, expires_at, now, ip, user_agent[:300]))

    def get_session(self, token_hash: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM sessions WHERE token_hash = %s", (token_hash,)).fetchone()
        return dict(row) if row else None

    def touch_session(self, token_hash: str) -> None:
        with self._db() as db:
            db.execute("UPDATE sessions SET last_seen_at = %s WHERE token_hash = %s", (utcnow(), token_hash))

    def list_sessions(self, user_id: int) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM sessions WHERE user_id = %s AND expires_at > %s "
                              "ORDER BY last_seen_at DESC", (user_id, utcnow())).fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, token_hash: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))

    def delete_user_sessions(self, user_id: int, keep: Optional[str] = None) -> int:
        with self._db() as db:
            return db.execute("DELETE FROM sessions WHERE user_id = %s AND token_hash <> %s",
                              (user_id, keep or "")).rowcount

    def purge_expired_sessions(self) -> int:
        with self._db() as db:
            return db.execute("DELETE FROM sessions WHERE expires_at <= %s", (utcnow(),)).rowcount

    def create_api_token(self, user_id: int, name: str, token_hash: str, prefix: str,
                         expires_at: Optional[str]) -> int:
        with self._db() as db:
            return int(db.execute("INSERT INTO api_tokens(user_id, name, token_hash, prefix, created_at, expires_at) "
                                  "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                                  (user_id, name, token_hash, prefix, utcnow(), expires_at)).fetchone()["id"])

    def list_api_tokens(self, user_id: int) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT id, user_id, name, prefix, created_at, expires_at, last_used_at "
                              "FROM api_tokens WHERE user_id = %s ORDER BY id DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def find_api_token(self, token_hash: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM api_tokens WHERE token_hash = %s", (token_hash,)).fetchone()
        return dict(row) if row else None

    def touch_api_token(self, token_id: int) -> None:
        with self._db() as db:
            db.execute("UPDATE api_tokens SET last_used_at = %s WHERE id = %s", (utcnow(), token_id))

    def delete_api_token(self, token_id: int, user_id: int) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM api_tokens WHERE id = %s AND user_id = %s",
                              (token_id, user_id)).rowcount > 0

    def delete_user_api_tokens(self, user_id: int) -> None:
        with self._db() as db:
            db.execute("DELETE FROM api_tokens WHERE user_id = %s", (user_id,))
