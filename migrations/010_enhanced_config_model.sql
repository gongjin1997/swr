-- 010_enhanced_config_model.sql
-- 增强配置模型：引入多维度配置（stage、type、kind、process_type）

-- 1. 创建新的配置表（替代 config_sets）
CREATE TABLE config_entries (
    id BIGSERIAL PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('platform', 'project', 'environment', 'service')),
    scope_id BIGINT NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT 'app' CHECK (stage IN ('init', 'app')),
    priority INTEGER NOT NULL DEFAULT 2,  -- 1=default, 2=service_common, 3=service_secret, 4=env_common, 5=env_secret, 6=platform_common, 7=platform_secret
    kind TEXT NOT NULL DEFAULT 'common' CHECK (kind IN ('common', 'secret')),
    process_type TEXT NOT NULL DEFAULT 'env' CHECK (process_type IN ('env', 'file', 'template')),
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    locked BOOLEAN NOT NULL DEFAULT FALSE,  -- 是否锁定（下层不能覆盖）
    version INTEGER NOT NULL DEFAULT 1,
    description TEXT NOT NULL DEFAULT '',
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_config_entries_key UNIQUE (scope_type, scope_id, stage, key)
);

CREATE INDEX idx_config_entries_scope ON config_entries (scope_type, scope_id, stage);
CREATE INDEX idx_config_entries_priority ON config_entries (priority DESC, scope_type, scope_id);

-- 2. 配置变更历史表
CREATE TABLE config_entry_history (
    id BIGSERIAL PRIMARY KEY,
    config_entry_id BIGINT NOT NULL,  -- 不设外键，允许记录删除的配置
    scope_type TEXT NOT NULL,
    scope_id BIGINT NOT NULL,
    stage TEXT NOT NULL,
    priority INTEGER NOT NULL,
    kind TEXT NOT NULL,
    process_type TEXT NOT NULL,
    key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    action TEXT NOT NULL CHECK (action IN ('create', 'update', 'delete')),
    changed_by BIGINT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_config_history_entry ON config_entry_history (config_entry_id, changed_at DESC);
CREATE INDEX idx_config_history_scope ON config_entry_history (scope_type, scope_id, changed_at DESC);

-- 3. 配置快照表（用于部署 OBOM）
CREATE TABLE config_snapshots (
    id BIGSERIAL PRIMARY KEY,
    deployment_id BIGINT NOT NULL,  -- 关联到部署记录
    service_id BIGINT NOT NULL,
    stage TEXT NOT NULL,
    snapshot_data TEXT NOT NULL,  -- JSON: {key: {value, source, priority, kind, process_type}}
    config_hash TEXT NOT NULL,  -- 配置的哈希值
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_config_snapshots_deployment ON config_snapshots (deployment_id);
CREATE INDEX idx_config_snapshots_service ON config_snapshots (service_id, created_at DESC);

-- 4. 配置模板表（用于快速创建预设配置）
CREATE TABLE config_templates (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'general',  -- general, database, cache, messaging, monitoring
    scope_type TEXT NOT NULL,  -- 适用的作用域
    stage TEXT NOT NULL DEFAULT 'app',
    priority INTEGER NOT NULL DEFAULT 2,
    entries TEXT NOT NULL,  -- JSON: [{key, value, kind, process_type, description}]
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_config_templates_name UNIQUE (name)
);

CREATE INDEX idx_config_templates_category ON config_templates (category, scope_type);

-- 5. 预置默认配置模板数据
INSERT INTO config_templates (name, description, category, scope_type, stage, priority, entries) VALUES
('java-application', 'Java 应用标准配置', 'general', 'service', 'app', 2,
 '[
    {"key": "JAVA_OPTS", "value": "-Xms256m -Xmx512m", "kind": "common", "process_type": "env", "description": "JVM 参数"},
    {"key": "SERVER_PORT", "value": "8080", "kind": "common", "process_type": "env", "description": "服务端口"},
    {"key": "SPRING_PROFILES_ACTIVE", "value": "prod", "kind": "common", "process_type": "env", "description": "Spring Profile"},
    {"key": "LOGGING_LEVEL_ROOT", "value": "INFO", "kind": "common", "process_type": "env", "description": "日志级别"}
  ]'::TEXT),

('nodejs-application', 'Node.js 应用标准配置', 'general', 'service', 'app', 2,
 '[
    {"key": "NODE_ENV", "value": "production", "kind": "common", "process_type": "env", "description": "运行环境"},
    {"key": "PORT", "value": "3000", "kind": "common", "process_type": "env", "description": "服务端口"},
    {"key": "LOG_LEVEL", "value": "info", "kind": "common", "process_type": "env", "description": "日志级别"}
  ]'::TEXT),

('redis-client', 'Redis 客户端配置', 'cache', 'service', 'app', 2,
 '[
    {"key": "REDIS_MAX_RETRIES", "value": "3", "kind": "common", "process_type": "env", "description": "最大重试次数"},
    {"key": "REDIS_CONNECT_TIMEOUT", "value": "5000", "kind": "common", "process_type": "env", "description": "连接超时（毫秒）"},
    {"key": "REDIS_POOL_SIZE", "value": "10", "kind": "common", "process_type": "env", "description": "连接池大小"}
  ]'::TEXT),

('database-client', '数据库客户端配置', 'database', 'service', 'app', 2,
 '[
    {"key": "DB_POOL_SIZE", "value": "20", "kind": "common", "process_type": "env", "description": "连接池大小"},
    {"key": "DB_CONNECT_TIMEOUT", "value": "10000", "kind": "common", "process_type": "env", "description": "连接超时（毫秒）"},
    {"key": "DB_IDLE_TIMEOUT", "value": "600000", "kind": "common", "process_type": "env", "description": "空闲超时（毫秒）"}
  ]'::TEXT),

('monitoring-prometheus', 'Prometheus 监控配置', 'monitoring', 'service', 'app', 2,
 '[
    {"key": "METRICS_ENABLED", "value": "true", "kind": "common", "process_type": "env", "description": "启用指标收集"},
    {"key": "METRICS_PORT", "value": "9090", "kind": "common", "process_type": "env", "description": "指标端口"},
    {"key": "METRICS_PATH", "value": "/metrics", "kind": "common", "process_type": "env", "description": "指标路径"}
  ]'::TEXT),

('platform-defaults', '平台默认配置', 'general', 'platform', 'app', 6,
 '[
    {"key": "TZ", "value": "Asia/Shanghai", "kind": "common", "process_type": "env", "description": "时区"},
    {"key": "LANG", "value": "zh_CN.UTF-8", "kind": "common", "process_type": "env", "description": "语言"},
    {"key": "LOG_FORMAT", "value": "json", "kind": "common", "process_type": "env", "description": "日志格式"}
  ]'::TEXT);

-- 6. 数据迁移：从旧的 config_sets 迁移到新的 config_entries
-- 注意：这是一个简化的迁移，实际迁移需要根据具体数据调整
INSERT INTO config_entries (
    scope_type, scope_id, stage, priority, kind, process_type, key, value, version, created_by, created_at, updated_at
)
SELECT
    cs.scope_type,
    cs.scope_id,
    'app' as stage,  -- 默认都是 app 阶段
    CASE
        WHEN cs.scope_type = 'platform' AND cs.kind IN ('secret_env', 'secret_files') THEN 7
        WHEN cs.scope_type = 'platform' THEN 6
        WHEN cs.scope_type = 'environment' AND cs.kind IN ('secret_env', 'secret_files') THEN 5
        WHEN cs.scope_type = 'environment' THEN 4
        WHEN cs.scope_type = 'service' AND cs.kind IN ('secret_env', 'secret_files') THEN 3
        ELSE 2
    END as priority,
    CASE
        WHEN cs.kind IN ('secret_env', 'secret_files') THEN 'secret'
        ELSE 'common'
    END as kind,
    CASE
        WHEN cs.kind IN ('env', 'secret_env') THEN 'env'
        WHEN cs.kind IN ('files', 'secret_files') THEN 'file'
        ELSE 'env'
    END as process_type,
    (entry->>'key')::TEXT as key,
    (entry->>'value')::TEXT as value,
    csv.number as version,
    cs.created_by,
    cs.created_at,
    cs.updated_at
FROM config_sets cs
JOIN config_set_versions csv ON cs.current_version_id = csv.id
CROSS JOIN LATERAL jsonb_array_elements(csv.entries::jsonb) as entry
WHERE csv.id = cs.current_version_id
ON CONFLICT (scope_type, scope_id, stage, key) DO NOTHING;

-- 7. 添加注释
COMMENT ON TABLE config_entries IS '配置条目表：多维度配置模型（stage/priority/kind/process_type）';
COMMENT ON COLUMN config_entries.stage IS '配置阶段：init=初始化阶段, app=应用阶段';
COMMENT ON COLUMN config_entries.priority IS '优先级：7=platform_secret, 6=platform_common, 5=env_secret, 4=env_common, 3=service_secret, 2=service_common, 1=default';
COMMENT ON COLUMN config_entries.kind IS '配置类型：common=普通配置, secret=敏感配置';
COMMENT ON COLUMN config_entries.process_type IS '处理类型：env=环境变量, file=配置文件, template=模板占位符';
COMMENT ON COLUMN config_entries.locked IS '是否锁定：true 表示下层不能覆盖此配置';

COMMENT ON TABLE config_entry_history IS '配置变更历史：记录所有配置的创建、更新、删除操作';
COMMENT ON TABLE config_snapshots IS '配置快照：部署时的配置完整快照，用于 OBOM 和精确回滚';
COMMENT ON TABLE config_templates IS '配置模板：预定义的配置集合，可快速应用到服务';
