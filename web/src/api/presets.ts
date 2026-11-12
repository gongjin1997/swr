/**
 * 环境预置 API 客户端
 */

import { del, get, post, put } from './base'

export interface Preset {
  id: number
  name: string
  description: string
  profile_template: Record<string, any>
  default_config_sets: Array<{
    kind: string
    name: string
    entries: Array<{ key: string; value?: string; size?: number }>
  }>
  required_resource_types: string[]
  required_params: Array<{
    key: string
    label: string
    required?: boolean
  }>
  created_at: string
  updated_at: string
  created_by: number
}

export interface CreatePresetRequest {
  name: string
  description?: string
  profile_template: Record<string, any>
  default_config_sets: Array<{
    kind: string
    name: string
    entries: Array<{ key: string; value?: string; size?: number }>
  }>
  required_resource_types: string[]
  required_params: Array<{
    key: string
    label: string
    required?: boolean
  }>
}

export interface UpdatePresetRequest extends CreatePresetRequest {}

export interface ApplyPresetRequest {
  preset_id: number
}

export const listPresets = () => get<{ items: Preset[]; total: number }>('/api/presets')

export const createPreset = (data: CreatePresetRequest) => post<Preset>('/api/presets', data)

export const getPreset = (id: number) => get<Preset>(`/api/presets/${id}`)

export const updatePreset = (id: number, data: UpdatePresetRequest) =>
  put<Preset>(`/api/presets/${id}`, data)

export const deletePreset = (id: number) => del(`/api/presets/${id}`)

export const applyPreset = (environmentId: number, data: ApplyPresetRequest) =>
  post(`/api/environments/${environmentId}/apply-preset`, data)
