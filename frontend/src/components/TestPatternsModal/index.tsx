/**
 * TestPatternsModal — regex testing workspace for event groups.
 *
 * Opens a full-screen modal that:
 * 1. Loads all raw streams for the group
 * 2. Mirrors the form's regex fields (skip_builtin, include/exclude, extraction)
 * 3. Shows real-time highlighting on every stream
 * 4. Supports interactive text selection for pattern generation
 * 5. Syncs patterns bidirectionally with the form (reads on open, writes on Apply)
 */

import { useState, useCallback, useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { getRawStreams } from "@/api/groups"
import { StreamList } from "./StreamList"
import { PatternPanel } from "./PatternPanel"
import { InteractiveSelector } from "./InteractiveSelector"
import { FlaskConical, Loader2 } from "lucide-react"

// ---------------------------------------------------------------------------
// Types — shared across child components
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface TestPatternsModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  groupId: number | null
  /** Current form patterns — modal reads these on open */
  initialPatterns?: Partial<PatternState>
  /** Called when user clicks Apply — writes patterns back to form */
  onApply?: (patterns: PatternState) => void
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function TestPatternsModal({
  open,
  onOpenChange,
  groupId,
  initialPatterns,
  onApply,
}: TestPatternsModalProps) {
  // Local pattern state — initialized from form, editable in modal
  const [patterns, setPatterns] = useState<PatternState>(EMPTY_PATTERNS)

  // Text selection state for interactive pattern generation
  const [selection, setSelection] = useState<{
    text: string
    streamName: string
  } | null>(null)

  // Sync form → modal when opening
  useEffect(() => {
    if (open && initialPatterns) {
      setPatterns({ ...EMPTY_PATTERNS, ...initialPatterns })
    }
  }, [open, initialPatterns])

  // Fetch raw streams
  const {
    data: streamsData,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["rawStreams", groupId],
    queryFn: () => (groupId ? getRawStreams(groupId) : Promise.reject("No group ID")),
    enabled: open && groupId != null,
    staleTime: 5 * 60 * 1000, // Cache for 5 minutes
  })

  const streams = streamsData?.streams ?? []

  const handlePatternChange = useCallback((update: Partial<PatternState>) => {
    setPatterns((prev) => ({ ...prev, ...update }))
  }, [])

  const handleTextSelect = useCallback(
    (text: string, streamName: string) => {
      setSelection({ text, streamName })
    },
    []
  )

  const handleApply = useCallback(() => {
    onApply?.(patterns)
    onOpenChange(false)
  }, [patterns, onApply, onOpenChange])

  const handleClose = useCallback(() => {
    setSelection(null)
    onOpenChange(false)
  }, [onOpenChange])

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-6xl h-[85vh] flex flex-col p-0 gap-0">
        <DialogHeader className="px-4 py-3 border-b border-border shrink-0">
          <DialogTitle className="flex items-center gap-2 text-sm">
            <FlaskConical className="h-4 w-4" />
            Test Patterns
            {streamsData && (
              <span className="text-muted-foreground font-normal">
                — {streamsData.group_name}
              </span>
            )}
          </DialogTitle>
          <DialogDescription className="text-xs">
            Test regex patterns against real streams. Select text in a stream name to generate patterns.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-1 min-h-0">
          {/* Left: Pattern panel */}
          <div className="w-96 shrink-0 border-r border-border overflow-y-auto">
            <PatternPanel patterns={patterns} onChange={handlePatternChange} />
          </div>

          {/* Right: Stream list */}
          <div className="flex-1 flex flex-col min-w-0">
            {isLoading && (
              <div className="flex-1 flex items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                <span className="ml-2 text-sm text-muted-foreground">
                  Loading streams...
                </span>
              </div>
            )}

            {error && (
              <div className="flex-1 flex items-center justify-center">
                <span className="text-sm text-destructive">
                  Failed to load streams. Make sure the group has an M3U source configured.
                </span>
              </div>
            )}

            {!isLoading && !error && streams.length === 0 && (
              <div className="flex-1 flex items-center justify-center">
                <span className="text-sm text-muted-foreground">
                  No streams found for this group.
                </span>
              </div>
            )}

            {!isLoading && !error && streams.length > 0 && (
              <StreamList
                streams={streams}
                patterns={patterns}
                onTextSelect={handleTextSelect}
              />
            )}

            {/* Interactive selector bar */}
            <InteractiveSelector
              selection={selection}
              onClear={() => setSelection(null)}
              onApplyPattern={handlePatternChange}
            />
          </div>
        </div>

        <DialogFooter className="px-4 py-3 border-t border-border shrink-0">
          <div className="flex items-center justify-between w-full">
            <span className="text-xs text-muted-foreground">
              Patterns are tested client-side using JavaScript regex.
              Named groups use Python syntax (?P&lt;name&gt;...) for backend compatibility.
            </span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={handleClose}>
                Cancel
              </Button>
              <Button size="sm" onClick={handleApply}>
                Apply to Form
              </Button>
            </div>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
