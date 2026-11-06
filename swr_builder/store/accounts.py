"""账号、会话与 API 令牌。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .schema import USER_FIELDS, utcnow

# 账号基础角色 → 默认授权（role, scope_type, 是否默认项目, 备注）。迁移 v1 中有一份冻结的历史副本。
LEGACY_ROLE_BINDINGS = {
    "admin": ("platform_admin", "global", False, "legacy administrator role"),
    "developer": ("developer", "project", True, "default project membership"),
    "viewer": ("project_viewer", "project", True, "default project membership"),
}


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
        db.execute(
            "INSERT OR IGNORE INTO role_bindings(subject_type, subject_id, role, scope_type, scope_id, created_by, created_at, note) "
            "VALUES ('user', ?, ?, ?, ?, ?, ?, ?)",
            (user_id, binding_role, scope_type, scope_id, created_by, utcnow(), note),
        )

    def reset_user_role_bindings(self, user_id: int, role: str, created_by: Optional[int]) -> None:
        """基础角色变化时，用该角色的默认授权替换用户的全部授权（单事务）。"""
        with self._db() as db:
            db.execute("DELETE FROM role_bindings WHERE subject_type = 'user' AND subject_id = ?", (user_id,))
            self._bind_legacy_role(db, user_id, role, created_by)

    def list_users(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM users ORDER BY id").fetchall()]

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_name(self, username: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    def create_user(self, **fields: Any) -> Dict[str, Any]:
        now = utcnow()
        fields.setdefault("created_at", now)
        fields.setdefault("updated_at", now)
        unknown = set(fields) - set(USER_FIELDS)
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        with self._db() as db:
            user_id = db.execute(f"INSERT INTO users({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
                                 tuple(fields.values())).lastrowid
            self._bind_legacy_role(db, user_id, fields.get("role", ""), None)
        return self.get_user(user_id)  # type: ignore[return-value]

    def update_user(self, user_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
        fields.setdefault("updated_at", utcnow())
        self._update("users", USER_FIELDS, user_id, fields)
        return self.get_user(user_id)

    def delete_user(self, user_id: int) -> bool:
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))
            db.execute("DELETE FROM role_bindings WHERE subject_type = 'user' AND subject_id = ?", (user_id,))
            return db.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount > 0

    def count_users(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def is_platform_admin(self,user_id: int) -> bool:
        user=self.get_user(user_id)
        if user and user['enabled'] and user['role']=='admin':return True
        now=utcnow()
        with self._db() as db:
            row=db.execute("SELECT 1 FROM role_bindings b JOIN users u ON u.id=b.subject_id WHERE b.subject_type='user' "
                "AND b.scope_type='global' AND b.role='platform_admin' AND b.revoked_at IS NULL AND u.enabled=1 "
                "AND (b.expires_at IS NULL OR b.expires_at>?) AND u.id=?",(now,user_id)).fetchone()
        return row is not None

    def count_platform_admins(self,exclude_id: Optional[int]=None) -> int:
        now=utcnow()
        with self._db() as db:
            rows=db.execute("SELECT id FROM users WHERE enabled=1 AND role='admin' UNION "
                "SELECT u.id FROM role_bindings b JOIN users u ON u.id=b.subject_id WHERE b.subject_type='user' "
                "AND b.scope_type='global' AND b.role='platform_admin' AND b.revoked_at IS NULL AND u.enabled=1 "
                "AND (b.expires_at IS NULL OR b.expires_at>?)",(now,)).fetchall()
        return sum(1 for row in rows if row['id']!=(exclude_id or 0))

    def create_session(self, token_hash: str, user_id: int, expires_at: str, ip: str, user_agent: str) -> None:
        now = utcnow()
        with self._db() as db:
            db.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at, last_seen_at, ip, user_agent) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)", (token_hash, user_id, now, expires_at, now, ip, user_agent[:300]))

    def get_session(self, token_hash: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)).fetchone()
        return dict(row) if row else None

    def touch_session(self, token_hash: str) -> None:
        with self._db() as db:
            db.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (utcnow(), token_hash))

    def list_sessions(self, user_id: int) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM sessions WHERE user_id = ? AND expires_at > ? ORDER BY last_seen_at DESC",
                              (user_id, utcnow())).fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, token_hash: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_user_sessions(self, user_id: int, keep: Optional[str] = None) -> int:
        with self._db() as db:
            return db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                              (user_id, keep or "")).rowcount

    def purge_expired_sessions(self) -> int:
        with self._db() as db:
            return db.execute("DELETE FROM sessions WHERE expires_at <= ?", (utcnow(),)).rowcount

    def create_api_token(self, user_id: int, name: str, token_hash: str, prefix: str,
                         expires_at: Optional[str]) -> int:
        with self._db() as db:
            return db.execute("INSERT INTO api_tokens(user_id, name, token_hash, prefix, created_at, expires_at) "
                              "VALUES (?, ?, ?, ?, ?, ?)",
                              (user_id, name, token_hash, prefix, utcnow(), expires_at)).lastrowid

    def list_api_tokens(self, user_id: int) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT id, user_id, name, prefix, created_at, expires_at, last_used_at "
                              "FROM api_tokens WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def find_api_token(self, token_hash: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM api_tokens WHERE token_hash = ?", (token_hash,)).fetchone()
        return dict(row) if row else None

    def touch_api_token(self, token_id: int) -> None:
        with self._db() as db:
            db.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (utcnow(), token_id))

    def delete_api_token(self, token_id: int, user_id: int) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM api_tokens WHERE id = ? AND user_id = ?", (token_id, user_id)).rowcount > 0

    def delete_user_api_tokens(self, user_id: int) -> None:
        with self._db() as db:
            db.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))
