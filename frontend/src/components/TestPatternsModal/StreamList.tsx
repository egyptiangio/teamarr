/**
 * StreamList — virtualized list of stream names with regex highlighting.
 *
 * Uses @tanstack/react-virtual for efficient rendering of large lists
 * (groups can have 10,000+ streams).
 */

import { useRef, useMemo } from "react"
import { useVirtualizer } from "@tanstack/react-virtual"
import { StreamItem } from "./StreamItem"
import { getMatchRanges, testMatch } from "@/lib/regex-utils"
import type { PatternState } from "./index"
import type { RawStream } from "@/api/groups"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface StreamListProps {
  streams: RawStream[]
  patterns: PatternState
  onTextSelect?: (text: string, streamName: string) => void
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function StreamList({ streams, patterns, onTextSelect }: StreamListProps) {
  const parentRef = useRef<HTMLDivElement>(null)

  // Pre-compute match results for all streams
  const results = useMemo(() => {
    return streams.map((stream) => {
      const name = stream.stream_name

      // Extraction ranges (teams, date, time, league)
      const extractionRanges = [
        ...(patterns.custom_regex_teams_enabled && patterns.custom_regex_teams
          ? getMatchRanges(patterns.custom_regex_teams, name)
          : []),
        ...(patterns.custom_regex_date_enabled && patterns.custom_regex_date
          ? getMatchRanges(patterns.custom_regex_date, name).map((r) => ({
              ...r,
              group: r.group || "date",
            }))
          : []),
        ...(patterns.custom_regex_time_enabled && patterns.custom_regex_time
          ? getMatchRanges(patterns.custom_regex_time, name).map((r) => ({
              ...r,
              group: r.group || "time",
            }))
          : []),
        ...(patterns.custom_regex_league_enabled && patterns.custom_regex_league
          ? getMatchRanges(patterns.custom_regex_league, name).map((r) => ({
              ...r,
              group: r.group || "league",
            }))
          : []),
      ]

      // Include/exclude (user-defined regex)
      const includeMatch =
        patterns.stream_include_regex_enabled && patterns.stream_include_regex
          ? testMatch(patterns.stream_include_regex, name)
          : null

      const excludeMatch =
        patterns.stream_exclude_regex_enabled && patterns.stream_exclude_regex
          ? testMatch(patterns.stream_exclude_regex, name)
          : false

      // Built-in filter result comes from backend (single source of truth)
      const builtinFilterReason = stream.builtin_filtered

      return { extractionRanges, includeMatch, excludeMatch, builtinFilterReason }
    })
  }, [streams, patterns])

  // Compute summary stats
  const stats = useMemo(() => {
    let included = 0
    let excluded = 0
    let builtinFiltered = 0
    let withExtractions = 0

    for (const r of results) {
      // Count streams matching builtin filters (tracked separately)
      if (r.builtinFilterReason) builtinFiltered++

      // For inclusion stats, respect skip_builtin_filter setting
      const effectivelyFiltered = !patterns.skip_builtin_filter && r.builtinFilterReason
      if (effectivelyFiltered) continue
      if (r.excludeMatch) excluded++
      else if (r.includeMatch === false) excluded++
      else {
        included++
        if (r.extractionRanges.length > 0) withExtractions++
      }
    }

    return {
      included,
      excluded,
      builtinFiltered,
      withExtractions,
      total: streams.length,
      skipBuiltin: patterns.skip_builtin_filter,
    }
  }, [results, streams.length, patterns.skip_builtin_filter])

  const virtualizer = useVirtualizer({
    count: streams.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 32,
    overscan: 20,
  })

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Stats bar */}
      <div className="flex items-center gap-3 px-3 py-1.5 text-xs text-muted-foreground border-b border-border bg-secondary/30">
        <span>{stats.total} streams</span>
        {stats.withExtractions > 0 && (
          <span className="text-success">{stats.withExtractions} with extractions</span>
        )}
        {stats.excluded > 0 && (
          <span className="text-destructive">{stats.excluded} excluded</span>
        )}
        {stats.builtinFiltered > 0 && (
          <span className={stats.skipBuiltin ? "text-yellow-500" : "text-muted-foreground/50"}>
            {stats.builtinFiltered} {stats.skipBuiltin ? "match builtin filters (skipped)" : "filtered (builtin)"}
          </span>
        )}
      </div>

      {/* Virtualized stream list */}
      <div
        ref={parentRef}
        className="flex-1 overflow-auto"
      >
        <div
          style={{
            height: `${virtualizer.getTotalSize()}px`,
            width: "100%",
            position: "relative",
          }}
        >
          {virtualizer.getVirtualItems().map((virtualRow) => {
            const idx = virtualRow.index
            const stream = streams[idx]
            const r = results[idx]
            return (
              <div
                key={virtualRow.key}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  height: `${virtualRow.size}px`,
                  transform: `translateY(${virtualRow.start}px)`,
                }}
              >
                <StreamItem
                  name={stream.stream_name}
                  index={idx}
                  extractionRanges={r.extractionRanges}
                  includeMatch={r.includeMatch}
                  excludeMatch={r.excludeMatch}
                  builtinFilterReason={r.builtinFilterReason}
                  skipBuiltinFilter={patterns.skip_builtin_filter}
                  onTextSelect={onTextSelect}
                />
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
