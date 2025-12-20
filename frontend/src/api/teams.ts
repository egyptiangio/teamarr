import { api } from "./client"

export interface Team {
  id: number
  provider: string
  provider_team_id: string
  league: string
  sport: string
  team_name: string
  team_abbrev: string | null
  team_logo_url: string | null
  team_color: string | null
  channel_id: string
  channel_logo_url: string | null
  template_id: number | null
  active: boolean
  created_at: string
  updated_at: string
}

export interface TeamCreate {
  provider?: string
  provider_team_id: string
  league: string
  sport: string
  team_name: string
  team_abbrev?: string | null
  team_logo_url?: string | null
  team_color?: string | null
  channel_id: string
  channel_logo_url?: string | null
  template_id?: number | null
  active?: boolean
}

export interface TeamUpdate {
  team_name?: string | null
  team_abbrev?: string | null
  team_logo_url?: string | null
  team_color?: string | null
  channel_id?: string | null
  channel_logo_url?: string | null
  template_id?: number | null
  active?: boolean | null
}

export interface TeamSearchResult {
  name: string
  abbrev: string | null
  short_name: string | null
  provider: string
  team_id: string
  league: string
  sport: string
  logo_url: string | null
}

export interface TeamSearchResponse {
  query: string
  count: number
  teams: TeamSearchResult[]
}

export async function listTeams(activeOnly = false): Promise<Team[]> {
  const params = activeOnly ? "?active_only=true" : ""
  return api.get(`/teams${params}`)
}

export async function getTeam(teamId: number): Promise<Team> {
  return api.get(`/teams/${teamId}`)
}

export async function createTeam(data: TeamCreate): Promise<Team> {
  return api.post("/teams", data)
}

export async function updateTeam(teamId: number, data: TeamUpdate): Promise<Team> {
  return api.put(`/teams/${teamId}`, data)
}

export async function deleteTeam(teamId: number): Promise<void> {
  return api.delete(`/teams/${teamId}`)
}

export async function searchTeams(
  query: string,
  league?: string,
  sport?: string
): Promise<TeamSearchResponse> {
  const params = new URLSearchParams({ q: query })
  if (league) params.set("league", league)
  if (sport) params.set("sport", sport)
  return api.get(`/cache/teams/search?${params}`)
}
