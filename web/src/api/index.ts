/** 各领域的接口函数。后端所有列表接口返回 { items, total }，删除接口返回 204。 */
import { http, upload } from './http'
import type {
  Account, AccountDetail, ApiToken, App, AppFields, AuditEntry, BindableUser, Binding, BindingRow, Cluster,
  ClusterDiscovery, Deployment, DeploymentTicket, DockerfileImport, GrantInput, GrantOptions, Job, LogChunk, Namespace,
  NamespacePolicy, NamespaceResources, Overview, Page, PlatformInfo, Preview, Project, RbacReview, RegistryOrg,
  Revision, RevisionDiff, RoleCatalog, RoleDefinition, ScopeType, Service, SessionProfile, SessionRow, SettingsView,
  SystemInfo, Tier, Versions,
} from './types'

export const authApi = {
  config: () => http.get<{ site_name: string; version: string }>('/api/auth/config'),
  login: (username: string, password: string, remember: boolean) =>
    http.post<Account>('/api/auth/login', { username, password, remember }),
  me: () => http.get<SessionProfile>('/api/auth/me'),
  logout: () => http.post<{ ok: boolean }>('/api/auth/logout'),
}

export const accountApi = {
  updateProfile: (display_name: string, email: string) =>
    http.put<Account>('/api/account/profile', { display_name, email }),
  changePassword: (old_password: string, new_password: string) =>
    http.put<{ ok: boolean }>('/api/account/password', { old_password, new_password }),
  sessions: () => http.get<Page<SessionRow>>('/api/account/sessions'),
  revokeSession: (id: string) => http.del(`/api/account/sessions/${encodeURIComponent(id)}`),
  tokens: () => http.get<Page<ApiToken>>('/api/account/tokens'),
  createToken: (name: string, expires_days: number) =>
    http.post<{ id: number; token: string }>('/api/account/tokens', { name, expires_days }),
  revokeToken: (id: number) => http.del(`/api/account/tokens/${id}`),
}

export const platformApi = {
  info: () => http.get<PlatformInfo>('/api/info'),
  overview: () => http.get<Overview>('/api/overview'),
  testRegistry: () => http.post<{ ok: boolean; message: string }>('/api/registry/test'),
}

// ------------------------------------------------------------------ 应用与构建
export const appApi = {
  defaults: () => http.get<AppFields>('/api/app-defaults'),
  list: (query: { project_id?: number } = {}) => http.get<Page<App>>('/api/apps', query),
  get: (id: number) => http.get<App>(`/api/apps/${id}`),
  create: (data: Partial<AppFields> & { project_id: number }) => http.post<App>('/api/apps', data),
  update: (id: number, data: Partial<AppFields>) => http.put<App>(`/api/apps/${id}`, data),
  remove: (id: number) => http.del(`/api/apps/${id}`),
  importDockerfile: (text: string) => http.post<DockerfileImport>('/api/dockerfile/import', { text }),
  versions: (id: number) => http.get<Versions>(`/api/apps/${id}/versions`),
  preview: (id: number, data: { mode: string; base_tag: string; tag: string; filenames: string[]; extract_zip: boolean }) =>
    http.post<Preview>(`/api/apps/${id}/preview`, data),
}

export interface BuildRequest {
  appId: number
  mode: string
  tag: string
  baseTag: string
  remark: string
  extractZip: boolean
  files: File[]
}

export const buildApi = {
  submit(req: BuildRequest, onProgress?: (ratio: number) => void) {
    const form = new FormData()
    form.append('app_id', String(req.appId))
    form.append('mode', req.mode)
    form.append('tag', req.tag)
    form.append('base_tag', req.baseTag)
    form.append('remark', req.remark)
    form.append('extract_zip', req.extractZip ? 'true' : 'false')
    for (const file of req.files) form.append('files', file, file.name)
    return upload<{ id: number }>('/api/builds', form, onProgress)
  },
}

export interface JobQuery {
  app_id?: number | null
  project_id?: number | null
  status?: string
  limit?: number
  offset?: number
}

export const jobApi = {
  list: (query: JobQuery = {}) => http.get<Page<Job>>('/api/jobs', { ...query }),
  get: (id: number) => http.get<Job>(`/api/jobs/${id}`),
  log: (id: number, offset: number) => http.get<LogChunk>(`/api/jobs/${id}/log`, { offset }),
  cancel: (id: number) => http.post<{ ok: boolean }>(`/api/jobs/${id}/cancel`),
}

// ------------------------------------------------------------------ 项目、集群、命名空间
export interface ProjectInput {
  slug: string
  name: string
  registry_orgs: RegistryOrg[]
}

export const projectApi = {
  list: () => http.get<Page<Project>>('/api/projects'),
  get: (id: number) => http.get<Project>(`/api/projects/${id}`),
  create: (data: ProjectInput) => http.post<Project>('/api/projects', data),
  update: (id: number, data: ProjectInput) => http.put<Project>(`/api/projects/${id}`, data),
  remove: (id: number) => http.del(`/api/projects/${id}`),
  assignNamespace: (id: number, namespaceId: number) => http.put<Project>(`/api/projects/${id}/namespaces/${namespaceId}`, {}),
  unassignNamespace: (id: number, namespaceId: number) => http.del(`/api/projects/${id}/namespaces/${namespaceId}`),
}

export interface ClusterRequest {
  name: string
  cce_cluster_id: string
  server?: string
  auth_type?: 'sa_token' | 'client_cert'
  config?: string
  credential?: { token?: string; ca_pem?: string; client_cert_pem?: string; client_key_pem?: string }
}

export const clusterApi = {
  list: () => http.get<Page<Cluster>>('/api/clusters'),
  create: (data: ClusterRequest) => http.post<Cluster>('/api/clusters', data),
  update: (id: number, data: Partial<ClusterRequest>) => http.put<Cluster>(`/api/clusters/${id}`, data),
  test: (id: number) => http.post<{ ok: boolean; version: string; warnings: string[] }>(`/api/clusters/${id}/test`),
  discover: (id: number) => http.get<ClusterDiscovery>(`/api/clusters/${id}/discovery`),
}

export const namespaceApi = {
  list: (query: { cluster_id?: number; tier?: Tier } = {}) => http.get<Page<Namespace>>('/api/namespaces', query),
  get: (id: number) => http.get<Namespace>(`/api/namespaces/${id}`),
  resources: (id: number) => http.get<NamespaceResources>(`/api/namespaces/${id}/resources`),
  adopt: (data: { name: string; cluster_id: number; tier: Tier; policy: Partial<NamespacePolicy>; project_id?: number }) =>
    http.post<Namespace>('/api/namespaces', data),
  update: (id: number, data: { tier: Tier; policy: NamespacePolicy }) => http.put<Namespace>(`/api/namespaces/${id}`, data),
  review: (id: number) => http.post<RbacReview>(`/api/namespaces/${id}/review`),
  rbacYaml: (id: number) => http.get<{ yaml: string; warning: string }>(`/api/namespaces/${id}/rbac-yaml`),
}

// ------------------------------------------------------------------ 服务与发布
export interface ServiceQuery {
  project_id?: number
  app_id?: number
  namespace_id?: number
}

export const serviceApi = {
  list: (query: ServiceQuery = {}) => http.get<Page<Service>>('/api/services', { ...query }),
  get: (id: number) => http.get<Service>(`/api/services/${id}`),
  runtime: (id: number) => http.get<NamespaceResources>(`/api/services/${id}/runtime`),
  builds: (id: number) => http.get<Page<Job>>(`/api/services/${id}/builds`),
  create: (data: { name: string; project_id: number; namespace_id: number; app_id: number; spec: unknown }) =>
    http.post<Service>('/api/services', data),
  updateSpec: (id: number, base_revision: number, spec: unknown) =>
    http.put<{ service: Service; revision: Revision }>(`/api/services/${id}`, { base_revision, spec }),
  remove: (id: number) => http.del(`/api/services/${id}`),
  revisions: (id: number) => http.get<Page<Revision>>(`/api/services/${id}/revisions`),
  diff: (id: number, from: number, to: number) => http.get<RevisionDiff>(`/api/services/${id}/revisions/diff`, { from, to }),
  setSecret: (id: number, key: string, value: string) =>
    http.put<{ key: string; version: number; updated_at: string }>(`/api/services/${id}/secrets/${encodeURIComponent(key)}`, { value }),
  deploy: (id: number, key: string, data: { job_id: number; use_latest_config: boolean; remark: string }) =>
    http.post<DeploymentTicket>(`/api/services/${id}/deployments`, data, { 'Idempotency-Key': key }),
  rollback: (id: number, key: string, data: { target_deployment_id: number; remark: string }) =>
    http.post<DeploymentTicket>(`/api/services/${id}/rollback`, data, { 'Idempotency-Key': key }),
  restart: (id: number, key: string, remark: string) =>
    http.post<DeploymentTicket>(`/api/services/${id}/restart`, { remark }, { 'Idempotency-Key': key }),
}

export interface DeploymentQuery {
  limit?: number
  offset?: number
  app_id?: number
  project_id?: number
  namespace_id?: number
  service_id?: number
  status?: string
}

export const deploymentApi = {
  list: (query: DeploymentQuery = {}) => http.get<Page<Deployment>>('/api/deployments', { ...query }),
  get: (id: number) => http.get<Deployment>(`/api/deployments/${id}`),
  log: (id: number, offset: number) => http.get<LogChunk>(`/api/deployments/${id}/log`, { offset }),
  approve: (id: number, comment = '') => http.post<Deployment>(`/api/deployments/${id}/approve`, { comment }),
  reject: (id: number, comment = '') => http.post<Deployment>(`/api/deployments/${id}/reject`, { comment }),
}

// ------------------------------------------------------------------ 访问控制
export interface AccountCreate {
  username: string
  display_name: string
  email: string
  password: string
  platform_admin: boolean
  grants: GrantInput[]
}

export interface AccountUpdate {
  display_name: string
  email: string
  enabled: boolean
  platform_admin?: boolean
}

export const userApi = {
  list: () => http.get<Page<Account>>('/api/users'),
  get: (id: number) => http.get<AccountDetail>(`/api/users/${id}`),
  create: (data: AccountCreate) => http.post<Account>('/api/users', data),
  update: (id: number, data: AccountUpdate) => http.put<Account>(`/api/users/${id}`, data),
  remove: (id: number) => http.del(`/api/users/${id}`),
  resetPassword: (id: number) => http.post<{ password: string }>(`/api/users/${id}/reset-password`, {}),
}

export interface BindingQuery {
  user_id?: number
  scope_type?: ScopeType
  scope_id?: number
  role?: string
}

export const bindingApi = {
  list: (query: BindingQuery = {}) => http.get<Page<BindingRow>>('/api/bindings', { ...query }),
  options: () => http.get<GrantOptions>('/api/bindings/options'),
  users: () => http.get<Page<BindableUser>>('/api/bindable-users'),
  create: (subjectId: number, grant: GrantInput) =>
    http.post<Binding>('/api/bindings', { subject_type: 'user', subject_id: subjectId, ...grant }),
  remove: (id: number) => http.del(`/api/bindings/${id}`),
}

export type RoleInput = Pick<RoleDefinition, 'code' | 'name' | 'scope_type' | 'permissions' | 'description' | 'enabled'>

export const roleApi = {
  catalog: () => http.get<RoleCatalog>('/api/role-definitions'),
  save: (code: string | null, data: RoleInput) =>
    code ? http.put<RoleDefinition>(`/api/role-definitions/${code}`, data) : http.post<RoleDefinition>('/api/role-definitions', data),
  remove: (code: string) => http.del(`/api/role-definitions/${code}`),
}

export const auditApi = {
  list: (query: { limit: number; offset: number; username?: string; action?: string }) =>
    http.get<Page<AuditEntry>>('/api/audit', query),
}

// ------------------------------------------------------------------ 系统设置
export interface SettingsChange {
  values: Record<string, unknown>
  clear: string[]
}

export const settingsApi = {
  view: () => http.get<SettingsView>('/api/settings'),
  system: () => http.get<SystemInfo>('/api/system'),
  save: (change: SettingsChange) => http.put<{ ok: boolean; changed: string[] }>('/api/settings', change),
  testRegistry: (change: SettingsChange) => http.post<{ ok: boolean; message: string }>('/api/settings/registry/test', change),
}
