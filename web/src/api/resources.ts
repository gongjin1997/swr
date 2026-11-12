/**
 * 资源管理 API 客户端
 */

import { del, get, post, put } from './base'

export interface Resource {
  id: number
  scope_type: 'platform' | 'environment'
  scope_id: number
  type: string
  name: string
  description: string
  version: number
  attributes: Record<string, any>
  created_at: string
  updated_at: string
  can_edit: boolean
  can_reveal: boolean
  binding_count?: number
}

export interface ResourceVersion {
  resource_id: number
  number: number
  attributes: Record<string, any>
  checksum: string
  note: string
  created_at: string
  created_by: number
}

export interface ResourceBinding {
  alias: string
  resource_id: number
  resource: {
    id: number
    type: string
    name: string
    scope_type: string
    scope_id: number
    version: number
    attributes: Record<string, any>
  }
}

export interface ResourceExports {
  exports: Record<string, {
    resource_id: number
    resource_type: string
    resource_name: string
    env: Array<[string, string]>
    secret_env: Array<[string, string]>
  }>
}

export interface CreateResourceRequest {
  scope_type: 'platform' | 'environment'
  scope_id?: number
  type: string
  name: string
  description?: string
  attributes: Record<string, any>
  note?: string
}

export interface UpdateResourceRequest {
  base_version: number
  attributes: Record<string, any>
  note?: string
  description?: string
}

export interface BindResourceRequest {
  resource_id: number
  alias: string
}

export const listResources = (params?: {
  scope_type?: string
  scope_id?: number
  type?: string
}) => get<{ items: Resource[]; total: number }>('/api/resources', params)

export const createResource = (data: CreateResourceRequest) =>
  post<Resource>('/api/resources', data)

export const getResource = (id: number) => get<Resource>(`/api/resources/${id}`)

export const updateResource = (id: number, data: UpdateResourceRequest) =>
  put<Resource>(`/api/resources/${id}`, data)

export const deleteResource = (id: number) => del(`/api/resources/${id}`)

export const listResourceVersions = (id: number) =>
  get<{ items: ResourceVersion[]; total: number }>(`/api/resources/${id}/versions`)

export const getResourceVersion = (id: number, version: number) =>
  get<ResourceVersion>(`/api/resources/${id}/versions/${version}`)

export const revealResource = (id: number) =>
  post<{ id: number; type: string; name: string; version: number; attributes: Record<string, any> }>(
    `/api/resources/${id}/reveal`
  )

export const listServiceBindings = (serviceId: number) =>
  get<{ items: ResourceBinding[]; total: number }>(`/api/services/${serviceId}/bindings`)

export const bindResource = (serviceId: number, data: BindResourceRequest) =>
  post<{ alias: string; resource_id: number }>(`/api/services/${serviceId}/bindings`, data)

export const unbindResource = (serviceId: number, alias: string) =>
  del(`/api/services/${serviceId}/bindings/${alias}`)

export const previewExports = (serviceId: number, reveal?: boolean) =>
  get<ResourceExports>(`/api/services/${serviceId}/exports`, { reveal })
