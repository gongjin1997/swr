/** 各领域的接口函数。部分旧接口返回裸数组，这里统一成 Page<T>，页面无需关心差异。 */
import { http, upload } from './http'
import type {
  ApiToken, App, AppFields, AuditEntry, BindableUser, Binding, Cluster, DeployProvider, DeployTarget, Deployment,
  DockerfileImport, Job, LogChunk, Namespace, NamespacePolicy, Page, PlatformInfo, Preview, Project, PublicUser,
  RegistryOrg, Revision, RevisionDiff, RolesInfo, Service, SessionProfile, SessionRow, SettingsView, SystemInfo,
  Tier, Versions,
} from './types'

const page = <T>(items: T[]): Page<T> => ({ items, total: items.length })

export const authApi = {
  config: () => http.get<{ site_name: string; version: string }>('/api/auth/config'),
  login: (username: string, password: string, remember: boolean) =>
    http.post<PublicUser>('/api/auth/login', { username, password, remember }),
  me: () => http.get<SessionProfile>('/api/auth/me'),
  logout: () => http.post<{ ok: boolean }>('/api/auth/logout'),
}

export const accountApi = {
  updateProfile: (display_name: string, email: string) =>
    http.put<PublicUser>('/api/account/profile', { display_name, email }),
  changePassword: (old_password: string, new_password: string) =>
    http.put<{ ok: boolean }>('/api/account/password', { old_password, new_password }),
  sessions: async () => page(await http.get<SessionRow[]>('/api/account/sessions')),
  revokeSession: (id: string) => http.del(`/api/account/sessions/${encodeURIComponent(id)}`),
  tokens: async () => page(await http.get<ApiToken[]>('/api/account/tokens')),
  createToken: (name: string, expires_days: number) =>
    http.post<{ id: number; token: string }>('/api/account/tokens', { name, expires_days }),
  revokeToken: (id: number) => http.del(`/api/account/tokens/${id}`),
}

export const platformApi = {
  info: () => http.get<PlatformInfo>('/api/info'),
  testRegistry: () => http.post<{ ok: boolean; message: string }>('/api/registry/test'),
}

export const appApi = {
  defaults: () => http.get<AppFields>('/api/app-defaults'),
  list: async () => page(await http.get<App[]>('/api/apps')),
  create: (data: Partial<AppFields> & { project_id?: number }) => http.post<App>('/api/apps', data),
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

export const jobApi = {
  list: (query: { app_id?: number | null; status?: string; limit?: number; offset?: number }) =>
    http.get<Page<Job>>('/api/jobs', query),
  get: (id: number) => http.get<Job>(`/api/jobs/${id}`),
  log: (id: number, offset: number) => http.get<LogChunk>(`/api/jobs/${id}/log`, { offset }),
  cancel: (id: number) => http.post<{ ok: boolean }>(`/api/jobs/${id}/cancel`),
}

export const projectApi = {
  list: () => http.get<Page<Project>>('/api/projects'),
  create: (data: { slug: string; name: string; registry_orgs: RegistryOrg[] }) => http.post<Project>('/api/projects', data),
  update: (id: number, data: { slug: string; name: string; registry_orgs: RegistryOrg[] }) =>
    http.put<Project>(`/api/projects/${id}`, data),
  remove: (id: number) => http.del(`/api/projects/${id}`),
  assignNamespace: (id: number, namespaceId: number) => http.put<Project>(`/api/projects/${id}/namespaces/${namespaceId}`, {}),
  unassignNamespace: (id: number, namespaceId: number) => http.del(`/api/projects/${id}/namespaces/${namespaceId}`),
}

export interface ClusterRequest {
  name: string
  server: string
  auth_type: 'sa_token'
  cce_cluster_id: string
  credential?: { token: string; ca_pem: string }
}

export const clusterApi = {
  list: () => http.get<Page<Cluster>>('/api/clusters'),
  create: (data: ClusterRequest) => http.post<Cluster>('/api/clusters', data),
  update: (id: number, data: Partial<ClusterRequest>) => http.put<Cluster>(`/api/clusters/${id}`, data),
  test: (id: number) => http.post<{ ok: boolean; version: string; warnings: string[] }>(`/api/clusters/${id}/test`),
}

export interface RbacReview {
  ok: boolean
  checks: { group: string; resource: string; verb: string; allowed: boolean }[]
  warnings: string[]
}

export const namespaceApi = {
  list: () => http.get<Page<Namespace>>('/api/namespaces'),
  adopt: (data: { name: string; cluster_id: number; tier: Tier; policy: Partial<NamespacePolicy> }) =>
    http.post<Namespace>('/api/namespaces', data),
  update: (id: number, data: { tier: Tier; policy: NamespacePolicy }) => http.put<Namespace>(`/api/namespaces/${id}`, data),
  review: (id: number) => http.post<RbacReview>(`/api/namespaces/${id}/review`),
  rbacYaml: (id: number) => http.get<{ yaml: string; warning: string }>(`/api/namespaces/${id}/rbac-yaml`),
}

export const serviceApi = {
  list: () => http.get<Page<Service>>('/api/services'),
  get: (id: number) => http.get<Service>(`/api/services/${id}`),
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
    http.post<Deployment>(`/api/services/${id}/deployments`, data, { 'Idempotency-Key': key }),
  rollback: (id: number, key: string, data: { target_deployment_id: number; remark: string }) =>
    http.post<Deployment>(`/api/services/${id}/rollback`, data, { 'Idempotency-Key': key }),
  restart: (id: number, key: string, remark: string) =>
    http.post<Deployment>(`/api/services/${id}/restart`, { remark }, { 'Idempotency-Key': key }),
}

export const deploymentApi = {
  list: (query: { limit?: number; offset?: number; app_id?: number } = {}) => http.get<Page<Deployment>>('/api/deployments', query),
  get: (id: number) => http.get<Deployment>(`/api/deployments/${id}`),
  log: (id: number, offset: number) => http.get<LogChunk>(`/api/deployments/${id}/log`, { offset }),
  approve: (id: number, comment = '') => http.post<Deployment>(`/api/deployments/${id}/approve`, { comment }),
  reject: (id: number, comment = '') => http.post<Deployment>(`/api/deployments/${id}/reject`, { comment }),
}

export const bindingApi = {
  list: () => http.get<Page<Binding>>('/api/bindings'),
  users: () => http.get<Page<BindableUser>>('/api/bindable-users'),
  create: (data: Omit<Binding, 'id'>) => http.post<Binding>('/api/bindings', data),
  remove: (id: number) => http.del(`/api/bindings/${id}`),
}

export const userApi = {
  list: async () => page(await http.get<PublicUser[]>('/api/users')),
  roles: () => http.get<RolesInfo>('/api/roles'),
  create: (data: { username: string; display_name: string; email: string; role: string; password: string }) =>
    http.post<PublicUser>('/api/users', data),
  update: (id: number, data: { display_name: string; email: string; role: string; enabled: boolean }) =>
    http.put<PublicUser>(`/api/users/${id}`, data),
  remove: (id: number) => http.del(`/api/users/${id}`),
  resetPassword: (id: number) => http.post<{ password: string }>(`/api/users/${id}/reset-password`, {}),
  audit: (query: { limit: number; offset: number; username?: string; action?: string }) =>
    http.get<Page<AuditEntry>>('/api/audit', query),
}

export interface SettingsChange {
  values: Record<string, unknown>
  clear: string[]
}

export const settingsApi = {
  view: () => http.get<SettingsView>('/api/settings'),
  system: () => http.get<SystemInfo>('/api/system'),
  save: (change: SettingsChange) => http.put<{ ok: boolean; changed: string[] }>('/api/settings', change),
  testRegistry: (change: SettingsChange) => http.post<{ ok: boolean; message: string }>('/api/settings/registry/test', change),
  testHcs: (change: SettingsChange) => http.post<{ validated?: boolean; ok?: boolean; message: string }>('/api/cloud/hcs/test', change),
}

export const legacyDeployApi = {
  providers: () => http.get<DeployProvider[]>('/api/deploy/providers'),
  targets: async () => page(await http.get<DeployTarget[]>('/api/deploy/targets')),
  save: (id: number | null, data: Record<string, unknown>) =>
    id ? http.put<DeployTarget>(`/api/deploy/targets/${id}`, data) : http.post<DeployTarget>('/api/deploy/targets', data),
  remove: (id: number) => http.del(`/api/deploy/targets/${id}`),
  test: (id: number) => http.post<{ ok: boolean; message: string }>(`/api/deploy/targets/${id}/test`),
}
