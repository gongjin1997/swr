/** 环境预置 API */
import { http } from './http'
import type { Preset, CreatePresetRequest, ApplyPresetRequest, Page } from './types'

export const presetApi = {
  list: () =>
    http.get<Page<Preset>>('/api/presets'),
  create: (data: CreatePresetRequest) =>
    http.post<Preset>('/api/presets', data),
  get: (id: number) =>
    http.get<Preset>(`/api/presets/${id}`),
  update: (id: number, data: CreatePresetRequest) =>
    http.put<Preset>(`/api/presets/${id}`, data),
  remove: (id: number) =>
    http.del(`/api/presets/${id}`),
  apply: (environmentId: number, data: ApplyPresetRequest) =>
    http.post(`/api/environments/${environmentId}/apply-preset`, data),
}
