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
  // Clean up ALL unused logos in Dispatcharr after generation
  cleanup_unused_logos: boolean
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
  show_timezone: boolean
  channel_id_format: string
  xmltv_generator_name: string
  xmltv_generator_url: string
  tsdb_api_key: string | null  // Optional TheSportsDB premium API key
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
}

export interface TeamFilterSettingsUpdate {
  enabled?: boolean
  include_teams?: TeamFilterEntry[] | null
  exclude_teams?: TeamFilterEntry[] | null
  mode?: "include" | "exclude"
  clear_include_teams?: boolean
  clear_exclude_teams?: boolean
}

export interface ChannelNumberingSettings {
  numbering_mode: "strict_block" | "rational_block" | "strict_compact"
  sorting_scope: "per_group" | "global"
  sort_by: "sport_league_time" | "time" | "stream_order"
}

export interface ChannelNumberingSettingsUpdate {
  numbering_mode?: "strict_block" | "rational_block" | "strict_compact"
  sorting_scope?: "per_group" | "global"
  sort_by?: "sport_league_time" | "time" | "stream_order"
}

export interface StreamOrderingRule {
  type: "m3u" | "group" | "regex"
  value: string
  priority: number  // 1-99, lower = higher priority
}

export interface StreamOrderingSettings {
  rules: StreamOrderingRule[]
}

export interface StreamOrderingSettingsUpdate {
  rules: StreamOrderingRule[]
}

export interface APISettings {
  timeout: number
  retry_count: number
  soccer_cache_refresh_frequency: string
  team_cache_refresh_frequency: string
  startup_cache_max_age_days: number  // 0 = disabled, >0 = refresh if older than N days
}

export interface APISettingsUpdate {
  timeout?: number
  retry_count?: number
  soccer_cache_refresh_frequency?: string
  team_cache_refresh_frequency?: string
  startup_cache_max_age_days?: number
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

export interface AllSettings {
  dispatcharr: DispatcharrSettings
  lifecycle: LifecycleSettings
  scheduler: SchedulerSettings
  epg: EPGSettings
  durations: DurationSettings
  reconciliation: ReconciliationSettings
  team_filter?: TeamFilterSettings
  channel_numbering?: ChannelNumberingSettings
  stream_ordering?: StreamOrderingSettings
  update_check?: UpdateCheckSettings
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

// API Settings
export async function getAPISettings(): Promise<APISettings> {
  return api.get("/settings/api")
}

export async function updateAPISettings(
  data: APISettingsUpdate
): Promise<APISettings> {
  return api.put("/settings/api", data)
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

// AI Settings Types
// Provider configuration types
export interface OllamaProviderConfig {
  enabled: boolean
  url: string
  model: string
  timeout: number
}

export interface OpenAIProviderConfig {
  enabled: boolean
  api_key: string
  model: string
  timeout: number
  organization: string
}

export interface AnthropicProviderConfig {
  enabled: boolean
  api_key: string
  model: string
  timeout: number
}

export interface GrokProviderConfig {
  enabled: boolean
  api_key: string
  model: string
  timeout: number
}

export interface AIProvidersConfig {
  ollama: OllamaProviderConfig
  openai: OpenAIProviderConfig
  anthropic: AnthropicProviderConfig
  grok: GrokProviderConfig
}

export interface AITaskAssignments {
  pattern_learning: string
  stream_parsing: string
  event_cards: string
  team_matching: string
  description_gen: string
}

// AI task types for display
export const AI_TASKS = [
  { key: "pattern_learning", label: "Pattern Learning", description: "Learning regex patterns from streams" },
  { key: "stream_parsing", label: "Stream Parsing", description: "Parsing individual stream names" },
  { key: "event_cards", label: "Event Cards", description: "Generating event group summaries (future)" },
  { key: "team_matching", label: "Team Matching", description: "AI-assisted team matching (future)" },
  { key: "description_gen", label: "Description Generation", description: "Programme descriptions (future)" },
] as const

export const AI_PROVIDERS = [
  { key: "ollama", label: "Ollama (Local)" },
  { key: "openai", label: "OpenAI (ChatGPT)" },
  { key: "anthropic", label: "Anthropic (Claude)" },
  { key: "grok", label: "xAI (Grok)" },
] as const

export interface AISettings {
  enabled: boolean
  providers: AIProvidersConfig
  task_assignments: AITaskAssignments
  batch_size: number
  learn_patterns: boolean
  fallback_to_regex: boolean
  // Legacy fields for backwards compatibility
  ollama_url: string
  model: string
  timeout: number
  use_for_parsing: boolean
  use_for_matching: boolean
}

export interface AISettingsUpdate {
  enabled?: boolean
  providers?: AIProvidersConfig
  task_assignments?: AITaskAssignments
  batch_size?: number
  learn_patterns?: boolean
  fallback_to_regex?: boolean
  // Legacy fields
  ollama_url?: string
  model?: string
  timeout?: number
  use_for_parsing?: boolean
  use_for_matching?: boolean
}

export interface AIStatus {
  enabled: boolean
  available: boolean
  ollama_url: string
  model: string
  error: string | null
}

export interface AIPattern {
  pattern_id: string
  regex: string
  description: string
  example_streams: string[]
  field_map: Record<string, string>
  confidence: number
  match_count: number
  fail_count: number
  group_id: number | null
}

export interface AIPatternListResponse {
  patterns: AIPattern[]
  total: number
}

export interface LearnPatternsGroupResult {
  group_id: number
  group_name: string
  success: boolean
  patterns_learned: number
  patterns: AIPattern[]
  coverage_percent: number
  error: string | null
}

export interface LearnPatternsResponse {
  success: boolean
  group_id: number
  group_name: string
  patterns_learned: number
  patterns: AIPattern[]
  coverage_percent: number
  error: string | null
  group_results?: LearnPatternsGroupResult[]
}

export interface TestParseResult {
  stream: string
  team1: string | null
  team2: string | null
  league: string | null
  sport: string | null
  date: string | null
  time: string | null
  confidence: number
}

export interface TestParseResponse {
  success: boolean
  results: TestParseResult[]
  error: string | null
}

// AI Settings API
export async function getAIStatus(): Promise<AIStatus> {
  return api.get("/ai/status")
}

export async function getAISettings(): Promise<AISettings> {
  return api.get("/ai/settings")
}

export async function updateAISettings(data: AISettingsUpdate): Promise<AISettings> {
  return api.put("/ai/settings", data)
}

export async function getAIPatterns(groupId?: number): Promise<AIPatternListResponse> {
  const params = groupId ? `?group_id=${groupId}` : ""
  return api.get(`/ai/patterns${params}`)
}

export async function deleteAIPattern(patternId: string): Promise<void> {
  return api.delete(`/ai/patterns/${patternId}`)
}

export async function deleteGroupPatterns(groupId: number): Promise<{ patterns_deleted: number }> {
  return api.delete(`/ai/patterns/group/${groupId}`)
}

export async function learnPatterns(groupId?: number, groupIds?: number[]): Promise<LearnPatternsResponse> {
  const body: { group_id?: number; group_ids?: number[] } = {}
  if (groupIds && groupIds.length > 0) {
    body.group_ids = groupIds
  } else if (groupId) {
    body.group_id = groupId
  }
  return api.post("/ai/learn", body)
}

// Background pattern learning task
export interface PatternLearningStatus {
  in_progress: boolean
  status: string
  message: string
  percent: number
  current_group: number
  total_groups: number
  current_group_name: string
  started_at: string | null
  completed_at: string | null
  error: string | null
  eta_seconds: number | null
  groups_completed: number
  patterns_learned: number
  avg_coverage: number
  group_results: Array<{
    group_id: number
    group_name: string
    success: boolean
    patterns_learned: number
    coverage_percent: number
    error: string | null
  }>
}

export async function startPatternLearning(groupId?: number, groupIds?: number[]): Promise<{ success: boolean; message: string }> {
  const body: { group_id?: number; group_ids?: number[] } = {}
  if (groupIds && groupIds.length > 0) {
    body.group_ids = groupIds
  } else if (groupId) {
    body.group_id = groupId
  }
  return api.post("/ai/learn/start", body)
}

export async function getPatternLearningStatus(): Promise<PatternLearningStatus> {
  return api.get("/ai/learn/status")
}

export async function abortPatternLearning(): Promise<{ success: boolean; message: string }> {
  return api.post("/ai/learn/abort")
}

export async function testParse(streams: string[]): Promise<TestParseResponse> {
  return api.post("/ai/test-parse", { streams })
}

