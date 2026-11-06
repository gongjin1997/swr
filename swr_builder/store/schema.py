"""表结构与字段白名单。"""
from datetime import datetime, timezone

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

USER_FIELDS = ("username", "display_name", "email", "password_hash", "role", "enabled", "must_change_password",
               "created_at", "updated_at", "last_login_at", "password_changed_at")
DEPLOYMENT_FIELDS = ("target_id", "target_name", "target_type", "environment", "app_id", "app_name", "job_id", "image",
                     "project_id", "service_id", "namespace_id", "revision_id", "kind", "digest", "idempotency_key",
                     "request_hash", "policy_snapshot", "status", "trigger", "operator", "created_by", "remark", "message", "error",
                     "lease_owner", "lease_until", "fencing_token", "created_at", "started_at", "finished_at")

JOB_FIELDS = (
    "app_id", "app_name", "repository", "requested_mode", "requested_tag", "requested_base_tag", "mode",
    "prev_tag", "base_tag", "base_image", "new_tag", "image", "digest", "status", "step", "operator",
    "remark", "files", "notes", "app_snapshot", "dockerfile", "error", "project_id", "created_at", "started_at", "finished_at",
    "created_by",
)
JSON_FIELDS = {"files", "notes", "app_snapshot"}
LIST_FIELDS = ("id", "app_id", "app_name", "repository", "mode", "requested_mode", "prev_tag", "base_tag",
               "new_tag", "requested_tag", "image", "digest", "status", "step", "operator", "remark", "error",
               "created_at", "started_at", "finished_at", "created_by", "project_id")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
