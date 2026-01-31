/**
 * PatternPanel — regex input fields that mirror the EventGroupForm.
 *
 * Shows the same fields the form has: skip_builtin, include/exclude,
 * teams, date, time, league. Each field has an enable checkbox and
 * a text input. Validation feedback is shown inline.
 */

import { useCallback } from "react"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { cn } from "@/lib/utils"
import { validateRegex } from "@/lib/regex-utils"
import type { PatternState } from "./patterns"
import {
  ShieldOff,
  Filter,
  FilterX,
  Users,
  Calendar,
  Clock,
  Trophy,
} from "lucide-react"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PatternPanelProps {
  patterns: PatternState
  onChange: (update: Partial<PatternState>) => void
}

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

const FIELDS: FieldConfig[] = [
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

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PatternPanel({ patterns, onChange }: PatternPanelProps) {
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
          Skip built-in filters (placeholders, unsupported sports)
        </span>
      </div>

      {/* Pattern fields */}
      {FIELDS.map((field) => {
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
      })}
    </div>
  )
}
