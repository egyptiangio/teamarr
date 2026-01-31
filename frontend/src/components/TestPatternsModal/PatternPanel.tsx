/**
 * PatternPanel — regex input fields that mirror the EventGroupForm.
 *
 * Shows the same fields the form has: skip_builtin, include/exclude,
 * plus extraction patterns organized by event type (Team vs Team, Combat/Event Card).
 * Each field has an enable checkbox and a text input. Validation feedback is shown inline.
 */

import { useState, useCallback } from "react"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { cn } from "@/lib/utils"
import { validateRegex } from "@/lib/regex-utils"
import type { PatternState } from "./index"
import {
  ShieldOff,
  Filter,
  FilterX,
  Users,
  Calendar,
  Clock,
  Trophy,
  Swords,
  Tag,
} from "lucide-react"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PatternPanelProps {
  patterns: PatternState
  onChange: (update: Partial<PatternState>) => void
}

type EventTypeTab = "team_vs_team" | "event_card"

// ---------------------------------------------------------------------------
// Field config
// ---------------------------------------------------------------------------

interface FieldConfig {
  patternKey: keyof PatternState
  enabledKey: keyof PatternState
  label: string
  placeholder: string
  icon: React.ReactNode
  color: string
}

// Stream filtering fields (always shown)
const FILTER_FIELDS: FieldConfig[] = [
  {
    patternKey: "stream_include_regex",
    enabledKey: "stream_include_regex_enabled",
    label: "Include Pattern",
    placeholder: 'e.g., Gonzaga|Washington State',
    icon: <Filter className="h-3.5 w-3.5" />,
    color: "text-success",
  },
  {
    patternKey: "stream_exclude_regex",
    enabledKey: "stream_exclude_regex_enabled",
    label: "Exclude Pattern",
    placeholder: 'e.g., \\(ES\\)|\\(ALT\\)|All.?Star',
    icon: <FilterX className="h-3.5 w-3.5" />,
    color: "text-destructive",
  },
]

// Team vs Team extraction fields
const TEAM_VS_TEAM_FIELDS: FieldConfig[] = [
  {
    patternKey: "custom_regex_teams",
    enabledKey: "custom_regex_teams_enabled",
    label: "Teams Extraction",
    placeholder: '(?P<team1>...) vs (?P<team2>...)',
    icon: <Users className="h-3.5 w-3.5" />,
    color: "text-blue-400",
  },
  {
    patternKey: "custom_regex_date",
    enabledKey: "custom_regex_date_enabled",
    label: "Date Extraction",
    placeholder: '(?P<date>\\d{4}-\\d{2}-\\d{2})',
    icon: <Calendar className="h-3.5 w-3.5" />,
    color: "text-yellow-400",
  },
  {
    patternKey: "custom_regex_time",
    enabledKey: "custom_regex_time_enabled",
    label: "Time Extraction",
    placeholder: '(?P<time>\\d{1,2}:\\d{2}\\s*(?:AM|PM)?)',
    icon: <Clock className="h-3.5 w-3.5" />,
    color: "text-orange-400",
  },
  {
    patternKey: "custom_regex_league",
    enabledKey: "custom_regex_league_enabled",
    label: "League Extraction",
    placeholder: '(?P<league>NHL|NBA|NFL|MLB)',
    icon: <Trophy className="h-3.5 w-3.5" />,
    color: "text-purple-400",
  },
]

// Combat / Event Card extraction fields
const EVENT_CARD_FIELDS: FieldConfig[] = [
  {
    patternKey: "custom_regex_fighters",
    enabledKey: "custom_regex_fighters_enabled",
    label: "Fighters Extraction",
    placeholder: '(?P<fighters>\\w+ vs \\w+)',
    icon: <Swords className="h-3.5 w-3.5" />,
    color: "text-red-400",
  },
  {
    patternKey: "custom_regex_event_name",
    enabledKey: "custom_regex_event_name_enabled",
    label: "Event Name Extraction",
    placeholder: '(?P<event_name>UFC \\d+|Fight Night)',
    icon: <Tag className="h-3.5 w-3.5" />,
    color: "text-cyan-400",
  },
  {
    patternKey: "custom_regex_date",
    enabledKey: "custom_regex_date_enabled",
    label: "Date Extraction",
    placeholder: '(?P<date>\\d{4}-\\d{2}-\\d{2})',
    icon: <Calendar className="h-3.5 w-3.5" />,
    color: "text-yellow-400",
  },
  {
    patternKey: "custom_regex_time",
    enabledKey: "custom_regex_time_enabled",
    label: "Time Extraction",
    placeholder: '(?P<time>\\d{1,2}:\\d{2}\\s*(?:AM|PM)?)',
    icon: <Clock className="h-3.5 w-3.5" />,
    color: "text-orange-400",
  },
]

// ---------------------------------------------------------------------------
// Field Renderer
// ---------------------------------------------------------------------------

function renderField(
  field: FieldConfig,
  patterns: PatternState,
  handleToggle: (key: keyof PatternState) => void,
  handleChange: (key: keyof PatternState, value: string) => void
) {
  const pattern = (patterns[field.patternKey] as string) || ""
  const enabled = patterns[field.enabledKey] as boolean
  const validation = pattern ? validateRegex(pattern) : null

  return (
    <div key={field.patternKey} className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Checkbox
          checked={enabled}
          onCheckedChange={() => handleToggle(field.enabledKey)}
        />
        <span className={cn("flex items-center gap-1 text-xs font-medium", field.color)}>
          {field.icon}
          {field.label}
        </span>
        {validation && !validation.valid && (
          <span className="text-xs text-destructive ml-auto truncate max-w-[200px]">
            {validation.error}
          </span>
        )}
        {validation?.valid && enabled && (
          <span className="text-xs text-success ml-auto">Valid</span>
        )}
      </div>
      <Input
        value={pattern}
        onChange={(e) => handleChange(field.patternKey, e.target.value)}
        placeholder={field.placeholder}
        className={cn(
          "text-xs font-mono h-7",
          !enabled && "opacity-50"
        )}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PatternPanel({ patterns, onChange }: PatternPanelProps) {
  const [eventType, setEventType] = useState<EventTypeTab>("team_vs_team")

  const handleToggle = useCallback(
    (key: keyof PatternState) => {
      onChange({ [key]: !patterns[key] })
    },
    [patterns, onChange]
  )

  const handleChange = useCallback(
    (key: keyof PatternState, value: string) => {
      onChange({ [key]: value || null })
    },
    [onChange]
  )

  // Get the extraction fields for the current event type
  const extractionFields = eventType === "team_vs_team" ? TEAM_VS_TEAM_FIELDS : EVENT_CARD_FIELDS

  return (
    <div className="flex flex-col gap-2 p-3">
      {/* Skip built-in filter toggle */}
      <div className="flex items-center gap-2 pb-2 border-b border-border">
        <Checkbox
          checked={patterns.skip_builtin_filter}
          onCheckedChange={() =>
            onChange({ skip_builtin_filter: !patterns.skip_builtin_filter })
          }
        />
        <ShieldOff className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-xs text-muted-foreground">
          Skip built-in filters
        </span>
      </div>

      {/* Stream Filter Patterns (always shown) */}
      <div className="space-y-2">
        <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Stream Filtering
        </div>
        {FILTER_FIELDS.map((field) =>
          renderField(field, patterns, handleToggle, handleChange)
        )}
      </div>

      {/* Extraction Patterns by Event Type */}
      <div className="space-y-2 pt-2 border-t border-border">
        <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Extraction Patterns
        </div>

        {/* Event Type Tabs */}
        <div className="flex gap-1 p-1 bg-muted rounded-lg">
          <button
            type="button"
            onClick={() => setEventType("team_vs_team")}
            className={cn(
              "flex-1 px-2 py-1 text-xs font-medium rounded transition-colors",
              eventType === "team_vs_team"
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            Team vs Team
          </button>
          <button
            type="button"
            onClick={() => setEventType("event_card")}
            className={cn(
              "flex-1 px-2 py-1 text-xs font-medium rounded transition-colors",
              eventType === "event_card"
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            )}
          >
            Combat / Event Card
          </button>
        </div>

        {/* Extraction fields for selected event type */}
        <div className="space-y-2">
          {extractionFields.map((field) =>
            renderField(field, patterns, handleToggle, handleChange)
          )}
        </div>
      </div>
    </div>
  )
}
