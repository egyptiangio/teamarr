// API Response Types

// Team filter entry for include/exclude filtering
export interface TeamFilterEntry {
  provider: string      // e.g., "espn", "tsdb"
  team_id: string       // provider_team_id from team_cache
  league: string        // e.g., "nfl", "nba"
  name?: string | null  // For display only, not used in matching
}

export interface EventGroup {
  id: number
  name: string
  display_name: string | null  // Optional display name override for UI
  leagues: string[]
  group_mode: string  // "single" or "multi" - persisted to preserve user intent
  parent_group_id: number | null
  template_id: number | null
  channel_start_number: number | null
  channel_group_id: number | null
  channel_group_mode: string  // Dynamic channel group assignment mode
  channel_profile_ids: (number | string)[] | null  // null = use global default, can include "{sport}", "{league}"
  stream_profile_id: number | null  // Stream profile (overrides global default)
  stream_timezone: string | null  // IANA timezone for interpreting stream dates (e.g., 'America/New_York')
  duplicate_event_handling: string
  channel_assignment_mode: string
  sort_order: number
  total_stream_count: number
  m3u_group_id: number | null
  m3u_group_name: string | null
  m3u_account_id: number | null
  m3u_account_name: string | null
  // Stream filtering
  stream_include_regex: string | null
  stream_include_regex_enabled: boolean
  stream_exclude_regex: string | null
  stream_exclude_regex_enabled: boolean
  custom_regex_teams: string | null
  custom_regex_teams_enabled: boolean
  custom_regex_date: string | null
  custom_regex_date_enabled: boolean
  custom_regex_time: string | null
  custom_regex_time_enabled: boolean
  custom_regex_league: string | null
  custom_regex_league_enabled: boolean
  skip_builtin_filter: boolean
  // Team filtering (canonical team selection, inherited by children)
  include_teams: TeamFilterEntry[] | null
  exclude_teams: TeamFilterEntry[] | null
  team_filter_mode: 'include' | 'exclude'
  // Processing stats
  last_refresh: string | null
  stream_count: number
  matched_count: number
  // Filtering stats (pre-match)
  filtered_include_regex: number
  filtered_exclude_regex: number
  filtered_not_event: number
  filtered_team: number
  // Matching stats
  failed_count: number  // FAILED: Match attempted but couldn't find event
  streams_excluded: number  // EXCLUDED: Matched but excluded by timing (past/final/early)
  // Excluded breakdown by reason
  excluded_event_final: number
  excluded_event_past: number
  excluded_before_window: number
  excluded_league_not_included: number
  // Multi-sport enhancements (Phase 3)
  channel_sort_order: string
  overlap_handling: string
  enabled: boolean
  created_at: string | null
  updated_at: string | null
  channel_count?: number | null
}

export interface EventGroupCreate {
  name: string
  display_name?: string | null  // Optional display name override
  leagues: string[]
  group_mode?: string  // "single" or "multi" - persisted to preserve user intent
  parent_group_id?: number | null
  template_id?: number | null
  channel_start_number?: number | null
  channel_group_id?: number | null
  channel_group_mode?: string  // Dynamic channel group assignment mode
  channel_profile_ids?: (number | string)[] | null  // null = use global default, can include "{sport}", "{league}"
  stream_profile_id?: number | null  // Stream profile (overrides global default)
  stream_timezone?: string | null  // IANA timezone for interpreting stream dates
  duplicate_event_handling?: string
  channel_assignment_mode?: string
  sort_order?: number
  total_stream_count?: number
  m3u_group_id?: number | null
  m3u_group_name?: string | null
  m3u_account_id?: number | null
  m3u_account_name?: string | null
  // Stream filtering
  stream_include_regex?: string | null
  stream_include_regex_enabled?: boolean
  stream_exclude_regex?: string | null
  stream_exclude_regex_enabled?: boolean
  custom_regex_teams?: string | null
  custom_regex_teams_enabled?: boolean
  custom_regex_date?: string | null
  custom_regex_date_enabled?: boolean
  custom_regex_time?: string | null
  custom_regex_time_enabled?: boolean
  custom_regex_league?: string | null
  custom_regex_league_enabled?: boolean
  skip_builtin_filter?: boolean
  // Team filtering (canonical team selection, inherited by children)
  include_teams?: TeamFilterEntry[] | null
  exclude_teams?: TeamFilterEntry[] | null
  team_filter_mode?: 'include' | 'exclude'
  // Multi-sport enhancements (Phase 3)
  channel_sort_order?: string
  overlap_handling?: string
  enabled?: boolean
}

export interface EventGroupUpdate extends Partial<EventGroupCreate> {
  clear_display_name?: boolean
  clear_parent_group_id?: boolean
  clear_template?: boolean
  clear_channel_start_number?: boolean
  clear_channel_group_id?: boolean
  clear_channel_profile_ids?: boolean
  clear_stream_profile_id?: boolean
  clear_stream_timezone?: boolean
  clear_m3u_group_id?: boolean
  clear_m3u_group_name?: boolean
  clear_m3u_account_id?: boolean
  clear_m3u_account_name?: boolean
  clear_stream_include_regex?: boolean
  clear_stream_exclude_regex?: boolean
  clear_custom_regex_teams?: boolean
  clear_custom_regex_date?: boolean
  clear_custom_regex_time?: boolean
  clear_custom_regex_league?: boolean
  clear_include_teams?: boolean
  clear_exclude_teams?: boolean
}

export interface EventGroupListResponse {
  groups: EventGroup[]
  total: number
}

export interface BulkGroupUpdateRequest {
  group_ids: number[]
  leagues?: string[]
  template_id?: number | null
  channel_group_id?: number | null
  channel_group_mode?: string
  channel_profile_ids?: (number | string)[] | null  // null = use global default, can include "{sport}", "{league}"
  stream_profile_id?: number | null  // Stream profile (overrides global default)
  stream_timezone?: string | null  // IANA timezone for interpreting stream dates
  channel_sort_order?: string
  overlap_handling?: string
  clear_template?: boolean
  clear_channel_group_id?: boolean
  clear_channel_profile_ids?: boolean
  clear_stream_profile_id?: boolean
}

export interface BulkGroupUpdateResult {
  group_id: number
  name: string
  success: boolean
  error?: string
}

export interface BulkGroupUpdateResponse {
  results: BulkGroupUpdateResult[]
  total_requested: number
  total_updated: number
  total_failed: number
}

export interface Template {
  id: number
  name: string
  title_template: string
  description_template: string | null
  pregame_title_template: string | null
  pregame_description_template: string | null
  postgame_title_template: string | null
  postgame_description_template: string | null
  pregame_duration_minutes: number
  postgame_duration_minutes: number
  created_at: string | null
  updated_at: string | null
}

export interface TemplateListResponse {
  templates: Template[]
  total: number
}

export interface Team {
  id: number
  team_id: string
  provider: string
  name: string
  display_name: string
  abbreviation: string | null
  league: string
  sport: string | null
  template_id: number | null
  channel_number: string | null
  channel_group_id: number | null
  channel_profile_ids: number[] | null  // null = use global default
  active: boolean
  created_at: string | null
  updated_at: string | null
}

export interface TeamListResponse {
  teams: Team[]
  total: number
}

export interface ManagedChannel {
  id: number
  event_epg_group_id: number | null
  team_id: number | null
  channel_id: string | null
  channel_number: string | null
  channel_name: string
  event_id: string | null
  event_name: string | null
  start_time: string | null
  end_time: string | null
  sync_status: string
  created_at: string | null
  updated_at: string | null
  deleted_at: string | null
}

export interface ChannelListResponse {
  channels: ManagedChannel[]
  total: number
}

export interface Settings {
  dispatcharr_url: string | null
  dispatcharr_api_key: string | null
  channel_range_start: number
  channel_range_end: number | null
  scheduler_enabled: boolean
  scheduler_interval_minutes: number
}

export interface CacheStatus {
  leagues_cached: number
  teams_cached: number
  last_refresh: string | null
}

export interface HealthResponse {
  status: string
  version?: string
}

export interface ProcessGroupResponse {
  group_id: number
  group_name: string
  streams_fetched: number
  streams_matched: number
  streams_unmatched: number
  channels_created: number
  channels_existing: number
  channels_skipped: number
  channel_errors: number
  errors: string[]
  duration_seconds: number
}

// Preview (stream matching without channel creation)
export interface PreviewStream {
  stream_id: number
  stream_name: string
  matched: boolean
  event_id: string | null
  event_name: string | null
  home_team: string | null
  away_team: string | null
  league: string | null
  start_time: string | null
  from_cache: boolean
  exclusion_reason: string | null
}

export interface PreviewGroupResponse {
  group_id: number
  group_name: string
  total_streams: number
  filtered_count: number
  matched_count: number
  unmatched_count: number
  filtered_not_event: number
  filtered_include_regex: number
  filtered_exclude_regex: number
  cache_hits: number
  cache_misses: number
  streams: PreviewStream[]
  errors: string[]
}

// Team Aliases
export interface TeamAlias {
  id: number
  alias: string
  league: string
  provider: string
  team_id: string
  team_name: string
  created_at: string | null
}

export interface TeamAliasCreate {
  alias: string
  league: string
  team_id: string
  team_name: string
  provider?: string
}

export interface TeamAliasListResponse {
  aliases: TeamAlias[]
  total: number
}
