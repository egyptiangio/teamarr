import { useState } from "react"
import { toast } from "sonner"
import {
  Trash2,
  Loader2,
  RefreshCw,
  Clock,
  Tv,
  Wrench,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
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
import { Select } from "@/components/ui/select"
import {
  useManagedChannels,
  useDeleteManagedChannel,
  useSyncLifecycle,
  useRunReconciliation,
  usePendingDeletions,
} from "@/hooks/useChannels"
import { useGroups } from "@/hooks/useGroups"
import type { ManagedChannel } from "@/api/channels"

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
  const [selectedGroupId, setSelectedGroupId] = useState<number | undefined>(undefined)
  const [includeDeleted, setIncludeDeleted] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<ManagedChannel | null>(null)

  const { data: groups } = useGroups()
  const {
    data: channelsData,
    isLoading,
    error,
    refetch,
  } = useManagedChannels(selectedGroupId, includeDeleted)
  const { data: pendingData } = usePendingDeletions()

  const deleteMutation = useDeleteManagedChannel()
  const syncMutation = useSyncLifecycle()
  const reconcileMutation = useRunReconciliation()

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

  const handleSync = async () => {
    try {
      const result = await syncMutation.mutateAsync()
      toast.success(
        `Sync complete: ${result.deleted_count} deleted, ${result.error_count} errors`
      )
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to sync lifecycle")
    }
  }

  const handleReconcile = async (autoFix: boolean) => {
    try {
      const result = await reconcileMutation.mutateAsync({ autoFix })
      if (autoFix) {
        toast.success(
          `Reconciliation complete: ${result.summary.fixed} fixed, ${result.summary.errors} errors`
        )
      } else {
        toast.info(`Found ${result.summary.total} issues`)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Reconciliation failed")
    }
  }

  if (error) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">Managed Channels</h1>
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
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Managed Channels</h1>
          <p className="text-muted-foreground">
            Event-based channels managed by Teamarr
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className="h-4 w-4 mr-1" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Action Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Lifecycle Sync */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-lg flex items-center gap-2">
              <RefreshCw className="h-5 w-5" />
              Lifecycle Sync
            </CardTitle>
            <CardDescription>Process pending creates/deletes</CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              onClick={handleSync}
              disabled={syncMutation.isPending}
              className="w-full"
            >
              {syncMutation.isPending && (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              )}
              Sync Now
            </Button>
          </CardContent>
        </Card>

        {/* Reconciliation */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-lg flex items-center gap-2">
              <Wrench className="h-5 w-5" />
              Reconciliation
            </CardTitle>
            <CardDescription>Detect and fix sync issues</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleReconcile(false)}
                disabled={reconcileMutation.isPending}
                className="flex-1"
              >
                Check
              </Button>
              <Button
                variant="default"
                size="sm"
                onClick={() => handleReconcile(true)}
                disabled={reconcileMutation.isPending}
                className="flex-1"
              >
                {reconcileMutation.isPending && (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                )}
                Fix
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Pending Deletions */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-lg flex items-center gap-2">
              <Clock className="h-5 w-5" />
              Pending Deletions
            </CardTitle>
            <CardDescription>Channels awaiting deletion</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{pendingData?.count ?? 0}</div>
            {pendingData && pendingData.count > 0 && (
              <p className="text-sm text-muted-foreground mt-1">
                Next: {pendingData.channels[0]?.channel_name}
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">Group:</span>
              <Select
                value={selectedGroupId?.toString() ?? ""}
                onChange={(e) =>
                  setSelectedGroupId(e.target.value ? parseInt(e.target.value) : undefined)
                }
                className="w-48"
              >
                <option value="">All Groups</option>
                {groups?.groups?.map((group) => (
                  <option key={group.id} value={group.id.toString()}>
                    {group.name}
                  </option>
                ))}
              </Select>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={includeDeleted}
                onChange={(e) => setIncludeDeleted(e.target.checked)}
              />
              Show deleted
            </label>
          </div>
        </CardContent>
      </Card>

      {/* Channels List */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Tv className="h-5 w-5" />
            Channels ({channelsData?.total ?? 0})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : channelsData?.channels.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              No managed channels found.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Channel</TableHead>
                  <TableHead>Event</TableHead>
                  <TableHead>League</TableHead>
                  <TableHead>Event Time</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Delete At</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {channelsData?.channels.map((channel) => (
                  <TableRow key={channel.id}>
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
                        <div className="truncate">
                          {channel.away_team} @ {channel.home_team}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{channel.league ?? "-"}</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatDateTime(channel.event_date)}
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
    </div>
  )
}
