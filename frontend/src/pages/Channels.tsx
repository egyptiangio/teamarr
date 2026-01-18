import { useState, useMemo } from "react"
import { toast } from "sonner"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  Trash2,
  Loader2,
  RefreshCw,
  Clock,
  Tv,
  Search,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
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
import { FilterSelect } from "@/components/ui/filter-select"
import {
  useManagedChannels,
  useDeleteManagedChannel,
  usePendingDeletions,
  useReconciliationStatus,
} from "@/hooks/useChannels"
import { useGroups } from "@/hooks/useGroups"
import { useQuery } from "@tanstack/react-query"
import { getLeagues } from "@/api/teams"
import {
  deleteDispatcharrChannel,
  deleteManagedChannel,
  previewResetChannels,
  executeResetChannels,
} from "@/api/channels"
import type { ManagedChannel, ResetChannelInfo } from "@/api/channels"
import { getLeagueDisplayName } from "@/lib/utils"

function formatDateTime(dateStr: string | null): string {
  if (!dateStr) return "-"
  const date = new Date(dateStr)
  return date.toLocaleString()
}

function formatRelativeTime(dateStr: string | null): string {
  if (!dateStr) return "-"
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = date.getTime() - now.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMins / 60)

  if (diffMs < 0) {
    const absMins = Math.abs(diffMins)
    const absHours = Math.abs(diffHours)
    if (absMins < 60) return `${absMins}m ago`
    if (absHours < 24) return `${absHours}h ago`
    return formatDateTime(dateStr)
  }

  if (diffMins < 60) return `in ${diffMins}m`
  if (diffHours < 24) return `in ${diffHours}h`
  return formatDateTime(dateStr)
}

function getSyncStatusBadge(status: string) {
  switch (status) {
    case "in_sync":
      return <Badge variant="success">In Sync</Badge>
    case "pending":
      return <Badge variant="secondary">Pending</Badge>
    case "created":
      return <Badge variant="info">Created</Badge>
    case "drifted":
      return <Badge variant="warning">Drifted</Badge>
    case "orphaned":
      return <Badge variant="destructive">Orphaned</Badge>
    case "error":
      return <Badge variant="destructive">Error</Badge>
    default:
      return <Badge variant="outline">{status}</Badge>
  }
}

export function Channels() {
  // Filter states
  const [groupFilter, setGroupFilter] = useState<string>("")
  const [leagueFilter, setLeagueFilter] = useState<string>("")
  const [statusFilter, setStatusFilter] = useState<string>("")
  const [includeDeleted, setIncludeDeleted] = useState(false)

  // UI states
  const [deleteConfirm, setDeleteConfirm] = useState<ManagedChannel | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [bulkDeleteConfirm, setBulkDeleteConfirm] = useState(false)
  const [orphansModalOpen, setOrphansModalOpen] = useState(false)
  const [deletingOrphanId, setDeletingOrphanId] = useState<number | null>(null)
  const [deletingAllOrphans, setDeletingAllOrphans] = useState(false)
  const [resetModalOpen, setResetModalOpen] = useState(false)
  const [resetLoading, setResetLoading] = useState(false)
  const [resetExecuting, setResetExecuting] = useState(false)
  const [resetChannels, setResetChannels] = useState<ResetChannelInfo[]>([])
  const [deletedCollapsed, setDeletedCollapsed] = useState(true)

  const queryClient = useQueryClient()

  const { data: groups } = useGroups()
  const { data: leaguesData } = useQuery({
    queryKey: ["leagues"],
    queryFn: () => getLeagues(),
    staleTime: 1000 * 60 * 60, // 1 hour
  })
  const selectedGroupId = groupFilter ? parseInt(groupFilter) : undefined
  const {
    data: channelsData,
    isLoading,
    error,
    refetch,
  } = useManagedChannels(selectedGroupId, includeDeleted)
  const { data: pendingData } = usePendingDeletions()

  // Fetch all channels including deleted for the Recently Deleted section
  const { data: allChannelsData } = useManagedChannels(undefined, true)

  // Filter to get only deleted channels (last 50, sorted by deletion time)
  const deletedChannels = useMemo(() => {
    if (!allChannelsData?.channels) return []
    return allChannelsData.channels
      .filter((ch) => ch.deleted_at !== null)
      .sort((a, b) => {
        const dateA = new Date(a.deleted_at!).getTime()
        const dateB = new Date(b.deleted_at!).getTime()
        return dateB - dateA // Most recent first
      })
      .slice(0, 50)
  }, [allChannelsData])

  const deleteMutation = useDeleteManagedChannel()

  // Fetch reconciliation status (for orphans)
  const {
    data: reconciliationData,
    isLoading: reconciliationLoading,
    refetch: refetchReconciliation,
  } = useReconciliationStatus()

  // Filter orphan_dispatcharr issues
  const orphanChannels = useMemo(() => {
    if (!reconciliationData?.issues_found) return []
    return reconciliationData.issues_found.filter(
      (issue) => issue.issue_type === "orphan_dispatcharr"
    )
  }, [reconciliationData])

  // Extract unique filter values from data
  const { leagues, statuses } = useMemo(() => {
    const channels = channelsData?.channels ?? []
    const leagueSet = new Set<string>()
    const statusSet = new Set<string>()
    for (const ch of channels) {
      if (ch.league) leagueSet.add(ch.league)
      if (ch.sync_status) statusSet.add(ch.sync_status)
    }
    return {
      leagues: Array.from(leagueSet).sort(),
      statuses: Array.from(statusSet).sort(),
    }
  }, [channelsData])

  // Group ID -> name lookup
  const groupLookup = useMemo(() => {
    const map = new Map<number, string>()
    for (const g of groups?.groups ?? []) {
      map.set(g.id, g.name)
    }
    return map
  }, [groups])

  // League slug -> display name lookup (uses {league} variable resolution: alias first, then name)
  const getLeagueDisplay = useMemo(() => {
    const map = new Map<string, string>()
    for (const league of leaguesData?.leagues ?? []) {
      // {league} variable uses league_alias if available, otherwise display_name (name field)
      map.set(league.slug, getLeagueDisplayName(league, true))
    }
    return (slug: string | null | undefined) => {
      if (!slug) return "-"
      return map.get(slug) ?? slug.toUpperCase()
    }
  }, [leaguesData])

  // Apply client-side filters
  const filteredChannels = useMemo(() => {
    let channels = channelsData?.channels ?? []
    if (leagueFilter) {
      channels = channels.filter((ch) => ch.league === leagueFilter)
    }
    if (statusFilter) {
      channels = channels.filter((ch) => ch.sync_status === statusFilter)
    }
    return channels
  }, [channelsData, leagueFilter, statusFilter])

  // Mutation for deleting orphan channel
  const deleteOrphanMutation = useMutation({
    mutationFn: deleteDispatcharrChannel,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["reconciliation"] })
      refetchReconciliation()
    },
  })

  // Mutation for bulk delete
  const bulkDeleteMutation = useMutation({
    mutationFn: async (ids: number[]) => {
      const results = await Promise.allSettled(
        ids.map((id) => deleteManagedChannel(id))
      )
      const succeeded = results.filter((r) => r.status === "fulfilled").length
      const failed = results.filter((r) => r.status === "rejected").length
      return { succeeded, failed }
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["managed-channels"] })
      refetch()
      toast.success(`Deleted ${result.succeeded} channel(s)${result.failed > 0 ? `, ${result.failed} failed` : ""}`)
      setSelectedIds(new Set())
      setBulkDeleteConfirm(false)
    },
    onError: () => {
      toast.error("Bulk delete failed")
    },
  })

  const handleDelete = async () => {
    if (!deleteConfirm) return
    try {
      const result = await deleteMutation.mutateAsync(deleteConfirm.id)
      if (result.success) {
        toast.success(result.message)
      } else {
        toast.error(result.message)
      }
      setDeleteConfirm(null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete channel")
    }
  }

  const handleDeleteOrphan = async (channelId: number) => {
    setDeletingOrphanId(channelId)
    try {
      await deleteOrphanMutation.mutateAsync(channelId)
      toast.success("Orphan channel deleted from Dispatcharr")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete orphan")
    } finally {
      setDeletingOrphanId(null)
    }
  }

  const handleDeleteAllOrphans = async () => {
    const channelIds = orphanChannels
      .map((o) => o.dispatcharr_channel_id)
      .filter((id): id is number => id !== null && id !== undefined)

    if (channelIds.length === 0) return

    setDeletingAllOrphans(true)
    try {
      const results = await Promise.allSettled(
        channelIds.map((id) => deleteOrphanMutation.mutateAsync(id))
      )
      const succeeded = results.filter((r) => r.status === "fulfilled").length
      const failed = results.filter((r) => r.status === "rejected").length

      if (failed === 0) {
        toast.success(`Deleted ${succeeded} orphan channels`)
      } else {
        toast.warning(`Deleted ${succeeded}, failed ${failed}`)
      }
      refetchReconciliation()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete orphans")
    } finally {
      setDeletingAllOrphans(false)
    }
  }

  const handleOpenResetModal = async () => {
    setResetModalOpen(true)
    setResetLoading(true)
    try {
      const response = await previewResetChannels()
      setResetChannels(response.channels)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load reset preview")
    } finally {
      setResetLoading(false)
    }
  }

  const handleExecuteReset = async () => {
    setResetExecuting(true)
    try {
      const response = await executeResetChannels()
      if (response.success) {
        toast.success(`Deleted ${response.deleted_count} channels from Dispatcharr`)
      } else {
        toast.warning(
          `Deleted ${response.deleted_count}, failed ${response.error_count}`
        )
      }
      setResetModalOpen(false)
      refetch()
      queryClient.invalidateQueries({ queryKey: ["reconciliation"] })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to reset channels")
    } finally {
      setResetExecuting(false)
    }
  }

  const handleBulkDelete = () => {
    bulkDeleteMutation.mutate(Array.from(selectedIds))
  }

  // Selection handlers
  const toggleSelect = (id: number) => {
    const newSet = new Set(selectedIds)
    if (newSet.has(id)) {
      newSet.delete(id)
    } else {
      newSet.add(id)
    }
    setSelectedIds(newSet)
  }

  const toggleSelectAll = () => {
    if (filteredChannels.length === 0) return
    if (selectedIds.size === filteredChannels.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(filteredChannels.map((c) => c.id)))
    }
  }

  const isAllSelected =
    filteredChannels.length > 0 &&
    selectedIds.size === filteredChannels.length

  if (error) {
    return (
      <div className="space-y-2">
        <h1 className="text-xl font-bold">Managed Channels</h1>
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-destructive">Error loading channels: {error.message}</p>
            <Button className="mt-4" onClick={() => refetch()}>
              Retry
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">Managed Channels</h1>
          <p className="text-sm text-muted-foreground">
            Event-based channels managed by Teamarr
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              refetchReconciliation()
              setOrphansModalOpen(true)
            }}
          >
            <Search className="h-4 w-4 mr-1" />
            Find Orphans
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={handleOpenResetModal}
          >
            <AlertTriangle className="h-4 w-4 mr-1" />
            Reset All
          </Button>
        </div>
      </div>

      {/* Fixed Batch Operations Bar */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-0 left-0 right-0 z-50 border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
          <div className="container max-w-screen-xl mx-auto px-4 py-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">
                {selectedIds.size} channel{selectedIds.size > 1 ? "s" : ""} selected
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setSelectedIds(new Set())}
                >
                  Clear Selection
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setBulkDeleteConfirm(true)}
                >
                  <Trash2 className="h-4 w-4 mr-1" />
                  Delete Selected
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Pending Deletions Info */}
      {pendingData && pendingData.count > 0 && (
        <Card className="bg-muted/50">
          <CardContent className="py-3">
            <div className="flex items-center gap-2">
              <Clock className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm">
                <strong>{pendingData.count}</strong> channel{pendingData.count > 1 ? "s" : ""} pending deletion
                {pendingData.channels[0] && (
                  <span className="text-muted-foreground">
                    {" "}— Next: {pendingData.channels[0].channel_name}
                  </span>
                )}
              </span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Channels List */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2">
              <Tv className="h-5 w-5" />
              Channels ({filteredChannels.length}
              {filteredChannels.length !== (channelsData?.channels.length ?? 0) && (
                <span className="text-muted-foreground font-normal">
                  {" "}of {channelsData?.channels.length ?? 0}
                </span>
              )}
              )
            </CardTitle>
            <div
              className="flex items-center gap-2 text-sm font-normal cursor-pointer"
              onClick={() => setIncludeDeleted(!includeDeleted)}
            >
              <Checkbox
                checked={includeDeleted}
                onCheckedChange={(checked) => setIncludeDeleted(!!checked)}
              />
              <span>Show deleted</span>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : (channelsData?.channels.length ?? 0) === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              No managed channels found.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10">
                    <Checkbox
                      checked={isAllSelected}
                      onCheckedChange={toggleSelectAll}
                    />
                  </TableHead>
                  <TableHead>Channel</TableHead>
                  <TableHead>Event</TableHead>
                  <TableHead className="w-28">Group</TableHead>
                  <TableHead className="w-20">League</TableHead>
                  <TableHead className="w-20">Status</TableHead>
                  <TableHead className="w-24">Delete At</TableHead>
                  <TableHead className="w-16 text-right">Actions</TableHead>
                </TableRow>
                {/* Filter row */}
                <TableRow className="border-b-2 border-border">
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                  <TableHead className="py-0.5 pb-1.5">
                    <FilterSelect
                      value={groupFilter}
                      onChange={setGroupFilter}
                      options={[
                        { value: "", label: "All" },
                        ...(groups?.groups?.map((g) => ({
                          value: g.id.toString(),
                          label: g.name,
                        })) ?? []),
                      ]}
                    />
                  </TableHead>
                  <TableHead className="py-0.5 pb-1.5">
                    <FilterSelect
                      value={leagueFilter}
                      onChange={setLeagueFilter}
                      options={[
                        { value: "", label: "All" },
                        ...leagues.map((l) => ({ value: l, label: l })),
                      ]}
                    />
                  </TableHead>
                  <TableHead className="py-0.5 pb-1.5">
                    <FilterSelect
                      value={statusFilter}
                      onChange={setStatusFilter}
                      options={[
                        { value: "", label: "All" },
                        ...statuses.map((s) => ({ value: s, label: s })),
                      ]}
                    />
                  </TableHead>
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredChannels.map((channel) => (
                  <TableRow key={channel.id}>
                    <TableCell>
                      <Checkbox
                        checked={selectedIds.has(channel.id)}
                        onCheckedChange={() => toggleSelect(channel.id)}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        {channel.logo_url && (
                          <img
                            src={channel.logo_url}
                            alt=""
                            className="h-6 w-6 object-contain"
                          />
                        )}
                        <div>
                          <div className="font-medium">{channel.channel_name}</div>
                          <div className="text-xs text-muted-foreground">
                            {channel.channel_number ? `#${channel.channel_number}` : ""}{" "}
                            {channel.tvg_id}
                          </div>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="max-w-xs">
                        <div className="truncate text-sm">
                          {channel.away_team} @ {channel.home_team}
                        </div>
                        {channel.event_date && (
                          <div className="text-xs text-muted-foreground">
                            {new Date(channel.event_date).toLocaleString(undefined, {
                              weekday: "short",
                              month: "short",
                              day: "numeric",
                              hour: "numeric",
                              minute: "2-digit",
                            })}
                          </div>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-sm truncate">
                      {groupLookup.get(channel.event_epg_group_id) ?? "-"}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="text-xs">{getLeagueDisplay(channel.league)}</Badge>
                    </TableCell>
                    <TableCell>{getSyncStatusBadge(channel.sync_status)}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatRelativeTime(channel.scheduled_delete_at)}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDeleteConfirm(channel)}
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

      {/* Recently Deleted */}
      {deletedChannels.length > 0 && (
        <Card>
          <CardHeader
            className="pb-2 cursor-pointer select-none hover:bg-muted/50 transition-colors"
            onClick={() => setDeletedCollapsed(!deletedCollapsed)}
          >
            <CardTitle className="text-base flex items-center gap-2">
              {deletedCollapsed ? (
                <ChevronRight className="h-4 w-4 text-muted-foreground" />
              ) : (
                <ChevronDown className="h-4 w-4 text-muted-foreground" />
              )}
              <Clock className="h-4 w-4 text-muted-foreground" />
              Recently Deleted ({deletedChannels.length})
            </CardTitle>
          </CardHeader>
          {!deletedCollapsed && <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Channel</TableHead>
                  <TableHead>Event</TableHead>
                  <TableHead>Group</TableHead>
                  <TableHead>League</TableHead>
                  <TableHead>Deleted</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {deletedChannels.map((channel) => (
                  <TableRow key={channel.id} className="text-muted-foreground">
                    <TableCell>
                      <div className="flex items-center gap-2">
                        {channel.logo_url && (
                          <img
                            src={channel.logo_url}
                            alt=""
                            className="h-6 w-6 object-contain opacity-50"
                          />
                        )}
                        <div>
                          <div className="font-medium">{channel.channel_name}</div>
                          <div className="text-xs">{channel.tvg_id}</div>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="max-w-xs truncate text-sm">
                        {channel.away_team} @ {channel.home_team}
                      </div>
                    </TableCell>
                    <TableCell className="text-sm">
                      {groupLookup.get(channel.event_epg_group_id) ?? "-"}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="text-xs">{getLeagueDisplay(channel.league)}</Badge>
                    </TableCell>
                    <TableCell className="text-sm">
                      {channel.deleted_at
                        ? new Date(channel.deleted_at).toLocaleString(undefined, {
                            month: "short",
                            day: "numeric",
                            hour: "numeric",
                            minute: "2-digit",
                          })
                        : "-"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>}
        </Card>
      )}

      {/* Delete Confirmation */}
      <Dialog
        open={deleteConfirm !== null}
        onOpenChange={(open) => !open && setDeleteConfirm(null)}
      >
        <DialogContent onClose={() => setDeleteConfirm(null)}>
          <DialogHeader>
            <DialogTitle>Delete Channel</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete "{deleteConfirm?.channel_name}"? This will
              also remove it from Dispatcharr if configured.
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

      {/* Bulk Delete Confirmation */}
      <Dialog open={bulkDeleteConfirm} onOpenChange={setBulkDeleteConfirm}>
        <DialogContent onClose={() => setBulkDeleteConfirm(false)}>
          <DialogHeader>
            <DialogTitle>Delete {selectedIds.size} Channels</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete {selectedIds.size} channel
              {selectedIds.size > 1 ? "s" : ""}? This will also remove them from
              Dispatcharr if configured.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBulkDeleteConfirm(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleBulkDelete}
              disabled={bulkDeleteMutation.isPending}
            >
              {bulkDeleteMutation.isPending && (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              )}
              Delete All
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Find Orphans Modal */}
      <Dialog open={orphansModalOpen} onOpenChange={setOrphansModalOpen}>
        <DialogContent onClose={() => setOrphansModalOpen(false)} className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-warning" />
              Orphan Channels
            </DialogTitle>
            <DialogDescription>
              Channels in Dispatcharr that aren't tracked by Teamarr
            </DialogDescription>
          </DialogHeader>

          <div className="py-4">
            {reconciliationLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : orphanChannels.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                No orphan channels found. Everything is in sync!
              </div>
            ) : (
              <div className="space-y-4">
                <p className="text-sm text-muted-foreground">
                  Found {orphanChannels.length} orphan channel
                  {orphanChannels.length > 1 ? "s" : ""}. These exist in Dispatcharr but
                  aren't tracked by Teamarr.
                </p>
                <div className="max-h-[50vh] overflow-y-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Channel Name</TableHead>
                      <TableHead>Dispatcharr ID</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {orphanChannels.map((orphan, idx) => (
                      <TableRow key={idx}>
                        <TableCell className="font-medium">
                          {orphan.channel_name ?? "Unknown"}
                        </TableCell>
                        <TableCell className="font-mono text-sm">
                          {orphan.dispatcharr_channel_id}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="destructive"
                            size="sm"
                            onClick={() =>
                              orphan.dispatcharr_channel_id &&
                              handleDeleteOrphan(orphan.dispatcharr_channel_id)
                            }
                            disabled={
                              !orphan.dispatcharr_channel_id ||
                              deletingOrphanId === orphan.dispatcharr_channel_id
                            }
                          >
                            {deletingOrphanId === orphan.dispatcharr_channel_id ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <Trash2 className="h-4 w-4" />
                            )}
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                </div>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setOrphansModalOpen(false)}>
              Close
            </Button>
            <Button
              variant="outline"
              onClick={() => refetchReconciliation()}
              disabled={reconciliationLoading}
            >
              <RefreshCw className={`h-4 w-4 mr-1 ${reconciliationLoading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
            {orphanChannels.length > 0 && (
              <Button
                variant="destructive"
                onClick={handleDeleteAllOrphans}
                disabled={deletingAllOrphans}
              >
                {deletingAllOrphans ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-1" />
                ) : (
                  <Trash2 className="h-4 w-4 mr-1" />
                )}
                Delete All ({orphanChannels.length})
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reset All Modal */}
      <Dialog open={resetModalOpen} onOpenChange={setResetModalOpen}>
        <DialogContent onClose={() => setResetModalOpen(false)} className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-destructive">
              <AlertTriangle className="h-5 w-5" />
              Reset All Teamarr Channels
            </DialogTitle>
            <DialogDescription>
              This will delete ALL Teamarr-created channels from Dispatcharr
            </DialogDescription>
          </DialogHeader>

          <div className="py-4">
            {resetLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : resetChannels.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                No Teamarr channels found in Dispatcharr.
              </div>
            ) : (
              <div className="space-y-4">
                <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4">
                  <p className="text-sm font-medium text-destructive">
                    ⚠️ Warning: Destructive Action
                  </p>
                  <p className="text-sm text-muted-foreground mt-1">
                    This will permanently delete {resetChannels.length} channel
                    {resetChannels.length > 1 ? "s" : ""} from Dispatcharr that have{" "}
                    <code className="text-xs bg-muted px-1 py-0.5 rounded">teamarr-event-*</code>{" "}
                    tvg_id.
                  </p>
                </div>
                <div className="max-h-[40vh] overflow-y-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Channel Name</TableHead>
                        <TableHead>Channel #</TableHead>
                        <TableHead>Streams</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {resetChannels.map((ch) => (
                        <TableRow key={ch.dispatcharr_channel_id}>
                          <TableCell className="font-medium">{ch.channel_name}</TableCell>
                          <TableCell>{ch.channel_number ?? "-"}</TableCell>
                          <TableCell>{ch.stream_count}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setResetModalOpen(false)}>
              Cancel
            </Button>
            {resetChannels.length > 0 && (
              <Button
                variant="destructive"
                onClick={handleExecuteReset}
                disabled={resetExecuting}
              >
                {resetExecuting ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-1" />
                ) : (
                  <Trash2 className="h-4 w-4 mr-1" />
                )}
                Delete All ({resetChannels.length})
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
