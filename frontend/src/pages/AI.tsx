import { useState } from "react"
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
  learnPatterns,
  deleteAIPattern,
  deleteGroupPatterns,
  type AIPattern,
} from "@/api/settings"
import { useGroups } from "@/hooks/useGroups"

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
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null)
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

  // Mutations
  const learnMutation = useMutation({
    mutationFn: (groupId: number) => learnPatterns(groupId),
    onSuccess: (data) => {
      if (data.success) {
        toast.success(`Learned ${data.patterns_learned} patterns (${data.coverage_percent.toFixed(0)}% coverage)`)
        queryClient.invalidateQueries({ queryKey: ["ai", "patterns"] })
      } else {
        toast.error(data.error || "Failed to learn patterns")
      }
    },
    onError: (err: Error) => toast.error(err.message),
  })

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

  // Filter patterns by selected group
  const filteredPatterns = selectedGroupId
    ? patterns.filter(p => p.group_id === selectedGroupId)
    : patterns

  // Get unique groups that have patterns
  const groupsWithPatterns = [...new Set(patterns.map(p => p.group_id).filter(Boolean))] as number[]

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
            Analyze streams from an event group to generate regex patterns
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-end gap-4">
            <div className="flex-1 max-w-sm">
              <Label htmlFor="learn-group">Event Group</Label>
              <select
                id="learn-group"
                className="w-full mt-1 px-3 py-2 border rounded-md text-sm bg-background"
                value={selectedGroupId ?? ""}
                onChange={(e) => setSelectedGroupId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">Select a group...</option>
                {groupsQuery.data?.groups?.map(g => (
                  <option key={g.id} value={g.id}>{g.name}</option>
                ))}
              </select>
            </div>
            <Button
              onClick={() => selectedGroupId && learnMutation.mutate(selectedGroupId)}
              disabled={!selectedGroupId || learnMutation.isPending || !status?.available}
            >
              {learnMutation.isPending ? (
                <Loader2 className="h-4 w-4 mr-1 animate-spin" />
              ) : (
                <Brain className="h-4 w-4 mr-1" />
              )}
              Learn Patterns
            </Button>
            {selectedGroupId && groupsWithPatterns.includes(selectedGroupId) && (
              <Button
                variant="outline"
                onClick={() => deleteGroupMutation.mutate(selectedGroupId)}
                disabled={deleteGroupMutation.isPending}
              >
                {deleteGroupMutation.isPending ? (
                  <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                ) : (
                  <Trash2 className="h-4 w-4 mr-1" />
                )}
                Clear Group Patterns
              </Button>
            )}
          </div>
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
                {selectedGroupId && ` for ${groupMap.get(selectedGroupId) || `Group ${selectedGroupId}`}`}
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <select
                className="px-3 py-1.5 border rounded-md text-sm bg-background"
                value={selectedGroupId ?? ""}
                onChange={(e) => setSelectedGroupId(e.target.value ? Number(e.target.value) : null)}
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
