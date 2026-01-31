import { useState, useMemo, useRef, useCallback } from "react"
import { toast } from "sonner"
import { useQuery } from "@tanstack/react-query"
import {
  Play,
  Download,
  Loader2,
  Clock,
  CheckCircle,
  XCircle,
  Link,
  Copy,
  Check,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Search,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { useGenerationProgress } from "@/contexts/generation-context"
import { useDateFormat } from "@/hooks/useDateFormat"
import {
  useStats,
  useRecentRuns,
  useEPGAnalysis,
  useEPGContent,
} from "@/hooks/useEPG"
import {
  getTeamXmltvUrl,
  getMatchedStreams,
  getFailedMatches,
  searchEvents,
  correctStreamMatch,
} from "@/api/epg"
import { getLeagues } from "@/api/teams"
import type { FailedMatch, MatchedStream, EventSearchResult, CorrectableStream } from "@/api/epg"
import type { CachedLeague } from "@/api/teams"
import { getLeagueDisplayName } from "@/lib/utils"

function formatDuration(ms: number | null): string {
  if (!ms) return "-"
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`
}

function formatBytes(bytes: number | undefined | null): string {
  if (bytes == null || isNaN(bytes) || bytes === 0) return "0 B"
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDateRange(start: string | null, end: string | null): string {
  if (!start || !end) return "N/A"
  const formatDate = (d: string) => `${d.slice(4, 6)}/${d.slice(6, 8)}`
  return `${formatDate(start)} - ${formatDate(end)}`
}

function getMatchMethodBadge(method: string | null) {
  switch (method) {
    case "cache":
      return <Badge variant="secondary">Cache</Badge>
    case "user_corrected":
      return <Badge variant="success">User Fixed</Badge>
    case "alias":
      return <Badge variant="info">Alias</Badge>
    case "pattern":
      return <Badge variant="outline">Pattern</Badge>
    case "fuzzy":
      return <Badge variant="warning">Fuzzy</Badge>
    case "keyword":
      return <Badge variant="secondary">Keyword</Badge>
    case "direct":
      return <Badge variant="success">Direct</Badge>
    default:
      return <Badge variant="outline">{method ?? "Unknown"}</Badge>
  }
}

function getFailReasonBadge(reason: string) {
  switch (reason) {
    case "unmatched":
      return <Badge variant="destructive">Unmatched</Badge>
    case "excluded_league":
      return <Badge variant="warning">Excluded League</Badge>
    case "filtered_include":
      return <Badge variant="secondary">Filtered (Include)</Badge>
    case "filtered_exclude":
      return <Badge variant="secondary">Filtered (Exclude)</Badge>
    case "exception":
      return <Badge variant="outline">Exception</Badge>
    default:
      return <Badge variant="outline">{reason}</Badge>
  }
}

export function EPG() {
  const { data: stats, isLoading: statsLoading, refetch: refetchStats } = useStats()
  const { data: runsData, isLoading: runsLoading, refetch: refetchRuns } = useRecentRuns(10, "full_epg")
  const { data: analysis, isLoading: analysisLoading, refetch: refetchAnalysis } = useEPGAnalysis()
  const { data: epgContent, isLoading: contentLoading } = useEPGContent(0) // 0 = no limit
  const { formatDateTime, formatRelativeTime } = useDateFormat()

  const [isDownloading, setIsDownloading] = useState(false)
  const [copied, setCopied] = useState(false)
  const [showXmlPreview, setShowXmlPreview] = useState(false)
  const [searchTerm, setSearchTerm] = useState("")
  const [currentMatch, setCurrentMatch] = useState(0)
  const [showLineNumbers, setShowLineNumbers] = useState(true)
  const previewRef = useRef<HTMLPreElement>(null)

  // Modal states
  const [matchedModalRunId, setMatchedModalRunId] = useState<number | null>(null)
  const [failedModalRunId, setFailedModalRunId] = useState<number | null>(null)
  const [eventMatcherOpen, setEventMatcherOpen] = useState(false)

  // Event matcher state
  const [matcherStream, setMatcherStream] = useState<CorrectableStream | null>(null)
  const [matcherLeague, setMatcherLeague] = useState("")
  const [matcherTargetDate, setMatcherTargetDate] = useState(() => {
    // Default to today in YYYY-MM-DD format
    const today = new Date()
    return today.toISOString().split("T")[0]
  })
  const [matcherEvents, setMatcherEvents] = useState<EventSearchResult[]>([])
  const [matcherLoading, setMatcherLoading] = useState(false)
  const [matcherSubmitting, setMatcherSubmitting] = useState(false)
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null)

  // Gap highlighting state
  const [highlightedGap, setHighlightedGap] = useState<{
    afterStop: string
    beforeStart: string
    afterProgram: string
    beforeProgram: string
  } | null>(null)

  // EPG URL for IPTV apps
  const epgUrl = `${window.location.origin}${getTeamXmltvUrl()}`

  // Fetch matched streams when modal is open
  const { data: matchedData, isLoading: matchedLoading } = useQuery({
    queryKey: ["matched-streams", matchedModalRunId],
    queryFn: () => getMatchedStreams(matchedModalRunId ?? undefined),
    enabled: matchedModalRunId !== null,
  })

  // Fetch failed matches when modal is open
  const { data: failedData, isLoading: failedLoading } = useQuery({
    queryKey: ["failed-matches", failedModalRunId],
    queryFn: () => getFailedMatches(failedModalRunId ?? undefined),
    enabled: failedModalRunId !== null,
  })

  // Fetch leagues for event matcher, matched modal, and failed modal
  const { data: leaguesData, isLoading: leaguesLoading } = useQuery({
    queryKey: ["cache", "leagues"],
    queryFn: () => getLeagues(false),
    enabled: eventMatcherOpen || matchedModalRunId !== null || failedModalRunId !== null,
    staleTime: 5 * 60 * 1000,
  })

  // Map league codes to display names (uses league_alias if available, otherwise name)
  const getLeagueDisplay = useMemo(() => {
    const map = new Map<string, string>()
    if (leaguesData?.leagues) {
      for (const league of leaguesData.leagues) {
        map.set(league.slug, getLeagueDisplayName(league, true))
      }
    }
    return (code: string | null) => code ? (map.get(code) ?? code) : "-"
  }, [leaguesData?.leagues])

  // Sort leagues by sport then name
  const sortedLeagues = useMemo(() => {
    if (!leaguesData?.leagues) return []
    return [...leaguesData.leagues].sort((a, b) => {
      const sportCompare = a.sport.localeCompare(b.sport)
      if (sportCompare !== 0) return sportCompare
      return a.name.localeCompare(b.name)
    })
  }, [leaguesData?.leagues])

  // Group leagues by sport
  const leaguesBySport = useMemo(() => {
    const grouped: Record<string, CachedLeague[]> = {}
    for (const league of sortedLeagues) {
      if (!grouped[league.sport]) grouped[league.sport] = []
      grouped[league.sport].push(league)
    }
    return grouped
  }, [sortedLeagues])

  const handleCopyUrl = async () => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(epgUrl)
      } else {
        const textArea = document.createElement("textarea")
        textArea.value = epgUrl
        textArea.style.position = "fixed"
        textArea.style.left = "-999999px"
        textArea.style.top = "-999999px"
        document.body.appendChild(textArea)
        textArea.focus()
        textArea.select()
        document.execCommand("copy")
        textArea.remove()
      }
      setCopied(true)
      toast.success("URL copied to clipboard")
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error("Failed to copy URL")
    }
  }

  // Generation progress (non-blocking toast)
  const { startGeneration, isGenerating } = useGenerationProgress()

  const handleGenerate = () => {
    startGeneration(() => {
      refetchAnalysis()
      refetchRuns()
      refetchStats()
    })
  }

  const handleDownload = async () => {
    setIsDownloading(true)
    try {
      const url = getTeamXmltvUrl()
      window.open(url, "_blank")
    } catch {
      toast.error("Failed to open XMLTV URL")
    } finally {
      setIsDownloading(false)
    }
  }

  // Open event matcher for correcting a stream (works with both failed and matched)
  const handleOpenEventMatcher = (stream: FailedMatch | MatchedStream) => {
    // Convert to CorrectableStream
    const correctable: CorrectableStream = {
      group_id: stream.group_id,
      stream_id: stream.stream_id,
      stream_name: stream.stream_name,
      group_name: stream.group_name,
      league_hint: "detected_league" in stream ? stream.detected_league : stream.league,
      current_event_id: "event_id" in stream ? stream.event_id : null,
    }
    setMatcherStream(correctable)
    setMatcherLeague(correctable.league_hint ?? "")
    setMatcherEvents([])
    setSelectedEventId(null)
    setEventMatcherOpen(true)
  }

  // Mark a stream as "no event" (skip it in future matching)
  const handleMarkAsNoEvent = async () => {
    if (!matcherStream) return
    if (matcherStream.stream_id === null) {
      toast.error("Cannot correct: stream_id is missing")
      return
    }

    setMatcherSubmitting(true)
    try {
      await correctStreamMatch({
        group_id: matcherStream.group_id,
        stream_id: matcherStream.stream_id,
        stream_name: matcherStream.stream_name,
        correct_event_id: null,
        correct_league: null,
      })
      toast.success("Stream marked as 'no event'", {
        description: "Changes will apply on next EPG generation",
      })
      setEventMatcherOpen(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to mark as no event")
    } finally {
      setMatcherSubmitting(false)
    }
  }

  const handleSearchEvents = async () => {
    if (!matcherLeague) return
    setMatcherLoading(true)
    try {
      const result = await searchEvents(matcherLeague, undefined, matcherTargetDate || undefined, 50)
      setMatcherEvents(result.events)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to search events")
    } finally {
      setMatcherLoading(false)
    }
  }

  const handleApplyCorrection = async () => {
    if (!matcherStream || !selectedEventId || !matcherLeague) return
    if (matcherStream.stream_id === null) {
      toast.error("Cannot correct: stream_id is missing")
      return
    }

    setMatcherSubmitting(true)
    try {
      await correctStreamMatch({
        group_id: matcherStream.group_id,
        stream_id: matcherStream.stream_id,
        stream_name: matcherStream.stream_name,
        correct_event_id: selectedEventId,
        correct_league: matcherLeague,
      })
      toast.success("Stream matched to event", {
        description: "Changes will apply on next EPG generation",
      })
      setEventMatcherOpen(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to apply correction")
    } finally {
      setMatcherSubmitting(false)
    }
  }

  // Search functionality for XML preview
  const searchMatches = useMemo(() => {
    if (!searchTerm || !epgContent?.content) return []
    const matches: number[] = []
    const lines = epgContent.content.split("\n")
    const searchLower = searchTerm.toLowerCase()
    lines.forEach((line, idx) => {
      if (line.toLowerCase().includes(searchLower)) {
        matches.push(idx)
      }
    })
    return matches
  }, [searchTerm, epgContent?.content])

  const scrollToMatch = useCallback((matchIndex: number) => {
    if (!previewRef.current || searchMatches.length === 0) return
    const lineNumber = searchMatches[matchIndex]
    const lineHeight = 20
    previewRef.current.scrollTop = lineNumber * lineHeight - 100
  }, [searchMatches])

  const nextMatch = () => {
    if (searchMatches.length === 0) return
    const next = (currentMatch + 1) % searchMatches.length
    setCurrentMatch(next)
    scrollToMatch(next)
  }

  const prevMatch = () => {
    if (searchMatches.length === 0) return
    const prev = (currentMatch - 1 + searchMatches.length) % searchMatches.length
    setCurrentMatch(prev)
    scrollToMatch(prev)
  }

  // Highlighted XML content
  const highlightedContent = useMemo(() => {
    if (!epgContent?.content) return ""
    const lines = epgContent.content.split("\n")

    if (highlightedGap) {
      const result: string[] = []
      let inProgramme = false
      let programmeLines: number[] = []
      let programmeType: "before" | "after" | null = null

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i]
        const lineNum = showLineNumbers ? `${(i + 1).toString().padStart(4)} | ` : ""

        if (line.includes("<programme")) {
          if (line.includes(`stop="${highlightedGap.afterStop}"`)) {
            inProgramme = true
            programmeType = "before"
            programmeLines = [i]
          } else if (line.includes(`start="${highlightedGap.beforeStart}"`)) {
            inProgramme = true
            programmeType = "after"
            programmeLines = [i]
          }
        }

        if (inProgramme) {
          if (!programmeLines.includes(i)) {
            programmeLines.push(i)
          }
        }

        if (inProgramme && line.includes("</programme>")) {
          inProgramme = false
          const bgClass = programmeType === "before"
            ? "bg-red-400/30"
            : "bg-blue-400/30"

          for (const lineIdx of programmeLines) {
            const ln = showLineNumbers ? `${(lineIdx + 1).toString().padStart(4)} | ` : ""
            result.push(`<span class="${bgClass}">${ln}${escapeHtml(lines[lineIdx])}</span>`)
          }
          programmeLines = []
          programmeType = null
          continue
        }

        if (!inProgramme) {
          result.push(`${lineNum}${escapeHtml(line)}`)
        }
      }
      return result.join("\n")
    }

    return lines.map((line, idx) => {
      const lineNum = showLineNumbers ? `${(idx + 1).toString().padStart(4)} | ` : ""
      const isMatch = searchTerm && line.toLowerCase().includes(searchTerm.toLowerCase())
      const isCurrentMatch = isMatch && searchMatches[currentMatch] === idx

      if (isCurrentMatch) {
        return `<span class="bg-yellow-500/40">${lineNum}${escapeHtml(line)}</span>`
      } else if (isMatch) {
        return `<span class="bg-yellow-500/20">${lineNum}${escapeHtml(line)}</span>`
      }
      return `${lineNum}${escapeHtml(line)}`
    }).join("\n")
  }, [epgContent?.content, showLineNumbers, searchTerm, currentMatch, searchMatches, highlightedGap])

  const hasIssues = (analysis?.unreplaced_variables?.length ?? 0) > 0 ||
                   (analysis?.coverage_gaps?.length ?? 0) > 0

  return (
    <div className="space-y-2">
      <div>
        <h1 className="text-xl font-bold">EPG Management</h1>
        <p className="text-sm text-muted-foreground">Generate and manage XMLTV output</p>
      </div>

      {/* Action Bar */}
      <div className="flex flex-wrap items-center gap-3 bg-secondary border border-border rounded px-3 py-2">
        <Button
          size="sm"
          onClick={handleGenerate}
          disabled={isGenerating}
        >
          {isGenerating ? (
            <Loader2 className="h-4 w-4 mr-1 animate-spin" />
          ) : (
            <Play className="h-4 w-4 mr-1" />
          )}
          {isGenerating ? "Generating..." : "Generate"}
        </Button>
        {stats?.last_run && (
          <span className="text-xs text-muted-foreground">
            Last: {formatRelativeTime(stats.last_run)}
          </span>
        )}
        <div className="h-4 w-px bg-border" />
        <Button
          variant="outline"
          size="sm"
          onClick={handleDownload}
          disabled={isDownloading}
        >
          {isDownloading ? (
            <Loader2 className="h-4 w-4 mr-1 animate-spin" />
          ) : (
            <Download className="h-4 w-4 mr-1" />
          )}
          Download
        </Button>
        <div className="h-4 w-px bg-border" />
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <Link className="h-4 w-4 text-muted-foreground shrink-0" />
          <Input
            value={epgUrl}
            readOnly
            className="text-xs font-mono h-8 flex-1 min-w-0"
            onClick={(e) => e.currentTarget.select()}
          />
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8 shrink-0"
            onClick={handleCopyUrl}
          >
            {copied ? (
              <Check className="h-4 w-4 text-green-500" />
            ) : (
              <Copy className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>

      {/* EPG Analysis - Stats Tiles */}
      <div className="grid grid-cols-3 md:grid-cols-6 gap-3">
        <div className="bg-secondary rounded px-3 py-2">
          <div className="text-lg font-semibold">{analysis?.channels.total ?? 0}</div>
          <div className="text-xs text-muted-foreground">Channels</div>
          {analysis && (
            <div className="text-xs text-muted-foreground">
              {analysis.channels.team_based}T / {analysis.channels.event_based}E
            </div>
          )}
        </div>
        <div className="bg-secondary rounded px-3 py-2">
          <div className="text-lg font-semibold">{analysis?.programmes.events ?? 0}</div>
          <div className="text-xs text-muted-foreground">Events</div>
        </div>
        <div className="bg-secondary rounded px-3 py-2">
          <div className="text-lg font-semibold text-blue-600">{analysis?.programmes.pregame ?? 0}</div>
          <div className="text-xs text-muted-foreground">Pregame</div>
        </div>
        <div className="bg-secondary rounded px-3 py-2">
          <div className="text-lg font-semibold text-purple-600">{analysis?.programmes.postgame ?? 0}</div>
          <div className="text-xs text-muted-foreground">Postgame</div>
        </div>
        <div className="bg-secondary rounded px-3 py-2">
          <div className="text-lg font-semibold text-orange-600">{analysis?.programmes.idle ?? 0}</div>
          <div className="text-xs text-muted-foreground">Idle</div>
        </div>
        <div className="bg-secondary rounded px-3 py-2">
          <div className="text-lg font-semibold">{analysis?.programmes.total ?? 0}</div>
          <div className="text-xs text-muted-foreground">Total</div>
          {analysis && (
            <div className="text-xs text-muted-foreground">
              {formatDateRange(analysis.date_range.start, analysis.date_range.end)}
            </div>
          )}
        </div>
      </div>

      {/* EPG Issues */}
      {analysisLoading ? (
        <div className="flex items-center justify-center py-4">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : analysis && hasIssues ? (
        <div className="border border-yellow-500/30 bg-yellow-500/10 rounded-lg p-3 space-y-2">
          <div className="flex items-center gap-2 text-yellow-600 font-medium text-sm">
            <AlertTriangle className="h-4 w-4" />
            Detected Issues
          </div>

          {analysis.unreplaced_variables.length > 0 && (
            <div>
              <div className="text-xs font-medium mb-1">
                Unreplaced Variables ({analysis.unreplaced_variables.length})
              </div>
              <div className="flex flex-wrap gap-1">
                {analysis.unreplaced_variables.map((v) => (
                  <code
                    key={v}
                    className="text-xs bg-yellow-500/20 px-1.5 py-0.5 rounded cursor-pointer hover:bg-yellow-500/40"
                    onClick={() => {
                      setSearchTerm(v)
                      setShowXmlPreview(true)
                    }}
                  >
                    {v}
                  </code>
                ))}
              </div>
            </div>
          )}

          {analysis.coverage_gaps.length > 0 && (
            <div>
              <div className="text-xs font-medium mb-1">
                Coverage Gaps ({analysis.coverage_gaps.length})
              </div>
              <div className="space-y-1 max-h-32 overflow-y-auto">
                {analysis.coverage_gaps.slice(0, 10).map((gap, idx) => (
                  <div
                    key={idx}
                    className="text-xs bg-yellow-500/20 px-2 py-1 rounded cursor-pointer hover:bg-yellow-500/40"
                    onClick={() => {
                      setSearchTerm("")
                      setHighlightedGap({
                        afterStop: gap.after_stop,
                        beforeStart: gap.before_start,
                        afterProgram: gap.after_program,
                        beforeProgram: gap.before_program,
                      })
                      setShowXmlPreview(true)
                      setTimeout(() => {
                        if (previewRef.current) {
                          const mark = previewRef.current.querySelector(".bg-red-400\\/30, .bg-blue-400\\/30")
                          if (mark) {
                            mark.scrollIntoView({ behavior: "smooth", block: "center" })
                          }
                        }
                      }, 100)
                    }}
                  >
                    <strong>{gap.channel}</strong>: {gap.gap_minutes}min gap between "{gap.after_program}" and "{gap.before_program}"
                  </div>
                ))}
                {analysis.coverage_gaps.length > 10 && (
                  <div className="text-xs text-muted-foreground">
                    ... and {analysis.coverage_gaps.length - 10} more
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      ) : analysis ? (
        <div className="border border-green-500/30 bg-green-500/10 rounded-lg p-3">
          <div className="flex items-center gap-2 text-green-600 font-medium text-sm">
            <CheckCircle className="h-4 w-4" />
            No Issues Detected
          </div>
          <p className="text-xs text-muted-foreground mt-1">
            All template variables resolved and no coverage gaps found.
          </p>
        </div>
      ) : null}

      {/* XML Preview Toggle */}
      <Card>
        <CardHeader
          className="cursor-pointer"
          onClick={() => setShowXmlPreview(!showXmlPreview)}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <CardTitle>XML Preview</CardTitle>
              {epgContent && (
                <Badge variant="secondary">
                  {epgContent.total_lines} lines | {formatBytes(epgContent.size_bytes)}
                </Badge>
              )}
            </div>
            {showXmlPreview ? (
              <ChevronUp className="h-5 w-5" />
            ) : (
              <ChevronDown className="h-5 w-5" />
            )}
          </div>
        </CardHeader>
        {showXmlPreview && (
          <CardContent>
            {contentLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : epgContent?.content ? (
              <div className="space-y-2">
                {/* Search Bar */}
                <div className="flex items-center gap-2">
                  <div className="relative flex-1">
                    <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                      placeholder="Search XML..."
                      value={searchTerm}
                      onChange={(e) => {
                        setSearchTerm(e.target.value)
                        setCurrentMatch(0)
                        setHighlightedGap(null)
                      }}
                      className="pl-8"
                    />
                  </div>
                  {highlightedGap && (
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-yellow-600">
                        Gap: "{highlightedGap.afterProgram}" → "{highlightedGap.beforeProgram}"
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setHighlightedGap(null)}
                        className="h-6 px-2 text-xs"
                      >
                        Clear
                      </Button>
                    </div>
                  )}
                  {searchMatches.length > 0 && !highlightedGap && (
                    <div className="flex items-center gap-1">
                      <span className="text-sm text-muted-foreground">
                        {currentMatch + 1}/{searchMatches.length}
                      </span>
                      <Button variant="outline" size="sm" onClick={prevMatch}>
                        Prev
                      </Button>
                      <Button variant="outline" size="sm" onClick={nextMatch}>
                        Next
                      </Button>
                    </div>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setShowLineNumbers(!showLineNumbers)}
                  >
                    {showLineNumbers ? "Hide" : "Show"} Lines
                  </Button>
                </div>

                {/* XML Content */}
                <pre
                  ref={previewRef}
                  className="bg-muted/50 rounded-lg p-4 text-xs font-mono overflow-auto max-h-[600px]"
                  dangerouslySetInnerHTML={{ __html: highlightedContent }}
                />
              </div>
            ) : (
              <div className="text-center py-8 text-muted-foreground">
                No XML content available. Generate EPG first.
              </div>
            )}
          </CardContent>
        )}
      </Card>

      {/* Recent Runs */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Runs</CardTitle>
          <CardDescription>Latest EPG generation runs (click Matched/Failed to view details)</CardDescription>
        </CardHeader>
        <CardContent>
          {runsLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : runsData?.runs.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              No runs recorded yet. Generate EPG to see history.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Status</TableHead>
                  <TableHead>Generated At</TableHead>
                  <TableHead>Events</TableHead>
                  <TableHead>Matched</TableHead>
                  <TableHead>Failed</TableHead>
                  <TableHead>Channels</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Size</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runsData?.runs.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell>
                      {run.status === "completed" ? (
                        <CheckCircle className="h-4 w-4 text-green-600" />
                      ) : run.status === "failed" ? (
                        <XCircle className="h-4 w-4 text-red-600" />
                      ) : run.status === "running" ? (
                        <Loader2 className="h-4 w-4 animate-spin text-blue-600" />
                      ) : (
                        <Clock className="h-4 w-4 text-muted-foreground" />
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatDateTime(run.started_at)}
                    </TableCell>
                    <TableCell>{run.programmes?.events ?? 0}</TableCell>
                    <TableCell>
                      <button
                        className="text-green-600 hover:underline font-medium"
                        onClick={() => setMatchedModalRunId(run.id)}
                      >
                        {run.streams?.matched ?? 0}
                      </button>
                    </TableCell>
                    <TableCell>
                      <button
                        className="text-red-600 hover:underline font-medium"
                        onClick={() => setFailedModalRunId(run.id)}
                      >
                        {run.streams?.unmatched ?? 0}
                      </button>
                    </TableCell>
                    <TableCell>{run.channels?.active ?? 0}</TableCell>
                    <TableCell>{formatDuration(run.duration_ms)}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatBytes(run.xmltv_size_bytes)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* All-time Stats */}
      <Card>
        <CardHeader>
          <CardTitle>All-Time Totals</CardTitle>
        </CardHeader>
        <CardContent>
          {statsLoading ? (
            <div className="flex items-center justify-center py-4">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <div>
                <span className="text-muted-foreground">Total Runs:</span>{" "}
                <strong>{stats?.total_runs ?? 0}</strong>
              </div>
              <div>
                <span className="text-muted-foreground">Programmes Generated:</span>{" "}
                <strong>{stats?.totals?.programmes_generated ?? 0}</strong>
              </div>
              <div>
                <span className="text-muted-foreground">Streams Matched:</span>{" "}
                <strong>{stats?.totals?.streams_matched ?? 0}</strong>
              </div>
              <div>
                <span className="text-muted-foreground">Channels Created:</span>{" "}
                <strong>{stats?.totals?.channels_created ?? 0}</strong>
              </div>
              <div>
                <span className="text-muted-foreground">Avg Duration:</span>{" "}
                <strong>{formatDuration(stats?.avg_duration_ms ?? 0)}</strong>
              </div>
              <div>
                <span className="text-muted-foreground">Last Run:</span>{" "}
                <strong>{formatRelativeTime(stats?.last_run ?? null)}</strong>
              </div>
              <div>
                <span className="text-muted-foreground">Cache Hits:</span>{" "}
                <strong>{stats?.totals?.streams_cached ?? 0}</strong>
              </div>
              <div>
                <span className="text-muted-foreground">Channels Deleted:</span>{" "}
                <strong>{stats?.totals?.channels_deleted ?? 0}</strong>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Matched Streams Modal */}
      <Dialog open={matchedModalRunId !== null} onOpenChange={() => setMatchedModalRunId(null)}>
        <DialogContent onClose={() => setMatchedModalRunId(null)} className="max-w-4xl max-h-[80vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <CheckCircle className="h-5 w-5 text-green-600" />
              Matched Streams
            </DialogTitle>
            <DialogDescription>
              Streams successfully matched to events (Run #{matchedModalRunId})
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-auto">
            {matchedLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : matchedData?.streams.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                No matched streams for this run.
              </div>
            ) : (
              <Table className="table-fixed w-full">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[28%]">Stream Name</TableHead>
                    <TableHead className="w-[23%]">Event</TableHead>
                    <TableHead className="w-[70px]">League</TableHead>
                    <TableHead className="w-[100px]">Method</TableHead>
                    <TableHead className="w-[18%]">Group</TableHead>
                    <TableHead className="w-[60px]">Fix</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {matchedData?.streams.map((stream) => (
                    <TableRow key={stream.id}>
                      <TableCell className="font-medium truncate" title={stream.stream_name}>
                        {stream.stream_name}
                      </TableCell>
                      <TableCell>
                        <div className="truncate" title={stream.event_name || `${stream.away_team} @ ${stream.home_team}`}>
                          {stream.event_name || `${stream.away_team} @ ${stream.home_team}`}
                        </div>
                        {stream.event_date && (
                          <div className="text-xs text-muted-foreground">
                            {new Date(stream.event_date).toLocaleDateString()}
                          </div>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge variant="secondary">{getLeagueDisplay(stream.league)}</Badge>
                      </TableCell>
                      <TableCell>
                        {getMatchMethodBadge(stream.match_method)}
                        {/* Use !! to convert number to boolean - prevents React rendering 0 */}
                        {!!stream.from_cache && stream.match_method !== "cache" && (
                          <Badge variant="outline" className="ml-1">Cached</Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm truncate" title={stream.group_name ?? undefined}>
                        {stream.group_name}
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleOpenEventMatcher(stream)}
                          title="Correct this match"
                        >
                          <AlertTriangle className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>

          <DialogFooter>
            <div className="text-sm text-muted-foreground">
              {matchedData?.count ?? 0} matched streams
            </div>
            <Button variant="outline" onClick={() => setMatchedModalRunId(null)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Failed Matches Modal */}
      <Dialog open={failedModalRunId !== null} onOpenChange={() => setFailedModalRunId(null)}>
        <DialogContent onClose={() => setFailedModalRunId(null)} className="max-w-4xl max-h-[80vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <XCircle className="h-5 w-5 text-red-600" />
              Failed Matches
            </DialogTitle>
            <DialogDescription>
              Streams that failed to match to events (Run #{failedModalRunId})
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-auto">
            {failedLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : failedData?.failures.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                No failed matches for this run.
              </div>
            ) : (
              <Table className="table-fixed w-full">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[30%]">Stream Name</TableHead>
                    <TableHead className="w-[100px]">Reason</TableHead>
                    <TableHead className="w-[25%]">Detected Teams</TableHead>
                    <TableHead className="w-[18%]">Group</TableHead>
                    <TableHead className="w-[60px]">Fix</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {failedData?.failures.map((failure) => (
                    <TableRow key={failure.id}>
                      <TableCell className="font-medium truncate" title={failure.stream_name}>
                        {failure.stream_name}
                      </TableCell>
                      <TableCell>
                        {getFailReasonBadge(failure.reason)}
                        {failure.exclusion_reason && (
                          <div className="text-xs text-muted-foreground mt-1 truncate" title={failure.exclusion_reason}>
                            {failure.exclusion_reason}
                          </div>
                        )}
                      </TableCell>
                      <TableCell className="text-sm">
                        {failure.extracted_team1 || failure.extracted_team2 ? (
                          <div>
                            {failure.extracted_team1 && <div className="truncate" title={failure.extracted_team1}>{failure.extracted_team1}</div>}
                            {failure.extracted_team2 && <div className="text-muted-foreground truncate" title={failure.extracted_team2}>vs {failure.extracted_team2}</div>}
                            {failure.detected_league && (
                              <Badge variant="outline" className="mt-1">{getLeagueDisplay(failure.detected_league)}</Badge>
                            )}
                          </div>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm truncate" title={failure.group_name ?? undefined}>
                        {failure.group_name}
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleOpenEventMatcher(failure)}
                          title="Fix this stream's match"
                        >
                          <AlertTriangle className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>

          <DialogFooter>
            <div className="text-sm text-muted-foreground">
              {failedData?.count ?? 0} failed matches
            </div>
            <Button variant="outline" onClick={() => setFailedModalRunId(null)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Event Matcher Dialog */}
      <Dialog open={eventMatcherOpen} onOpenChange={setEventMatcherOpen}>
        <DialogContent onClose={() => setEventMatcherOpen(false)} className="max-w-2xl max-h-[80vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle>
              {matcherStream?.current_event_id ? "Correct Stream Match" : "Match Stream to Event"}
            </DialogTitle>
            <DialogDescription>
              {matcherStream?.current_event_id
                ? "This stream is currently matched incorrectly. Select the correct event or skip it."
                : "Select the correct event for this stream, or skip it if it shouldn't match."}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-4 flex-1 overflow-hidden flex flex-col">
            {/* Stream Info */}
            {matcherStream && (
              <div className="bg-muted p-3 rounded-md">
                <p className="text-xs text-muted-foreground mb-1">Stream Name</p>
                <p className="font-medium text-sm truncate" title={matcherStream.stream_name}>
                  {matcherStream.stream_name}
                </p>
                <div className="flex items-center gap-4 mt-1">
                  {matcherStream.group_name && (
                    <p className="text-xs text-muted-foreground">
                      Group: {matcherStream.group_name}
                    </p>
                  )}
                  {matcherStream.current_event_id && (
                    <Badge variant="warning" className="text-xs">
                      Currently matched (incorrect)
                    </Badge>
                  )}
                </div>
              </div>
            )}

            {/* League and Date Selection */}
            <div className="flex gap-2">
              {leaguesLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading leagues...
                </div>
              ) : (
                <>
                  <Select
                    className="flex-1"
                    value={matcherLeague}
                    onChange={(e) => setMatcherLeague(e.target.value)}
                  >
                    <option value="">Select a league...</option>
                    {Object.entries(leaguesBySport).map(([sport, leagues]) => (
                      <optgroup key={sport} label={sport}>
                        {leagues.map((league) => (
                          <option key={league.slug} value={league.slug}>
                            {getLeagueDisplayName(league)}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                  </Select>
                  <Input
                    type="date"
                    value={matcherTargetDate}
                    onChange={(e) => setMatcherTargetDate(e.target.value)}
                    className="w-40"
                    title="Target date for event search"
                  />
                  <Button
                    onClick={handleSearchEvents}
                    disabled={!matcherLeague || matcherLoading}
                  >
                    {matcherLoading ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Search className="h-4 w-4" />
                    )}
                    Search
                  </Button>
                </>
              )}
            </div>

            {/* Events List */}
            <div className="flex-1 overflow-auto border rounded-md">
              {matcherEvents.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground text-sm">
                  {matcherLoading ? "Searching..." : "Select a league and click Search"}
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[40%]">Matchup</TableHead>
                      <TableHead className="w-[25%]">Time</TableHead>
                      <TableHead className="w-[20%]">Status</TableHead>
                      <TableHead className="w-[15%]">Select</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {matcherEvents.map((event) => (
                      <TableRow
                        key={event.event_id}
                        className={selectedEventId === event.event_id ? "bg-muted" : ""}
                      >
                        <TableCell className="font-medium">
                          {event.away_team && event.home_team ? (
                            <span>
                              {event.away_team} @ {event.home_team}
                            </span>
                          ) : (
                            event.event_name
                          )}
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {formatDateTime(event.start_time)}
                        </TableCell>
                        <TableCell>
                          <Badge variant={event.status === "in" ? "default" : "secondary"}>
                            {event.status || "scheduled"}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <Button
                            variant={selectedEventId === event.event_id ? "default" : "outline"}
                            size="sm"
                            onClick={() => setSelectedEventId(event.event_id)}
                          >
                            {selectedEventId === event.event_id ? <Check className="h-4 w-4" /> : "Select"}
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </div>
          </div>

          <DialogFooter className="flex justify-between sm:justify-between">
            <Button
              variant="destructive"
              onClick={handleMarkAsNoEvent}
              disabled={matcherSubmitting}
              title="Mark this stream to be skipped (no event match)"
            >
              {matcherSubmitting ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <XCircle className="h-4 w-4 mr-2" />
              )}
              Skip Stream
            </Button>
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setEventMatcherOpen(false)}>
                Cancel
              </Button>
              <Button
                onClick={handleApplyCorrection}
                disabled={!selectedEventId || matcherSubmitting}
              >
                {matcherSubmitting ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Applying...
                  </>
                ) : (
                  "Apply Match"
                )}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// Helper function to escape HTML
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;")
}
