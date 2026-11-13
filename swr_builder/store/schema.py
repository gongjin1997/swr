"""表结构基线与字段白名单。

基线等价于 v1.3 SQLite 的 v7 结构，改动：
- 时间列为 TIMESTAMPTZ，布尔列为 BOOLEAN，账号 ID 列（created_by 等）为 BIGINT；
- 不区分大小写的唯一约束（用户名、项目、集群名）改为 lower() 唯一索引；
- 原来由触发器保证的 project_id 有效性改为 NOT NULL 加外键；
- 约束显式命名，代码按约束名识别冲突。
"""
from datetime import datetime, timezone

BASELINE = [
    """CREATE TABLE projects (
        id BIGSERIAL PRIMARY KEY,
        slug TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )""",
    "CREATE UNIQUE INDEX uq_projects_slug ON projects (lower(slug))",
    "CREATE UNIQUE INDEX uq_projects_name ON projects (lower(name))",
    """CREATE TABLE users (
        id BIGSERIAL PRIMARY KEY,
        username TEXT NOT NULL,
        display_name TEXT,
        email TEXT,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        last_login_at TIMESTAMPTZ,
        password_changed_at TIMESTAMPTZ
    )""",
    "CREATE UNIQUE INDEX uq_users_username ON users (lower(username))",
    """CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        last_seen_at TIMESTAMPTZ,
        ip TEXT,
        user_agent TEXT
    )""",
    "CREATE INDEX idx_sessions_user ON sessions (user_id)",
    """CREATE TABLE api_tokens (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        name TEXT NOT NULL,
        token_hash TEXT NOT NULL,
        prefix TEXT,
        created_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ,
        last_used_at TIMESTAMPTZ,
        CONSTRAINT uq_api_tokens_hash UNIQUE (token_hash)
    )""",
    "CREATE INDEX idx_api_tokens_user ON api_tokens (user_id)",
    """CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMPTZ,
        updated_by TEXT
    )""",
    """CREATE TABLE audit_logs (
        id BIGSERIAL PRIMARY KEY,
        created_at TIMESTAMPTZ NOT NULL,
        username TEXT,
        action TEXT NOT NULL,
        target TEXT,
        detail TEXT,
        ip TEXT
    )""",
    "CREATE INDEX idx_audit_logs_username ON audit_logs (username, id)",
    """CREATE TABLE apps (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        data TEXT NOT NULL,
        project_id BIGINT NOT NULL REFERENCES projects(id),
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_apps_name UNIQUE (name)
    )""",
    "CREATE INDEX idx_apps_project ON apps (project_id, id)",
    """CREATE TABLE jobs (
        id BIGSERIAL PRIMARY KEY,
        project_id BIGINT NOT NULL REFERENCES projects(id),
        app_id BIGINT,
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
        created_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL,
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ
    )""",
    "CREATE INDEX idx_jobs_app ON jobs (app_id, id)",
    "CREATE INDEX idx_jobs_status ON jobs (status)",
    "CREATE INDEX idx_jobs_project ON jobs (project_id, id)",
    """CREATE TABLE project_registry_orgs (
        project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        namespace TEXT NOT NULL,
        base_prefixes_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY (project_id, namespace)
    )""",
    """CREATE TABLE custom_roles (
        code TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        scope_type TEXT NOT NULL CHECK (scope_type IN ('global', 'project', 'namespace')),
        permissions TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        updated_at TIMESTAMPTZ NOT NULL
    )""",
    """CREATE TABLE role_bindings (
        id BIGSERIAL PRIMARY KEY,
        subject_type TEXT NOT NULL CHECK (subject_type IN ('user', 'group')),
        subject_id BIGINT NOT NULL,
        role TEXT NOT NULL,
        scope_type TEXT NOT NULL CHECK (scope_type IN ('global', 'project', 'namespace')),
        scope_id BIGINT NOT NULL DEFAULT 0,
        expires_at TIMESTAMPTZ,
        revoked_at TIMESTAMPTZ,
        created_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        CONSTRAINT uq_role_bindings_grant UNIQUE (subject_type, subject_id, role, scope_type, scope_id)
    )""",
    "CREATE INDEX idx_role_bindings_scope ON role_bindings (scope_type, scope_id, subject_type, subject_id)",
    "CREATE INDEX idx_role_bindings_subject ON role_bindings (subject_type, subject_id)",
    """CREATE TABLE clusters (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        auth_type TEXT NOT NULL CHECK (auth_type IN ('sa_token', 'client_cert', 'cce_cert')),
        server TEXT NOT NULL,
        credential_encrypted TEXT NOT NULL,
        cce_cluster_id TEXT,
        credential_expires_at TIMESTAMPTZ,
        cert_duration_days INTEGER NOT NULL DEFAULT 30,
        status TEXT NOT NULL DEFAULT 'unverified',
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )""",
    "CREATE UNIQUE INDEX uq_clusters_name ON clusters (lower(name))",
    """CREATE TABLE namespaces (
        id BIGSERIAL PRIMARY KEY,
        cluster_id BIGINT NOT NULL REFERENCES clusters(id) ON DELETE RESTRICT,
        name TEXT NOT NULL,
        tier TEXT NOT NULL CHECK (tier IN ('dev', 'test', 'staging', 'prod')),
        policy_json TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_namespaces_cluster_name UNIQUE (cluster_id, name)
    )""",
    """CREATE TABLE project_namespaces (
        project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        namespace_id BIGINT NOT NULL REFERENCES namespaces(id) ON DELETE RESTRICT,
        created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (project_id, namespace_id)
    )""",
    "CREATE INDEX idx_project_namespaces_namespace ON project_namespaces (namespace_id, project_id)",
    """CREATE TABLE services (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
        namespace_id BIGINT NOT NULL REFERENCES namespaces(id) ON DELETE RESTRICT,
        app_id BIGINT NOT NULL REFERENCES apps(id) ON DELETE RESTRICT,
        current_revision_id BIGINT,
        deployed_revision_id BIGINT,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_services_namespace_name UNIQUE (namespace_id, name)
    )""",
    "CREATE INDEX idx_services_project ON services (project_id, id)",
    "CREATE INDEX idx_services_namespace ON services (namespace_id, id)",
    """CREATE TABLE service_revisions (
        id BIGSERIAL PRIMARY KEY,
        service_id BIGINT NOT NULL REFERENCES services(id) ON DELETE RESTRICT,
        number INTEGER NOT NULL,
        spec_json TEXT NOT NULL,
        secret_versions_json TEXT NOT NULL DEFAULT '{}',
        created_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_service_revisions_number UNIQUE (service_id, number)
    )""",
    """CREATE TABLE service_secrets (
        id BIGSERIAL PRIMARY KEY,
        service_id BIGINT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        version INTEGER NOT NULL,
        ciphertext TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_service_secrets_version UNIQUE (service_id, key, version)
    )""",
    """CREATE TABLE deploy_targets (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        data TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_deploy_targets_name UNIQUE (name)
    )""",
    """CREATE TABLE deployments (
        id BIGSERIAL PRIMARY KEY,
        project_id BIGINT NOT NULL REFERENCES projects(id),
        target_id BIGINT,
        target_name TEXT,
        target_type TEXT,
        environment TEXT,
        app_id BIGINT,
        app_name TEXT,
        job_id BIGINT,
        image TEXT NOT NULL,
        service_id BIGINT,
        namespace_id BIGINT,
        revision_id BIGINT,
        kind TEXT NOT NULL DEFAULT 'deploy',
        digest TEXT,
        idempotency_key TEXT,
        request_hash TEXT,
        policy_snapshot TEXT,
        status TEXT NOT NULL,
        trigger TEXT,
        operator TEXT,
        created_by BIGINT,
        remark TEXT,
        message TEXT,
        error TEXT,
        lease_owner TEXT,
        lease_until TIMESTAMPTZ,
        fencing_token BIGINT,
        created_at TIMESTAMPTZ NOT NULL,
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ,
        CONSTRAINT uq_deployments_idempotency UNIQUE (created_by, service_id, idempotency_key)
    )""",
    "CREATE INDEX idx_deployments_target ON deployments (target_id, id)",
    "CREATE INDEX idx_deployments_project ON deployments (project_id, id)",
    "CREATE INDEX idx_deployments_service ON deployments (service_id, id)",
    "CREATE INDEX idx_deployments_status ON deployments (status, id)",
    # 同一服务同一时刻只有一个执行中的发布；部分唯一索引在 PostgreSQL 与 GaussDB 行存表上都可用
    "CREATE UNIQUE INDEX uq_deployments_active_service ON deployments (service_id) "
    "WHERE service_id IS NOT NULL AND status IN ('applying', 'verifying')",
    """CREATE TABLE approvals (
        id BIGSERIAL PRIMARY KEY,
        deployment_id BIGINT NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
        approver_id BIGINT NOT NULL,
        decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
        comment TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_approvals_approver UNIQUE (deployment_id, approver_id)
    )""",
]

# 迁移 2：环境档案、负载均衡入口、分层配置集、证书
ENVIRONMENT_CONFIG = [
    """CREATE TABLE ingress_profiles (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        spec TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        updated_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_ingress_profiles_name UNIQUE (name)
    )""",
    """CREATE TABLE environment_profiles (
        namespace_id BIGINT PRIMARY KEY REFERENCES namespaces(id) ON DELETE CASCADE,
        ingress_profile_id BIGINT REFERENCES ingress_profiles(id) ON DELETE RESTRICT,
        profile TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        updated_by BIGINT,
        updated_at TIMESTAMPTZ NOT NULL
    )""",
    "CREATE INDEX idx_environment_profiles_ingress ON environment_profiles (ingress_profile_id)",
    """CREATE TABLE certificates (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('x509', 'pkcs12', 'jks', 'key', 'file')),
        description TEXT NOT NULL DEFAULT '',
        current_version_id BIGINT,
        created_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_certificates_name UNIQUE (name)
    )""",
    """CREATE TABLE certificate_versions (
        id BIGSERIAL PRIMARY KEY,
        certificate_id BIGINT NOT NULL REFERENCES certificates(id) ON DELETE CASCADE,
        number INTEGER NOT NULL,
        filename TEXT NOT NULL,
        ciphertext TEXT NOT NULL,
        size INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        metadata TEXT NOT NULL,
        not_after TIMESTAMPTZ,
        note TEXT NOT NULL DEFAULT '',
        created_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_certificate_versions_number UNIQUE (certificate_id, number)
    )""",
    """CREATE TABLE config_sets (
        id BIGSERIAL PRIMARY KEY,
        scope_type TEXT NOT NULL CHECK (scope_type IN ('platform', 'project', 'environment', 'service')),
        scope_id BIGINT NOT NULL DEFAULT 0,
        name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('env', 'secret_env', 'files', 'secret_files')),
        description TEXT NOT NULL DEFAULT '',
        current_version_id BIGINT,
        created_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_config_sets_scope_name UNIQUE (scope_type, scope_id, name)
    )""",
    """CREATE TABLE config_set_versions (
        id BIGSERIAL PRIMARY KEY,
        config_set_id BIGINT NOT NULL REFERENCES config_sets(id) ON DELETE CASCADE,
        number INTEGER NOT NULL,
        entries TEXT NOT NULL,
        checksum TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_config_set_versions_number UNIQUE (config_set_id, number)
    )""",
    # 配置版本引用的证书版本：证书被引用时不能删除，"被谁使用"可以直接查询
    """CREATE TABLE config_certificate_refs (
        config_set_version_id BIGINT NOT NULL REFERENCES config_set_versions(id) ON DELETE CASCADE,
        certificate_id BIGINT NOT NULL REFERENCES certificates(id) ON DELETE RESTRICT,
        certificate_version_id BIGINT NOT NULL REFERENCES certificate_versions(id) ON DELETE RESTRICT,
        entry_key TEXT NOT NULL,
        PRIMARY KEY (config_set_version_id, entry_key)
    )""",
    "CREATE INDEX idx_config_certificate_refs_certificate ON config_certificate_refs (certificate_id)",
]

# 迁移 3：托管服务发布的渲染快照。与发布单在同一个事务中写入，worker 领取任务时快照一定存在
MANAGED_DELIVERY = [
    """CREATE TABLE deployment_manifests (
        deployment_id BIGINT PRIMARY KEY REFERENCES deployments(id) ON DELETE CASCADE,
        service_id BIGINT NOT NULL,
        inputs TEXT NOT NULL,
        objects TEXT NOT NULL,
        checksum TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )""",
    "CREATE INDEX idx_deployment_manifests_service ON deployment_manifests (service_id, deployment_id)",
]

# 迁移 4：离线补丁包与铺底数据中执行过的 SQL 脚本（见 store/sqlscripts.py），保证同一脚本只执行一次
PATCH_SCRIPTS = [
    """CREATE TABLE patch_scripts (
        id BIGSERIAL PRIMARY KEY,
        patch TEXT NOT NULL,
        path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        transactional BOOLEAN NOT NULL,
        duration_ms BIGINT NOT NULL,
        applied_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_patch_scripts_patch_path UNIQUE (patch, path)
    )""",
]

# 迁移 5：资源管理、资源绑定与环境预置
RESOURCE_MANAGEMENT = [
    """CREATE TABLE resources (
        id BIGSERIAL PRIMARY KEY,
        scope_type TEXT NOT NULL CHECK (scope_type IN ('platform', 'environment')),
        scope_id BIGINT NOT NULL DEFAULT 0,
        type TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        current_version_id BIGINT,
        created_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_resources_scope_name UNIQUE (scope_type, scope_id, name)
    )""",
    "CREATE INDEX idx_resources_scope ON resources (scope_type, scope_id)",
    """CREATE TABLE resource_versions (
        id BIGSERIAL PRIMARY KEY,
        resource_id BIGINT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
        number INTEGER NOT NULL,
        attributes TEXT NOT NULL,
        checksum TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT uq_resource_versions_number UNIQUE (resource_id, number)
    )""",
    """CREATE TABLE service_resource_bindings (
        service_id BIGINT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
        resource_id BIGINT NOT NULL REFERENCES resources(id) ON DELETE RESTRICT,
        alias TEXT NOT NULL,
        PRIMARY KEY (service_id, alias)
    )""",
    "CREATE INDEX idx_service_bindings_resource ON service_resource_bindings (resource_id)",
    """CREATE TABLE environment_presets (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        profile_template TEXT NOT NULL,
        default_config_sets TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        created_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        updated_by BIGINT,
        CONSTRAINT uq_environment_presets_name UNIQUE (name)
    )""",
    "ALTER TABLE environment_profiles ADD COLUMN required_params TEXT NOT NULL DEFAULT '[]'",
]

# 迁移 6：资源有明确的上级；一个服务对同一资源只绑定一次（别名改为可选的变量名前缀）；
# 预置简化（删除 required_resource_types/required_params，添加 version/updated_by）
RESOURCE_BINDINGS = [
    "ALTER TABLE resources ADD COLUMN parent_id BIGINT",
    "ALTER TABLE resources ADD CONSTRAINT fk_resources_parent FOREIGN KEY (parent_id) REFERENCES resources(id) "
    "ON DELETE RESTRICT",
    "CREATE INDEX idx_resources_parent ON resources (parent_id)",
    "ALTER TABLE service_resource_bindings RENAME TO service_resource_bindings_v5",
    """CREATE TABLE service_resource_bindings (
        service_id BIGINT NOT NULL,
        resource_id BIGINT NOT NULL,
        alias TEXT NOT NULL DEFAULT '',
        created_by BIGINT,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_service_resource_bindings PRIMARY KEY (service_id, resource_id),
        CONSTRAINT fk_service_resource_bindings_service FOREIGN KEY (service_id) REFERENCES services(id)
            ON DELETE CASCADE,
        CONSTRAINT fk_service_resource_bindings_resource FOREIGN KEY (resource_id) REFERENCES resources(id)
            ON DELETE RESTRICT
    )""",
    # 旧别名只是名称、不影响导出，迁移后一律为空（不加前缀）
    "INSERT INTO service_resource_bindings(service_id, resource_id, alias, created_at) "
    "SELECT service_id, resource_id, '', CURRENT_TIMESTAMP FROM service_resource_bindings_v5 "
    "GROUP BY service_id, resource_id",
    "DROP TABLE service_resource_bindings_v5",
    "CREATE INDEX idx_service_resource_bindings_resource ON service_resource_bindings (resource_id)",
    # 预置简化：环境要求并入档案，预置只保留模板
    "ALTER TABLE environment_presets DROP COLUMN IF EXISTS required_resource_types",
    "ALTER TABLE environment_presets DROP COLUMN IF EXISTS required_params",
    "ALTER TABLE environment_presets ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE environment_presets ADD COLUMN IF NOT EXISTS updated_by BIGINT",
]

# 迁移 7：软删除。内容类对象不物理删除，只记 deleted_at；查询一律过滤 deleted_at IS NULL。
# 唯一约束换成只约束未删除行的部分索引，删除后可以重新使用同一个名字。
SOFT_DELETE_TABLES = ("users", "projects", "apps", "clusters", "namespaces", "services", "ingress_profiles",
                      "certificates", "config_sets", "resources", "environment_presets", "custom_roles")
SOFT_DELETE = [f"ALTER TABLE {table} ADD COLUMN deleted_at TIMESTAMPTZ" for table in SOFT_DELETE_TABLES] + [
    # 原来的唯一约束 / 索引改成部分唯一索引
    "DROP INDEX uq_users_username",
    "CREATE UNIQUE INDEX uq_users_username ON users (lower(username)) WHERE deleted_at IS NULL",
    "DROP INDEX uq_projects_slug",
    "CREATE UNIQUE INDEX uq_projects_slug ON projects (lower(slug)) WHERE deleted_at IS NULL",
    "DROP INDEX uq_projects_name",
    "CREATE UNIQUE INDEX uq_projects_name ON projects (lower(name)) WHERE deleted_at IS NULL",
    "ALTER TABLE apps DROP CONSTRAINT uq_apps_name",
    "CREATE UNIQUE INDEX uq_apps_name ON apps (name) WHERE deleted_at IS NULL",
    "DROP INDEX uq_clusters_name",
    "CREATE UNIQUE INDEX uq_clusters_name ON clusters (lower(name)) WHERE deleted_at IS NULL",
    "ALTER TABLE namespaces DROP CONSTRAINT uq_namespaces_cluster_name",
    "CREATE UNIQUE INDEX uq_namespaces_cluster_name ON namespaces (cluster_id, name) WHERE deleted_at IS NULL",
    "ALTER TABLE services DROP CONSTRAINT uq_services_namespace_name",
    "CREATE UNIQUE INDEX uq_services_namespace_name ON services (namespace_id, name) WHERE deleted_at IS NULL",
    "ALTER TABLE ingress_profiles DROP CONSTRAINT uq_ingress_profiles_name",
    "CREATE UNIQUE INDEX uq_ingress_profiles_name ON ingress_profiles (name) WHERE deleted_at IS NULL",
    "ALTER TABLE certificates DROP CONSTRAINT uq_certificates_name",
    "CREATE UNIQUE INDEX uq_certificates_name ON certificates (name) WHERE deleted_at IS NULL",
    "ALTER TABLE config_sets DROP CONSTRAINT uq_config_sets_scope_name",
    "CREATE UNIQUE INDEX uq_config_sets_scope_name ON config_sets (scope_type, scope_id, name) WHERE deleted_at IS NULL",
    "ALTER TABLE resources DROP CONSTRAINT uq_resources_scope_name",
    "CREATE UNIQUE INDEX uq_resources_scope_name ON resources (scope_type, scope_id, name) WHERE deleted_at IS NULL",
    "ALTER TABLE environment_presets DROP CONSTRAINT uq_environment_presets_name",
    "CREATE UNIQUE INDEX uq_environment_presets_name ON environment_presets (name) WHERE deleted_at IS NULL",
]

# 有自增主键的表：导入旧数据后要重置序列
SERIAL_TABLES = ("projects", "users", "api_tokens", "audit_logs", "apps", "jobs", "role_bindings", "clusters",
                 "namespaces", "services", "service_revisions", "service_secrets", "deploy_targets", "deployments",
                 "approvals", "resources", "resource_versions", "environment_presets")

USER_FIELDS = ("username", "display_name", "email", "password_hash", "role", "enabled", "must_change_password",
               "created_at", "updated_at", "last_login_at", "password_changed_at")
DEPLOYMENT_FIELDS = ("target_id", "target_name", "target_type", "environment", "app_id", "app_name", "job_id", "image",
                     "project_id", "service_id", "namespace_id", "revision_id", "kind", "digest", "idempotency_key",
                     "request_hash", "policy_snapshot", "status", "trigger", "operator", "created_by", "remark", "message",
                     "error", "lease_owner", "lease_until", "fencing_token", "created_at", "started_at", "finished_at")

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
