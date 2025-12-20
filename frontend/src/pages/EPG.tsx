import { useState, useMemo, useRef, useCallback } from "react"
import { toast } from "sonner"
import {
  Play,
  Download,
  RefreshCw,
  Loader2,
  Clock,
  CheckCircle,
  XCircle,
  ExternalLink,
  Link,
  Copy,
  Check,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Search,
  FileText,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  useStats,
  useRecentRuns,
  useGenerateTeamEpg,
  useEPGAnalysis,
  useEPGContent,
} from "@/hooks/useEPG"
import { getTeamXmltvUrl } from "@/api/epg"

function formatDuration(ms: number | null): string {
  if (!ms) return "-"
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B"
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatRelativeTime(dateStr: string | null): string {
  if (!dateStr) return "Never"
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffMins < 1) return "Just now"
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  return `${diffDays}d ago`
}

function formatDateRange(start: string | null, end: string | null): string {
  if (!start || !end) return "N/A"
  const formatDate = (d: string) => `${d.slice(4, 6)}/${d.slice(6, 8)}`
  return `${formatDate(start)} - ${formatDate(end)}`
}

export function EPG() {
  const { data: stats, isLoading: statsLoading, refetch: refetchStats } = useStats()
  const { data: runsData, isLoading: runsLoading, refetch: refetchRuns } = useRecentRuns(10)
  const { data: analysis, isLoading: analysisLoading, refetch: refetchAnalysis } = useEPGAnalysis()
  const { data: epgContent, isLoading: contentLoading } = useEPGContent(2000)

  const generateMutation = useGenerateTeamEpg()

  const [isDownloading, setIsDownloading] = useState(false)
  const [copied, setCopied] = useState(false)
  const [showXmlPreview, setShowXmlPreview] = useState(false)
  const [searchTerm, setSearchTerm] = useState("")
  const [currentMatch, setCurrentMatch] = useState(0)
  const [showLineNumbers, setShowLineNumbers] = useState(true)
  const previewRef = useRef<HTMLPreElement>(null)

  // EPG URL for IPTV apps
  const epgUrl = `${window.location.origin}${getTeamXmltvUrl()}`

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

  const handleGenerate = async () => {
    try {
      const result = await generateMutation.mutateAsync({})
      toast.success(
        `Generated ${result.programmes_count} programmes for ${result.teams_processed} teams in ${result.duration_seconds.toFixed(1)}s`
      )
      refetchAnalysis()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to generate EPG")
    }
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
    const lineHeight = 20 // Approximate line height
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
  }, [epgContent?.content, showLineNumbers, searchTerm, currentMatch, searchMatches])

  const hasIssues = (analysis?.unreplaced_variables?.length ?? 0) > 0 ||
                   (analysis?.coverage_gaps?.length ?? 0) > 0

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">EPG Management</h1>
          <p className="text-muted-foreground">Generate and manage XMLTV output</p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              refetchStats()
              refetchRuns()
              refetchAnalysis()
            }}
          >
            <RefreshCw className="h-4 w-4 mr-1" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Action Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Generate EPG */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-lg flex items-center gap-2">
              <Play className="h-5 w-5" />
              Generate EPG
            </CardTitle>
            <CardDescription>Create fresh EPG with current schedules</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <Button
              onClick={handleGenerate}
              disabled={generateMutation.isPending}
              className="w-full"
            >
              {generateMutation.isPending && (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              )}
              Generate Now
            </Button>
            {stats?.last_run && (
              <p className="text-xs text-muted-foreground text-center">
                Last: {formatRelativeTime(stats.last_run)}
              </p>
            )}
          </CardContent>
        </Card>

        {/* Download XMLTV */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-lg flex items-center gap-2">
              <Download className="h-5 w-5" />
              Download EPG
            </CardTitle>
            <CardDescription>Download XMLTV file to your computer</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <Button
              variant="outline"
              onClick={handleDownload}
              disabled={isDownloading}
              className="w-full"
            >
              {isDownloading ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <ExternalLink className="h-4 w-4 mr-2" />
              )}
              Open XMLTV
            </Button>
            <p className="text-xs text-muted-foreground text-center">
              Format: XMLTV (.xml)
            </p>
          </CardContent>
        </Card>

        {/* EPG URL */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-lg flex items-center gap-2">
              <Link className="h-5 w-5" />
              EPG URL
            </CardTitle>
            <CardDescription>Direct URL for IPTV applications</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex gap-2">
              <Input
                value={epgUrl}
                readOnly
                className="text-xs font-mono"
                onClick={(e) => e.currentTarget.select()}
              />
              <Button
                variant="outline"
                size="icon"
                onClick={handleCopyUrl}
              >
                {copied ? (
                  <Check className="h-4 w-4 text-green-500" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground text-center">
              Use this URL in your IPTV app
            </p>
          </CardContent>
        </Card>
      </div>

      {/* EPG Analysis */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FileText className="h-5 w-5" />
            EPG Analysis
          </CardTitle>
          <CardDescription>Current EPG content breakdown and issues</CardDescription>
        </CardHeader>
        <CardContent>
          {analysisLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : analysis ? (
            <div className="space-y-4">
              {/* Stats Grid */}
              <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                <div className="text-center p-3 bg-muted/50 rounded-lg">
                  <div className="text-2xl font-bold">{analysis.channels.total}</div>
                  <div className="text-xs text-muted-foreground">Channels</div>
                  <div className="text-xs text-muted-foreground mt-1">
                    {analysis.channels.team_based} team / {analysis.channels.event_based} event
                  </div>
                </div>
                <div className="text-center p-3 bg-muted/50 rounded-lg">
                  <div className="text-2xl font-bold">{analysis.programmes.events}</div>
                  <div className="text-xs text-muted-foreground">Events</div>
                </div>
                <div className="text-center p-3 bg-muted/50 rounded-lg">
                  <div className="text-2xl font-bold text-blue-600">{analysis.programmes.pregame}</div>
                  <div className="text-xs text-muted-foreground">Pregame</div>
                </div>
                <div className="text-center p-3 bg-muted/50 rounded-lg">
                  <div className="text-2xl font-bold text-purple-600">{analysis.programmes.postgame}</div>
                  <div className="text-xs text-muted-foreground">Postgame</div>
                </div>
                <div className="text-center p-3 bg-muted/50 rounded-lg">
                  <div className="text-2xl font-bold text-orange-600">{analysis.programmes.idle}</div>
                  <div className="text-xs text-muted-foreground">Idle</div>
                </div>
              </div>

              {/* Date Range and Total */}
              <div className="flex items-center justify-between text-sm text-muted-foreground border-t pt-3">
                <span>Date Range: <strong>{formatDateRange(analysis.date_range.start, analysis.date_range.end)}</strong></span>
                <span>Total Programmes: <strong>{analysis.programmes.total}</strong></span>
              </div>

              {/* Issues Section */}
              {hasIssues ? (
                <div className="border border-yellow-500/30 bg-yellow-500/10 rounded-lg p-4 space-y-3">
                  <div className="flex items-center gap-2 text-yellow-600 font-medium">
                    <AlertTriangle className="h-4 w-4" />
                    Detected Issues
                  </div>

                  {analysis.unreplaced_variables.length > 0 && (
                    <div>
                      <div className="text-sm font-medium mb-1">
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
                      <div className="text-sm font-medium mb-1">
                        Coverage Gaps ({analysis.coverage_gaps.length})
                      </div>
                      <div className="space-y-1 max-h-32 overflow-y-auto">
                        {analysis.coverage_gaps.slice(0, 10).map((gap, idx) => (
                          <div
                            key={idx}
                            className="text-xs bg-yellow-500/20 px-2 py-1 rounded cursor-pointer hover:bg-yellow-500/40"
                            onClick={() => {
                              setSearchTerm(gap.channel)
                              setShowXmlPreview(true)
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
              ) : (
                <div className="border border-green-500/30 bg-green-500/10 rounded-lg p-4">
                  <div className="flex items-center gap-2 text-green-600 font-medium">
                    <CheckCircle className="h-4 w-4" />
                    No Issues Detected
                  </div>
                  <p className="text-sm text-muted-foreground mt-1">
                    All template variables resolved and no coverage gaps found.
                  </p>
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground">
              No EPG data available. Generate EPG first.
            </div>
          )}
        </CardContent>
      </Card>

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
                      }}
                      className="pl-8"
                    />
                  </div>
                  {searchMatches.length > 0 && (
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
                  className="bg-muted/50 rounded-lg p-4 text-xs font-mono overflow-auto max-h-96"
                  dangerouslySetInnerHTML={{ __html: highlightedContent }}
                />

                {epgContent.truncated && (
                  <p className="text-xs text-muted-foreground text-center">
                    Showing first 2000 lines of {epgContent.total_lines} total
                  </p>
                )}
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
          <CardDescription>Latest EPG generation runs</CardDescription>
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
                  <TableHead>Type</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Programmes</TableHead>
                  <TableHead>Streams</TableHead>
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
                    <TableCell>
                      <Badge variant="secondary">{run.run_type}</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatRelativeTime(run.started_at)}
                    </TableCell>
                    <TableCell>{formatDuration(run.duration_ms)}</TableCell>
                    <TableCell>{run.programmes_total}</TableCell>
                    <TableCell>
                      {run.streams_matched > 0 && (
                        <span className="text-green-600">{run.streams_matched}</span>
                      )}
                      {run.streams_unmatched > 0 && (
                        <span className="text-red-600 ml-1">
                          /{run.streams_unmatched}
                        </span>
                      )}
                      {run.streams_matched === 0 && run.streams_unmatched === 0 && "-"}
                    </TableCell>
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
