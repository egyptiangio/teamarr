import { api } from "./client"

export interface EPGGenerateRequest {
  team_ids?: number[] | null
  days_ahead?: number | null
}

export interface EPGGenerateResponse {
  programmes_count: number
  teams_processed: number
  events_processed: number
  duration_seconds: number
}

export interface StatsResponse {
  total_runs: number
  successful_runs: number
  failed_runs: number
  last_24h: {
    runs: number
    successful: number
    failed: number
    programmes_generated: number
    streams_matched: number
    channels_created: number
  }
  totals: {
    programmes_generated: number
    streams_matched: number
    streams_unmatched: number
    streams_cached: number
    channels_created: number
    channels_deleted: number
  }
  by_type: Record<string, number>
  avg_duration_ms: number
  last_run: string | null
}

export interface ProcessingRun {
  id: number
  run_type: string
  run_id: string | null
  group_id: number | null
  team_id: number | null
  started_at: string
  completed_at: string | null
  duration_ms: number | null
  status: string
  error_message: string | null
  streams_fetched: number
  streams_matched: number
  streams_unmatched: number
  streams_cached: number
  channels_created: number
  channels_updated: number
  channels_deleted: number
  channels_errors: number
  programmes_total: number
  programmes_events: number
  programmes_pregame: number
  programmes_postgame: number
  programmes_idle: number
  xmltv_size_bytes: number
  extra_metrics: Record<string, unknown>
}

export interface RunsResponse {
  runs: ProcessingRun[]
  count: number
}

export interface CacheStatus {
  last_refresh: string | null
  leagues_count: number
  teams_count: number
  refresh_duration_seconds: number
  is_stale: boolean
  is_empty: boolean
  refresh_in_progress: boolean
  last_error: string | null
}

export async function generateTeamEpg(request?: EPGGenerateRequest): Promise<EPGGenerateResponse> {
  return api.post("/epg/generate", request ?? {})
}

export function getTeamXmltvUrl(teamIds?: number[], daysAhead?: number): string {
  const params = new URLSearchParams()
  if (teamIds?.length) params.set("team_ids", teamIds.join(","))
  if (daysAhead) params.set("days_ahead", daysAhead.toString())
  const query = params.toString()
  return `/api/v1/epg/xmltv${query ? `?${query}` : ""}`
}

export async function getStats(): Promise<StatsResponse> {
  return api.get("/stats")
}

export async function getRecentRuns(
  limit = 20,
  runType?: string
): Promise<RunsResponse> {
  const params = new URLSearchParams({ limit: limit.toString() })
  if (runType) params.set("run_type", runType)
  return api.get(`/stats/runs?${params}`)
}

export async function getCacheStatus(): Promise<CacheStatus> {
  return api.get("/cache/status")
}

export async function refreshCache(): Promise<{ status: string; message: string }> {
  return api.post("/cache/refresh", {})
}

// EPG Analysis types and functions

export interface EPGAnalysis {
  channels: {
    total: number
    team_based: number
    event_based: number
  }
  programmes: {
    total: number
    events: number
    pregame: number
    postgame: number
    idle: number
  }
  date_range: {
    start: string | null
    end: string | null
  }
  unreplaced_variables: string[]
  coverage_gaps: CoverageGap[]
}

export interface CoverageGap {
  channel: string
  after_program: string
  before_program: string
  after_stop: string
  before_start: string
  gap_minutes: number
}

export interface EPGContent {
  content: string
  total_lines: number
  truncated: boolean
  size_bytes: number
}

export async function getEPGAnalysis(): Promise<EPGAnalysis> {
  return api.get("/epg/analysis")
}

export async function getEPGContent(maxLines = 2000): Promise<EPGContent> {
  return api.get(`/epg/content?max_lines=${maxLines}`)
}
