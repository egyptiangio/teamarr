import { api } from "./client"

// Filler content structure
export interface FillerContent {
  title: string
  subtitle: string | null
  description: string
  art_url: string | null
}

// Conditional settings for postgame/idle
export interface ConditionalSettings {
  enabled: boolean
  description_final: string | null
  description_not_final: string | null
}

// Idle offseason settings (no game in 30-day lookahead)
// Each field can be independently enabled
export interface IdleOffseasonSettings {
  title_enabled: boolean
  title: string | null
  subtitle_enabled: boolean
  subtitle: string | null
  description_enabled: boolean
  description: string | null
}

// Conditional description entry
export interface ConditionalDescription {
  condition: string
  condition_value?: string
  template: string
  priority: number
}

// XMLTV flags
export interface XmltvFlags {
  new: boolean
  live: boolean
  date: boolean
}

export interface Template {
  id: number
  name: string
  template_type: string
  sport: string | null
  league: string | null

  // Programme formatting
  title_format: string
  subtitle_template: string | null
  description_template: string | null
  program_art_url: string | null

  // Duration
  game_duration_mode: string
  game_duration_override: number | null

  // XMLTV
  xmltv_flags: XmltvFlags | null
  xmltv_categories: string[] | null
  categories_apply_to: string

  // Filler: Pregame
  pregame_enabled: boolean
  pregame_fallback: FillerContent | null

  // Filler: Postgame
  postgame_enabled: boolean
  postgame_fallback: FillerContent | null
  postgame_conditional: ConditionalSettings | null

  // Filler: Idle
  idle_enabled: boolean
  idle_content: FillerContent | null
  idle_conditional: ConditionalSettings | null
  idle_offseason: IdleOffseasonSettings | null

  // Conditional descriptions
  conditional_descriptions: ConditionalDescription[] | null

  // Event template specific
  event_channel_name: string | null
  event_channel_logo_url: string | null

  // Usage counts (from list endpoint)
  team_count?: number
  group_count?: number

  created_at: string
  updated_at: string
}

export interface TemplateCreate {
  name: string
  template_type?: string
  sport?: string | null
  league?: string | null

  // Programme formatting
  title_format?: string
  subtitle_template?: string | null
  description_template?: string | null
  program_art_url?: string | null

  // Duration
  game_duration_mode?: string
  game_duration_override?: number | null

  // XMLTV
  xmltv_flags?: XmltvFlags | null
  xmltv_categories?: string[] | null
  categories_apply_to?: string

  // Filler: Pregame
  pregame_enabled?: boolean
  pregame_fallback?: FillerContent | null

  // Filler: Postgame
  postgame_enabled?: boolean
  postgame_fallback?: FillerContent | null
  postgame_conditional?: ConditionalSettings | null

  // Filler: Idle
  idle_enabled?: boolean
  idle_content?: FillerContent | null
  idle_conditional?: ConditionalSettings | null
  idle_offseason?: IdleOffseasonSettings | null

  // Conditional descriptions
  conditional_descriptions?: ConditionalDescription[] | null

  // Event template specific
  event_channel_name?: string | null
  event_channel_logo_url?: string | null
}

export interface TemplateUpdate extends Partial<TemplateCreate> {}

export async function listTemplates(): Promise<Template[]> {
  return api.get("/templates")
}

export async function getTemplate(templateId: number): Promise<Template> {
  return api.get(`/templates/${templateId}`)
}

export async function createTemplate(data: TemplateCreate): Promise<Template> {
  return api.post("/templates", data)
}

export async function updateTemplate(
  templateId: number,
  data: TemplateUpdate
): Promise<Template> {
  return api.put(`/templates/${templateId}`, data)
}

export async function deleteTemplate(templateId: number): Promise<void> {
  return api.delete(`/templates/${templateId}`)
}
