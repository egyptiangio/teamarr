import { api } from "./client"

// Settings Types
export interface DispatcharrSettings {
  enabled: boolean
  url: string | null
  username: string | null
  password: string | null
  epg_id: number | null
  // null = all profiles (default), [] = no profiles, [1,2,...] = specific profiles
  // Can include wildcards: "{sport}", "{league}"
  default_channel_profile_ids: (number | string)[] | null
  // Default stream profile for event channels (overrideable per-group)
  default_stream_profile_id: number | null
  // Default channel group for event channels (overrideable per-league)
  default_channel_group_id: number | null
  // Channel group mode: 'static', 'sport', 'league', or custom pattern
  default_channel_group_mode: string | null
  // Clean up ALL unused logos in Dispatcharr after generation
  cleanup_unused_logos: boolean
}

export interface LifecycleSettings {
  channel_create_timing: string
  channel_delete_timing: string
  channel_pre_buffer_minutes: number
  channel_post_buffer_minutes: number
  channel_range_start: number
  channel_range_end: number | null
}

export interface SchedulerSettings {
  enabled: boolean
  interval_minutes: number
  // Scheduled channel reset (for Jellyfin logo cache issues)
  channel_reset_enabled: boolean
  channel_reset_cron: string | null
}

export interface SchedulerSettingsUpdate {
  enabled?: boolean
  interval_minutes?: number
  channel_reset_enabled?: boolean
  channel_reset_cron?: string | null
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
  epg_xtream_fallback_enabled: boolean
  epg_xtream_cache_hours: number
  epg_channel_source_enabled: boolean
  epg_channel_source_groups: number[]
  stream_profile_overrides: StreamProfileOverride[]
  epg_stream_pre_buffer_minutes: number
  epg_stream_post_buffer_minutes: number
  tennis_majors_only: boolean
  /** Game-thumbs base URL prefixed onto relative art paths in templates (z02s). */
  art_base_url: string
}

export interface StreamProfileOverride {
  target_type: "dispatcharr_channel_group"
  target_id: number
  stream_profile_id: number
}

// Note: team_schedule_days_ahead default is 30 (for Team EPG)
// Note: event_match_days_ahead default is 3 (for Event Groups)

// Dynamic dict - sports are defined in backend DurationSettings dataclass
// No need to duplicate field definitions here
export type DurationSettings = Record<string, number>

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
  sport_naming: "us" | "international"
  matchup_order: "auto" | "away_first" | "home_first"
  show_timezone: boolean
  channel_id_format: string
  xmltv_generator_name: string
  xmltv_generator_url: string
  tsdb_api_key: string | null  // Optional TheSportsDB premium API key
}

export interface TSDBKeyValidationResult {
  valid: boolean
  message: string
}

export async function validateTSDBKey(apiKey: string): Promise<TSDBKeyValidationResult> {
  return api.post("/settings/tsdb/validate-key", { api_key: apiKey })
}

export interface TeamFilterEntry {
  provider: string
  team_id: string
  league: string
  name?: string | null
}

export interface TeamFilterSettings {
  enabled: boolean
  include_teams: TeamFilterEntry[] | null
  exclude_teams: TeamFilterEntry[] | null
  mode: "include" | "exclude"
  bypass_filter_for_playoffs: boolean
}

export interface TeamFilterSettingsUpdate {
  enabled?: boolean
  include_teams?: TeamFilterEntry[] | null
  exclude_teams?: TeamFilterEntry[] | null
  mode?: "include" | "exclude"
  clear_include_teams?: boolean
  clear_exclude_teams?: boolean
  bypass_filter_for_playoffs?: boolean
}

export type ChannelStabilityMode = "compact" | "gap" | "strict"

export interface ChannelNumberingSettings {
  /** Always "auto" since v88 — manual mode was replaced by pinned blocks (#333). */
  global_channel_mode: "auto" | "manual"
  /** Legacy manual-mode starts; migrated to pinned blocks in v88, no longer read. */
  league_channel_starts: Record<string, number>
  global_consolidation_mode: "consolidate" | "separate"
  channel_stability_mode: ChannelStabilityMode
  channel_gap_size: number
  channel_daily_reset_enabled: boolean
  channel_daily_reset_time: string
  // One-shot re-grid armed for the next generation (read-only; set via relayout endpoint)
  force_channel_relayout_pending: boolean
}

export interface ChannelNumberingSettingsUpdate {
  global_channel_mode?: "auto" | "manual"
  league_channel_starts?: Record<string, number>
  global_consolidation_mode?: "consolidate" | "separate"
  channel_stability_mode?: ChannelStabilityMode
  channel_gap_size?: number
  channel_daily_reset_enabled?: boolean
  channel_daily_reset_time?: string
}

export interface StreamOrderingRule {
  type: "m3u" | "group" | "regex" | "stream_type" | "team_feed" | "not_team_feed" | "epg_match" | "dispatcharr_group" | "home_feed" | "away_feed" | "stats_metric" | "catch_all"
  value: string
  priority: number  // 1-99, lower = higher priority (orders 'priority'-mode rules / sets band)
  mode: "priority" | "score"  // 'priority' = hard first-match band; 'score' = additive
  points: number  // signed; summed across matched 'score' rules (ignored for 'priority')
}

export interface StreamOrderingSettings {
  rules: StreamOrderingRule[]
}

export interface StreamOrderingSettingsUpdate {
  rules: StreamOrderingRule[]
}

export interface UpdateCheckSettings {
  enabled: boolean
  notify_stable: boolean
  notify_dev: boolean
  github_owner: string
  github_repo: string
  dev_branch: string
  auto_detect_branch: boolean
}

export interface UpdateCheckSettingsUpdate {
  enabled?: boolean
  notify_stable?: boolean
  notify_dev?: boolean
  github_owner?: string
  github_repo?: string
  dev_branch?: string
  auto_detect_branch?: boolean
}

export interface UpdateInfo {
  current_version: string
  latest_version: string | null
  update_available: boolean
  checked_at: string
  build_type: "stable" | "dev" | "unknown"
  download_url: string | null
  latest_stable: string | null
  latest_dev: string | null
  latest_date: string | null  // ISO timestamp of when latest version was released
}

export interface ExceptionKeyword {
  id: number
  label: string
  match_terms: string
  match_term_list: string[]
  behavior: "consolidate" | "separate" | "ignore"
  enabled: boolean
  created_at: string | null
}

export interface ExceptionKeywordListResponse {
  keywords: ExceptionKeyword[]
  total: number
}

export interface FeedSeparationSettings {
  enabled: boolean
  // Sport codes the split applies to; [] = every sport (#732)
  sports: string[]
  home_terms: string[]
  away_terms: string[]
  detect_team_names: boolean
  label_style: "team_name" | "short_name" | "home_away"
}

export interface FeedSeparationSettingsUpdate {
  enabled?: boolean
  sports?: string[]
  home_terms?: string[]
  away_terms?: string[]
  detect_team_names?: boolean
  label_style?: "team_name" | "short_name" | "home_away"
}

// One Emby/Jellyfin server target (#471). Secrets round-trip masked
// ("********") for untouched rows; the backend merges stored values back.
export interface MediaServerEntry {
  name: string
  url: string | null
  username: string | null
  password: string | null
  api_key: string | null
}

export interface EmbySettings {
  enabled: boolean
  // Full-replace on update: send the complete list
  servers: MediaServerEntry[]
}

export interface EmbyTestResponse {
  success: boolean
  server_name?: string | null
  server_version?: string | null
  error?: string | null
}

export interface JellyfinSettings {
  enabled: boolean
  servers: MediaServerEntry[]
}

export interface JellyfinTestResponse {
  success: boolean
  server_name?: string | null
  server_version?: string | null
  error?: string | null
}

export interface ChannelsDVRServer {
  name: string
  url: string | null
  source_name: string | null
  lineup_id: string | null
}

export interface ChannelsDVRSettings {
  enabled: boolean
  // Full-replace on update: send the complete list
  servers: ChannelsDVRServer[]
}

export interface ChannelsDVRTestResponse {
  success: boolean
  server_version?: string | null
  source_name?: string | null
  error?: string | null
}

export interface ProxySettings {
  enabled: boolean
  url: string | null
  user_agent: string | null
  excluded_providers: string[]
}

export interface ChannelsDVRSourcesResponse {
  success: boolean
  sources: string[]
  error?: string | null
}

export interface ChannelsDVRLineup {
  id: string
  name: string
}

export interface ChannelsDVRLineupsResponse {
  success: boolean
  lineups: ChannelsDVRLineup[]
  error?: string | null
}

export interface AllSettings {
  dispatcharr: DispatcharrSettings
  lifecycle: LifecycleSettings
  scheduler: SchedulerSettings
  epg: EPGSettings
  durations: DurationSettings
  reconciliation: ReconciliationSettings
  display?: DisplaySettings
  team_filter?: TeamFilterSettings
  channel_numbering?: ChannelNumberingSettings
  stream_ordering?: StreamOrderingSettings
  update_check?: UpdateCheckSettings
  feed_separation?: FeedSeparationSettings
  emby?: EmbySettings
  jellyfin?: JellyfinSettings
  channelsdvr?: ChannelsDVRSettings
  proxy?: ProxySettings
  epg_generation_counter: number
  schema_version: number
  // UI timezone info (read-only, from environment or fallback to epg_timezone)
  ui_timezone: string
  ui_timezone_source: "env" | "epg"
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
  error?: string  // Present when configured but connection failed
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
  data: SchedulerSettingsUpdate
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

// Team Filter Settings API
export async function getTeamFilterSettings(): Promise<TeamFilterSettings> {
  return api.get("/settings/team-filter")
}

export async function updateTeamFilterSettings(
  data: TeamFilterSettingsUpdate
): Promise<TeamFilterSettings> {
  return api.put("/settings/team-filter", data)
}

// Exception Keywords API
export async function getExceptionKeywords(
  includeDisabled: boolean = false
): Promise<ExceptionKeywordListResponse> {
  return api.get(`/keywords?include_disabled=${includeDisabled}`)
}

export async function createExceptionKeyword(data: {
  label: string
  match_terms: string
  behavior: string
  enabled?: boolean
}): Promise<ExceptionKeyword> {
  return api.post("/keywords", data)
}

export async function updateExceptionKeyword(
  id: number,
  data: Partial<{
    label: string
    match_terms: string
    behavior: string
    enabled: boolean
  }>
): Promise<ExceptionKeyword> {
  return api.put(`/keywords/${id}`, data)
}

export async function deleteExceptionKeyword(id: number): Promise<void> {
  return api.delete(`/keywords/${id}`)
}

// Channel Numbering Settings API
export async function getChannelNumberingSettings(): Promise<ChannelNumberingSettings> {
  return api.get("/settings/channel-numbering")
}

export async function updateChannelNumberingSettings(
  data: ChannelNumberingSettingsUpdate
): Promise<ChannelNumberingSettings> {
  return api.put("/settings/channel-numbering", data)
}

// Arm a one-shot full re-grid for the next generation (gap/strict modes only).
export async function requestChannelRelayout(): Promise<ChannelNumberingSettings> {
  return api.post("/settings/channel-numbering/relayout", {})
}

// Stream Ordering Settings API
export async function getStreamOrderingSettings(): Promise<StreamOrderingSettings> {
  return api.get("/settings/stream-ordering")
}

export async function updateStreamOrderingSettings(
  data: StreamOrderingSettingsUpdate
): Promise<StreamOrderingSettings> {
  return api.put("/settings/stream-ordering", data)
}

// Update Check Settings API
export async function getUpdateCheckSettings(): Promise<UpdateCheckSettings> {
  return api.get("/settings/update-check")
}

export async function updateUpdateCheckSettings(
  data: UpdateCheckSettingsUpdate
): Promise<UpdateCheckSettings> {
  return api.put("/settings/update-check", data)
}

// Check for updates
export async function checkForUpdates(force: boolean = false): Promise<UpdateInfo> {
  return api.get(`/updates/check?force=${force}`)
}

// Feed Separation Settings API
export async function getFeedSeparationSettings(): Promise<FeedSeparationSettings> {
  return api.get("/settings/feed-separation")
}

export async function updateFeedSeparationSettings(
  data: FeedSeparationSettingsUpdate
): Promise<FeedSeparationSettings> {
  return api.put("/settings/feed-separation", data)
}

// Per-League Subscription Config Types
export interface SubscriptionLeagueConfig {
  league_code: string
  channel_profile_ids: (number | string)[] | null
  channel_group_id: number | null
  channel_group_mode: string | null
  matchup_order: string | null // "auto" | "away_first" | "home_first"; null = global setting
}

export interface LeagueConfigListResponse {
  configs: SubscriptionLeagueConfig[]
  total: number
}

// Per-League Subscription Config API
export async function getLeagueConfigs(): Promise<LeagueConfigListResponse> {
  return api.get("/league-configs")
}

export async function upsertLeagueConfig(
  leagueCode: string,
  data: {
    channel_profile_ids?: (number | string)[] | null
    channel_group_id?: number | null
    channel_group_mode?: string | null
    matchup_order?: string | null
  }
): Promise<SubscriptionLeagueConfig> {
  return api.put(`/league-configs/${encodeURIComponent(leagueCode)}`, data)
}

export async function deleteLeagueConfig(leagueCode: string): Promise<void> {
  return api.delete(`/league-configs/${encodeURIComponent(leagueCode)}`)
}

// Emby Settings API
export async function getEmbySettings(): Promise<EmbySettings> {
  return api.get("/settings/emby")
}

export async function updateEmbySettings(data: Partial<EmbySettings>): Promise<EmbySettings> {
  return api.put("/settings/emby", data)
}

export async function testEmbyConnection(data?: { url?: string; username?: string; password?: string; api_key?: string }): Promise<EmbyTestResponse> {
  return api.post("/emby/test", data || {})
}

// Jellyfin Settings API
export async function getJellyfinSettings(): Promise<JellyfinSettings> {
  return api.get("/settings/jellyfin")
}

export async function updateJellyfinSettings(data: Partial<JellyfinSettings>): Promise<JellyfinSettings> {
  return api.put("/settings/jellyfin", data)
}

export async function testJellyfinConnection(data?: { url?: string; username?: string; password?: string; api_key?: string }): Promise<JellyfinTestResponse> {
  return api.post("/jellyfin/test", data || {})
}

// Channels DVR Settings API
export async function getChannelsDVRSettings(): Promise<ChannelsDVRSettings> {
  return api.get("/settings/channelsdvr")
}

export async function updateChannelsDVRSettings(data: Partial<ChannelsDVRSettings>): Promise<ChannelsDVRSettings> {
  return api.put("/settings/channelsdvr", data)
}

export async function testChannelsDVRConnection(data?: { url?: string; source_name?: string }): Promise<ChannelsDVRTestResponse> {
  return api.post("/channelsdvr/test", data || {})
}

export async function getChannelsDVRSources(url?: string): Promise<ChannelsDVRSourcesResponse> {
  const qs = url ? `?url=${encodeURIComponent(url)}` : ""
  return api.get(`/channelsdvr/sources${qs}`)
}

export async function getChannelsDVRLineups(url?: string): Promise<ChannelsDVRLineupsResponse> {
  const qs = url ? `?url=${encodeURIComponent(url)}` : ""
  return api.get(`/channelsdvr/lineups${qs}`)
}

// Provider Proxy Settings API
export async function getProxySettings(): Promise<ProxySettings> {
  return api.get("/settings/proxy")
}

export async function updateProxySettings(data: Partial<ProxySettings>): Promise<ProxySettings> {
  return api.put("/settings/proxy", data)
}

export async function getProxyProviders(): Promise<string[]> {
  return api.get("/settings/proxy/providers")
}
