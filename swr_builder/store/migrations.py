"""版本化 schema 迁移。

每个迁移只写自己的变更；记录检查、校验和、事务与写入 schema_migrations 由 `apply_migrations` 统一处理。
已发布迁移的版本号、名称（校验和 = sha256(名称)）和内容不可修改，否则存量数据库启动时会校验失败。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Callable, List, Tuple

from ..images import registry_path_parts
from .schema import utcnow

Migration = Tuple[int, str, Callable[[sqlite3.Connection], None]]


def _project_scopes(db: sqlite3.Connection) -> None:
    for table in ("apps", "jobs", "deployments"):
        columns = {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if "project_id" not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN project_id INTEGER")

    now = utcnow()
    project = db.execute("SELECT id FROM projects WHERE slug = 'default'").fetchone()
    if project:
        project_id = project["id"]
    else:
        project_id = db.execute(
            "INSERT INTO projects(slug, name, created_at, updated_at) VALUES ('default', '默认项目', ?, ?)",
            (now, now),
        ).lastrowid

    db.execute("UPDATE apps SET project_id = ? WHERE project_id IS NULL", (project_id,))
    db.execute("UPDATE jobs SET project_id = ? WHERE project_id IS NULL", (project_id,))
    db.execute("UPDATE deployments SET project_id = ? WHERE project_id IS NULL", (project_id,))

    for app_row in db.execute("SELECT data FROM apps WHERE project_id = ?", (project_id,)).fetchall():
        app = json.loads(app_row["data"])
        namespace, repository = app.get("organization"), app.get("repository")
        if namespace and repository:
            db.execute(
                "INSERT OR IGNORE INTO project_registry_orgs(project_id, namespace, base_prefixes_json) VALUES (?, ?, ?)",
                (project_id, namespace, json.dumps([repository], ensure_ascii=False)),
            )
        parts = registry_path_parts(app.get("base_image", ""))
        if parts:
            db.execute(
                "INSERT OR IGNORE INTO project_registry_orgs(project_id, namespace, base_prefixes_json) VALUES (?, ?, ?)",
                (project_id, parts[0], json.dumps([parts[1]], ensure_ascii=False)),
            )

    # 历史行为冻结：v1.1.1 的角色 → 授权映射（新用户的映射见 store.accounts.LEGACY_ROLE_BINDINGS）
    for user in db.execute("SELECT id, role FROM users").fetchall():
        role = user["role"]
        if role == "admin":
            binding = ("platform_admin", "global", 0)
        elif role == "developer":
            binding = ("developer", "project", project_id)
        elif role == "viewer":
            binding = ("project_viewer", "project", project_id)
        else:
            continue
        db.execute(
            "INSERT OR IGNORE INTO role_bindings(subject_type, subject_id, role, scope_type, scope_id, created_at, note) "
            "VALUES ('user', ?, ?, ?, ?, ?, 'migrated from v1.1.1 role')",
            (user["id"], *binding, now),
        )

    db.execute("CREATE INDEX IF NOT EXISTS idx_apps_project ON apps(project_id, id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_deployments_project ON deployments(project_id, id)")


def _cluster_namespaces(db: sqlite3.Connection) -> None:
    """表已在 SCHEMA_EXT 中创建，这里只登记版本。"""


def _services_and_deployments(db: sqlite3.Connection) -> None:
    columns = {r["name"] for r in db.execute("PRAGMA table_info(deployments)").fetchall()}
    additions = {
        "service_id": "INTEGER", "namespace_id": "INTEGER", "revision_id": "INTEGER",
        "kind": "TEXT NOT NULL DEFAULT 'deploy'", "digest": "TEXT", "idempotency_key": "TEXT", "request_hash": "TEXT",
        "policy_snapshot": "TEXT", "lease_owner": "TEXT", "lease_until": "TEXT", "fencing_token": "INTEGER",
    }
    for name, definition in additions.items():
        if name not in columns:
            db.execute(f"ALTER TABLE deployments ADD COLUMN {name} {definition}")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_deployments_idempotency ON deployments(created_by,service_id,idempotency_key) "
               "WHERE idempotency_key IS NOT NULL")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_deployments_active_service ON deployments(service_id) "
               "WHERE service_id IS NOT NULL AND status IN ('applying','verifying')")


def _scope_guards(db: sqlite3.Connection) -> None:
    for table in ("apps", "jobs", "deployments"):
        for operation in ("INSERT", "UPDATE"):
            trigger = f"{table}_project_guard_{operation.lower()}"
            db.execute(f"CREATE TRIGGER IF NOT EXISTS {trigger} BEFORE {operation} ON {table} "
                       "WHEN NEW.project_id IS NULL OR NOT EXISTS(SELECT 1 FROM projects WHERE id=NEW.project_id) "
                       f"BEGIN SELECT RAISE(ABORT,'invalid {table}.project_id'); END")


def _idempotency_hash(db: sqlite3.Connection) -> None:
    columns = {r["name"] for r in db.execute("PRAGMA table_info(deployments)").fetchall()}
    if "request_hash" not in columns:
        db.execute("ALTER TABLE deployments ADD COLUMN request_hash TEXT")


def _custom_roles(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS custom_roles (code TEXT PRIMARY KEY,name TEXT NOT NULL,"
               "scope_type TEXT NOT NULL CHECK(scope_type IN ('global','project','namespace')),"
               "permissions TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',"
               "enabled INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL)")


def _reconcile_legacy_admins(db: sqlite3.Connection) -> None:
    """v1.3.0-dev.2 起权限只来自 role_bindings。此前 users.role = 'admin' 无论授权如何都是超级管理员，
    这里为这些账号补齐（或恢复已过期/撤销的）平台管理员授权，保证升级前后的实际权限一致。"""
    now = utcnow()
    for user in db.execute("SELECT id FROM users WHERE role = 'admin'").fetchall():
        row = db.execute("SELECT id FROM role_bindings WHERE subject_type = 'user' AND subject_id = ? "
                         "AND role = 'platform_admin' AND scope_type = 'global' AND scope_id = 0",
                         (user["id"],)).fetchone()
        if row:
            db.execute("UPDATE role_bindings SET revoked_at = NULL, expires_at = NULL WHERE id = ?", (row["id"],))
        else:
            db.execute("INSERT INTO role_bindings(subject_type, subject_id, role, scope_type, scope_id, created_at, note) "
                       "VALUES ('user', ?, 'platform_admin', 'global', 0, ?, 'reconciled from legacy admin role')",
                       (user["id"], now))
    # 过期时间按字符串与 UTC 当前时间比较，统一换算成 UTC，避免 +08:00 等时区偏移导致授权晚于预期失效
    for row in db.execute("SELECT id, expires_at FROM role_bindings WHERE expires_at IS NOT NULL").fetchall():
        try:
            parsed = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        normalized = parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
        if normalized != row["expires_at"]:
            db.execute("UPDATE role_bindings SET expires_at = ? WHERE id = ?", (normalized, row["id"]))
    db.execute("CREATE INDEX IF NOT EXISTS idx_role_bindings_subject ON role_bindings(subject_type, subject_id)")


MIGRATIONS: List[Migration] = [
    (1, "default-project-and-scoped-bindings-v1", _project_scopes),
    (2, "clusters-namespaces-project-allocation-v1", _cluster_namespaces),
    (3, "service-revisions-approvals-deployment-scope-v1", _services_and_deployments),
    (4, "project-scope-integrity-guards-v1", _scope_guards),
    (5, "deployment-idempotency-payload-hash-v1", _idempotency_hash),
    (6, "custom-scoped-role-definitions-v1", _custom_roles),
    (7, "bindings-only-authorization-legacy-admin-reconcile-v1", _reconcile_legacy_admins),
]


def apply_migrations(db: sqlite3.Connection, migrations: List[Migration] = MIGRATIONS) -> None:
    for version, name, migrate in migrations:
        checksum = hashlib.sha256(name.encode("ascii")).hexdigest()
        row = db.execute("SELECT name, checksum FROM schema_migrations WHERE version = ?", (version,)).fetchone()
        if row:
            if row["name"] != name or row["checksum"] != checksum:
                raise RuntimeError(f"schema migration {version} checksum mismatch")
            continue
        db.execute("BEGIN IMMEDIATE")
        try:
            migrate(db)
            db.execute("INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                       (version, name, checksum, utcnow()))
            db.commit()
        except Exception:
            db.rollback()
            raise
