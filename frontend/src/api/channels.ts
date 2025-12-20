import { api } from "./client"

export interface ManagedChannel {
  id: number
  event_epg_group_id: number
  event_id: string
  event_provider: string
  tvg_id: string
  channel_name: string
  channel_number: string | null
  logo_url: string | null
  dispatcharr_channel_id: number | null
  dispatcharr_uuid: string | null
  home_team: string | null
  away_team: string | null
  event_date: string | null
  event_name: string | null
  league: string | null
  sport: string | null
  scheduled_delete_at: string | null
  sync_status: string
  created_at: string | null
  updated_at: string | null
}

export interface ManagedChannelListResponse {
  channels: ManagedChannel[]
  total: number
}

export interface ReconciliationIssue {
  issue_type: string
  severity: string
  managed_channel_id: number | null
  dispatcharr_channel_id: number | null
  channel_name: string | null
  event_id: string | null
  details: Record<string, unknown>
  suggested_action: string | null
  auto_fixable: boolean
}

export interface ReconciliationSummary {
  orphan_teamarr: number
  orphan_dispatcharr: number
  duplicate: number
  drift: number
  total: number
  fixed: number
  skipped: number
  errors: number
}

export interface ReconciliationResponse {
  started_at: string | null
  completed_at: string | null
  summary: ReconciliationSummary
  issues_found: ReconciliationIssue[]
  issues_fixed: Record<string, unknown>[]
  issues_skipped: Record<string, unknown>[]
  errors: string[]
}

export interface SyncResponse {
  created_count: number
  existing_count: number
  skipped_count: number
  deleted_count: number
  error_count: number
  created: Record<string, unknown>[]
  errors: Record<string, unknown>[]
}

export interface DeleteResponse {
  success: boolean
  message: string
}

export interface PendingDeletionsResponse {
  count: number
  channels: Array<{
    id: number
    channel_name: string
    tvg_id: string
    scheduled_delete_at: string | null
    dispatcharr_channel_id: number | null
  }>
}

export async function listManagedChannels(
  groupId?: number,
  includeDeleted = false
): Promise<ManagedChannelListResponse> {
  const params = new URLSearchParams()
  if (groupId !== undefined) params.set("group_id", groupId.toString())
  if (includeDeleted) params.set("include_deleted", "true")
  const query = params.toString()
  return api.get(`/channels/managed${query ? `?${query}` : ""}`)
}

export async function getManagedChannel(channelId: number): Promise<ManagedChannel> {
  return api.get(`/channels/managed/${channelId}`)
}

export async function deleteManagedChannel(channelId: number): Promise<DeleteResponse> {
  return api.delete(`/channels/managed/${channelId}`)
}

export async function syncLifecycle(): Promise<SyncResponse> {
  return api.post("/channels/sync", {})
}

export async function getReconciliationStatus(
  groupIds?: number[]
): Promise<ReconciliationResponse> {
  const params = groupIds?.length ? `?group_ids=${groupIds.join(",")}` : ""
  return api.get(`/channels/reconciliation/status${params}`)
}

export async function runReconciliation(
  autoFix: boolean,
  groupIds?: number[]
): Promise<ReconciliationResponse> {
  return api.post("/channels/reconciliation/fix", {
    auto_fix: autoFix,
    group_ids: groupIds,
  })
}

export async function getPendingDeletions(): Promise<PendingDeletionsResponse> {
  return api.get("/channels/pending-deletions")
}
