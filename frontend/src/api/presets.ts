import { api } from "./client"

export interface ConditionPreset {
  id: number
  name: string
  description: string | null
  conditions: Array<{
    condition: string
    template: string
    priority: number
    condition_value?: string | number
  }>
  created_at: string | null
}

export interface ConditionPresetCreate {
  name: string
  description?: string | null
  conditions: Array<{
    condition: string
    template: string
    priority: number
    condition_value?: string | number
  }>
}

export interface ConditionPresetListResponse {
  presets: ConditionPreset[]
  total: number
}

export async function fetchPresets(): Promise<ConditionPresetListResponse> {
  return api.get("/presets")
}

export async function getPreset(id: number): Promise<ConditionPreset> {
  return api.get(`/presets/${id}`)
}

export async function createPreset(data: ConditionPresetCreate): Promise<ConditionPreset> {
  return api.post("/presets", data)
}

export async function updatePreset(id: number, data: Partial<ConditionPresetCreate>): Promise<ConditionPreset> {
  return api.put(`/presets/${id}`, data)
}

export async function deletePreset(id: number): Promise<void> {
  return api.delete(`/presets/${id}`)
}
