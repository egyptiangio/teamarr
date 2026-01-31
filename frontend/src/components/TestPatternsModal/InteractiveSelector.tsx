/**
 * InteractiveSelector — popover for labeling selected text.
 *
 * When the user selects text in a stream name, this floating popover
 * asks "What is this?" with options: team1, team2, date, time, league.
 * The selection is then used to generate a regex pattern.
 */

import { useState, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { generatePattern, generateTeamsPattern } from "@/lib/pattern-generator"
import type { TextSelection } from "@/lib/pattern-generator"
import type { PatternState } from "./patterns"
import { Users, Calendar, Clock, Trophy } from "lucide-react"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface InteractiveSelectorProps {
  /** Currently selected text and the stream it came from */
  selection: { text: string; streamName: string } | null
  /** Called to clear the selection */
  onClear: () => void
  /** Called with pattern updates to apply */
  onApplyPattern: (update: Partial<PatternState>) => void
}

interface FieldOption {
  field: TextSelection["field"]
  label: string
  icon: React.ReactNode
  color: string
}

const OPTIONS: FieldOption[] = [
  { field: "team1", label: "Team 1", icon: <Users className="h-3.5 w-3.5" />, color: "text-blue-400" },
  { field: "team2", label: "Team 2", icon: <Users className="h-3.5 w-3.5" />, color: "text-cyan-400" },
  { field: "date", label: "Date", icon: <Calendar className="h-3.5 w-3.5" />, color: "text-yellow-400" },
  { field: "time", label: "Time", icon: <Clock className="h-3.5 w-3.5" />, color: "text-orange-400" },
  { field: "league", label: "League", icon: <Trophy className="h-3.5 w-3.5" />, color: "text-purple-400" },
]

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function InteractiveSelector({
  selection,
  onClear,
  onApplyPattern,
}: InteractiveSelectorProps) {
  const [pendingTeam1, setPendingTeam1] = useState<{
    text: string
    streamName: string
  } | null>(null)

  // Clear pending team1 if selection is cleared
  useEffect(() => {
    if (!selection) return
  }, [selection])

  const handleSelect = useCallback(
    (field: TextSelection["field"]) => {
      if (!selection) return

      if (field === "team1") {
        // Store team1 selection, wait for team2
        setPendingTeam1({ text: selection.text, streamName: selection.streamName })
        onClear()
        return
      }

      if (field === "team2" && pendingTeam1) {
        // Combine team1 + team2 into a teams pattern
        const pattern = generateTeamsPattern(
          pendingTeam1.text,
          selection.text,
          selection.streamName
        )
        if (pattern) {
          onApplyPattern({
            custom_regex_teams: pattern,
            custom_regex_teams_enabled: true,
          })
        }
        setPendingTeam1(null)
        onClear()
        return
      }

      // Single-field pattern generation
      const pattern = generatePattern(
        { text: selection.text, field },
        selection.streamName
      )
      if (pattern) {
        const fieldMap: Record<string, { patternKey: keyof PatternState; enabledKey: keyof PatternState }> = {
          date: { patternKey: "custom_regex_date", enabledKey: "custom_regex_date_enabled" },
          time: { patternKey: "custom_regex_time", enabledKey: "custom_regex_time_enabled" },
          league: { patternKey: "custom_regex_league", enabledKey: "custom_regex_league_enabled" },
          team1: { patternKey: "custom_regex_teams", enabledKey: "custom_regex_teams_enabled" },
          team2: { patternKey: "custom_regex_teams", enabledKey: "custom_regex_teams_enabled" },
        }
        const mapping = fieldMap[field]
        if (mapping) {
          onApplyPattern({
            [mapping.patternKey]: pattern,
            [mapping.enabledKey]: true,
          })
        }
      }
      setPendingTeam1(null)
      onClear()
    },
    [selection, pendingTeam1, onClear, onApplyPattern]
  )

  if (!selection && !pendingTeam1) return null

  return (
    <div className="flex items-center gap-2 px-3 py-2 bg-secondary/50 border-t border-border shrink-0">
      {pendingTeam1 && !selection && (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-blue-400 font-medium">
            Team 1: &quot;{pendingTeam1.text}&quot;
          </span>
          <span className="text-muted-foreground">
            — Now select Team 2 in a stream name
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-5 text-xs px-2"
            onClick={() => setPendingTeam1(null)}
          >
            Cancel
          </Button>
        </div>
      )}

      {selection && (
        <div className="flex items-center gap-2 text-xs flex-wrap">
          <span className="text-foreground font-medium shrink-0">
            &quot;{selection.text.length > 40
              ? selection.text.slice(0, 40) + "..."
              : selection.text}&quot;
          </span>
          <span className="text-muted-foreground shrink-0">is a:</span>
          {OPTIONS.map((opt) => {
            // If team1 is pending, only show team2
            if (pendingTeam1 && opt.field !== "team2") return null
            // If team1 is not pending, show all options
            return (
              <Button
                key={opt.field}
                variant="outline"
                size="sm"
                className={`h-6 text-xs px-2 gap-1 ${opt.color}`}
                onClick={() => handleSelect(opt.field)}
              >
                {opt.icon}
                {opt.label}
              </Button>
            )
          })}
          <Button
            variant="ghost"
            size="sm"
            className="h-5 text-xs px-2"
            onClick={() => {
              setPendingTeam1(null)
              onClear()
            }}
          >
            Cancel
          </Button>
        </div>
      )}
    </div>
  )
}
