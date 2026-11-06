/** 后端接口的响应类型（字段与 swr_builder 的 API 保持一致）。 */

export interface Page<T> {
  items: T[]
  total: number
}

export interface PublicUser {
  id: number
  username: string
  display_name: string
  email: string
  role: string
  role_label: string
  enabled: boolean
  must_change_password: boolean
  created_at: string | null
  last_login_at: string | null
  password_changed_at: string | null
}

export interface Binding {
  id: number
  subject_type: 'user'
  subject_id: number
  role: string
  scope_type: 'global' | 'project' | 'namespace'
  scope_id: number
  expires_at: string | null
  note: string
}

export interface SessionProfile extends PublicUser {
  permissions: string[]
  site_name: string
  deploy_enabled: boolean
  bindings: Binding[]
  projects: { id: number; name: string; perms: string[] }[]
  namespaces: { id: number; name: string; cluster: string; tier: string; perms: string[] }[]
}

export interface PlatformInfo {
  version: string
  registry: { address: string; organization: string; username: string; configured: boolean }
  docker: { available: boolean; version: string; arch: string; error: string }
  user: string
  max_upload_mb: number
}

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
}

export interface DeployWindow {
  days: number[]
  start: string
  end: string
  tz: string
}

export interface NamespacePolicy {
  require_approval: boolean
  min_approvals: number
  max_replicas: number
  allowed_registry_orgs: string[]
  rollout_timeout: number
  freeze: { enabled: boolean; until: string | null; reason: string }
  deploy_windows: DeployWindow[]
  [key: string]: unknown
}

export type Tier = 'dev' | 'test' | 'staging' | 'prod'

export interface Namespace {
  id: number
  cluster_id: number
  cluster_name: string
  name: string
  tier: Tier
  policy: NamespacePolicy
}

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
  approvals?: Approval[]
}

export interface BindableUser {
  id: number
  username: string
  display_name: string
  enabled: boolean
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

export interface AuditEntry {
  id: number
  created_at: string
  username: string
  action: string
  target: string
  detail: string
  ip: string
}

export interface RolesInfo {
  roles: { value: string; label: string; permissions: string[] }[]
  permissions: Record<string, string>
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

export interface ProviderField {
  key: string
  label: string
  type: 'str' | 'secret' | 'bool' | 'int'
  required: boolean
  default: unknown
  help?: string
}

export interface DeployProvider {
  type: string
  label: string
  description: string
  available: boolean
  fields: ProviderField[]
}

export interface DeployTarget {
  id: number
  name: string
  type: string
  environment: string
  enabled: boolean
  auto_deploy: boolean
  app_ids: number[]
  config?: Record<string, unknown>
  secrets_set?: Record<string, boolean>
}
