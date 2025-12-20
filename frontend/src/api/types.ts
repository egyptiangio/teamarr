// API Response Types

export interface EventGroup {
  id: number
  name: string
  leagues: string[]
  template_id: number | null
  channel_start_number: number | null
  channel_group_id: number | null
  stream_profile_id: number | null
  channel_profile_ids: number[]
  create_timing: string
  delete_timing: string
  duplicate_event_handling: string
  channel_assignment_mode: string
  sort_order: number
  total_stream_count: number
  m3u_group_id: number | null
  m3u_group_name: string | null
  enabled: boolean
  created_at: string | null
  updated_at: string | null
  channel_count?: number | null
}

export interface EventGroupCreate {
  name: string
  leagues: string[]
  template_id?: number | null
  channel_start_number?: number | null
  channel_group_id?: number | null
  stream_profile_id?: number | null
  channel_profile_ids?: number[]
  create_timing?: string
  delete_timing?: string
  duplicate_event_handling?: string
  channel_assignment_mode?: string
  sort_order?: number
  total_stream_count?: number
  m3u_group_id?: number | null
  m3u_group_name?: string | null
  enabled?: boolean
}

export interface EventGroupUpdate extends Partial<EventGroupCreate> {
  clear_template?: boolean
  clear_channel_start_number?: boolean
  clear_channel_group_id?: boolean
  clear_stream_profile_id?: boolean
  clear_channel_profile_ids?: boolean
  clear_m3u_group_id?: boolean
  clear_m3u_group_name?: boolean
}

export interface EventGroupListResponse {
  groups: EventGroup[]
  total: number
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
  stream_profile_id: number | null
  channel_profile_ids: number[]
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
