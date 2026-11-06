"""SQLite 存储：应用、构建记录、账号、会话、系统设置、审计日志、部署（预留）。"""
from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

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


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.executescript(SCHEMA_EXT)
            # 1.0 → 1.1 升级：构建记录增加提交账号
            cols = {r["name"] for r in db.execute("PRAGMA table_info(jobs)").fetchall()}
            if "created_by" not in cols:
                db.execute("ALTER TABLE jobs ADD COLUMN created_by TEXT")
            self._migrate_project_scopes(db)
            self._migrate_cluster_namespaces(db)
            self._migrate_services_and_deployments(db)
            self._migrate_scope_guards(db)
            self._migrate_idempotency_hash(db)

    @staticmethod
    def _migrate_project_scopes(db: sqlite3.Connection) -> None:
        version = 1
        migration = "default-project-and-scoped-bindings-v1"
        checksum = hashlib.sha256(migration.encode("ascii")).hexdigest()
        row = db.execute("SELECT name, checksum FROM schema_migrations WHERE version = ?", (version,)).fetchone()
        if row:
            if row["name"] != migration or row["checksum"] != checksum:
                raise RuntimeError(f"schema migration {version} checksum mismatch")
            return

        db.execute("BEGIN IMMEDIATE")
        try:
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
                base_image = app.get("base_image", "")
                if isinstance(base_image, str) and "/" in base_image:
                    image_name = base_image.split("@", 1)[0].split(":", 1)[0]
                    parts = image_name.split("/")
                    if len(parts) >= 3 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
                        parts = parts[1:]
                    if len(parts) >= 2 and parts[0]:
                        db.execute(
                            "INSERT OR IGNORE INTO project_registry_orgs(project_id, namespace, base_prefixes_json) VALUES (?, ?, ?)",
                            (project_id, parts[0], json.dumps(["/".join(parts[1:])], ensure_ascii=False)),
                        )

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
            db.execute(
                "INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (version, migration, checksum, now),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def _migrate_cluster_namespaces(db: sqlite3.Connection) -> None:
        version = 2
        migration = "clusters-namespaces-project-allocation-v1"
        checksum = hashlib.sha256(migration.encode("ascii")).hexdigest()
        row = db.execute("SELECT name, checksum FROM schema_migrations WHERE version = ?", (version,)).fetchone()
        if row:
            if row["name"] != migration or row["checksum"] != checksum:
                raise RuntimeError(f"schema migration {version} checksum mismatch")
            return
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                       (version, migration, checksum, utcnow()))
            db.commit()
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def _migrate_services_and_deployments(db: sqlite3.Connection) -> None:
        version=3
        migration='service-revisions-approvals-deployment-scope-v1'
        checksum=hashlib.sha256(migration.encode('ascii')).hexdigest()
        row=db.execute('SELECT name,checksum FROM schema_migrations WHERE version=?',(version,)).fetchone()
        if row:
            if row['name']!=migration or row['checksum']!=checksum:raise RuntimeError(f'schema migration {version} checksum mismatch')
            return
        db.execute('BEGIN IMMEDIATE')
        try:
            columns={r['name'] for r in db.execute('PRAGMA table_info(deployments)').fetchall()}
            additions={
                'service_id':'INTEGER','namespace_id':'INTEGER','revision_id':'INTEGER','kind':"TEXT NOT NULL DEFAULT 'deploy'",
                'digest':'TEXT','idempotency_key':'TEXT','request_hash':'TEXT','policy_snapshot':'TEXT','lease_owner':'TEXT','lease_until':'TEXT','fencing_token':'INTEGER'
            }
            for name,definition in additions.items():
                if name not in columns:db.execute(f'ALTER TABLE deployments ADD COLUMN {name} {definition}')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_deployments_idempotency ON deployments(created_by,service_id,idempotency_key) WHERE idempotency_key IS NOT NULL")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_deployments_active_service ON deployments(service_id) WHERE service_id IS NOT NULL AND status IN ('applying','verifying')")
            db.execute('INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)',
                (version,migration,checksum,utcnow()))
            db.commit()
        except Exception:
            db.rollback();raise

    @staticmethod
    def _migrate_scope_guards(db: sqlite3.Connection) -> None:
        version=4
        migration='project-scope-integrity-guards-v1'
        checksum=hashlib.sha256(migration.encode('ascii')).hexdigest()
        row=db.execute('SELECT name,checksum FROM schema_migrations WHERE version=?',(version,)).fetchone()
        if row:
            if row['name']!=migration or row['checksum']!=checksum:raise RuntimeError(f'schema migration {version} checksum mismatch')
            return
        db.execute('BEGIN IMMEDIATE')
        try:
            for table in ('apps','jobs','deployments'):
                for operation in ('INSERT','UPDATE'):
                    trigger=f'{table}_project_guard_{operation.lower()}'
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {trigger} BEFORE {operation} ON {table} "
                        "WHEN NEW.project_id IS NULL OR NOT EXISTS(SELECT 1 FROM projects WHERE id=NEW.project_id) "
                        f"BEGIN SELECT RAISE(ABORT,'invalid {table}.project_id'); END")
            db.execute('INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)',
                (version,migration,checksum,utcnow()))
            db.commit()
        except Exception:
            db.rollback();raise

    @staticmethod
    def _migrate_idempotency_hash(db: sqlite3.Connection) -> None:
        version=5
        migration='deployment-idempotency-payload-hash-v1'
        checksum=hashlib.sha256(migration.encode('ascii')).hexdigest()
        row=db.execute('SELECT name,checksum FROM schema_migrations WHERE version=?',(version,)).fetchone()
        if row:
            if row['name']!=migration or row['checksum']!=checksum:raise RuntimeError(f'schema migration {version} checksum mismatch')
            return
        db.execute('BEGIN IMMEDIATE')
        try:
            columns={r['name'] for r in db.execute('PRAGMA table_info(deployments)').fetchall()}
            if 'request_hash' not in columns:db.execute('ALTER TABLE deployments ADD COLUMN request_hash TEXT')
            db.execute('INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)',
                (version,migration,checksum,utcnow()))
            db.commit()
        except Exception:
            db.rollback();raise

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(str(self.path), timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    # ------------------------------------------------------------------ 应用
    @staticmethod
    def _app(row: sqlite3.Row) -> Dict[str, Any]:
        data = json.loads(row["data"])
        data.update(id=row["id"], created_at=row["created_at"], updated_at=row["updated_at"])
        if "project_id" in row.keys():
            data["project_id"] = row["project_id"]
        return data

    @staticmethod
    def _app_data(data: Dict[str, Any]) -> str:
        clean = {k: v for k, v in data.items() if k not in ("id", "created_at", "updated_at")}
        return json.dumps(clean, ensure_ascii=False)

    def list_apps(self, project_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        if project_ids is not None and not project_ids:
            return []
        with self._db() as db:
            if project_ids is None:
                rows = db.execute("SELECT * FROM apps ORDER BY name COLLATE NOCASE").fetchall()
            else:
                marks = ",".join("?" for _ in project_ids)
                rows = db.execute(f"SELECT * FROM apps WHERE project_id IN ({marks}) ORDER BY name COLLATE NOCASE",
                                  project_ids).fetchall()
        return [self._app(r) for r in rows]

    def get_app(self, app_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
        return self._app(row) if row else None

    def list_projects(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE").fetchall()
            orgs = db.execute("SELECT * FROM project_registry_orgs ORDER BY namespace").fetchall()
            allocations = db.execute("SELECT project_id,namespace_id FROM project_namespaces ORDER BY namespace_id").fetchall()
        by_project: Dict[int, List[Dict[str, Any]]] = {}
        for row in orgs:
            by_project.setdefault(row["project_id"], []).append({
                "namespace": row["namespace"], "base_prefixes": json.loads(row["base_prefixes_json"])
            })
        namespace_ids: Dict[int,List[int]]={}
        for row in allocations:namespace_ids.setdefault(row['project_id'],[]).append(row['namespace_id'])
        result = []
        for row in rows:
            item = dict(row)
            item["registry_orgs"] = by_project.get(item["id"], [])
            item["namespace_ids"] = namespace_ids.get(item["id"],[])
            result.append(item)
        return result

    def get_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        return next((p for p in self.list_projects() if p["id"] == project_id), None)

    def list_clusters(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows=db.execute("SELECT id,name,auth_type,server,cce_cluster_id,credential_expires_at,cert_duration_days,status,created_at,updated_at FROM clusters ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(row) for row in rows]

    def get_cluster(self, cluster_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row=db.execute("SELECT id,name,auth_type,server,cce_cluster_id,credential_expires_at,cert_duration_days,status,created_at,updated_at FROM clusters WHERE id=?",(cluster_id,)).fetchone()
        return dict(row) if row else None

    def get_cluster_record(self, cluster_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row=db.execute("SELECT * FROM clusters WHERE id=?",(cluster_id,)).fetchone()
        return dict(row) if row else None

    def create_cluster(self, data: Dict[str, Any]) -> int:
        now=utcnow()
        with self._db() as db:
            cur=db.execute("INSERT INTO clusters(name,auth_type,server,credential_encrypted,cce_cluster_id,credential_expires_at,cert_duration_days,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (data['name'],data['auth_type'],data['server'],data['credential_encrypted'],data.get('cce_cluster_id'),data.get('credential_expires_at'),data.get('cert_duration_days',30),data.get('status','unverified'),now,now))
            return int(cur.lastrowid)

    def update_cluster(self, cluster_id: int, data: Dict[str, Any]) -> bool:
        fields=('name','auth_type','server','credential_encrypted','cce_cluster_id','credential_expires_at','cert_duration_days','status')
        values=[data.get(k) for k in fields]
        with self._db() as db:
            return db.execute("UPDATE clusters SET name=?,auth_type=?,server=?,credential_encrypted=?,cce_cluster_id=?,credential_expires_at=?,cert_duration_days=?,status=?,updated_at=? WHERE id=?",
                (*values,utcnow(),cluster_id)).rowcount>0

    def set_cluster_status(self, cluster_id: int, status: str) -> None:
        with self._db() as db:
            db.execute("UPDATE clusters SET status=?,updated_at=? WHERE id=?",(status,utcnow(),cluster_id))

    @staticmethod
    def _namespace(row: sqlite3.Row) -> Dict[str, Any]:
        item=dict(row)
        item['policy']=json.loads(item.pop('policy_json'))
        return item

    def list_namespaces(self, namespace_ids: Optional[List[int]] = None, cluster_id: Optional[int] = None,
                        tier: Optional[str] = None) -> List[Dict[str, Any]]:
        where,args=[],[]
        if namespace_ids is not None:
            if not namespace_ids:return []
            where.append(f"n.id IN ({','.join('?' for _ in namespace_ids)})");args.extend(namespace_ids)
        if cluster_id is not None:where.append('n.cluster_id=?');args.append(cluster_id)
        if tier is not None:where.append('n.tier=?');args.append(tier)
        clause=(' WHERE '+' AND '.join(where)) if where else ''
        with self._db() as db:
            rows=db.execute("SELECT n.*,c.name AS cluster_name FROM namespaces n JOIN clusters c ON c.id=n.cluster_id"+clause+" ORDER BY c.name,n.name",args).fetchall()
        return [self._namespace(row) for row in rows]

    def get_namespace(self, namespace_id: int) -> Optional[Dict[str, Any]]:
        return next((row for row in self.list_namespaces() if row['id']==namespace_id),None)

    def create_namespace(self, cluster_id: int, name: str, tier: str, policy: Dict[str, Any]) -> int:
        now=utcnow()
        with self._db() as db:
            cur=db.execute("INSERT INTO namespaces(cluster_id,name,tier,policy_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (cluster_id,name,tier,json.dumps(policy,ensure_ascii=False),now,now))
            return int(cur.lastrowid)

    def update_namespace(self, namespace_id: int, tier: str, policy: Dict[str, Any]) -> bool:
        with self._db() as db:
            return db.execute("UPDATE namespaces SET tier=?,policy_json=?,updated_at=? WHERE id=?",
                (tier,json.dumps(policy,ensure_ascii=False),utcnow(),namespace_id)).rowcount>0

    def delete_namespace(self, namespace_id: int) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM namespaces WHERE id=?",(namespace_id,)).rowcount>0

    def project_namespace_ids(self, project_id: int) -> List[int]:
        with self._db() as db:
            return [row['namespace_id'] for row in db.execute("SELECT namespace_id FROM project_namespaces WHERE project_id=? ORDER BY namespace_id",(project_id,))]

    def assign_namespace(self, project_id: int, namespace_id: int) -> None:
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO project_namespaces(project_id,namespace_id,created_at) VALUES(?,?,?)",
                (project_id,namespace_id,utcnow()))

    def unassign_namespace(self, project_id: int, namespace_id: int) -> bool:
        with self._db() as db:
            linked=db.execute("SELECT EXISTS(SELECT 1 FROM services WHERE namespace_id=?)",(namespace_id,)).fetchone()[0] if self._table_exists(db,'services') else 0
            if linked:return False
            return db.execute("DELETE FROM project_namespaces WHERE project_id=? AND namespace_id=?",(project_id,namespace_id)).rowcount>0

    @staticmethod
    def _table_exists(db: sqlite3.Connection, table: str) -> bool:
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() is not None

    def namespace_ids_for_user(self,user_id: int,permission_roles: Dict[str,set],permission: str) -> List[int]:
        bindings=self.list_bindings(user_id=user_id,scope_type='namespace')
        return sorted({b['scope_id'] for b in bindings if permission in permission_roles.get(b['role'],set()) or '*' in permission_roles.get(b['role'],set())})

    @staticmethod
    def _revision(row: sqlite3.Row) -> Dict[str, Any]:
        item=dict(row)
        item['spec']=json.loads(item.pop('spec_json'))
        item['secret_versions']=json.loads(item.pop('secret_versions_json'))
        return item

    @staticmethod
    def _service(row: sqlite3.Row) -> Dict[str, Any]:
        item=dict(row)
        item['spec']=json.loads(item.pop('spec_json')) if item.get('spec_json') else None
        item.pop('spec_json',None)
        return item

    def get_service(self, service_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row=db.execute("SELECT s.*,r.spec_json FROM services s LEFT JOIN service_revisions r ON r.id=s.current_revision_id WHERE s.id=?",(service_id,)).fetchone()
        return self._service(row) if row else None

    def list_services(self, project_ids: Optional[List[int]]=None, namespace_ids: Optional[List[int]]=None) -> List[Dict[str,Any]]:
        where=[];args=[]
        if project_ids is not None or namespace_ids is not None:
            scopes=[]
            if project_ids:
                scopes.append(f"s.project_id IN ({','.join('?' for _ in project_ids)})");args.extend(project_ids)
            if namespace_ids:
                scopes.append(f"s.namespace_id IN ({','.join('?' for _ in namespace_ids)})");args.extend(namespace_ids)
            if not scopes:return []
            where.append('('+' OR '.join(scopes)+')')
        clause=' WHERE '+' AND '.join(where) if where else ''
        with self._db() as db:
            rows=db.execute("SELECT s.*,r.spec_json FROM services s LEFT JOIN service_revisions r ON r.id=s.current_revision_id"+clause+" ORDER BY s.name COLLATE NOCASE",args).fetchall()
        return [self._service(row) for row in rows]

    def create_service(self,name: str,project_id: int,namespace_id: int,app_id: int,spec: Dict[str,Any],created_by: int) -> Dict[str,Any]:
        now=utcnow()
        with self._db() as db:
            app=db.execute('SELECT project_id FROM apps WHERE id=?',(app_id,)).fetchone()
            allocation=db.execute('SELECT 1 FROM project_namespaces WHERE project_id=? AND namespace_id=?',(project_id,namespace_id)).fetchone()
            if not app or app['project_id']!=project_id:raise ValueError('应用不属于指定项目')
            if not allocation:raise ValueError('项目尚未分配目标命名空间')
            service_id=db.execute('INSERT INTO services(name,project_id,namespace_id,app_id,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                (name,project_id,namespace_id,app_id,now,now)).lastrowid
            revision_id=db.execute('INSERT INTO service_revisions(service_id,number,spec_json,secret_versions_json,created_by,created_at) VALUES(?,1,?,?,?,?)',
                (service_id,json.dumps(spec,ensure_ascii=False),json.dumps({},ensure_ascii=False),created_by,now)).lastrowid
            db.execute('UPDATE services SET current_revision_id=? WHERE id=?',(revision_id,service_id))
        return self.get_service(int(service_id))  # type: ignore[return-value]

    def update_service_revision(self,service_id: int,base_revision: int,spec: Dict[str,Any],created_by: int) -> Optional[Dict[str,Any]]:
        now=utcnow()
        with self._db() as db:
            service=db.execute('SELECT current_revision_id FROM services WHERE id=?',(service_id,)).fetchone()
            if not service:return None
            if service['current_revision_id']!=base_revision:raise RuntimeError('REVISION_CONFLICT')
            previous=db.execute('SELECT secret_versions_json FROM service_revisions WHERE id=?',(base_revision,)).fetchone()
            versions=previous['secret_versions_json'] if previous else '{}'
            number=db.execute('SELECT COALESCE(MAX(number),0)+1 FROM service_revisions WHERE service_id=?',(service_id,)).fetchone()[0]
            revision_id=db.execute('INSERT INTO service_revisions(service_id,number,spec_json,secret_versions_json,created_by,created_at) VALUES(?,?,?,?,?,?)',
                (service_id,number,json.dumps(spec,ensure_ascii=False),versions,created_by,now)).lastrowid
            changed=db.execute('UPDATE services SET current_revision_id=?,updated_at=? WHERE id=? AND current_revision_id=?',
                (revision_id,now,service_id,base_revision)).rowcount
            if not changed:raise RuntimeError('REVISION_CONFLICT')
            row=db.execute('SELECT * FROM service_revisions WHERE id=?',(revision_id,)).fetchone()
        return self.get_service(service_id),self._revision(row)

    def get_revision(self,service_id: int,revision_id: int) -> Optional[Dict[str,Any]]:
        with self._db() as db:
            row=db.execute('SELECT * FROM service_revisions WHERE id=? AND service_id=?',(revision_id,service_id)).fetchone()
        return self._revision(row) if row else None

    def list_revisions(self,service_id: int) -> List[Dict[str,Any]]:
        with self._db() as db:
            rows=db.execute('SELECT * FROM service_revisions WHERE service_id=? ORDER BY number DESC',(service_id,)).fetchall()
        return [self._revision(row) for row in rows]

    def create_secret_revision(self,service_id: int,key: str,ciphertext: str,created_by: int) -> Optional[Dict[str,Any]]:
        now=utcnow()
        with self._db() as db:
            service=db.execute('SELECT current_revision_id FROM services WHERE id=?',(service_id,)).fetchone()
            if not service:return None
            current=db.execute('SELECT * FROM service_revisions WHERE id=?',(service['current_revision_id'],)).fetchone()
            versions=json.loads(current['secret_versions_json'])
            with db:
                row=db.execute('SELECT COALESCE(MAX(version),0)+1 AS version FROM service_secrets WHERE service_id=? AND key=?',(service_id,key)).fetchone()
                version=row['version']
                db.execute('INSERT INTO service_secrets(service_id,key,version,ciphertext,created_at) VALUES(?,?,?,?,?)',
                    (service_id,key,version,ciphertext,now))
                versions[key]=version
                number=db.execute('SELECT COALESCE(MAX(number),0)+1 FROM service_revisions WHERE service_id=?',(service_id,)).fetchone()[0]
                revision_id=db.execute('INSERT INTO service_revisions(service_id,number,spec_json,secret_versions_json,created_by,created_at) VALUES(?,?,?,?,?,?)',
                    (service_id,number,current['spec_json'],json.dumps(versions,ensure_ascii=False),created_by,now)).lastrowid
                db.execute('UPDATE services SET current_revision_id=?,updated_at=? WHERE id=?',(revision_id,now,service_id))
                result=db.execute('SELECT * FROM service_revisions WHERE id=?',(revision_id,)).fetchone()
        return self._revision(result)

    def delete_service(self,service_id: int) -> bool:
        with self._db() as db:
            active=db.execute("SELECT EXISTS(SELECT 1 FROM deployments WHERE service_id=? AND status IN ('pending_approval','queued','applying','verifying'))",(service_id,)).fetchone()[0]
            if active:return False
            db.execute('DELETE FROM service_secrets WHERE service_id=?',(service_id,))
            db.execute('DELETE FROM service_revisions WHERE service_id=?',(service_id,))
            return db.execute('DELETE FROM services WHERE id=?',(service_id,)).rowcount>0

    def mark_service_deployed_revision(self,service_id: int,revision_id: int) -> None:
        with self._db() as db:
            db.execute('UPDATE services SET deployed_revision_id=?,updated_at=? WHERE id=?',(revision_id,utcnow(),service_id))

    def complete_service_deployment(self,deployment_id: int,worker_id: str,fencing_token: int,service_id: int,
                                    revision_id: int,message: str) -> bool:
        now=utcnow()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT 1 FROM deployments WHERE id=? AND lease_owner=? AND fencing_token=? AND status='verifying'",
                (deployment_id,worker_id,fencing_token)).fetchone()
            if not row:return False
            changed=db.execute("UPDATE deployments SET status='success',message=?,finished_at=?,lease_owner=NULL,lease_until=NULL WHERE id=? AND lease_owner=? AND fencing_token=? AND status='verifying'",
                (message,now,deployment_id,worker_id,fencing_token)).rowcount
            if changed:
                db.execute('UPDATE services SET deployed_revision_id=?,updated_at=? WHERE id=?',(revision_id,now,service_id))
            return changed==1

    def expire_pending_approvals(self,cutoff: str) -> int:
        with self._db() as db:
            return db.execute("UPDATE deployments SET status='expired',error='approval expired after 24 hours',finished_at=? "
                "WHERE service_id IS NOT NULL AND status='pending_approval' AND created_at<=?",(utcnow(),cutoff)).rowcount

    def create_project(self, slug: str, name: str, registry_orgs: List[Dict[str, Any]]) -> Dict[str, Any]:
        now = utcnow()
        with self._db() as db:
            project_id = db.execute(
                "INSERT INTO projects(slug, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (slug, name, now, now),
            ).lastrowid
            for item in registry_orgs:
                db.execute("INSERT INTO project_registry_orgs(project_id, namespace, base_prefixes_json) VALUES (?, ?, ?)",
                           (project_id, item["namespace"], json.dumps(item["base_prefixes"], ensure_ascii=False)))
        return self.get_project(project_id)  # type: ignore[return-value]

    def add_project_registry_org(self, project_id: int, namespace: str, prefixes: List[str]) -> None:
        with self._db() as db:
            row = db.execute("SELECT base_prefixes_json FROM project_registry_orgs WHERE project_id = ? AND namespace = ?",
                             (project_id, namespace)).fetchone()
            current = set(json.loads(row["base_prefixes_json"])) if row else set()
            current.update(prefixes)
            db.execute(
                "INSERT INTO project_registry_orgs(project_id, namespace, base_prefixes_json) VALUES (?, ?, ?) "
                "ON CONFLICT(project_id, namespace) DO UPDATE SET base_prefixes_json = excluded.base_prefixes_json",
                (project_id, namespace, json.dumps(sorted(current), ensure_ascii=False)),
            )

    def ensure_default_project_registry_orgs(self, default_namespace: str) -> None:
        if not default_namespace:
            return
        project_id = self.default_project_id()
        with self._db() as db:
            rows = db.execute("SELECT data FROM apps WHERE project_id = ?", (project_id,)).fetchall()
        if not rows:
            self.add_project_registry_org(project_id, default_namespace, [])
            return
        grouped: Dict[str, set] = {}
        for row in rows:
            app = json.loads(row["data"])
            namespace = app.get("organization") or default_namespace
            repository = app.get("repository")
            if namespace and repository:
                grouped.setdefault(namespace, set())
            base_image = app.get("base_image", "")
            if isinstance(base_image, str) and "/" in base_image:
                image_name = base_image.split("@", 1)[0].split(":", 1)[0]
                parts = image_name.split("/")
                if len(parts) >= 3 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
                    parts = parts[1:]
                if len(parts) >= 2 and parts[0]:
                    grouped.setdefault(parts[0], set()).add("/".join(parts[1:]))
        for namespace, prefixes in grouped.items():
            self.add_project_registry_org(project_id, namespace, sorted(prefixes))

    def update_project(self, project_id: int, slug: str, name: str,
                       registry_orgs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        now = utcnow()
        with self._db() as db:
            changed = db.execute("UPDATE projects SET slug = ?, name = ?, updated_at = ? WHERE id = ?",
                                 (slug, name, now, project_id)).rowcount
            if not changed:
                return None
            db.execute("DELETE FROM project_registry_orgs WHERE project_id = ?", (project_id,))
            for item in registry_orgs:
                db.execute("INSERT INTO project_registry_orgs(project_id, namespace, base_prefixes_json) VALUES (?, ?, ?)",
                           (project_id, item["namespace"], json.dumps(item["base_prefixes"], ensure_ascii=False)))
        return self.get_project(project_id)

    def delete_project(self, project_id: int) -> bool:
        with self._db() as db:
            project=db.execute("SELECT slug FROM projects WHERE id=?",(project_id,)).fetchone()
            if not project or project['slug']=='default':return False
            referenced = db.execute(
                "SELECT EXISTS(SELECT 1 FROM apps WHERE project_id = ?) OR "
                "EXISTS(SELECT 1 FROM jobs WHERE project_id = ?) OR "
                "EXISTS(SELECT 1 FROM deployments WHERE project_id = ?) OR "
                "EXISTS(SELECT 1 FROM role_bindings WHERE scope_type='project' AND scope_id=?)",
                (project_id, project_id, project_id,project_id),
            ).fetchone()[0]
            if referenced:
                return False
            return db.execute("DELETE FROM projects WHERE id = ?", (project_id,)).rowcount > 0

    def default_project_id(self) -> int:
        with self._db() as db:
            row = db.execute("SELECT id FROM projects WHERE slug = 'default'").fetchone()
        if not row:
            raise RuntimeError("default project migration is missing")
        return int(row["id"])

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

    def find_app_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM apps WHERE name = ?", (name,)).fetchone()
        return self._app(row) if row else None

    def create_app(self, data: Dict[str, Any], project_id: Optional[int] = None) -> Dict[str, Any]:
        now = utcnow()
        project_id = project_id or self.default_project_id()
        with self._db() as db:
            cur = db.execute("INSERT INTO apps(name, data, created_at, updated_at, project_id) VALUES (?, ?, ?, ?, ?)",
                             (data["name"], self._app_data(data), now, now, project_id))
            app_id = cur.lastrowid
        return self.get_app(app_id)  # type: ignore[return-value]

    def update_app(self, app_id: int, data: Dict[str, Any], project_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            cur = db.execute("UPDATE apps SET name = ?, data = ?, updated_at = ?, project_id = COALESCE(?, project_id) WHERE id = ?",
                             (data["name"], self._app_data(data), utcnow(), project_id, app_id))
            if cur.rowcount == 0:
                return None
        return self.get_app(app_id)

    def delete_app(self, app_id: int) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM apps WHERE id = ?", (app_id,)).rowcount > 0

    # ------------------------------------------------------------------ 构建记录
    @staticmethod
    def _encode(fields: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        for key, value in fields.items():
            if key not in JOB_FIELDS:
                raise KeyError(key)
            out[key] = json.dumps(value, ensure_ascii=False) if key in JSON_FIELDS and value is not None else value
        return out

    @staticmethod
    def _job(row: sqlite3.Row) -> Dict[str, Any]:
        job = dict(row)
        for key in JSON_FIELDS:
            if key in job and job[key]:
                job[key] = json.loads(job[key])
        return job

    def create_job(self, **fields: Any) -> int:
        fields.setdefault("project_id", self.default_project_id())
        data = self._encode(fields)
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        with self._db() as db:
            return db.execute(f"INSERT INTO jobs({cols}) VALUES ({marks})", tuple(data.values())).lastrowid

    def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        data = self._encode(fields)
        sets = ", ".join(f"{k} = ?" for k in data)
        with self._db() as db:
            db.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*data.values(), job_id))

    def delete_job(self, job_id: int) -> None:
        with self._db() as db:
            db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def list_jobs(self, app_id: Optional[int] = None, status: Optional[str] = None,
                  limit: int = 30, offset: int = 0,
                  project_ids: Optional[List[int]] = None) -> Tuple[List[Dict[str, Any]], int]:
        where, args = [], []
        if project_ids is not None:
            if not project_ids:
                return [], 0
            where.append(f"project_id IN ({','.join('?' for _ in project_ids)})")
            args.extend(project_ids)
        if app_id:
            where.append("app_id = ?")
            args.append(app_id)
        if status:
            where.append("status = ?")
            args.append(status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._db() as db:
            total = db.execute(f"SELECT COUNT(*) FROM jobs {clause}", args).fetchone()[0]
            rows = db.execute(f"SELECT {', '.join(LIST_FIELDS)} FROM jobs {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                              (*args, limit, offset)).fetchall()
        return [dict(r) for r in rows], total

    def fail_unfinished_jobs(self, reason: str) -> int:
        with self._db() as db:
            return db.execute("UPDATE jobs SET status = 'failed', error = ?, finished_at = ? "
                              "WHERE status IN ('queued', 'running')", (reason, utcnow())).rowcount

    def count_jobs(self) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # ------------------------------------------------------------------ 账号
    def _insert(self, table: str, allowed: Tuple[str, ...], fields: Dict[str, Any]) -> int:
        unknown = set(fields) - set(allowed)
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        with self._db() as db:
            return db.execute(f"INSERT INTO {table}({cols}) VALUES ({marks})", tuple(fields.values())).lastrowid

    def _update(self, table: str, allowed: Tuple[str, ...], row_id: int, fields: Dict[str, Any]) -> bool:
        if not fields:
            return True
        unknown = set(fields) - set(allowed)
        if unknown:
            raise KeyError(", ".join(sorted(unknown)))
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._db() as db:
            return db.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*fields.values(), row_id)).rowcount > 0

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
        user_id = self._insert("users", USER_FIELDS, fields)
        role = fields.get("role")
        if role == "admin":
            self.bind_user_role(user_id, "platform_admin", "global", 0, note="legacy administrator role")
        elif role in ("developer", "viewer"):
            self.bind_user_role(user_id, "developer" if role == "developer" else "project_viewer",
                                "project", self.default_project_id(), note="default project membership")
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

    def count_active_admins(self, exclude_id: Optional[int] = None) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND enabled = 1 AND id != ?",
                              (exclude_id or 0,)).fetchone()[0]

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

    # ------------------------------------------------------------------ 会话
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

    # ------------------------------------------------------------------ API 令牌
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

    # ------------------------------------------------------------------ 系统设置
    def get_settings(self) -> Dict[str, Any]:
        with self._db() as db:
            rows = db.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: json.loads(r["value"]) for r in rows}

    def get_settings_meta(self) -> Dict[str, Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT key, updated_at, updated_by FROM settings").fetchall()
        return {r["key"]: {"updated_at": r["updated_at"], "updated_by": r["updated_by"]} for r in rows}

    def save_settings(self, values: Dict[str, Any], removed: List[str], username: str) -> None:
        now = utcnow()
        with self._db() as db:
            for key, value in values.items():
                db.execute("INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                           "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                           "updated_by = excluded.updated_by",
                           (key, json.dumps(value, ensure_ascii=False), now, username))
            for key in removed:
                db.execute("DELETE FROM settings WHERE key = ?", (key,))

    # ------------------------------------------------------------------ 审计日志
    def add_audit(self, username: str, action: str, target: str = "", detail: str = "", ip: str = "") -> None:
        with self._db() as db:
            db.execute("INSERT INTO audit_logs(created_at, username, action, target, detail, ip) VALUES (?, ?, ?, ?, ?, ?)",
                       (utcnow(), username, action, target[:200], detail[:1000], ip[:64]))

    def list_audit(self, limit: int = 50, offset: int = 0, username: str = "",
                   action: str = "") -> Tuple[List[Dict[str, Any]], int]:
        where, args = [], []
        if username:
            where.append("username = ?")
            args.append(username)
        if action:
            where.append("action LIKE ?")
            args.append(action + "%")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._db() as db:
            total = db.execute(f"SELECT COUNT(*) FROM audit_logs {clause}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM audit_logs {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                              (*args, limit, offset)).fetchall()
        return [dict(r) for r in rows], total

    # ------------------------------------------------------------------ 部署目标（预留模块）
    @staticmethod
    def _target(row: sqlite3.Row) -> Dict[str, Any]:
        data = json.loads(row["data"])
        data.update(id=row["id"], name=row["name"], type=row["type"],
                    created_at=row["created_at"], updated_at=row["updated_at"])
        return data

    def list_deploy_targets(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM deploy_targets ORDER BY name COLLATE NOCASE").fetchall()
        return [self._target(r) for r in rows]

    def get_deploy_target(self, target_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM deploy_targets WHERE id = ?", (target_id,)).fetchone()
        return self._target(row) if row else None

    def find_deploy_target_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM deploy_targets WHERE name = ?", (name,)).fetchone()
        return self._target(row) if row else None

    def save_deploy_target(self, data: Dict[str, Any], target_id: Optional[int] = None) -> Dict[str, Any]:
        body = json.dumps({k: v for k, v in data.items()
                           if k not in ("id", "name", "type", "created_at", "updated_at")}, ensure_ascii=False)
        now = utcnow()
        with self._db() as db:
            if target_id is None:
                target_id = db.execute("INSERT INTO deploy_targets(name, type, data, created_at, updated_at) "
                                       "VALUES (?, ?, ?, ?, ?)", (data["name"], data["type"], body, now, now)).lastrowid
            else:
                db.execute("UPDATE deploy_targets SET name = ?, type = ?, data = ?, updated_at = ? WHERE id = ?",
                           (data["name"], data["type"], body, now, target_id))
        return self.get_deploy_target(target_id)  # type: ignore[arg-type,return-value]

    def delete_deploy_target(self, target_id: int) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM deploy_targets WHERE id = ?", (target_id,)).rowcount > 0

    # ------------------------------------------------------------------ 部署记录（预留模块）
    def create_deployment(self, **fields: Any) -> int:
        fields.setdefault("created_at", utcnow())
        fields.setdefault("project_id",self.default_project_id())
        return self._insert("deployments", DEPLOYMENT_FIELDS, fields)

    def create_idempotent_service_deployment(self, **fields: Any) -> Dict[str,Any]:
        fields.setdefault('created_at',utcnow())
        allowed=set(DEPLOYMENT_FIELDS)
        if set(fields)-allowed:raise KeyError(', '.join(sorted(set(fields)-allowed)))
        with self._db() as db:
            existing=db.execute('SELECT * FROM deployments WHERE created_by=? AND service_id=? AND idempotency_key=?',
                (fields['created_by'],fields['service_id'],fields['idempotency_key'])).fetchone()
            if existing:return dict(existing)
            cols=', '.join(fields);marks=', '.join('?' for _ in fields)
            cur=db.execute(f'INSERT INTO deployments({cols}) VALUES({marks})',tuple(fields.values()))
            row=db.execute('SELECT * FROM deployments WHERE id=?',(cur.lastrowid,)).fetchone()
        return dict(row)

    def record_approval_transition(self,deployment_id: int,approver_id: int,decision: str,comment: str,
                                   min_approvals: int) -> Dict[str,Any]:
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            deployment=db.execute('SELECT status,created_by FROM deployments WHERE id=? AND service_id IS NOT NULL',
                (deployment_id,)).fetchone()
            if not deployment:raise LookupError('deployment not found')
            if deployment['status']!='pending_approval':raise RuntimeError('deployment is not pending approval')
            if str(deployment['created_by'])==str(approver_id):raise PermissionError('submitter cannot approve own deployment')
            try:
                db.execute('INSERT INTO approvals(deployment_id,approver_id,decision,comment,created_at) VALUES(?,?,?,?,?)',
                    (deployment_id,approver_id,decision,comment,utcnow()))
            except sqlite3.IntegrityError as exc:
                if 'approvals.deployment_id, approvals.approver_id' in str(exc):raise FileExistsError('duplicate approval') from None
                raise
            if decision=='rejected':
                db.execute("UPDATE deployments SET status='rejected',finished_at=? WHERE id=? AND status='pending_approval'",
                    (utcnow(),deployment_id))
            else:
                count=db.execute("SELECT count(*) FROM approvals WHERE deployment_id=? AND decision='approved'",
                    (deployment_id,)).fetchone()[0]
                if count>=max(1,min_approvals):
                    db.execute("UPDATE deployments SET status='queued' WHERE id=? AND status='pending_approval'",(deployment_id,))
            row=db.execute('SELECT * FROM deployments WHERE id=?',(deployment_id,)).fetchone()
        return dict(row)

    def update_deployment(self, deployment_id: int, **fields: Any) -> None:
        self._update("deployments", DEPLOYMENT_FIELDS, deployment_id, fields)

    def claim_next_service_deployment(self,worker_id: str,lease_seconds: int=900) -> Optional[Dict[str,Any]]:
        lease_until=(datetime.now(timezone.utc)+timedelta(seconds=lease_seconds)).isoformat(timespec='seconds')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM deployments d WHERE d.service_id IS NOT NULL AND d.status='queued' "
                "AND NOT EXISTS(SELECT 1 FROM deployments active WHERE active.service_id=d.service_id AND active.status IN ('applying','verifying')) "
                "ORDER BY d.id LIMIT 1").fetchone()
            if not row:return None
            token=(row['fencing_token'] or 0)+1
            changed=db.execute("UPDATE deployments SET status='applying',started_at=?,lease_owner=?,lease_until=?,fencing_token=? WHERE id=? AND status='queued'",
                (utcnow(),worker_id,lease_until,token,row['id'])).rowcount
            if not changed:return None
            claimed=db.execute('SELECT * FROM deployments WHERE id=?',(row['id'],)).fetchone()
        return dict(claimed)

    def service_deployment_lease_valid(self,deployment_id: int,worker_id: str,fencing_token: int) -> bool:
        with self._db() as db:
            row=db.execute("SELECT 1 FROM deployments WHERE id=? AND service_id IS NOT NULL AND lease_owner=? AND fencing_token=? AND status IN ('applying','verifying') AND lease_until>?",
                (deployment_id,worker_id,fencing_token,utcnow())).fetchone()
        return row is not None

    def update_service_deployment(self,deployment_id: int,worker_id: str,fencing_token: int,**fields: Any) -> bool:
        unknown=set(fields)-set(DEPLOYMENT_FIELDS)
        if unknown:raise KeyError(', '.join(sorted(unknown)))
        if not fields:return True
        assignments=', '.join(f'{key}=?' for key in fields)
        with self._db() as db:
            cur=db.execute(f"UPDATE deployments SET {assignments} WHERE id=? AND lease_owner=? AND fencing_token=? AND status IN ('applying','verifying')",
                (*fields.values(),deployment_id,worker_id,fencing_token))
        return cur.rowcount==1

    def mark_interrupted_service_deployments_unknown(self,reason: str) -> int:
        with self._db() as db:
            return db.execute("UPDATE deployments SET status='unknown',error=?,finished_at=?,lease_owner=NULL,lease_until=NULL WHERE service_id IS NOT NULL AND status IN ('applying','verifying')",
                (reason,utcnow())).rowcount

    def mark_service_deployment_pending_approval(self,deployment_id: int,worker_id: str,fencing_token: int) -> bool:
        with self._db() as db:
            changed=db.execute("UPDATE deployments SET status='pending_approval',lease_owner=NULL,lease_until=NULL "
                "WHERE id=? AND lease_owner=? AND fencing_token=? AND status IN ('applying','verifying')",
                (deployment_id,worker_id,fencing_token)).rowcount
        return changed==1

    def get_deployment(self, deployment_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row = db.execute("SELECT * FROM deployments WHERE id = ?", (deployment_id,)).fetchone()
        return dict(row) if row else None

    def find_deployment_idempotency(self, actor_id: int, service_id: int, key: str) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row=db.execute('SELECT * FROM deployments WHERE created_by=? AND service_id=? AND idempotency_key=?',
                (actor_id,service_id,key)).fetchone()
        return dict(row) if row else None

    def create_approval(self,deployment_id: int,approver_id: int,decision: str,comment: str='') -> int:
        with self._db() as db:
            cur=db.execute('INSERT INTO approvals(deployment_id,approver_id,decision,comment,created_at) VALUES(?,?,?,?,?)',
                (deployment_id,approver_id,decision,comment,utcnow()))
            return int(cur.lastrowid)

    def list_approvals(self,deployment_id: int) -> List[Dict[str,Any]]:
        with self._db() as db:
            rows=db.execute('SELECT * FROM approvals WHERE deployment_id=? ORDER BY id',(deployment_id,)).fetchall()
        return [dict(row) for row in rows]

    def count_approvals(self,deployment_id: int,decision: str='approved') -> int:
        with self._db() as db:
            return db.execute('SELECT count(*) FROM approvals WHERE deployment_id=? AND decision=?',
                (deployment_id,decision)).fetchone()[0]

    def list_deployments(self, target_id: Optional[int] = None, app_id: Optional[int] = None,
                         limit: int = 30, offset: int = 0,
                         project_ids: Optional[List[int]] = None,
                         namespace_ids: Optional[List[int]] = None) -> Tuple[List[Dict[str, Any]], int]:
        where, args = [], []
        if project_ids is not None or namespace_ids is not None:
            project_ids=project_ids or []
            namespace_ids=namespace_ids or []
            if not project_ids and not namespace_ids:
                return [], 0
            scope=[]
            if project_ids:
                scope.append(f"project_id IN ({','.join('?' for _ in project_ids)})");args.extend(project_ids)
            if namespace_ids:
                scope.append(f"namespace_id IN ({','.join('?' for _ in namespace_ids)})");args.extend(namespace_ids)
            where.append('('+' OR '.join(scope)+')')
        if target_id:
            where.append("target_id = ?")
            args.append(target_id)
        if app_id:
            where.append("app_id = ?")
            args.append(app_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._db() as db:
            total = db.execute(f"SELECT COUNT(*) FROM deployments {clause}", args).fetchone()[0]
            rows = db.execute(f"SELECT * FROM deployments {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                              (*args, limit, offset)).fetchall()
        return [dict(r) for r in rows], total

    def fail_unfinished_deployments(self, reason: str) -> int:
        with self._db() as db:
            return db.execute("UPDATE deployments SET status = 'failed', error = ?, finished_at = ? "
                              "WHERE service_id IS NULL AND status IN ('queued', 'running')", (reason, utcnow())).rowcount

    def delete_user_api_tokens(self, user_id: int) -> None:
        with self._db() as db:
            db.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))
