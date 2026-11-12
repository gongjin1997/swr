/** 资源管理 API */
import { http } from './http'
import type {
  Resource, ResourceVersion, ResourceBinding, ResourceExport,
  CreateResourceRequest, UpdateResourceRequest, BindResourceRequest, Page
} from './types'

export const resourceApi = {
  list: (params?: { scope_type?: string; scope_id?: number; type?: string }) =>
    http.get<Page<Resource>>('/api/resources', { params }),
  create: (data: CreateResourceRequest) =>
    http.post<Resource>('/api/resources', data),
  get: (id: number) =>
    http.get<Resource>(`/api/resources/${id}`),
  update: (id: number, data: UpdateResourceRequest) =>
    http.put<Resource>(`/api/resources/${id}`, data),
  delete: (id: number) =>
    http.delete(`/api/resources/${id}`),
  versions: (id: number) =>
    http.get<Page<ResourceVersion>>(`/api/resources/${id}/versions`),
  getVersion: (id: number, version: number) =>
    http.get<ResourceVersion>(`/api/resources/${id}/versions/${version}`),
  reveal: (id: number) =>
    http.post<{ id: number; type: string; name: string; version: number; attributes: Record<string, any> }>(
      `/api/resources/${id}/reveal`
    ),
}

export const serviceResourceApi = {
  listBindings: (serviceId: number) =>
    http.get<Page<ResourceBinding>>(`/api/services/${serviceId}/bindings`),
  bind: (serviceId: number, data: BindResourceRequest) =>
    http.post<{ alias: string; resource_id: number }>(`/api/services/${serviceId}/bindings`, data),
  unbind: (serviceId: number, alias: string) =>
    http.delete(`/api/services/${serviceId}/bindings/${alias}`),
  previewExports: (serviceId: number, reveal?: boolean) =>
    http.get<{ exports: Record<string, ResourceExport> }>(`/api/services/${serviceId}/exports`, { params: { reveal } }),
}
