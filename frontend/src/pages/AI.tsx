import { useState, useEffect, useCallback } from "react"
import { toast } from "sonner"
import {
  Brain,
  Loader2,
  Trash2,
  RefreshCw,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Pencil,
  Save,
  X,
  FlaskConical,
  ChevronDown,
  ChevronRight,
  Square,
} from "lucide-react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import {
  getAIStatus,
  getAIPatterns,
  deleteAIPattern,
  deleteGroupPatterns,
  startPatternLearning,
  getPatternLearningStatus,
  abortPatternLearning,
  type AIPattern,
  type PatternLearningStatus,
} from "@/api/settings"
import { useGroups } from "@/hooks/useGroups"

// Format seconds as "Xm Ys" or "Xs"
function formatETA(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`
  }
  // Hours
  const hours = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  if (mins === 0 && secs === 0) return `${hours}h`
  if (secs === 0) return `${hours}h ${mins}m`
  return `${hours}h ${mins}m ${secs}s`
}

// API functions for pattern management
async function updatePattern(patternId: string, data: { regex?: string; description?: string }) {
  const response = await fetch(`/api/v1/ai/patterns/${patternId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || "Failed to update pattern")
  }
  return response.json()
}

async function testRegex(regex: string, streams: string[]) {
  const response = await fetch("/api/v1/ai/test-regex", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ regex, streams }),
  })
  if (!response.ok) throw new Error("Failed to test regex")
  return response.json()
}

interface PatternCardProps {
  pattern: AIPattern
  groupName: string | undefined
  onEdit: () => void
  onDelete: () => void
  isDeleting: boolean
}

function PatternCard({ pattern, groupName, onEdit, onDelete, isDeleting }: PatternCardProps) {
  const [expanded, setExpanded] = useState(false)
  const successRate = pattern.match_count + pattern.fail_count > 0
    ? (pattern.match_count / (pattern.match_count + pattern.fail_count)) * 100
    : 0

  return (
    <div className="border rounded-lg p-3 space-y-2">
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setExpanded(!expanded)}
              className="p-0.5 hover:bg-accent rounded"
            >
              {expanded ? (
                <ChevronDown className="h-4 w-4" />
              ) : (
                <ChevronRight className="h-4 w-4" />
              )}
            </button>
            <code className="text-xs bg-muted px-1.5 py-0.5 rounded truncate max-w-[400px] block">
              {pattern.regex}
            </code>
          </div>
          <p className="text-sm text-muted-foreground mt-1 ml-6">
            {pattern.description || "No description"}
          </p>
        </div>
        <div className="flex items-center gap-2 ml-2">
          <Badge variant="secondary" className="text-xs">
            {groupName || `Group ${pattern.group_id}`}
          </Badge>
          <Badge
            variant={pattern.confidence >= 0.7 ? "success" : pattern.confidence >= 0.5 ? "warning" : "destructive"}
            className="text-xs"
          >
            {(pattern.confidence * 100).toFixed(0)}%
          </Badge>
          <Button variant="ghost" size="sm" onClick={onEdit}>
            <Pencil className="h-3 w-3" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onDelete}
            disabled={isDeleting}
            className="text-destructive hover:text-destructive"
          >
            {isDeleting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
          </Button>
        </div>
      </div>

      {expanded && (
        <div className="ml-6 mt-2 space-y-2 text-sm">
          <div className="flex gap-4">
            <span className="text-muted-foreground">
              Matches: <strong className="text-foreground">{pattern.match_count}</strong>
            </span>
            <span className="text-muted-foreground">
              Fails: <strong className="text-foreground">{pattern.fail_count}</strong>
            </span>
            <span className="text-muted-foreground">
              Success Rate: <strong className={cn(
                successRate >= 80 ? "text-green-500" : successRate >= 50 ? "text-yellow-500" : "text-red-500"
              )}>{successRate.toFixed(0)}%</strong>
            </span>
          </div>
          {pattern.example_streams.length > 0 && (
            <div>
              <p className="text-muted-foreground text-xs mb-1">Example streams:</p>
              <div className="space-y-0.5">
                {pattern.example_streams.slice(0, 3).map((s, i) => (
                  <code key={i} className="block text-xs bg-muted/50 px-2 py-0.5 rounded truncate">
                    {s}
                  </code>
                ))}
              </div>
            </div>
          )}
          {Object.keys(pattern.field_map).length > 0 && (
            <div>
              <p className="text-muted-foreground text-xs mb-1">Field mapping:</p>
              <code className="text-xs bg-muted/50 px-2 py-0.5 rounded">
                {JSON.stringify(pattern.field_map)}
              </code>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

interface EditPatternModalProps {
  pattern: AIPattern
  onClose: () => void
  onSave: (data: { regex: string; description: string }) => void
  isSaving: boolean
}

function EditPatternModal({ pattern, onClose, onSave, isSaving }: EditPatternModalProps) {
  const [regex, setRegex] = useState(pattern.regex)
  const [description, setDescription] = useState(pattern.description)
  const [testStreams, setTestStreams] = useState(pattern.example_streams.join("\n"))
  const [testResults, setTestResults] = useState<{ stream: string; matched: boolean; groups: Record<string, string> }[] | null>(null)
  const [testing, setTesting] = useState(false)

  const handleTest = async () => {
    const streams = testStreams.split("\n").filter(s => s.trim())
    if (streams.length === 0) return

    setTesting(true)
    try {
      const result = await testRegex(regex, streams)
      if (result.success) {
        setTestResults(result.results)
        toast.success(`${result.matches}/${result.total} streams matched`)
      } else {
        toast.error(result.error || "Invalid regex")
        setTestResults(null)
      }
    } catch {
      toast.error("Failed to test regex")
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-background border rounded-lg shadow-lg w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        <div className="p-4 border-b flex items-center justify-between">
          <h3 className="font-semibold">Edit Pattern</h3>
          <Button variant="ghost" size="sm" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="p-4 space-y-4 overflow-y-auto flex-1">
          <div className="space-y-2">
            <Label>Regex Pattern</Label>
            <Input
              value={regex}
              onChange={(e) => setRegex(e.target.value)}
              className="font-mono text-sm"
              placeholder="(?P<team1>.*?) vs (?P<team2>.*)"
            />
            <p className="text-xs text-muted-foreground">
              Use named groups: (?P&lt;team1&gt;...), (?P&lt;team2&gt;...), (?P&lt;league&gt;...), (?P&lt;date&gt;...), (?P&lt;time&gt;...)
            </p>
          </div>

          <div className="space-y-2">
            <Label>Description</Label>
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Pattern for NHL streams with date prefix"
            />
          </div>

          <div className="space-y-2">
            <Label>Test Streams (one per line)</Label>
            <textarea
              className="w-full h-32 p-2 text-sm border rounded-md font-mono resize-none"
              value={testStreams}
              onChange={(e) => setTestStreams(e.target.value)}
              placeholder="Paste stream names to test..."
            />
            <Button variant="outline" size="sm" onClick={handleTest} disabled={testing}>
              {testing ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <FlaskConical className="h-4 w-4 mr-1" />}
              Test Pattern
            </Button>
          </div>

          {testResults && (
            <div className="space-y-2">
              <Label>Test Results</Label>
              <div className="max-h-48 overflow-auto border rounded p-2 space-y-1">
                {testResults.map((r, i) => (
                  <div key={i} className={cn(
                    "text-xs p-1 rounded",
                    r.matched ? "bg-green-500/10" : "bg-red-500/10"
                  )}>
                    <div className="flex items-center gap-2">
                      {r.matched ? (
                        <CheckCircle className="h-3 w-3 text-green-500" />
                      ) : (
                        <XCircle className="h-3 w-3 text-red-500" />
                      )}
                      <span className="truncate">{r.stream}</span>
                    </div>
                    {r.matched && Object.keys(r.groups).length > 0 && (
                      <div className="ml-5 mt-1 text-muted-foreground">
                        {Object.entries(r.groups).map(([k, v]) => (
                          <span key={k} className="mr-2">{k}: <strong>{v}</strong></span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="p-4 border-t flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={() => onSave({ regex, description })} disabled={isSaving}>
            {isSaving ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Save className="h-4 w-4 mr-1" />}
            Save
          </Button>
        </div>
      </div>
    </div>
  )
}

export function AI() {
  const queryClient = useQueryClient()
  const [selectedGroupIds, setSelectedGroupIds] = useState<Set<number>>(new Set())
  const [filterGroupId, setFilterGroupId] = useState<number | null>(null)
  const [editingPattern, setEditingPattern] = useState<AIPattern | null>(null)

  // Queries
  const statusQuery = useQuery({
    queryKey: ["ai", "status"],
    queryFn: getAIStatus,
    refetchInterval: 30000,
  })

  const patternsQuery = useQuery({
    queryKey: ["ai", "patterns"],
    queryFn: () => getAIPatterns(),
  })

  const groupsQuery = useGroups(true)

  // Group lookup map
  const groupMap = new Map(
    groupsQuery.data?.groups?.map(g => [g.id, g.name]) ?? []
  )

  // Pattern learning status polling
  const [learningStatus, setLearningStatus] = useState<PatternLearningStatus | null>(null)
  const [isPolling, setIsPolling] = useState(false)

  const pollLearningStatus = useCallback(async () => {
    try {
      const status = await getPatternLearningStatus()
      setLearningStatus(status)

      if (status.in_progress) {
        // Continue polling
        setIsPolling(true)
      } else {
        // Done - show result and refresh patterns
        setIsPolling(false)
        if (status.status === "complete") {
          toast.success(`Learned ${status.patterns_learned} patterns from ${status.groups_completed} group(s) (${status.avg_coverage.toFixed(0)}% avg coverage)`)
          queryClient.invalidateQueries({ queryKey: ["ai", "patterns"] })
          setSelectedGroupIds(new Set())
        } else if (status.status === "error") {
          toast.error(status.error || "Pattern learning failed")
        } else if (status.status === "aborted") {
          toast.warning("Pattern learning was aborted")
          queryClient.invalidateQueries({ queryKey: ["ai", "patterns"] })
        }
      }
    } catch (err) {
      console.error("Failed to poll learning status:", err)
    }
  }, [queryClient])

  // Poll for status when learning is in progress
  useEffect(() => {
    if (!isPolling) return

    const interval = setInterval(pollLearningStatus, 1000)
    return () => clearInterval(interval)
  }, [isPolling, pollLearningStatus])

  // Check for in-progress learning on mount
  useEffect(() => {
    pollLearningStatus()
  }, [pollLearningStatus])

  // Start learning mutation
  const startLearnMutation = useMutation({
    mutationFn: (groupIds: number[]) => startPatternLearning(undefined, groupIds),
    onSuccess: (data) => {
      if (data.success) {
        toast.info(data.message)
        setIsPolling(true)
        pollLearningStatus()
      } else {
        toast.error("Failed to start pattern learning")
      }
    },
    onError: (err: Error) => toast.error(err.message),
  })

  // Abort learning mutation
  const abortLearnMutation = useMutation({
    mutationFn: abortPatternLearning,
    onSuccess: (data) => {
      if (data.success) {
        toast.info("Aborting pattern learning...")
      }
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const isLearning = learningStatus?.in_progress ?? false

  const deleteMutation = useMutation({
    mutationFn: (patternId: string) => deleteAIPattern(patternId),
    onSuccess: () => {
      toast.success("Pattern deleted")
      queryClient.invalidateQueries({ queryKey: ["ai", "patterns"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const deleteGroupMutation = useMutation({
    mutationFn: (groupId: number) => deleteGroupPatterns(groupId),
    onSuccess: (data) => {
      toast.success(`Deleted ${data.patterns_deleted} patterns`)
      queryClient.invalidateQueries({ queryKey: ["ai", "patterns"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const updateMutation = useMutation({
    mutationFn: ({ patternId, data }: { patternId: string; data: { regex?: string; description?: string } }) =>
      updatePattern(patternId, data),
    onSuccess: () => {
      toast.success("Pattern updated")
      setEditingPattern(null)
      queryClient.invalidateQueries({ queryKey: ["ai", "patterns"] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const status = statusQuery.data
  const patterns = patternsQuery.data?.patterns ?? []
  const allGroups = groupsQuery.data?.groups ?? []

  // Filter patterns by selected group (for viewing)
  const filteredPatterns = filterGroupId
    ? patterns.filter(p => p.group_id === filterGroupId)
    : patterns

  // Get unique groups that have patterns
  const groupsWithPatterns = [...new Set(patterns.map(p => p.group_id).filter(Boolean))] as number[]

  // Multi-select helpers
  const toggleGroup = (groupId: number) => {
    setSelectedGroupIds(prev => {
      const next = new Set(prev)
      if (next.has(groupId)) {
        next.delete(groupId)
      } else {
        next.add(groupId)
      }
      return next
    })
  }

  const selectAll = () => {
    setSelectedGroupIds(new Set(allGroups.map(g => g.id)))
  }

  const selectNone = () => {
    setSelectedGroupIds(new Set())
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Brain className="h-6 w-6" />
            AI Pattern Management
          </h1>
          <p className="text-muted-foreground">
            Manage AI-learned regex patterns for stream parsing
          </p>
        </div>
        <div className="flex items-center gap-2">
          {status?.available ? (
            <Badge variant="success" className="gap-1">
              <CheckCircle className="h-3 w-3" /> Ollama Connected
            </Badge>
          ) : status?.enabled ? (
            <Badge variant="destructive" className="gap-1">
              <AlertTriangle className="h-3 w-3" /> Ollama Unavailable
            </Badge>
          ) : (
            <Badge variant="secondary">AI Disabled</Badge>
          )}
        </div>
      </div>

      {/* Status Card */}
      {status && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-lg">Status</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-4 gap-4 text-sm">
              <div>
                <span className="text-muted-foreground">Ollama URL:</span>
                <p className="font-mono text-xs">{status.ollama_url}</p>
              </div>
              <div>
                <span className="text-muted-foreground">Model:</span>
                <p className="font-medium">{status.model}</p>
              </div>
              <div>
                <span className="text-muted-foreground">Total Patterns:</span>
                <p className="font-medium">{patterns.length}</p>
              </div>
              <div>
                <span className="text-muted-foreground">Groups with Patterns:</span>
                <p className="font-medium">{groupsWithPatterns.length}</p>
              </div>
            </div>
            {status.error && (
              <div className="mt-2 p-2 bg-destructive/10 border border-destructive/20 rounded text-sm text-destructive">
                {status.error}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Learn Patterns Card */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-lg">Learn Patterns</CardTitle>
          <CardDescription>
            Select event groups to analyze and generate regex patterns. This may take a few minutes per group.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Selection controls */}
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={selectAll}>
              Select All
            </Button>
            <Button variant="outline" size="sm" onClick={selectNone}>
              Select None
            </Button>
            <span className="text-sm text-muted-foreground ml-2">
              {selectedGroupIds.size} of {allGroups.length} groups selected
            </span>
          </div>

          {/* Groups checklist */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2 max-h-64 overflow-y-auto border rounded-md p-3">
            {allGroups.map(g => (
              <label
                key={g.id}
                className={cn(
                  "flex items-center gap-2 p-2 rounded-md cursor-pointer hover:bg-accent text-sm",
                  selectedGroupIds.has(g.id) && "bg-accent"
                )}
              >
                <input
                  type="checkbox"
                  checked={selectedGroupIds.has(g.id)}
                  onChange={() => toggleGroup(g.id)}
                  className="rounded"
                />
                <span className="truncate">{g.name}</span>
                {groupsWithPatterns.includes(g.id) && (
                  <Badge variant="secondary" className="text-xs ml-auto">
                    {patterns.filter(p => p.group_id === g.id).length}
                  </Badge>
                )}
              </label>
            ))}
          </div>

          {/* Action buttons / Progress display */}
          {isLearning ? (
            <div className="space-y-3 p-4 border rounded-lg bg-muted/30">
              {/* Progress header */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin text-primary" />
                  <span className="font-medium">Learning Patterns...</span>
                </div>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => abortLearnMutation.mutate()}
                  disabled={abortLearnMutation.isPending || learningStatus?.status === "aborted"}
                >
                  <Square className="h-3 w-3 mr-1" />
                  Abort
                </Button>
              </div>

              {/* Progress bar */}
              <div className="space-y-1">
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">
                    Group {learningStatus?.current_group ?? 0} of {learningStatus?.total_groups ?? 0}
                  </span>
                  <span className="font-medium">{learningStatus?.percent ?? 0}%</span>
                </div>
                <div className="h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary transition-all duration-300"
                    style={{ width: `${learningStatus?.percent ?? 0}%` }}
                  />
                </div>
              </div>

              {/* Current group and ETA */}
              <div className="flex items-center justify-between text-sm text-muted-foreground">
                <span className="truncate max-w-[60%]">
                  {learningStatus?.current_group_name || learningStatus?.message || "Starting..."}
                </span>
                {learningStatus?.eta_seconds != null && learningStatus.eta_seconds > 0 && (
                  <span>ETA: {formatETA(learningStatus.eta_seconds)}</span>
                )}
              </div>

              {/* Stats */}
              {(learningStatus?.patterns_learned ?? 0) > 0 && (
                <div className="text-sm">
                  <span className="text-muted-foreground">Patterns learned so far: </span>
                  <span className="font-medium">{learningStatus?.patterns_learned}</span>
                </div>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Button
                onClick={() => startLearnMutation.mutate([...selectedGroupIds])}
                disabled={selectedGroupIds.size === 0 || startLearnMutation.isPending || !status?.available}
              >
                {startLearnMutation.isPending ? (
                  <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                ) : (
                  <Brain className="h-4 w-4 mr-1" />
                )}
                Learn Patterns for {selectedGroupIds.size} Group{selectedGroupIds.size !== 1 ? "s" : ""}
              </Button>
              {selectedGroupIds.size > 0 && [...selectedGroupIds].some(id => groupsWithPatterns.includes(id)) && (
                <Button
                  variant="outline"
                  onClick={() => {
                    // Delete patterns for all selected groups that have patterns
                    const toDelete = [...selectedGroupIds].filter(id => groupsWithPatterns.includes(id))
                    toDelete.forEach(id => deleteGroupMutation.mutate(id))
                  }}
                  disabled={deleteGroupMutation.isPending}
                >
                  {deleteGroupMutation.isPending ? (
                    <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                  ) : (
                    <Trash2 className="h-4 w-4 mr-1" />
                  )}
                  Clear Selected Patterns
                </Button>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Patterns List */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-lg">Learned Patterns</CardTitle>
              <CardDescription>
                {filteredPatterns.length} pattern{filteredPatterns.length !== 1 ? "s" : ""}
                {filterGroupId && ` for ${groupMap.get(filterGroupId) || `Group ${filterGroupId}`}`}
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <select
                className="px-3 py-1.5 border rounded-md text-sm bg-background"
                value={filterGroupId ?? ""}
                onChange={(e) => setFilterGroupId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">All Groups</option>
                {groupsWithPatterns.map(gid => (
                  <option key={gid} value={gid}>{groupMap.get(gid) || `Group ${gid}`}</option>
                ))}
              </select>
              <Button
                variant="outline"
                size="sm"
                onClick={() => patternsQuery.refetch()}
                disabled={patternsQuery.isFetching}
              >
                {patternsQuery.isFetching ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4" />
                )}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {patternsQuery.isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : filteredPatterns.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              No patterns found. Select a group and click "Learn Patterns" to get started.
            </div>
          ) : (
            <div className="space-y-2">
              {filteredPatterns.map(pattern => (
                <PatternCard
                  key={pattern.pattern_id}
                  pattern={pattern}
                  groupName={pattern.group_id ? groupMap.get(pattern.group_id) : undefined}
                  onEdit={() => setEditingPattern(pattern)}
                  onDelete={() => deleteMutation.mutate(pattern.pattern_id)}
                  isDeleting={deleteMutation.isPending && deleteMutation.variables === pattern.pattern_id}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Edit Modal */}
      {editingPattern && (
        <EditPatternModal
          pattern={editingPattern}
          onClose={() => setEditingPattern(null)}
          onSave={(data) => updateMutation.mutate({ patternId: editingPattern.pattern_id, data })}
          isSaving={updateMutation.isPending}
        />
      )}
    </div>
  )
}
