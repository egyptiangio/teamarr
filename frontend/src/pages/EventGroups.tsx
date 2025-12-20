import { useState, useMemo } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { useQuery } from "@tanstack/react-query"
import {
  Play,
  Trash2,
  Pencil,
  Power,
  PowerOff,
  Loader2,
  Download,
  X,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
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
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import {
  useGroups,
  useDeleteGroup,
  useToggleGroup,
  useProcessGroup,
} from "@/hooks/useGroups"
import { useTemplates } from "@/hooks/useTemplates"
import type { EventGroup } from "@/api/types"

// Fetch leagues for logo lookup
async function fetchLeagues(): Promise<{ slug: string; name: string; logo_url: string | null }[]> {
  const response = await fetch("/api/v1/cache/leagues")
  if (!response.ok) return []
  const data = await response.json()
  return data.leagues || []
}

export function EventGroups() {
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useGroups(true)
  const { data: templates } = useTemplates()
  const { data: cachedLeagues } = useQuery({ queryKey: ["leagues"], queryFn: fetchLeagues })
  const deleteMutation = useDeleteGroup()
  const toggleMutation = useToggleGroup()
  const processMutation = useProcessGroup()

  // Create league logo lookup map
  const leagueLogos = useMemo(() => {
    const map: Record<string, string> = {}
    if (cachedLeagues) {
      for (const league of cachedLeagues) {
        if (league.logo_url) {
          map[league.slug] = league.logo_url
        }
      }
    }
    return map
  }, [cachedLeagues])

  // Selection state
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())

  // Filter state
  const [leagueFilter, setLeagueFilter] = useState("")
  const [statusFilter, setStatusFilter] = useState<"all" | "enabled" | "disabled">("all")

  const [deleteConfirm, setDeleteConfirm] = useState<EventGroup | null>(null)
  const [showBulkDelete, setShowBulkDelete] = useState(false)

  // Get unique leagues from groups for filter dropdown
  const uniqueLeagues = useMemo(() => {
    if (!data?.groups) return []
    const leagues = new Set<string>()
    data.groups.forEach((g) => g.leagues.forEach((l) => leagues.add(l)))
    return [...leagues].sort()
  }, [data?.groups])

  // Filter groups
  const filteredGroups = useMemo(() => {
    if (!data?.groups) return []
    return data.groups.filter((group) => {
      if (leagueFilter && !group.leagues.includes(leagueFilter)) return false
      if (statusFilter === "enabled" && !group.enabled) return false
      if (statusFilter === "disabled" && group.enabled) return false
      return true
    })
  }, [data?.groups, leagueFilter, statusFilter])

  // Calculate stats
  const stats = useMemo(() => {
    if (!data?.groups) return { total: 0, enabled: 0, totalStreams: 0, totalChannels: 0 }
    const groups = data.groups
    return {
      total: groups.length,
      enabled: groups.filter((g) => g.enabled).length,
      totalStreams: groups.reduce((sum, g) => sum + (g.total_stream_count || 0), 0),
      totalChannels: groups.reduce((sum, g) => sum + (g.channel_count || 0), 0),
    }
  }, [data?.groups])

  const handleDelete = async () => {
    if (!deleteConfirm) return

    try {
      const result = await deleteMutation.mutateAsync(deleteConfirm.id)
      toast.success(result.message)
      setDeleteConfirm(null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete group")
    }
  }

  const handleToggle = async (group: EventGroup) => {
    try {
      await toggleMutation.mutateAsync({
        groupId: group.id,
        enabled: !group.enabled,
      })
      toast.success(`${group.enabled ? "Disabled" : "Enabled"} group "${group.name}"`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to toggle group")
    }
  }

  const handleProcess = async (group: EventGroup) => {
    try {
      const result = await processMutation.mutateAsync(group.id)
      toast.success(
        `Processed "${group.name}": ${result.channels_created} created, ${result.streams_matched} matched`
      )
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to process group")
    }
  }

  // Selection handlers
  const toggleSelect = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const toggleSelectAll = () => {
    if (selectedIds.size === filteredGroups.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(filteredGroups.map((g) => g.id)))
    }
  }

  // Bulk actions
  const handleBulkToggle = async (enable: boolean) => {
    const groupsToToggle = filteredGroups.filter(
      (g) => selectedIds.has(g.id) && g.enabled !== enable
    )
    for (const group of groupsToToggle) {
      try {
        await toggleMutation.mutateAsync({ groupId: group.id, enabled: enable })
      } catch (err) {
        console.error(`Failed to toggle group ${group.name}:`, err)
      }
    }
    toast.success(`${enable ? "Enabled" : "Disabled"} ${groupsToToggle.length} groups`)
    setSelectedIds(new Set())
  }

  const handleBulkDelete = async () => {
    let deleted = 0
    for (const id of selectedIds) {
      try {
        await deleteMutation.mutateAsync(id)
        deleted++
      } catch (err) {
        console.error(`Failed to delete group ${id}:`, err)
      }
    }
    toast.success(`Deleted ${deleted} groups`)
    setSelectedIds(new Set())
    setShowBulkDelete(false)
  }

  const clearFilters = () => {
    setLeagueFilter("")
    setStatusFilter("all")
  }

  const hasActiveFilters = leagueFilter || statusFilter !== "all"

  if (error) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">Event Groups</h1>
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-destructive">
              Error loading groups: {error.message}
            </p>
            <Button className="mt-4" onClick={() => refetch()}>
              Retry
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Event Groups</h1>
          <p className="text-muted-foreground">
            Configure event-based EPG from M3U stream groups
          </p>
        </div>
        <Button onClick={() => navigate("/event-groups/import")}>
          <Download className="h-4 w-4 mr-1.5" />
          Import Groups
        </Button>
      </div>

      {/* Stats Tiles */}
      {data?.groups && data.groups.length > 0 && (
        <div className="grid grid-cols-4 gap-3">
          <Card className="p-3">
            <div className="text-2xl font-bold">{stats.total}</div>
            <div className="text-xs text-muted-foreground uppercase tracking-wide">Groups</div>
          </Card>
          <Card className="p-3">
            <div className="text-2xl font-bold">{stats.enabled}</div>
            <div className="text-xs text-muted-foreground uppercase tracking-wide">Enabled</div>
          </Card>
          <Card className="p-3">
            <div className="text-2xl font-bold">{stats.totalStreams}</div>
            <div className="text-xs text-muted-foreground uppercase tracking-wide">Total Streams</div>
          </Card>
          <Card className="p-3">
            <div className="text-2xl font-bold">{stats.totalChannels}</div>
            <div className="text-xs text-muted-foreground uppercase tracking-wide">Channels</div>
          </Card>
        </div>
      )}

      {/* Filters */}
      <Card>
        <CardContent className="py-3">
          <div className="flex items-center gap-3">
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">League</Label>
              <Select
                value={leagueFilter}
                onChange={(e) => setLeagueFilter(e.target.value)}
                className="w-40"
              >
                <option value="">All leagues</option>
                {uniqueLeagues.map((league) => (
                  <option key={league} value={league}>
                    {league.toUpperCase()}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs text-muted-foreground">Status</Label>
              <Select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
                className="w-32"
              >
                <option value="all">All</option>
                <option value="enabled">Enabled</option>
                <option value="disabled">Disabled</option>
              </Select>
            </div>
            {hasActiveFilters && (
              <Button variant="ghost" size="sm" onClick={clearFilters} className="mt-5">
                <X className="h-4 w-4 mr-1" />
                Clear
              </Button>
            )}
            <div className="flex-1" />
            <div className="text-sm text-muted-foreground mt-5">
              {filteredGroups.length} of {data?.groups.length ?? 0} groups
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Bulk Actions */}
      {selectedIds.size > 0 && (
        <Card className="border-primary">
          <CardContent className="py-2">
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium">
                {selectedIds.size} selected
              </span>
              <Button variant="outline" size="sm" onClick={() => handleBulkToggle(true)}>
                <Power className="h-4 w-4 mr-1" />
                Enable
              </Button>
              <Button variant="outline" size="sm" onClick={() => handleBulkToggle(false)}>
                <PowerOff className="h-4 w-4 mr-1" />
                Disable
              </Button>
              <Button variant="destructive" size="sm" onClick={() => setShowBulkDelete(true)}>
                <Trash2 className="h-4 w-4 mr-1" />
                Delete
              </Button>
              <div className="flex-1" />
              <Button variant="ghost" size="sm" onClick={() => setSelectedIds(new Set())}>
                Clear selection
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Groups Table */}
      <Card>
        <CardHeader>
          <CardTitle>Groups ({filteredGroups.length})</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : filteredGroups.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              {data?.groups.length === 0
                ? "No event groups configured. Create one to get started."
                : "No groups match the current filters."}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10">
                    <Checkbox
                      checked={selectedIds.size === filteredGroups.length && filteredGroups.length > 0}
                      onCheckedChange={toggleSelectAll}
                    />
                  </TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>Leagues</TableHead>
                  <TableHead>Template</TableHead>
                  <TableHead>Mode</TableHead>
                  <TableHead className="text-center">Channels</TableHead>
                  <TableHead className="w-16">Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredGroups.map((group) => (
                  <TableRow key={group.id}>
                    <TableCell>
                      <Checkbox
                        checked={selectedIds.has(group.id)}
                        onCheckedChange={() => toggleSelect(group.id)}
                      />
                    </TableCell>
                    <TableCell className="font-medium">{group.name}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {group.leagues.slice(0, 3).map((league) => (
                          leagueLogos[league] ? (
                            <img
                              key={league}
                              src={leagueLogos[league]}
                              alt={league.toUpperCase()}
                              title={league.toUpperCase()}
                              className="h-6 w-auto object-contain"
                            />
                          ) : (
                            <Badge key={league} variant="secondary">
                              {league.toUpperCase()}
                            </Badge>
                          )
                        ))}
                        {group.leagues.length > 3 && (
                          <Badge variant="outline">
                            +{group.leagues.length - 3}
                          </Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <span className="text-muted-foreground text-sm">
                        {group.template_id
                          ? templates?.find((t) => t.id === group.template_id)?.name ?? `#${group.template_id}`
                          : "—"}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge variant={group.channel_assignment_mode === "auto" ? "secondary" : "outline"}>
                        {group.channel_assignment_mode}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-center">{group.channel_count ?? 0}</TableCell>
                    <TableCell>
                      <Badge variant={group.enabled ? "default" : "secondary"}>
                        {group.enabled ? "On" : "Off"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => handleProcess(group)}
                          disabled={processMutation.isPending}
                          title="Process group"
                        >
                          {processMutation.isPending &&
                          processMutation.variables === group.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Play className="h-4 w-4" />
                          )}
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => handleToggle(group)}
                          title={group.enabled ? "Disable" : "Enable"}
                        >
                          {group.enabled ? (
                            <PowerOff className="h-4 w-4" />
                          ) : (
                            <Power className="h-4 w-4" />
                          )}
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => navigate(`/event-groups/${group.id}`)}
                          title="Edit"
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => setDeleteConfirm(group)}
                          title="Delete"
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={deleteConfirm !== null}
        onOpenChange={(open) => !open && setDeleteConfirm(null)}
      >
        <DialogContent onClose={() => setDeleteConfirm(null)}>
          <DialogHeader>
            <DialogTitle>Delete Event Group</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete "{deleteConfirm?.name}"? This will
              also delete all {deleteConfirm?.channel_count ?? 0} managed
              channels associated with this group.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteConfirm(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending && (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              )}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Bulk Delete Confirmation Dialog */}
      <Dialog open={showBulkDelete} onOpenChange={setShowBulkDelete}>
        <DialogContent onClose={() => setShowBulkDelete(false)}>
          <DialogHeader>
            <DialogTitle>Delete {selectedIds.size} Groups</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete {selectedIds.size} groups? This will
              also delete all managed channels associated with these groups.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowBulkDelete(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleBulkDelete}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending && (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              )}
              Delete {selectedIds.size} Groups
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
