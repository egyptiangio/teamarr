import { api } from "./client"
import type {
  BulkGroupUpdateRequest,
  BulkGroupUpdateResponse,
  EventGroup,
  EventGroupCreate,
  EventGroupListResponse,
  EventGroupUpdate,
  PreviewGroupResponse,
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

export async function previewGroup(
  groupId: number
): Promise<PreviewGroupResponse> {
  return api.get(`/groups/${groupId}/preview`)
}

export interface RawStream {
  stream_id: number
  stream_name: string
  /** Reason stream would be filtered by builtin filters (null if passes) */
  builtin_filtered: string | null
}

export interface RawStreamsResponse {
  group_id: number
  group_name: string
  total: number
  streams: RawStream[]
}

export async function getRawStreams(
  groupId: number
): Promise<RawStreamsResponse> {
  return api.get(`/groups/${groupId}/streams/raw`)
}

export async function reorderGroups(
  groups: { group_id: number; sort_order: number }[]
): Promise<{ success: boolean; updated_count: number; message: string }> {
  return api.post("/groups/reorder", { groups })
}

export async function bulkUpdateGroups(
  data: BulkGroupUpdateRequest
): Promise<BulkGroupUpdateResponse> {
  return api.put("/groups/bulk", data)
}

export interface ClearCacheResponse {
  success: boolean
  group_id?: number
  group_name?: string
  entries_cleared?: number
  total_cleared?: number
  by_group?: { group_id: number; cleared: number }[]
}

export async function clearGroupMatchCache(
  groupId: number
): Promise<ClearCacheResponse> {
  return api.post(`/groups/${groupId}/cache/clear`)
}

export async function clearGroupsMatchCache(
  groupIds: number[]
): Promise<ClearCacheResponse> {
  return api.post("/groups/cache/clear", { group_ids: groupIds })
}
