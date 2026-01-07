import { api } from "./client"

// Settings Types
export interface DispatcharrSettings {
  enabled: boolean
  url: string | null
  username: string | null
  password: string | null
  epg_id: number | null
}

export interface LifecycleSettings {
  channel_create_timing: string
  channel_delete_timing: string
  channel_range_start: number
  channel_range_end: number | null
}

export interface SchedulerSettings {
  enabled: boolean
  interval_minutes: number
}

export interface EPGSettings {
  team_schedule_days_ahead: number
  event_match_days_ahead: number
  epg_output_days_ahead: number
  epg_lookback_hours: number
  epg_timezone: string
  epg_output_path: string
  include_final_events: boolean
  midnight_crossover_mode: string
  cron_expression: string
}

// Note: team_schedule_days_ahead default is 30 (for Team EPG)
// Note: event_match_days_ahead default is 3 (for Event Groups)

export interface DurationSettings {
  default: number
  basketball: number
  football: number
  hockey: number
  baseball: number
  soccer: number
  mma: number
  rugby: number
  boxing: number
  tennis: number
  golf: number
  racing: number
  cricket: number
}

export interface ReconciliationSettings {
  reconcile_on_epg_generation: boolean
  reconcile_on_startup: boolean
  auto_fix_orphan_teamarr: boolean
  auto_fix_orphan_dispatcharr: boolean
  auto_fix_duplicates: boolean
  default_duplicate_event_handling: string
  channel_history_retention_days: number
}

export interface DisplaySettings {
  time_format: string
  show_timezone: boolean
  channel_id_format: string
  xmltv_generator_name: string
  xmltv_generator_url: string
  tsdb_api_key: string | null  // Optional TheSportsDB premium API key
}

export interface ExceptionKeyword {
  id: number
  keywords: string
  keyword_list: string[]
  behavior: "consolidate" | "separate" | "ignore"
  display_name: string | null
  enabled: boolean
  created_at: string | null
}

export interface ExceptionKeywordListResponse {
  keywords: ExceptionKeyword[]
  total: number
}

export interface AllSettings {
  dispatcharr: DispatcharrSettings
  lifecycle: LifecycleSettings
  scheduler: SchedulerSettings
  epg: EPGSettings
  durations: DurationSettings
  reconciliation: ReconciliationSettings
  epg_generation_counter: number
  schema_version: number
}

export interface ConnectionTestResponse {
  success: boolean
  url: string | null
  username: string | null
  version: string | null
  account_count: number | null
  group_count: number | null
  channel_count: number | null
  error: string | null
}

export interface SchedulerStatus {
  running: boolean
  cron_expression: string | null
  last_run: string | null
  next_run: string | null
}

// Note: cron_description is handled on frontend via cronstrue library

export interface DispatcharrStatus {
  configured: boolean
  connected: boolean
}

export interface EPGSource {
  id: number
  name: string
  source_type: string
  status: string
}

export interface EPGSourcesResponse {
  success: boolean
  sources: EPGSource[]
  error?: string
}

// API Functions
export async function getSettings(): Promise<AllSettings> {
  return api.get("/settings")
}

export async function getDispatcharrSettings(): Promise<DispatcharrSettings> {
  return api.get("/settings/dispatcharr")
}

export async function updateDispatcharrSettings(
  data: Partial<DispatcharrSettings>
): Promise<DispatcharrSettings> {
  return api.put("/settings/dispatcharr", data)
}

export async function testDispatcharrConnection(data?: {
  url?: string
  username?: string
  password?: string
}): Promise<ConnectionTestResponse> {
  return api.post("/dispatcharr/test", data || {})
}

export async function getDispatcharrStatus(): Promise<DispatcharrStatus> {
  return api.get("/dispatcharr/status")
}

export async function getDispatcharrEPGSources(): Promise<EPGSourcesResponse> {
  return api.get("/dispatcharr/epg-sources")
}

export async function getLifecycleSettings(): Promise<LifecycleSettings> {
  return api.get("/settings/lifecycle")
}

export async function updateLifecycleSettings(
  data: LifecycleSettings
): Promise<LifecycleSettings> {
  return api.put("/settings/lifecycle", data)
}

export async function getSchedulerSettings(): Promise<SchedulerSettings> {
  return api.get("/settings/scheduler")
}

export async function updateSchedulerSettings(
  data: SchedulerSettings
): Promise<SchedulerSettings> {
  return api.put("/settings/scheduler", data)
}

export async function getSchedulerStatus(): Promise<SchedulerStatus> {
  return api.get("/scheduler/status")
}

export async function getEPGSettings(): Promise<EPGSettings> {
  return api.get("/settings/epg")
}

export async function updateEPGSettings(data: EPGSettings): Promise<EPGSettings> {
  return api.put("/settings/epg", data)
}

export async function getDurationSettings(): Promise<DurationSettings> {
  return api.get("/settings/durations")
}

export async function updateDurationSettings(
  data: DurationSettings
): Promise<DurationSettings> {
  return api.put("/settings/durations", data)
}

export async function getReconciliationSettings(): Promise<ReconciliationSettings> {
  return api.get("/settings/reconciliation")
}

export async function updateReconciliationSettings(
  data: ReconciliationSettings
): Promise<ReconciliationSettings> {
  return api.put("/settings/reconciliation", data)
}

export async function getDisplaySettings(): Promise<DisplaySettings> {
  return api.get("/settings/display")
}

export async function updateDisplaySettings(
  data: DisplaySettings
): Promise<DisplaySettings> {
  return api.put("/settings/display", data)
}

// Exception Keywords API
export async function getExceptionKeywords(
  includeDisabled: boolean = false
): Promise<ExceptionKeywordListResponse> {
  return api.get(`/keywords?include_disabled=${includeDisabled}`)
}

export async function createExceptionKeyword(data: {
  keywords: string
  behavior: string
  display_name?: string
  enabled?: boolean
}): Promise<ExceptionKeyword> {
  return api.post("/keywords", data)
}

export async function updateExceptionKeyword(
  id: number,
  data: Partial<{
    keywords: string
    behavior: string
    display_name: string | null
    enabled: boolean
  }>
): Promise<ExceptionKeyword> {
  return api.put(`/keywords/${id}`, data)
}

export async function deleteExceptionKeyword(id: number): Promise<void> {
  return api.delete(`/keywords/${id}`)
}
