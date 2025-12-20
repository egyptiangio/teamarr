import { api } from "./client"
import type {
  EventGroup,
  EventGroupCreate,
  EventGroupListResponse,
  EventGroupUpdate,
  ProcessGroupResponse,
} from "./types"

export async function listGroups(
  includeDisabled = false,
  includeStats = true
): Promise<EventGroupListResponse> {
  const params = new URLSearchParams()
  if (includeDisabled) params.set("include_disabled", "true")
  if (includeStats) params.set("include_stats", "true")
  return api.get(`/groups?${params}`)
}

export async function getGroup(groupId: number): Promise<EventGroup> {
  return api.get(`/groups/${groupId}`)
}

export async function createGroup(data: EventGroupCreate): Promise<EventGroup> {
  return api.post("/groups", data)
}

export async function updateGroup(
  groupId: number,
  data: EventGroupUpdate
): Promise<EventGroup> {
  return api.put(`/groups/${groupId}`, data)
}

export async function deleteGroup(
  groupId: number
): Promise<{ success: boolean; message: string; channels_deleted: number }> {
  return api.delete(`/groups/${groupId}`)
}

export async function enableGroup(
  groupId: number
): Promise<{ success: boolean; message: string }> {
  return api.post(`/groups/${groupId}/enable`)
}

export async function disableGroup(
  groupId: number
): Promise<{ success: boolean; message: string }> {
  return api.post(`/groups/${groupId}/disable`)
}

export async function processGroup(
  groupId: number
): Promise<ProcessGroupResponse> {
  return api.post(`/groups/${groupId}/process`)
}

export async function processAllGroups(): Promise<{
  groups_processed: number
  total_channels_created: number
  total_errors: number
  duration_seconds: number
  results: ProcessGroupResponse[]
}> {
  return api.post("/groups/process-all")
}
