import { useState, useMemo, useEffect } from "react"
import { AlertCircle, FlaskConical, Plus, Trash2, Lightbulb, Copy, Check, ChevronDown, ChevronRight, Pencil, Loader2, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import { previewGroup } from "@/api/groups"

interface PatternConfig {
  // Stream filtering
  stream_include_regex?: string | null
  stream_include_regex_enabled?: boolean
  stream_exclude_regex?: string | null
  stream_exclude_regex_enabled?: boolean
  // Team matching
  custom_regex_teams?: string | null
  custom_regex_teams_enabled?: boolean
  custom_regex_date?: string | null
  custom_regex_date_enabled?: boolean
  custom_regex_time?: string | null
  custom_regex_time_enabled?: boolean
  custom_regex_league?: string | null
  custom_regex_league_enabled?: boolean
}

interface TestPatternsModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  patterns: PatternConfig
  onApplyPattern?: (field: keyof PatternConfig, value: string) => void
  groupId?: number  // If provided, fetch actual streams from this group
}

interface PatternResult {
  name: string
  pattern: string
  type: "filter" | "extract"
  valid: boolean
  error?: string
  matches: boolean
  groups?: Record<string, string>
}

interface StreamResult {
  streamName: string
  patterns: PatternResult[]
  included: boolean
  excluded: boolean
}

interface PatternSuggestion {
  field: "teams" | "date" | "time" | "league" | "include" | "exclude"
  pattern: string
  description: string
  matchCount: number
  example?: string
}

// Convert Python-style regex to JavaScript-style
// - Named groups: (?P<name>...) → (?<name>...)
// - Named backreferences: (?P=name) → \k<name>
function pythonToJsRegex(pattern: string): string {
  return pattern
    .replace(/\(\?P</g, "(?<")
    .replace(/\(\?P=(\w+)\)/g, "\\k<$1>")
}

// Validate regex and return error if invalid
function validateRegex(pattern: string): { valid: boolean; error?: string; jsPattern?: string } {
  // Convert Python-style named groups to JavaScript-style
  const jsPattern = pythonToJsRegex(pattern)
  try {
    new RegExp(jsPattern)
    return { valid: true, jsPattern }
  } catch (e) {
    return { valid: false, error: (e as Error).message }
  }
}

// Test a pattern against a stream name
function testPattern(
  streamName: string,
  pattern: string | null | undefined,
  enabled: boolean | undefined,
  name: string,
  type: "filter" | "extract"
): PatternResult | null {
  if (!enabled || !pattern) return null

  const validation = validateRegex(pattern)
  if (!validation.valid) {
    return {
      name,
      pattern,
      type,
      valid: false,
      error: validation.error,
      matches: false,
    }
  }

  try {
    // Use the JavaScript-converted pattern
    const regex = new RegExp(validation.jsPattern!, "i")
    const match = streamName.match(regex)

    if (match) {
      // Extract named groups if present
      const groups: Record<string, string> = {}
      if (match.groups) {
        Object.entries(match.groups).forEach(([key, value]) => {
          if (value) groups[key] = value
        })
      }

      return {
        name,
        pattern,
        type,
        valid: true,
        matches: true,
        groups: Object.keys(groups).length > 0 ? groups : undefined,
      }
    }

    return {
      name,
      pattern,
      type,
      valid: true,
      matches: false,
    }
  } catch (e) {
    return {
      name,
      pattern,
      type,
      valid: false,
      error: (e as Error).message,
      matches: false,
    }
  }
}

// Field colors for highlighting - optimized for contrast in both light/dark modes
const FIELD_COLORS: Record<string, { bg: string; text: string; label: string }> = {
  // Teams: Green - light mode: light green bg + dark text, dark mode: medium green bg + white text
  teams: { bg: "bg-green-200 dark:bg-green-700", text: "text-green-900 dark:text-white", label: "Teams" },
  team1: { bg: "bg-green-200 dark:bg-green-700", text: "text-green-900 dark:text-white", label: "Team 1" },
  team2: { bg: "bg-green-300 dark:bg-green-600", text: "text-green-900 dark:text-white", label: "Team 2" },
  // Date: Purple
  date: { bg: "bg-purple-200 dark:bg-purple-700", text: "text-purple-900 dark:text-white", label: "Date" },
  day: { bg: "bg-purple-200 dark:bg-purple-700", text: "text-purple-900 dark:text-white", label: "Day" },
  month: { bg: "bg-purple-300 dark:bg-purple-600", text: "text-purple-900 dark:text-white", label: "Month" },
  // Time: Yellow/Amber - needs special handling for contrast
  time: { bg: "bg-amber-200 dark:bg-amber-600", text: "text-amber-900 dark:text-white", label: "Time" },
  time1: { bg: "bg-amber-200 dark:bg-amber-600", text: "text-amber-900 dark:text-white", label: "Time 1" },
  time2: { bg: "bg-amber-300 dark:bg-amber-500", text: "text-amber-900 dark:text-white", label: "Time 2" },
  start: { bg: "bg-amber-200 dark:bg-amber-600", text: "text-amber-900 dark:text-white", label: "Start" },
  end: { bg: "bg-amber-300 dark:bg-amber-500", text: "text-amber-900 dark:text-white", label: "End" },
  tz1: { bg: "bg-amber-100 dark:bg-amber-700", text: "text-amber-800 dark:text-white", label: "TZ1" },
  tz2: { bg: "bg-amber-100 dark:bg-amber-700", text: "text-amber-800 dark:text-white", label: "TZ2" },
  // League: Blue
  league: { bg: "bg-blue-200 dark:bg-blue-700", text: "text-blue-900 dark:text-white", label: "League" },
  // Include: Emerald
  include: { bg: "bg-emerald-200 dark:bg-emerald-700", text: "text-emerald-900 dark:text-white", label: "Include" },
  // Exclude: Red
  exclude: { bg: "bg-red-200 dark:bg-red-700", text: "text-red-900 dark:text-white", label: "Exclude" },
}

// Get all match ranges for multi-pattern highlighting
interface MatchRange {
  start: number
  end: number
  field: string
  value: string
}

function getAllMatchRanges(
  streamName: string,
  patterns: { field: string; pattern: string | null | undefined; enabled: boolean }[]
): MatchRange[] {
  const ranges: MatchRange[] = []

  for (const { field, pattern, enabled } of patterns) {
    if (!enabled || !pattern) continue

    const validation = validateRegex(pattern)
    if (!validation.valid || !validation.jsPattern) continue

    try {
      const regex = new RegExp(validation.jsPattern, "i")
      const match = regex.exec(streamName)

      if (match) {
        // If we have named groups, highlight each group separately
        if (match.groups) {
          for (const [groupName, groupValue] of Object.entries(match.groups)) {
            if (groupValue) {
              const groupStart = streamName.indexOf(groupValue, match.index)
              if (groupStart !== -1) {
                ranges.push({
                  start: groupStart,
                  end: groupStart + groupValue.length,
                  field: groupName,
                  value: groupValue,
                })
              }
            }
          }
        } else {
          // No named groups, highlight full match
          ranges.push({
            start: match.index,
            end: match.index + match[0].length,
            field,
            value: match[0],
          })
        }
      }
    } catch {
      // Ignore errors
    }
  }

  // Sort by start position
  return ranges.sort((a, b) => a.start - b.start)
}

// Analyze streams and suggest patterns dynamically
function suggestPatterns(streams: string[]): PatternSuggestion[] {
  const suggestions: PatternSuggestion[] = []
  if (streams.length === 0) return suggestions

  // Detect team separators and suggest team extraction patterns
  const separators = [
    { sep: " vs ", regex: "(?P<team1>.+?)\\s+vs\\s+(?P<team2>.+)" },
    { sep: " vs. ", regex: "(?P<team1>.+?)\\s+vs\\.\\s+(?P<team2>.+)" },
    { sep: " @ ", regex: "(?P<team1>.+?)\\s+@\\s+(?P<team2>.+)" },
    { sep: " at ", regex: "(?P<team1>.+?)\\s+at\\s+(?P<team2>.+)" },
    { sep: " v ", regex: "(?P<team1>.+?)\\s+v\\s+(?P<team2>.+)" },
  ]

  let teamPatternFound = false
  for (const { sep, regex } of separators) {
    const matches = streams.filter(s => s.toLowerCase().includes(sep.toLowerCase()))
    if (matches.length > 0) {
      suggestions.push({
        field: "teams",
        pattern: regex,
        description: `Extract teams using "${sep.trim()}" separator`,
        matchCount: matches.length,
        example: matches[0],
      })
      teamPatternFound = true
      break
    }
  }

  // If no standard separator found, try to detect "LEAGUE TEAM1 - TEAM2" pattern
  // Common in streams like "NHL CHICAGO - TAMPA BAY | ..."
  if (!teamPatternFound) {
    // Pattern: LEAGUE followed by TEAM - TEAM before a pipe or end
    const leagueTeamPattern = /\b(NHL|NBA|NFL|MLB|MLS|EPL|UFC)\s+([A-Z][A-Z.\s]+?)\s+-\s+([A-Z][A-Z.\s]+?)(?:\s*\||$)/i
    const matchingStreams = streams.filter(s => leagueTeamPattern.test(s))

    if (matchingStreams.length > 0) {
      // Detect which league is most common
      const leagueCounts: Record<string, number> = {}
      for (const s of matchingStreams) {
        const match = s.match(leagueTeamPattern)
        if (match) {
          const league = match[1].toUpperCase()
          leagueCounts[league] = (leagueCounts[league] || 0) + 1
        }
      }
      const topLeague = Object.entries(leagueCounts).sort((a, b) => b[1] - a[1])[0]?.[0] || "NHL"

      suggestions.push({
        field: "teams",
        pattern: `${topLeague}\\s+(?P<team1>[A-Z][A-Z.\\s]+?)\\s+-\\s+(?P<team2>[A-Z][A-Z.\\s]+?)(?:\\s*\\||$)`,
        description: `Extract teams from "${topLeague} TEAM - TEAM" format`,
        matchCount: matchingStreams.length,
        example: matchingStreams[0],
      })
      teamPatternFound = true
    }
  }

  // Try generic "ALLCAPS - ALLCAPS" pattern before pipe
  if (!teamPatternFound) {
    const capsPattern = /([A-Z][A-Z.\s]{2,}?)\s+-\s+([A-Z][A-Z.\s]{2,}?)(?:\s*\||$)/
    const matchingStreams = streams.filter(s => capsPattern.test(s))

    if (matchingStreams.length >= streams.length * 0.3) { // At least 30% match
      suggestions.push({
        field: "teams",
        pattern: "(?P<team1>[A-Z][A-Z.\\s]{2,}?)\\s+-\\s+(?P<team2>[A-Z][A-Z.\\s]{2,}?)(?:\\s*\\||$)",
        description: "Extract teams from TEAM - TEAM format (before |)",
        matchCount: matchingStreams.length,
        example: matchingStreams[0],
      })
    }
  }

  // Detect time patterns - order matters! Most specific first
  const timePatterns = [
    {
      // Dual timezone: "13:00 UK / 08:00 ET" or "13:00UK/08:00ET"
      regex: "(?P<time1>\\d{1,2}:\\d{2})\\s*(?P<tz1>UK|ET|PT|CT|GMT|EST|PST|CST)\\s*/\\s*(?P<time2>\\d{1,2}:\\d{2})\\s*(?P<tz2>UK|ET|PT|CT|GMT|EST|PST|CST)",
      test: /\d{1,2}:\d{2}\s*(?:UK|ET|PT|CT|GMT|EST|PST|CST)\s*\/\s*\d{1,2}:\d{2}\s*(?:UK|ET|PT|CT|GMT|EST|PST|CST)/i,
      desc: "Dual timezone (e.g., 13:00 UK / 08:00 ET)"
    },
    {
      // Time range: "13:00-15:00" or "13:00 - 15:00"
      regex: "(?P<start>\\d{1,2}:\\d{2})\\s*-\\s*(?P<end>\\d{1,2}:\\d{2})",
      test: /\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}/,
      desc: "Time range (e.g., 13:00-15:00)"
    },
    {
      // HH:MM:SS format
      regex: "(?P<time>\\d{1,2}:\\d{2}:\\d{2})",
      test: /\d{1,2}:\d{2}:\d{2}/,
      desc: "Time HH:MM:SS format"
    },
    {
      // 12-hour with AM/PM: "7:30 PM" or "7:30PM"
      regex: "(?P<time>\\d{1,2}:\\d{2}\\s*(?:AM|PM))",
      test: /\d{1,2}:\d{2}\s*(?:AM|PM)/i,
      desc: "12-hour time (e.g., 7:30 PM)"
    },
    {
      // Hour only with AM/PM: "7PM" or "7 PM"
      regex: "(?P<time>\\d{1,2}\\s*(?:AM|PM))",
      test: /\b\d{1,2}\s*(?:AM|PM)\b/i,
      desc: "Hour with AM/PM (e.g., 7PM)"
    },
    {
      // 24-hour format: "00:55" or "13:30" - but NOT if it looks like a date separator
      // Use word boundary or common delimiters to avoid matching dates
      regex: "(?P<time>\\d{2}:\\d{2})",
      test: /(?:^|[\s|])(\d{2}:\d{2})(?:[\s|]|$)/,
      desc: "24-hour time (e.g., 00:55, 13:30)"
    },
  ]

  for (const { regex, test, desc } of timePatterns) {
    const matches = streams.filter(s => test.test(s))
    if (matches.length > 0) {
      const match = matches[0].match(test)
      suggestions.push({
        field: "time",
        pattern: regex,
        description: desc,
        matchCount: matches.length,
        example: match ? (match[1] || match[0]) : undefined,
      })
      break
    }
  }

  // Detect date patterns - order matters! Most specific first
  // IMPORTANT: Check for "Day Month" before "Month Day" to avoid matching time as day
  const datePatterns = [
    {
      // "Sat 24 Jan" or "Mon 26 Jan" format - most specific, check first
      regex: "(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\\s+(?P<day>\\d{1,2})\\s+(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)",
      test: /(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s+\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)/i,
      desc: "Weekday Day Month (e.g., Sat 24 Jan)"
    },
    {
      // "24 Jan" or "15 January" - Day Month format
      // Use negative lookahead to avoid matching "00" from times like "00:55"
      regex: "(?P<day>\\d{1,2})\\s+(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*",
      test: /\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*/i,
      desc: "Day Month (e.g., 24 Jan)",
      validate: (s: string) => {
        // Make sure we're not matching a time component
        const match = s.match(/\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)/i)
        if (!match) return false
        const dayNum = parseInt(match[1])
        // Valid days are 1-31, times starting with 00 are suspicious
        return dayNum >= 1 && dayNum <= 31
      }
    },
    {
      // "Jan 15" or "January 15" - Month Day format
      // Negative lookahead: don't match if day is followed by colon (it's a time)
      regex: "(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\s+(?P<day>\\d{1,2})(?!:)",
      test: /(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})(?!:)/i,
      desc: "Month Day (e.g., Jan 15)"
    },
    {
      // MM/DD or MM/DD/YYYY format
      regex: "(?P<date>\\d{1,2}/\\d{1,2}(?:/\\d{2,4})?)",
      test: /\d{1,2}\/\d{1,2}(?:\/\d{2,4})?/,
      desc: "Date MM/DD or MM/DD/YYYY"
    },
    {
      // YYYY-MM-DD ISO format
      regex: "(?P<date>\\d{4}-\\d{2}-\\d{2})",
      test: /\d{4}-\d{2}-\d{2}/,
      desc: "ISO date (e.g., 2024-01-15)"
    },
  ]

  for (const { regex, test, desc, validate } of datePatterns) {
    const matches = streams.filter(s => {
      if (!test.test(s)) return false
      if (validate && !validate(s)) return false
      return true
    })
    if (matches.length > 0) {
      const match = matches[0].match(test)
      suggestions.push({
        field: "date",
        pattern: regex,
        description: desc,
        matchCount: matches.length,
        example: match ? match[0] : undefined,
      })
      break
    }
  }

  // Detect league prefixes
  const leaguePatterns = [
    {
      regex: "^(?P<league>NFL|NBA|NHL|MLB|MLS|WNBA|NCAAF|NCAAB)\\s*:",
      test: /^(NFL|NBA|NHL|MLB|MLS|WNBA|NCAAF|NCAAB)\s*:/i,
      desc: "League prefix with colon (e.g., NFL:)"
    },
    {
      regex: "^(?P<league>NFL|NBA|NHL|MLB|MLS|WNBA|NCAAF|NCAAB)\\s+",
      test: /^(NFL|NBA|NHL|MLB|MLS|WNBA|NCAAF|NCAAB)\s+/i,
      desc: "League prefix (e.g., NFL )"
    },
    {
      // "PPV X - NHL" or similar format
      regex: "PPV\\s*\\d+\\s*-\\s*(?P<league>NFL|NBA|NHL|MLB|MLS|EPL|UFC)",
      test: /PPV\s*\d+\s*-\s*(NFL|NBA|NHL|MLB|MLS|EPL|UFC)/i,
      desc: "League after PPV number (e.g., PPV 1 - NHL)"
    },
    {
      regex: "^(?P<league>[A-Z][A-Za-z0-9+]+)\\s*:",
      test: /^[A-Z][A-Za-z0-9+]+\s*:/,
      desc: "Generic prefix with colon"
    },
  ]

  for (const { regex, test, desc } of leaguePatterns) {
    const matches = streams.filter(s => test.test(s))
    if (matches.length > 0) {
      const match = matches[0].match(test)
      suggestions.push({
        field: "league",
        pattern: regex,
        description: desc,
        matchCount: matches.length,
        example: match ? match[0] : undefined,
      })
      break
    }
  }

  // Detect common include patterns (what streams have in common)
  const commonPrefixes = findCommonPrefixes(streams)
  if (commonPrefixes.length > 0) {
    const prefix = commonPrefixes[0]
    suggestions.push({
      field: "include",
      pattern: `^${escapeRegex(prefix)}`,
      description: `Include streams starting with "${prefix}"`,
      matchCount: streams.filter(s => s.startsWith(prefix)).length,
    })
  }

  return suggestions
}

// Find common prefixes in stream names
function findCommonPrefixes(streams: string[]): string[] {
  if (streams.length < 2) return []

  const prefixes: Record<string, number> = {}

  for (const stream of streams) {
    // Check for "PREFIX:" or "PREFIX |" patterns
    const colonMatch = stream.match(/^([^:]+):\s*/)
    if (colonMatch) {
      const prefix = colonMatch[1].trim()
      prefixes[prefix] = (prefixes[prefix] || 0) + 1
    }

    const pipeMatch = stream.match(/^([^|]+)\s*\|\s*/)
    if (pipeMatch) {
      const prefix = pipeMatch[1].trim()
      prefixes[prefix] = (prefixes[prefix] || 0) + 1
    }
  }

  // Return prefixes that appear in at least 50% of streams
  const threshold = streams.length * 0.5
  return Object.entries(prefixes)
    .filter(([, count]) => count >= threshold)
    .sort((a, b) => b[1] - a[1])
    .map(([prefix]) => prefix)
}

// Escape special regex characters
function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

// Highlight matched text in stream name with multiple patterns/colors
function MultiHighlightedStream({
  text,
  patterns,
}: {
  text: string
  patterns: { field: string; pattern: string | null | undefined; enabled: boolean }[]
}) {
  const ranges = getAllMatchRanges(text, patterns)

  if (ranges.length === 0) {
    return <span className="opacity-50">{text}</span>
  }

  // Build segments, handling overlaps by using first match
  const segments: { text: string; field?: string }[] = []
  let lastEnd = 0

  for (const range of ranges) {
    // Skip if this range overlaps with previous
    if (range.start < lastEnd) continue

    // Add unmatched text before this range
    if (range.start > lastEnd) {
      segments.push({ text: text.slice(lastEnd, range.start) })
    }

    // Add matched text
    segments.push({
      text: text.slice(range.start, range.end),
      field: range.field,
    })

    lastEnd = range.end
  }

  // Add remaining unmatched text
  if (lastEnd < text.length) {
    segments.push({ text: text.slice(lastEnd) })
  }

  return (
    <span>
      {segments.map((seg, idx) => {
        if (seg.field) {
          const colors = FIELD_COLORS[seg.field] || FIELD_COLORS.teams
          return (
            <mark
              key={idx}
              className={cn(colors.bg, colors.text, "px-0.5 rounded font-medium")}
              title={colors.label}
            >
              {seg.text}
            </mark>
          )
        }
        return <span key={idx}>{seg.text}</span>
      })}
    </span>
  )
}

// Sample stream names for testing (fallback when no group is available)
const SAMPLE_STREAMS = [
  "NFL: Kansas City Chiefs vs Philadelphia Eagles",
  "NBA: Lakers @ Celtics 7:30 PM",
  "NHL: TOR vs MTL 01/15",
  "ESPN+: Manchester United vs Liverpool (EN)",
]

export function TestPatternsModal({
  open,
  onOpenChange,
  patterns,
  onApplyPattern,
  groupId,
}: TestPatternsModalProps) {
  const [streamNames, setStreamNames] = useState<string[]>([])
  const [newStreamName, setNewStreamName] = useState("")
  const [showSuggestions, setShowSuggestions] = useState(true)
  const [copiedPattern, setCopiedPattern] = useState<string | null>(null)
  const [isLoadingStreams, setIsLoadingStreams] = useState(false)
  const [streamsError, setStreamsError] = useState<string | null>(null)
  const [hasLoadedGroupStreams, setHasLoadedGroupStreams] = useState(false)

  // Editing state for suggestions
  const [editingSuggestion, setEditingSuggestion] = useState<number | null>(null)
  const [editedPattern, setEditedPattern] = useState<string>("")

  // Fetch streams from group when modal opens
  const fetchGroupStreams = async () => {
    if (!groupId) {
      setStreamNames(SAMPLE_STREAMS)
      return
    }

    setIsLoadingStreams(true)
    setStreamsError(null)
    try {
      const response = await previewGroup(groupId)
      const names = response.streams.map(s => s.stream_name)
      if (names.length > 0) {
        setStreamNames(names)
        setHasLoadedGroupStreams(true)
      } else {
        setStreamNames(SAMPLE_STREAMS)
        setStreamsError("No streams found in group - showing sample streams")
      }
    } catch (err) {
      setStreamsError(err instanceof Error ? err.message : "Failed to fetch streams")
      setStreamNames(SAMPLE_STREAMS)
    } finally {
      setIsLoadingStreams(false)
    }
  }

  // Load streams when modal opens
  useEffect(() => {
    if (open && streamNames.length === 0) {
      fetchGroupStreams()
    }
  }, [open, groupId])

  // Generate pattern suggestions based on current streams
  const suggestions = useMemo(() => suggestPatterns(streamNames), [streamNames])

  // Count enabled patterns
  const enabledPatternCount = useMemo(() => {
    let count = 0
    if (patterns.stream_include_regex_enabled && patterns.stream_include_regex) count++
    if (patterns.stream_exclude_regex_enabled && patterns.stream_exclude_regex) count++
    if (patterns.custom_regex_teams_enabled && patterns.custom_regex_teams) count++
    if (patterns.custom_regex_date_enabled && patterns.custom_regex_date) count++
    if (patterns.custom_regex_time_enabled && patterns.custom_regex_time) count++
    if (patterns.custom_regex_league_enabled && patterns.custom_regex_league) count++
    return count
  }, [patterns])

  // Test all patterns against all stream names
  const results: StreamResult[] = useMemo(() => {
    return streamNames.map((streamName) => {
      const patternResults: PatternResult[] = []

      // Test stream filtering patterns
      const includeResult = testPattern(
        streamName,
        patterns.stream_include_regex,
        patterns.stream_include_regex_enabled,
        "Include",
        "filter"
      )
      if (includeResult) patternResults.push(includeResult)

      const excludeResult = testPattern(
        streamName,
        patterns.stream_exclude_regex,
        patterns.stream_exclude_regex_enabled,
        "Exclude",
        "filter"
      )
      if (excludeResult) patternResults.push(excludeResult)

      // Test extraction patterns
      const teamsResult = testPattern(
        streamName,
        patterns.custom_regex_teams,
        patterns.custom_regex_teams_enabled,
        "Teams",
        "extract"
      )
      if (teamsResult) patternResults.push(teamsResult)

      const dateResult = testPattern(
        streamName,
        patterns.custom_regex_date,
        patterns.custom_regex_date_enabled,
        "Date",
        "extract"
      )
      if (dateResult) patternResults.push(dateResult)

      const timeResult = testPattern(
        streamName,
        patterns.custom_regex_time,
        patterns.custom_regex_time_enabled,
        "Time",
        "extract"
      )
      if (timeResult) patternResults.push(timeResult)

      const leagueResult = testPattern(
        streamName,
        patterns.custom_regex_league,
        patterns.custom_regex_league_enabled,
        "League",
        "extract"
      )
      if (leagueResult) patternResults.push(leagueResult)

      // Determine if stream would be included/excluded
      const included = includeResult ? includeResult.matches : true
      const excluded = excludeResult ? excludeResult.matches : false

      return {
        streamName,
        patterns: patternResults,
        included,
        excluded,
      }
    })
  }, [streamNames, patterns])

  const handleAddStream = () => {
    if (newStreamName.trim()) {
      setStreamNames([...streamNames, newStreamName.trim()])
      setNewStreamName("")
    }
  }

  const handleRemoveStream = (index: number) => {
    setStreamNames(streamNames.filter((_, i) => i !== index))
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault()
      handleAddStream()
    }
  }

  const handleCopyPattern = (pattern: string) => {
    navigator.clipboard.writeText(pattern)
    setCopiedPattern(pattern)
    setTimeout(() => setCopiedPattern(null), 2000)
  }

  const handleApplyPattern = (suggestion: PatternSuggestion, customPattern?: string) => {
    if (!onApplyPattern) return

    const fieldMap: Record<PatternSuggestion["field"], keyof PatternConfig> = {
      teams: "custom_regex_teams",
      date: "custom_regex_date",
      time: "custom_regex_time",
      league: "custom_regex_league",
      include: "stream_include_regex",
      exclude: "stream_exclude_regex",
    }

    onApplyPattern(fieldMap[suggestion.field], customPattern || suggestion.pattern)
    setEditingSuggestion(null)
  }

  const handleStartEdit = (idx: number, pattern: string) => {
    setEditingSuggestion(idx)
    setEditedPattern(pattern)
  }

  const handleCancelEdit = () => {
    setEditingSuggestion(null)
    setEditedPattern("")
  }

  const handlePatternChange = (value: string) => {
    setEditedPattern(value)
  }

  const fieldLabels: Record<PatternSuggestion["field"], string> = {
    teams: "Teams",
    date: "Date",
    time: "Time",
    league: "League",
    include: "Include",
    exclude: "Exclude",
  }

  // Check if edited pattern is valid
  const editPatternValidation = editedPattern ? validateRegex(editedPattern) : null

  // Build patterns array for multi-highlighting
  const activePatterns = useMemo(() => {
    const p: { field: string; pattern: string | null | undefined; enabled: boolean }[] = []
    p.push({ field: "teams", pattern: patterns.custom_regex_teams, enabled: patterns.custom_regex_teams_enabled || false })
    p.push({ field: "date", pattern: patterns.custom_regex_date, enabled: patterns.custom_regex_date_enabled || false })
    p.push({ field: "time", pattern: patterns.custom_regex_time, enabled: patterns.custom_regex_time_enabled || false })
    p.push({ field: "league", pattern: patterns.custom_regex_league, enabled: patterns.custom_regex_league_enabled || false })
    p.push({ field: "include", pattern: patterns.stream_include_regex, enabled: patterns.stream_include_regex_enabled || false })
    p.push({ field: "exclude", pattern: patterns.stream_exclude_regex, enabled: patterns.stream_exclude_regex_enabled || false })
    return p
  }, [patterns])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-6xl max-h-[85vh] overflow-hidden flex flex-col" onClose={() => onOpenChange(false)}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FlaskConical className="h-5 w-5" />
            Test Regex Patterns
          </DialogTitle>
          <DialogDescription className="flex items-center justify-between">
            <span>
              {groupId && hasLoadedGroupStreams ? (
                <>Testing against <strong>{streamNames.length}</strong> streams from this group.</>
              ) : (
                <>Testing against sample streams.</>
              )}
              {" "}Python-style <code className="text-xs bg-muted px-1 rounded">(?P&lt;name&gt;...)</code> supported.
            </span>
            {/* Color legend */}
            <div className="flex gap-2 text-xs">
              <span className={cn("px-1.5 rounded font-medium", FIELD_COLORS.teams.bg, FIELD_COLORS.teams.text)}>Teams</span>
              <span className={cn("px-1.5 rounded font-medium", FIELD_COLORS.date.bg, FIELD_COLORS.date.text)}>Date</span>
              <span className={cn("px-1.5 rounded font-medium", FIELD_COLORS.time.bg, FIELD_COLORS.time.text)}>Time</span>
              <span className={cn("px-1.5 rounded font-medium", FIELD_COLORS.league.bg, FIELD_COLORS.league.text)}>League</span>
            </div>
          </DialogDescription>
        </DialogHeader>

        {/* Loading state */}
        {isLoadingStreams && (
          <div className="flex items-center justify-center py-8 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin mr-2" />
            Loading streams from group...
          </div>
        )}

        {/* Two-column layout */}
        {!isLoadingStreams && (
          <div className="flex-1 grid grid-cols-2 gap-4 overflow-hidden">
            {/* LEFT COLUMN: Patterns */}
            <div className="flex flex-col overflow-hidden">
              <h3 className="text-sm font-medium mb-2 flex items-center gap-2">
                <Lightbulb className="h-4 w-4 text-yellow-500" />
                Patterns
              </h3>
              <div className="flex-1 overflow-y-auto space-y-3 pr-2">
                {/* Error message */}
                {streamsError && (
                  <div className="text-xs text-yellow-600 dark:text-yellow-400 bg-yellow-50 dark:bg-yellow-950/30 p-2 rounded">
                    {streamsError}
                  </div>
                )}

                {/* Active Patterns Section */}
                {enabledPatternCount > 0 && (
                  <div className="border rounded-lg p-3 bg-muted/30">
                    <p className="text-xs font-medium mb-2">Active Patterns ({enabledPatternCount})</p>
                    <div className="space-y-1.5">
                      {patterns.custom_regex_teams_enabled && patterns.custom_regex_teams && (
                        <div className="flex items-start gap-2">
                          <span className={cn("text-xs px-1.5 rounded shrink-0 font-medium", FIELD_COLORS.teams.bg, FIELD_COLORS.teams.text)}>Teams</span>
                          <code className="text-xs font-mono break-all">{patterns.custom_regex_teams}</code>
                        </div>
                      )}
                      {patterns.custom_regex_date_enabled && patterns.custom_regex_date && (
                        <div className="flex items-start gap-2">
                          <span className={cn("text-xs px-1.5 rounded shrink-0 font-medium", FIELD_COLORS.date.bg, FIELD_COLORS.date.text)}>Date</span>
                          <code className="text-xs font-mono break-all">{patterns.custom_regex_date}</code>
                        </div>
                      )}
                      {patterns.custom_regex_time_enabled && patterns.custom_regex_time && (
                        <div className="flex items-start gap-2">
                          <span className={cn("text-xs px-1.5 rounded shrink-0 font-medium", FIELD_COLORS.time.bg, FIELD_COLORS.time.text)}>Time</span>
                          <code className="text-xs font-mono break-all">{patterns.custom_regex_time}</code>
                        </div>
                      )}
                      {patterns.custom_regex_league_enabled && patterns.custom_regex_league && (
                        <div className="flex items-start gap-2">
                          <span className={cn("text-xs px-1.5 rounded shrink-0 font-medium", FIELD_COLORS.league.bg, FIELD_COLORS.league.text)}>League</span>
                          <code className="text-xs font-mono break-all">{patterns.custom_regex_league}</code>
                        </div>
                      )}
                      {patterns.stream_include_regex_enabled && patterns.stream_include_regex && (
                        <div className="flex items-start gap-2">
                          <span className={cn("text-xs px-1.5 rounded shrink-0 font-medium", FIELD_COLORS.include.bg, FIELD_COLORS.include.text)}>Include</span>
                          <code className="text-xs font-mono break-all">{patterns.stream_include_regex}</code>
                        </div>
                      )}
                      {patterns.stream_exclude_regex_enabled && patterns.stream_exclude_regex && (
                        <div className="flex items-start gap-2">
                          <span className={cn("text-xs px-1.5 rounded shrink-0 font-medium", FIELD_COLORS.exclude.bg, FIELD_COLORS.exclude.text)}>Exclude</span>
                          <code className="text-xs font-mono break-all">{patterns.stream_exclude_regex}</code>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Pattern Suggestions */}
                {suggestions.length > 0 && (
                  <div className="border rounded-lg">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="w-full justify-between px-3"
                      onClick={() => setShowSuggestions(!showSuggestions)}
                    >
                      <span className="flex items-center gap-2">
                        <Lightbulb className="h-4 w-4 text-yellow-500" />
                        Suggestions ({suggestions.length})
                      </span>
                      {showSuggestions ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                    </Button>
                    {showSuggestions && (
                      <div className="space-y-2 p-2 border-t bg-muted/30">
                        {suggestions.map((suggestion, idx) => (
                          <div key={idx} className="p-2 bg-background rounded border">
                            <div className="flex items-start justify-between gap-2">
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 mb-1">
                                  <span className={cn("text-xs px-1.5 rounded font-medium", FIELD_COLORS[suggestion.field]?.bg || "bg-muted", FIELD_COLORS[suggestion.field]?.text || "text-foreground")}>
                                    {fieldLabels[suggestion.field]}
                                  </span>
                                  <span className="text-xs text-muted-foreground">
                                    {suggestion.matchCount} match{suggestion.matchCount !== 1 ? "es" : ""}
                                  </span>
                                </div>

                                {editingSuggestion === idx ? (
                                  <div className="space-y-2">
                                    <Input
                                      value={editedPattern}
                                      onChange={(e) => handlePatternChange(e.target.value)}
                                      className="font-mono text-xs h-7"
                                      autoFocus
                                    />
                                    {editPatternValidation && !editPatternValidation.valid && (
                                      <p className="text-xs text-red-500 flex items-center gap-1">
                                        <AlertCircle className="h-3 w-3" />
                                        {editPatternValidation.error}
                                      </p>
                                    )}
                                  </div>
                                ) : (
                                  <code className="text-xs font-mono block break-all text-foreground">
                                    {suggestion.pattern}
                                  </code>
                                )}

                                <p className="text-xs text-muted-foreground mt-1">{suggestion.description}</p>
                              </div>

                              <div className="flex gap-1 shrink-0">
                                {editingSuggestion === idx ? (
                                  <>
                                    <Button type="button" variant="ghost" size="sm" className="h-6 text-xs" onClick={handleCancelEdit}>
                                      Cancel
                                    </Button>
                                    {onApplyPattern && (
                                      <Button
                                        type="button"
                                        variant="default"
                                        size="sm"
                                        className="h-6 text-xs"
                                        onClick={() => handleApplyPattern(suggestion, editedPattern)}
                                        disabled={!editPatternValidation?.valid}
                                      >
                                        Apply
                                      </Button>
                                    )}
                                  </>
                                ) : (
                                  <>
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="sm"
                                      className="h-6 w-6 p-0"
                                      onClick={() => handleStartEdit(idx, suggestion.pattern)}
                                      title="Edit pattern"
                                    >
                                      <Pencil className="h-3 w-3" />
                                    </Button>
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="sm"
                                      className="h-6 w-6 p-0"
                                      onClick={() => handleCopyPattern(suggestion.pattern)}
                                      title="Copy pattern"
                                    >
                                      {copiedPattern === suggestion.pattern ? (
                                        <Check className="h-3 w-3 text-green-500" />
                                      ) : (
                                        <Copy className="h-3 w-3" />
                                      )}
                                    </Button>
                                    {onApplyPattern && (
                                      <Button
                                        type="button"
                                        variant="secondary"
                                        size="sm"
                                        className="h-6 text-xs"
                                        onClick={() => handleApplyPattern(suggestion)}
                                      >
                                        Apply
                                      </Button>
                                    )}
                                  </>
                                )}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* No patterns message */}
                {enabledPatternCount === 0 && suggestions.length === 0 && (
                  <div className="py-8 text-center text-muted-foreground">
                    <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-50" />
                    <p>No patterns to test.</p>
                    <p className="text-sm mt-1">Add stream names to get suggestions.</p>
                  </div>
                )}
              </div>
            </div>

            {/* RIGHT COLUMN: Stream Results */}
            <div className="flex flex-col overflow-hidden border-l pl-4">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-medium">Stream Preview ({streamNames.length})</h3>
                <div className="flex gap-1">
                  {groupId && (
                    <Button type="button" variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={fetchGroupStreams} title="Reload">
                      <RefreshCw className="h-3 w-3" />
                    </Button>
                  )}
                </div>
              </div>

              {/* Add stream input */}
              <div className="flex gap-2 mb-2">
                <Input
                  value={newStreamName}
                  onChange={(e) => setNewStreamName(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Add test stream..."
                  className="flex-1 font-mono text-xs h-8"
                />
                <Button type="button" variant="outline" size="sm" className="h-8" onClick={handleAddStream} disabled={!newStreamName.trim()}>
                  <Plus className="h-3 w-3" />
                </Button>
              </div>

              {/* Stream list with highlighting */}
              <div className="flex-1 overflow-y-auto space-y-1.5">
                {streamNames.map((stream, idx) => {
                  const result = results[idx]
                  const hasAnyMatch = activePatterns.some(p => {
                    if (!p.enabled || !p.pattern) return false
                    const validation = validateRegex(p.pattern)
                    if (!validation.valid) return false
                    try {
                      return new RegExp(validation.jsPattern!, "i").test(stream)
                    } catch {
                      return false
                    }
                  })

                  return (
                    <div
                      key={idx}
                      className={cn(
                        "p-2 rounded border text-xs group",
                        result?.excluded
                          ? "border-red-300 bg-red-50/50 dark:border-red-800 dark:bg-red-950/30"
                          : hasAnyMatch
                          ? "border-border bg-background"
                          : "border-border bg-muted/30 opacity-60"
                      )}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <code className="font-mono break-all flex-1">
                          <MultiHighlightedStream text={stream} patterns={activePatterns} />
                        </code>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-5 w-5 p-0 opacity-0 group-hover:opacity-50 hover:!opacity-100 shrink-0"
                          onClick={() => handleRemoveStream(idx)}
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </div>

                      {/* Show extracted values below stream */}
                      {result && result.patterns.length > 0 && (
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {result.patterns.map((p, pIdx) => {
                            if (!p.valid || !p.matches) return null
                            const baseColors = FIELD_COLORS[p.name.toLowerCase()] || FIELD_COLORS.teams
                            return p.groups ? (
                              Object.entries(p.groups).map(([key, value]) => {
                                // Use specific color for the group key if available
                                const colors = FIELD_COLORS[key.toLowerCase()] || baseColors
                                return (
                                  <span key={`${pIdx}-${key}`} className={cn("px-1.5 py-0.5 rounded text-xs font-medium", colors.bg, colors.text)}>
                                    {key}={value}
                                  </span>
                                )
                              })
                            ) : (
                              <span key={pIdx} className={cn("px-1.5 py-0.5 rounded text-xs font-medium", baseColors.bg, baseColors.text)}>
                                {p.name} ✓
                              </span>
                            )
                          })}
                          {result.excluded && (
                            <span className="px-1.5 py-0.5 rounded text-xs bg-red-200 dark:bg-red-800">Excluded</span>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}

                {streamNames.length === 0 && (
                  <div className="py-8 text-center text-muted-foreground text-sm">
                    No streams to test
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
