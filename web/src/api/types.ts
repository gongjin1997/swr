/** 后端接口的响应类型（字段与 swr_builder 的 API 保持一致）。所有列表接口都返回 Page。 */

export interface Page<T> {
  items: T[]
  total: number
}

export type ScopeType = 'global' | 'project' | 'namespace'
export type Tier = 'dev' | 'test' | 'staging' | 'prod'
export type TagType = 'primary' | 'success' | 'warning' | 'danger' | 'info'

// ------------------------------------------------------------------ 账号与权限
export interface Account {
  id: number
  username: string
  display_name: string
  email: string
  /** platform_admin、首个全局角色或 member */
  role: string
  /** 简短身份说明，如"平台管理员""开发者、环境审批员" */
  role_label: string
  is_platform_admin: boolean
  global_roles: string[]
  grant_count: number
  enabled: boolean
  must_change_password: boolean
  created_at: string | null
  last_login_at: string | null
  password_changed_at: string | null
  /** 当前账号能否管理该账号（仅账号列表/详情返回） */
  can_manage?: boolean
}

export interface Binding {
  id: number
  subject_type: 'user'
  subject_id: number
  role: string
  scope_type: ScopeType
  scope_id: number
  expires_at: string | null
  note: string
}

export interface BindingRow extends Binding {
  created_at: string
  created_by: number | null
  created_by_username: string | null
  role_name: string
  role_enabled: boolean
  scope_label: string
  status: 'active' | 'expired'
  subject: { id: number; username: string; display_name: string; enabled: boolean }
  can_revoke: boolean
}

export interface ScopedAccess {
  id: number
  name: string
  perms: string[]
  cluster?: string
  tier?: string
}

export interface AccountDetail extends Account {
  can_manage: boolean
  bindings: BindingRow[]
  access: { global: string[]; projects: ScopedAccess[]; namespaces: ScopedAccess[] }
}

export interface SessionProject {
  id: number
  name: string
  slug: string
  perms: string[]
}

export interface SessionNamespace {
  id: number
  name: string
  cluster: string
  tier: Tier
  perms: string[]
}

export interface SessionProfile extends Account {
  /** 在任一范围内拥有的权限（菜单与入口） */
  permissions: string[]
  /** 全局拥有的权限 */
  global_permissions: string[]
  site_name: string
  deploy_enabled: boolean
  bindings: Binding[]
  projects: SessionProject[]
  namespaces: SessionNamespace[]
}

export interface PermissionMeta {
  code: string
  label: string
  group: string
  scopes: ScopeType[]
  implies: string[]
  grantable: boolean
}

export interface RoleDefinition {
  code: string
  name: string
  scope_type: ScopeType
  permissions: string[]
  description: string
  enabled: boolean
  builtin: boolean
  binding_count: number
}

export interface RoleCatalog {
  items: RoleDefinition[]
  permissions: Record<string, string>
  scope_permissions: Record<ScopeType, string[]>
  permission_catalog: PermissionMeta[]
  groups: { code: string; label: string }[]
  scopes: { code: ScopeType; label: string }[]
}

export interface GrantOptions {
  global: { roles: string[] } | null
  project: { id: number; name: string; slug: string; roles: string[] }[]
  namespace: { id: number; name: string; cluster: string; tier: Tier; roles: string[] }[]
}

export interface GrantInput {
  role: string
  scope_type: ScopeType
  scope_id: number
  expires_at?: string | null
  note?: string
}

export interface BindableUser {
  id: number
  username: string
  display_name: string
  enabled: boolean
}

export interface SessionRow {
  id: string
  ip: string
  user_agent: string
  created_at: string
  last_seen_at: string
  expires_at: string
  current: boolean
}

export interface ApiToken {
  id: number
  name: string
  prefix: string
  created_at: string
  expires_at: string | null
  last_used_at: string | null
}

export interface AuditEntry {
  id: number
  created_at: string
  username: string
  action: string
  target: string
  detail: string
  ip: string
}

// ------------------------------------------------------------------ 平台
export interface PlatformInfo {
  version: string
  registry: { address: string; organization: string; username: string; configured: boolean }
  docker: { available: boolean; version: string; arch: string; error: string }
  user: string
  max_upload_mb: number
}

export interface PendingApproval {
  id: number
  service_id: number
  target_name: string
  project_name: string
  namespace_name: string
  cluster_name: string
  environment: Tier
  operator: string
  created_at: string
  require_admin_approval: boolean
}

export interface Overview {
  projects: number
  apps?: number
  services?: number
  jobs?: { total: number; active: number; success: number; failed: number; recent: Job[] }
  approvals?: PendingApproval[]
}

export interface SettingField {
  section: string
  key: string
  path: string
  type: 'str' | 'secret' | 'bool' | 'int' | 'float' | 'list'
  label: string
  help: string
  minimum: number
  maximum: number
  value: unknown
  is_set: boolean
  locked_by: string | null
  source: 'env' | 'db' | 'file'
}

export interface SettingsView {
  sections: { key: string; label: string; fields: SettingField[] }[]
}

export interface SystemInfo {
  version: string
  python: string
  data_dir: string
  disk: { total: number; free: number }
  users: number
  jobs: number
  config_priority: string
}

// ------------------------------------------------------------------ 应用与构建
export type BuildMode = 'incremental' | 'full'

export interface AppFields {
  name: string
  repository: string
  organization: string
  description: string
  base_image: string
  default_mode: BuildMode
  max_layers: number
  target_dir: string
  target_name: string
  chown: string
  platform: string
  latest_strategy: 'version' | 'time'
  initial_tag: string
  tag_template: string
  tag_regex: string
  push_latest: boolean
  full_template: string
  incremental_template: string
}

export interface App extends AppFields {
  id: number
  project_id: number
  image_repo: string
  effective_organization: string
  created_at: string
  updated_at: string
}

export interface ImageInfo {
  layers: number | null
  size: number | null
  os: string
  architecture: string
  created: string | null
}

export interface Versions {
  image_repo: string
  total: number
  tags: string[]
  latest: string | null
  latest_info: ImageInfo | null
  next_tag: string
  max_layers: number | null
  warning: string | null
}

export interface BuildPlan {
  mode: BuildMode
  requested_mode: BuildMode
  latest: string | null
  base_image: string
  image: string
  expected_arch: string
  notes: string[]
}

export interface Preview {
  plan: BuildPlan
  files: { name: string; dest: string }[]
  dockerfile: string
}

export interface DockerfileImport extends Partial<AppFields> {
  warnings: string[]
}

export type JobStatus = 'queued' | 'running' | 'success' | 'failed' | 'cancelled'

export interface Job {
  id: number
  app_id: number
  app_name: string
  project_id: number
  repository: string
  mode: BuildMode | null
  requested_mode: BuildMode
  requested_tag: string | null
  prev_tag: string | null
  base_tag: string | null
  base_image?: string | null
  new_tag: string | null
  image: string | null
  digest: string | null
  status: JobStatus
  step: string
  operator: string
  remark: string
  error: string | null
  notes?: string[] | null
  created_by: number | string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface LogChunk {
  text: string
  offset: number
  status: string
  step?: string
  finished: boolean
}

// ------------------------------------------------------------------ 项目、集群、命名空间
export interface RegistryOrg {
  namespace: string
  base_prefixes: string[]
}

export interface Project {
  id: number
  slug: string
  name: string
  registry_orgs: RegistryOrg[]
  namespace_ids: number[]
  created_at: string
  updated_at: string
}

export interface Cluster {
  id: number
  name: string
  auth_type: string
  server: string
  cce_cluster_id: string | null
  credential_expires_at: string | null
  cert_duration_days: number
  status: string
  created_at: string
  updated_at: string
}

export interface ClusterDiscovery {
  cluster_id: number
  version: string
  namespaces: { name: string; status: string; managed_id: number | null }[]
  nodes: { name: string; version: string; ready: boolean; architecture: string }[]
  warnings: string[]
  fetched_at: string
}

export interface DeployWindow {
  days: number[]
  start: string
  end: string
  tz: string
}

export interface NamespacePolicy {
  require_approval: boolean
  require_admin_approval: boolean
  min_approvals: number
  max_replicas: number
  allowed_registry_orgs: string[]
  rollout_timeout: number
  freeze: { enabled: boolean; until: string | null; reason: string }
  deploy_windows: DeployWindow[]
  [key: string]: unknown
}

export interface Namespace {
  id: number
  cluster_id: number
  cluster_name: string
  name: string
  tier: Tier
  policy: NamespacePolicy
  project_ids: number[]
}

export interface RbacReview {
  ok: boolean
  checks: { group: string; resource: string; verb: string; allowed: boolean }[]
  warnings: string[]
}

export interface KubeDeployment {
  name: string
  uid: string
  replicas: number
  ready_replicas: number
  containers: { name: string; image: string }[]
}

export interface NamespaceResources {
  namespace_id: number
  namespace_name: string
  cluster_name: string
  deployments: KubeDeployment[]
  services: { name: string; type: string; cluster_ip: string; ports: { port: number; targetPort: string | number }[] }[]
  pods: { name: string; phase: string; ready: boolean; node: string }[]
  ingresses: { name: string; hosts: string[] }[]
  warnings: string[]
  fetched_at: string
  found?: boolean
  container?: { name: string; image: string } | null
}

// ------------------------------------------------------------------ 服务与发布
export interface ImageOnlySpec {
  mode: 'image_only'
  target: { kind: 'Deployment'; name: string; container: string }
}

export interface Service {
  id: number
  name: string
  project_id: number
  namespace_id: number
  app_id: number
  current_revision_id: number
  deployed_revision_id: number | null
  spec: ImageOnlySpec | Record<string, unknown>
  project_name: string
  app_name: string
  cluster_name: string
  namespace_name: string
  tier: Tier
  created_at: string
  updated_at: string
}

export interface Revision {
  id: number
  service_id: number
  number: number
  spec: Record<string, unknown>
  secret_versions: Record<string, number>
  created_by: number
  created_at: string
}

export interface RevisionDiff {
  from: number
  to: number
  changes: { path: string; old: unknown; new: unknown }[]
}

export interface Approval {
  id: number
  approver_id: number
  approver_username: string | null
  approver_display_name: string | null
  decision: 'approved' | 'rejected'
  comment: string
  created_at: string
}

export interface Deployment {
  id: number
  service_id: number | null
  target_id: number | null
  target_name: string
  target_type: string
  kind: string
  project_id: number
  project_name: string
  namespace_id: number | null
  namespace_name: string
  cluster_name: string
  environment: string
  app_id: number
  app_name: string
  job_id: number
  revision_id: number | null
  image: string
  digest: string | null
  status: string
  trigger: string
  operator: string
  message: string | null
  error: string | null
  remark: string
  created_at: string
  finished_at: string | null
  can_approve: boolean
  can_reject: boolean
  require_admin_approval: boolean
  approvals?: Approval[]
}

/** 提交发布、回滚、重新发布的响应；完整记录用 deploymentApi.get 获取。 */
export type DeploymentTicket = Pick<Deployment, 'id' | 'service_id' | 'job_id' | 'revision_id' | 'kind' | 'status' | 'digest' | 'created_at'>

// ------------------------------------------------------------------ 环境与配置
export type ConfigScope = 'platform' | 'project' | 'environment' | 'service'
export type ConfigKind = 'env' | 'secret_env' | 'files' | 'secret_files'
export type FileFormat = 'properties' | 'yaml' | 'json' | 'xml' | 'text'

/** 接口返回的条目：敏感条目不带值，只说明来源（直接填写或引用证书）。 */
export interface ConfigEntry {
  key: string
  description: string
  locked: boolean
  value?: string
  format?: FileFormat
  secret?: boolean
  source?: 'value' | 'certificate'
  certificate_id?: number
  certificate_name?: string
  certificate_version?: number | null
  size?: number | null
}

/** 保存时提交的条目：敏感条目三选一——value（新值）、keep（沿用上一版本）、certificate_id（引用证书）。 */
export interface ConfigEntryInput {
  key: string
  description?: string
  locked?: boolean
  value?: string
  format?: FileFormat
  keep?: boolean
  certificate_id?: number
  certificate_version?: number
}

export interface ConfigSet {
  id: number
  scope_type: ConfigScope
  scope_id: number
  name: string
  kind: ConfigKind
  description: string
  version: number
  updated_at: string
  created_at: string
  entries: ConfigEntry[]
  can_edit: boolean
  can_reveal: boolean
}

export interface ConfigVersion {
  id: number
  number: number
  checksum: string
  note: string
  created_by: number | null
  created_by_username: string | null
  created_at: string
}

export interface ConfigChange {
  key: string
  change: 'added' | 'removed' | 'changed'
  old?: string | null
  new?: string | null
}

export interface ConfigOrigin {
  scope_type: ConfigScope
  scope_id: number
  config_set_id: number
  version: number
}

export interface EffectiveEntry extends ConfigEntry {
  origin: ConfigOrigin
  overrides: ConfigOrigin[]
}

export interface EffectiveFile {
  name: string
  kind: ConfigKind
  origin: ConfigOrigin
  keys: string[]
  overrides: ConfigOrigin[]
}

export interface EffectiveConfig {
  env: EffectiveEntry[]
  secret_env: EffectiveEntry[]
  files: EffectiveFile[]
  warnings: string[]
}

export type CompareStatus = 'same' | 'different' | 'left_only' | 'right_only' | 'hidden'

export interface CompareRow {
  key: string
  left: string | null
  right: string | null
  status: CompareStatus
}

export interface CompareFile {
  name: string
  status: CompareStatus | ''
  files: { key: string; status: CompareStatus; properties?: CompareRow[] }[]
}

export interface EnvironmentCompare {
  env: CompareRow[]
  secret_env: CompareRow[]
  files: CompareFile[]
}

export type NamingKey = 'deployment' | 'service' | 'ingress' | 'configmap' | 'secret' | 'properties'

export interface EnvironmentProfile {
  naming: Record<NamingKey, string>
  host: string
  scheduling: { node_pool_label: string; node_pool: string; anti_affinity: boolean; zone_spread: boolean }
  image_pull_secret: string
  log: { enabled: boolean; host_path: string; mount_path: string; sub_path_expr: string }
  defaults: { replicas: number; cpu_request: string; memory_request: string; cpu_limit: string; memory_limit: string }
}

export interface IngressSpec {
  ingress_class: string
  elb_class: string
  elb_id: string
  port: number
  certificate_ids: string[]
  annotations: Record<string, string>
}

export interface IngressProfile {
  id: number
  name: string
  description: string
  spec: IngressSpec
  version: number
  environment_count: number
  created_at: string
  updated_at: string
}

export interface EnvironmentSummary {
  id: number
  name: string
  cluster_id: number
  cluster_name: string
  tier: Tier
  project_ids: number[]
  profile_version: number
  ingress_profile_id: number | null
  ingress_profile_name: string | null
  service_count: number
  can_edit: boolean
}

export interface EnvironmentDetail extends EnvironmentSummary {
  policy: NamespacePolicy
  profile: EnvironmentProfile
  profile_saved: boolean
  ingress_profile: IngressProfile | null
  config_sets: ConfigSet[]
  can_reveal: boolean
}

export type ExpiryStatus = 'ok' | 'warning' | 'expiring' | 'expired' | 'unknown'

export interface Certificate {
  id: number
  name: string
  kind: 'x509' | 'pkcs12' | 'jks' | 'key' | 'file'
  kind_label: string
  description: string
  version: number
  filename: string
  size: number
  sha256: string
  metadata: Record<string, unknown>
  not_after: string | null
  used_by: number
  status: ExpiryStatus
  days_left: number | null
  created_at: string
  updated_at: string
}

export interface CertificateVersion {
  number: number
  filename: string
  size: number
  sha256: string
  metadata: Record<string, unknown>
  not_after: string | null
  note: string
  created_at: string
  created_by_username: string | null
  status: ExpiryStatus
  days_left: number | null
}

export interface CertificateUsage {
  id: number
  scope_type: ConfigScope
  scope_id: number
  name: string
  kind: ConfigKind
  entry_key: string
  certificate_version: number
}

export interface CertificateDetail extends Certificate {
  versions: CertificateVersion[]
  usage: CertificateUsage[]
}

export interface CertificateOption {
  id: number
  name: string
  kind: Certificate['kind']
  kind_label: string
  version: number
  filename: string
  status: ExpiryStatus
  days_left: number | null
}

export interface CertificateUpload {
  filename: string
  content_base64: string
  password?: string
  not_after?: string
  note?: string
}
