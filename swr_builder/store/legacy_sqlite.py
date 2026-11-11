"""v1.x 的 SQLite 结构与迁移，只用于 `python -m swr_builder db import-sqlite`。

导入时先用 SQLite 备份接口把旧库复制到临时文件，在副本上补齐到 v7 结构，再读出数据写入 PostgreSQL。
原文件只读打开，不做任何修改。下面的结构与迁移按 v1.3.0-dev.2 原样保留，不要修改。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Tuple

from ..images import registry_path_parts
from .schema import utcnow

Migration = Tuple[int, str, Callable[[sqlite3.Connection], None]]
LEGACY_VERSION = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS apps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER,
    app_name TEXT,
    repository TEXT,
    requested_mode TEXT,
    requested_tag TEXT,
    requested_base_tag TEXT,
    mode TEXT,
    prev_tag TEXT,
    base_tag TEXT,
    base_image TEXT,
    new_tag TEXT,
    image TEXT,
    digest TEXT,
    status TEXT NOT NULL,
    step TEXT,
    operator TEXT,
    remark TEXT,
    files TEXT,
    notes TEXT,
    app_snapshot TEXT,
    dockerfile TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_app ON jobs(app_id, id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""

SCHEMA_EXT = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT,
    email TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT,
    password_changed_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT,
    ip TEXT,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    prefix TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT,
    updated_by TEXT
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    username TEXT,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT,
    ip TEXT
);
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_registry_orgs (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    namespace TEXT NOT NULL,
    base_prefixes_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(project_id, namespace)
);
CREATE TABLE IF NOT EXISTS role_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type TEXT NOT NULL CHECK(subject_type IN ('user','group')),
    subject_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('global','project','namespace')),
    scope_id INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    revoked_at TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    UNIQUE(subject_type, subject_id, role, scope_type, scope_id)
);
CREATE INDEX IF NOT EXISTS idx_role_bindings_scope ON role_bindings(scope_type, scope_id, subject_type, subject_id);
CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    auth_type TEXT NOT NULL CHECK(auth_type IN ('sa_token','client_cert','cce_cert')),
    server TEXT NOT NULL,
    credential_encrypted TEXT NOT NULL,
    cce_cluster_id TEXT,
    credential_expires_at TEXT,
    cert_duration_days INTEGER NOT NULL DEFAULT 30,
    status TEXT NOT NULL DEFAULT 'unverified',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS namespaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id INTEGER NOT NULL REFERENCES clusters(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    tier TEXT NOT NULL CHECK(tier IN ('dev','test','staging','prod')),
    policy_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(cluster_id, name)
);
CREATE TABLE IF NOT EXISTS project_namespaces (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    namespace_id INTEGER NOT NULL REFERENCES namespaces(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(project_id, namespace_id)
);
CREATE INDEX IF NOT EXISTS idx_namespaces_cluster ON namespaces(cluster_id, name);
CREATE INDEX IF NOT EXISTS idx_project_namespaces_namespace ON project_namespaces(namespace_id, project_id);
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    namespace_id INTEGER NOT NULL REFERENCES namespaces(id) ON DELETE RESTRICT,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE RESTRICT,
    current_revision_id INTEGER,
    deployed_revision_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(namespace_id,name)
);
CREATE TABLE IF NOT EXISTS service_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE RESTRICT,
    number INTEGER NOT NULL,
    spec_json TEXT NOT NULL,
    secret_versions_json TEXT NOT NULL DEFAULT '{}',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(service_id,number)
);
CREATE TABLE IF NOT EXISTS service_secrets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    version INTEGER NOT NULL,
    ciphertext TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(service_id,key,version)
);
CREATE INDEX IF NOT EXISTS idx_services_project ON services(project_id,id);
CREATE INDEX IF NOT EXISTS idx_services_namespace ON services(namespace_id,id);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id INTEGER NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    approver_id INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('approved','rejected')),
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(deployment_id,approver_id)
);
CREATE TABLE IF NOT EXISTS deploy_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deployments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id INTEGER,
    target_name TEXT,
    target_type TEXT,
    environment TEXT,
    app_id INTEGER,
    app_name TEXT,
    job_id INTEGER,
    image TEXT NOT NULL,
    request_hash TEXT,
    status TEXT NOT NULL,
    trigger TEXT,
    operator TEXT,
    created_by TEXT,
    remark TEXT,
    message TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_deployments_target ON deployments(target_id, id);
"""


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


def apply_legacy_migrations(db: sqlite3.Connection, migrations: List[Migration] = MIGRATIONS) -> None:
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


def open_upgraded_copy(path: Path) -> Tuple[sqlite3.Connection, Path]:
    """把旧库复制到临时目录并补齐到 v7，返回 (连接, 临时目录)。调用方负责关闭连接并删除临时目录。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到 SQLite 数据库 {path}")
    workdir = Path(tempfile.mkdtemp(prefix="swr-sqlite-import-"))
    copy = workdir / "builder.db"
    try:
        source = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        try:
            target = sqlite3.connect(str(copy))
            try:
                source.backup(target)  # 包含 WAL 中已提交的数据
            finally:
                target.close()
        finally:
            source.close()
        db = sqlite3.connect(str(copy))
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA)
        db.executescript(SCHEMA_EXT)
        cols = {r["name"] for r in db.execute("PRAGMA table_info(jobs)").fetchall()}
        if "created_by" not in cols:
            db.execute("ALTER TABLE jobs ADD COLUMN created_by TEXT")
        db.commit()
        apply_legacy_migrations(db)
        versions = [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
        if max(versions or [0]) > LEGACY_VERSION:
            raise RuntimeError(f"SQLite 数据库版本 {max(versions)} 高于 v1.3 支持的 {LEGACY_VERSION}")
        return db, workdir
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
