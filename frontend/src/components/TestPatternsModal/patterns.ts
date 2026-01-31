export interface PatternState {
  skip_builtin_filter: boolean
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
}

export const EMPTY_PATTERNS: PatternState = {
  skip_builtin_filter: false,
  stream_include_regex: null,
  stream_include_regex_enabled: false,
  stream_exclude_regex: null,
  stream_exclude_regex_enabled: false,
  custom_regex_teams: null,
  custom_regex_teams_enabled: false,
  custom_regex_date: null,
  custom_regex_date_enabled: false,
  custom_regex_time: null,
  custom_regex_time_enabled: false,
  custom_regex_league: null,
  custom_regex_league_enabled: false,
}
