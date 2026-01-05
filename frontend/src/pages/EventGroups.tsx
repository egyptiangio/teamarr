import React, { useState, useMemo } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { useQuery } from "@tanstack/react-query"
import {
  Search,
  Trash2,
  Pencil,
  Loader2,
  Download,
  X,
  Check,
  AlertCircle,
  GripVertical,
  ArrowUp,
  ArrowDown,
  ArrowUpDown,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Switch } from "@/components/ui/switch"
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
import { Select } from "@/components/ui/select"
import {
  useGroups,
  useDeleteGroup,
  useToggleGroup,
  usePreviewGroup,
  useReorderGroups,
  useUpdateGroup,
} from "@/hooks/useGroups"
import { useTemplates } from "@/hooks/useTemplates"
import type { EventGroup, PreviewGroupResponse } from "@/api/types"

// Fetch leagues for logo lookup and sport mapping
async function fetchLeagues(): Promise<{ slug: string; name: string; logo_url: string | null; sport: string | null }[]> {
  const response = await fetch("/api/v1/cache/leagues")
  if (!response.ok) return []
  const data = await response.json()
  return data.leagues || []
}

// Fetch Dispatcharr channel groups for name lookup
async function fetchChannelGroups(): Promise<{ id: number; name: string }[]> {
  const response = await fetch("/api/v1/groups/dispatcharr/channel-groups")
  if (!response.ok) return []
  const data = await response.json()
  return data.groups || []
}

// Sport emoji mapping
const SPORT_EMOJIS: Record<string, string> = {
  football: "🏈",
  basketball: "🏀",
  baseball: "⚾",
  hockey: "🏒",
  soccer: "⚽",
  mma: "🥊",
  boxing: "🥊",
  golf: "⛳",
  tennis: "🎾",
  lacrosse: "🥍",
  cricket: "🏏",
  rugby: "🏉",
  racing: "🏁",
  motorsports: "🏎️",
}

export function EventGroups() {
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useGroups(true)
  const { data: templates } = useTemplates()
  const { data: cachedLeagues } = useQuery({ queryKey: ["leagues"], queryFn: fetchLeagues })
  const { data: channelGroups } = useQuery({ queryKey: ["dispatcharr-channel-groups"], queryFn: fetchChannelGroups })
  const deleteMutation = useDeleteGroup()
  const toggleMutation = useToggleGroup()
  const updateMutation = useUpdateGroup()
  const previewMutation = usePreviewGroup()
  const reorderMutation = useReorderGroups()

  // Drag-and-drop state for AUTO groups
  const [draggedGroupId, setDraggedGroupId] = useState<number | null>(null)

  // Preview modal state
  const [previewData, setPreviewData] = useState<PreviewGroupResponse | null>(null)
  const [showPreviewModal, setShowPreviewModal] = useState(false)

  // Create league lookup maps (logo and sport)
  const { leagueLogos, leagueSports } = useMemo(() => {
    const logos: Record<string, string> = {}
    const sports: Record<string, string> = {}
    if (cachedLeagues) {
      for (const league of cachedLeagues) {
        if (league.logo_url) {
          logos[league.slug] = league.logo_url
        }
        if (league.sport) {
          sports[league.slug] = league.sport.toLowerCase()
        }
      }
    }
    return { leagueLogos: logos, leagueSports: sports }
  }, [cachedLeagues])

  // Create channel group ID to name lookup
  const channelGroupNames = useMemo(() => {
    const names: Record<number, string> = {}
    if (channelGroups) {
      for (const group of channelGroups) {
        names[group.id] = group.name
      }
    }
    return names
  }, [channelGroups])

  // Get sport(s) for a group based on its leagues
  const getGroupSports = (group: EventGroup): string[] => {
    const sports = new Set<string>()
    for (const league of group.leagues) {
      const sport = leagueSports[league]
      if (sport) sports.add(sport)
    }
    return [...sports].sort()
  }

  // Selection state
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())

  // Filter state
  const [leagueFilter, setLeagueFilter] = useState("")
  const [sportFilter, setSportFilter] = useState("")
  const [templateFilter, setTemplateFilter] = useState<number | "">("")
  const [statusFilter, setStatusFilter] = useState<"" | "enabled" | "disabled">("")

  const [deleteConfirm, setDeleteConfirm] = useState<EventGroup | null>(null)
  const [showBulkDelete, setShowBulkDelete] = useState(false)
  const [showBulkTemplate, setShowBulkTemplate] = useState(false)
  const [bulkTemplateId, setBulkTemplateId] = useState<number | null>(null)

  // Column sorting state
  type SortColumn = "name" | "sport" | "template" | "matched" | "status" | null
  type SortDirection = "asc" | "desc"
  const [sortColumn, setSortColumn] = useState<SortColumn>(null)
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc")

  // Handle column sort
  const handleSort = (column: SortColumn) => {
    if (sortColumn === column) {
      setSortDirection(sortDirection === "asc" ? "desc" : "asc")
    } else {
      setSortColumn(column)
      setSortDirection("asc")
    }
  }

  // Sort icon component
  const SortIcon = ({ column }: { column: SortColumn }) => {
    if (sortColumn !== column) return <ArrowUpDown className="h-3 w-3 ml-1 opacity-30" />
    return sortDirection === "asc" ? (
      <ArrowUp className="h-3 w-3 ml-1" />
    ) : (
      <ArrowDown className="h-3 w-3 ml-1" />
    )
  }

  // Get unique leagues and sports from groups for filter dropdowns
  const { uniqueLeagues, uniqueSports } = useMemo(() => {
    if (!data?.groups) return { uniqueLeagues: [], uniqueSports: [] }
    const leagues = new Set<string>()
    const sports = new Set<string>()
    data.groups.forEach((g) => {
      g.leagues.forEach((l) => {
        leagues.add(l)
        const sport = leagueSports[l]
        if (sport) sports.add(sport)
      })
    })
    return {
      uniqueLeagues: [...leagues].sort(),
      uniqueSports: [...sports].sort(),
    }
  }, [data?.groups, leagueSports])

  // Filter groups and organize parent/child, separating AUTO and MANUAL
  const { parentGroups, autoGroups, manualGroups, filteredGroups, childrenMap } = useMemo(() => {
    if (!data?.groups) return { parentGroups: [], autoGroups: [], manualGroups: [], filteredGroups: [], childrenMap: {} as Record<number, EventGroup[]> }

    // Separate parent and child groups
    const parents: EventGroup[] = []
    const childrenMap: Record<number, EventGroup[]> = {}

    for (const group of data.groups) {
      if (typeof group.parent_group_id === 'number') {
        if (!childrenMap[group.parent_group_id]) {
          childrenMap[group.parent_group_id] = []
        }
        childrenMap[group.parent_group_id].push(group)
      } else {
        parents.push(group)
      }
    }

    // Filter parents
    const filteredParents = parents.filter((group) => {
      if (leagueFilter && !group.leagues.includes(leagueFilter)) return false
      if (sportFilter) {
        const groupSports = group.leagues.map(l => leagueSports[l]).filter(Boolean)
        if (!groupSports.includes(sportFilter)) return false
      }
      if (templateFilter !== "") {
        if (templateFilter === 0) {
          // "Unassigned" - match groups with null template_id
          if (group.template_id !== null) return false
        } else {
          if (group.template_id !== templateFilter) return false
        }
      }
      if (statusFilter === "enabled" && !group.enabled) return false
      if (statusFilter === "disabled" && group.enabled) return false
      return true
    })

    // Separate AUTO and MANUAL groups, sort AUTO by sort_order
    const auto = filteredParents
      .filter((g) => g.channel_assignment_mode === "auto")
      .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0))
    const manual = filteredParents.filter((g) => g.channel_assignment_mode !== "auto")

    // Build flat list: AUTO groups first (with children), then MANUAL groups (with children)
    const flat: EventGroup[] = []

    // Add AUTO groups with their children
    for (const parent of auto) {
      flat.push(parent)
      const children = childrenMap[parent.id] || []
      const filteredChildren = children.filter((group) => {
        if (statusFilter === "enabled" && !group.enabled) return false
        if (statusFilter === "disabled" && group.enabled) return false
        return true
      })
      flat.push(...filteredChildren)
    }

    // Add MANUAL groups with their children
    for (const parent of manual) {
      flat.push(parent)
      const children = childrenMap[parent.id] || []
      const filteredChildren = children.filter((group) => {
        if (statusFilter === "enabled" && !group.enabled) return false
        if (statusFilter === "disabled" && group.enabled) return false
        return true
      })
      flat.push(...filteredChildren)
    }

    return {
      parentGroups: filteredParents,
      autoGroups: auto,
      manualGroups: manual,
      filteredGroups: flat,
      childrenMap,
    }
  }, [data?.groups, leagueFilter, sportFilter, templateFilter, statusFilter, leagueSports])

  // Apply sorting to MANUAL groups only (AUTO groups use drag-and-drop order)
  const sortedGroups = useMemo(() => {
    if (!sortColumn) return filteredGroups

    // Sort function for groups
    const sortFn = (a: EventGroup, b: EventGroup) => {
      let cmp = 0
      switch (sortColumn) {
        case "name":
          cmp = a.name.localeCompare(b.name)
          break
        case "sport": {
          const sportsA = a.leagues.map(l => leagueSports[l]).filter(Boolean).sort().join(",")
          const sportsB = b.leagues.map(l => leagueSports[l]).filter(Boolean).sort().join(",")
          cmp = sportsA.localeCompare(sportsB)
          break
        }
        case "template": {
          const tA = a.template_id ? templates?.find(t => t.id === a.template_id)?.name || "" : ""
          const tB = b.template_id ? templates?.find(t => t.id === b.template_id)?.name || "" : ""
          cmp = tA.localeCompare(tB)
          break
        }
        case "matched":
          cmp = (a.matched_count || 0) - (b.matched_count || 0)
          break
        case "status":
          cmp = (a.enabled ? 1 : 0) - (b.enabled ? 1 : 0)
          break
      }
      return sortDirection === "asc" ? cmp : -cmp
    }

    // Only sort MANUAL groups - AUTO groups keep their drag-and-drop order
    const sortedManual = [...manualGroups].sort(sortFn)

    // Rebuild flat list: AUTO groups first (unsorted), then sorted MANUAL groups
    const result: EventGroup[] = []

    // AUTO groups with children (keep original order)
    for (const parent of autoGroups) {
      result.push(parent)
      const children = childrenMap?.[parent.id] || []
      result.push(...children)
    }

    // MANUAL groups with children (sorted)
    for (const parent of sortedManual) {
      result.push(parent)
      const children = childrenMap?.[parent.id] || []
      result.push(...children)
    }

    return result.length > 0 ? result : filteredGroups
  }, [filteredGroups, autoGroups, manualGroups, childrenMap, sortColumn, sortDirection, leagueSports, templates])

  // Filter templates to only show event templates
  const eventTemplates = useMemo(() => {
    return templates?.filter((t) => t.template_type === "event") ?? []
  }, [templates])

  // Calculate rich stats like V1
  const stats = useMemo(() => {
    if (!data?.groups) return {
      totalStreams: 0,
      totalFiltered: 0,
      filteredIncludeRegex: 0,
      filteredExcludeRegex: 0,
      filteredNoMatch: 0,
      filteredNotEvent: 0,
      eligible: 0,
      matched: 0,
      matchRate: 0,
      // Per-group breakdowns for tooltips
      streamsByGroup: [] as { name: string; count: number }[],
      eligibleByGroup: [] as { name: string; count: number }[],
      matchedByGroup: [] as { name: string; count: number; rate: number }[],
    }

    // Sum all groups (parents + children) - each has distinct streams from different M3U accounts
    const groups = data.groups
    const totalStreams = groups.reduce((sum, g) => sum + (g.total_stream_count || 0), 0)
    const filteredIncludeRegex = groups.reduce((sum, g) => sum + (g.filtered_include_regex || 0), 0)
    const filteredExcludeRegex = groups.reduce((sum, g) => sum + (g.filtered_exclude_regex || 0), 0)
    const filteredNotEvent = groups.reduce((sum, g) => sum + (g.filtered_not_event || 0), 0)
    // Note: filtered_no_match is NOT a filter reason - it's a match failure (streams passed filters but no event match)
    const totalFiltered = filteredIncludeRegex + filteredExcludeRegex + filteredNotEvent
    const eligible = groups.reduce((sum, g) => sum + (g.stream_count || 0), 0)
    const matched = groups.reduce((sum, g) => sum + (g.matched_count || 0), 0)
    const matchRate = eligible > 0 ? Math.round((matched / eligible) * 100) : 0

    // Per-group breakdowns for tooltips (all groups, not just parents)
    const streamsByGroup = groups
      .filter(g => (g.total_stream_count || 0) > 0)
      .map(g => ({ name: g.name, count: g.total_stream_count || 0 }))
      .sort((a, b) => b.count - a.count)
    const eligibleByGroup = groups
      .filter(g => (g.stream_count || 0) > 0)
      .map(g => ({ name: g.name, count: g.stream_count || 0 }))
      .sort((a, b) => b.count - a.count)
    const matchedByGroup = groups
      .filter(g => (g.stream_count || 0) > 0)
      .map(g => ({
        name: g.name,
        count: g.matched_count || 0,
        rate: g.stream_count ? Math.round(((g.matched_count || 0) / g.stream_count) * 100) : 0,
      }))
      .sort((a, b) => b.count - a.count)

    return {
      totalStreams,
      totalFiltered,
      filteredIncludeRegex,
      filteredExcludeRegex,
      filteredNotEvent,
      eligible,
      matched,
      matchRate,
      streamsByGroup,
      eligibleByGroup,
      matchedByGroup,
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

  const handlePreview = async (group: EventGroup) => {
    try {
      const result = await previewMutation.mutateAsync(group.id)
      setPreviewData(result)
      setShowPreviewModal(true)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to preview group")
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
    if (selectedIds.size === sortedGroups.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(sortedGroups.map((g) => g.id)))
    }
  }

  // Bulk actions
  const handleBulkToggle = async (enable: boolean) => {
    const groupsToToggle = sortedGroups.filter(
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

  const handleBulkAssignTemplate = async () => {
    const ids = Array.from(selectedIds)
    let succeeded = 0
    for (const id of ids) {
      try {
        await updateMutation.mutateAsync({
          groupId: id,
          data: { template_id: bulkTemplateId, clear_template: bulkTemplateId === null },
        })
        succeeded++
      } catch {
        // Continue with others
      }
    }
    toast.success(`Assigned template to ${succeeded} groups`)
    setSelectedIds(new Set())
    setShowBulkTemplate(false)
    setBulkTemplateId(null)
  }

  const clearFilters = () => {
    setLeagueFilter("")
    setSportFilter("")
    setTemplateFilter("")
    setStatusFilter("")
  }

  const hasActiveFilters = leagueFilter || sportFilter || templateFilter !== "" || statusFilter !== ""

  // Drag-and-drop handlers for AUTO groups
  const handleDragStart = (e: React.DragEvent, groupId: number) => {
    setDraggedGroupId(groupId)
    e.dataTransfer.effectAllowed = "move"
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = "move"
  }

  const handleDrop = async (e: React.DragEvent, targetGroupId: number) => {
    e.preventDefault()
    if (!draggedGroupId || draggedGroupId === targetGroupId) {
      setDraggedGroupId(null)
      return
    }

    // Find current positions
    const draggedIndex = autoGroups.findIndex((g) => g.id === draggedGroupId)
    const targetIndex = autoGroups.findIndex((g) => g.id === targetGroupId)

    if (draggedIndex === -1 || targetIndex === -1) {
      setDraggedGroupId(null)
      return
    }

    // Build new order
    const newOrder = [...autoGroups]
    const [dragged] = newOrder.splice(draggedIndex, 1)
    newOrder.splice(targetIndex, 0, dragged)

    // Assign new sort_order values
    const reorderData = newOrder.map((g, i) => ({ group_id: g.id, sort_order: i }))

    try {
      await reorderMutation.mutateAsync(reorderData)
      toast.success("Group order updated")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to reorder groups")
    }

    setDraggedGroupId(null)
  }

  const handleDragEnd = () => {
    setDraggedGroupId(null)
  }

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
    <div className="space-y-2">
      {/* Header - Compact */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">Event Groups</h1>
          <p className="text-sm text-muted-foreground">
            Configure event-based EPG from M3U stream groups
          </p>
        </div>
        <Button size="sm" onClick={() => navigate("/event-groups/import")}>
          <Download className="h-4 w-4 mr-1" />
          Import
        </Button>
      </div>

      {/* Stats Tiles - V1 Style: Grid with 4 equal columns filling width */}
      {data?.groups && data.groups.length > 0 && (
        <div className="grid grid-cols-4 gap-3">
            {/* Total Streams */}
            <div className="group relative">
              <div className="bg-secondary rounded px-3 py-2 cursor-help">
                <div className="text-xl font-bold">{stats.totalStreams}</div>
                <div className="text-[0.65rem] text-muted-foreground uppercase tracking-wider">Streams</div>
              </div>
              {stats.streamsByGroup.length > 0 && (
                <div className="absolute left-0 top-full mt-1 z-50 hidden group-hover:block">
                  <Card className="p-3 shadow-lg border min-w-[200px]">
                    <div className="text-xs font-medium text-muted-foreground mb-2">By Event Group</div>
                    <div className="space-y-1 max-h-48 overflow-y-auto">
                      {stats.streamsByGroup.slice(0, 10).map((g, i) => (
                        <div key={i} className="flex justify-between text-sm">
                          <span className="truncate max-w-[140px]">{g.name}</span>
                          <span className="font-medium ml-2">{g.count}</span>
                        </div>
                      ))}
                    </div>
                  </Card>
                </div>
              )}
            </div>

            {/* Filtered */}
            <div className="group relative">
              <div className="bg-secondary rounded px-3 py-2 cursor-help">
                <div className={`text-xl font-bold ${stats.totalFiltered > 0 ? 'text-amber-500' : ''}`}>{stats.totalFiltered}</div>
                <div className="text-[0.65rem] text-muted-foreground uppercase tracking-wider">Filtered</div>
              </div>
              {stats.totalFiltered > 0 && (
                <div className="absolute left-0 top-full mt-1 z-50 hidden group-hover:block">
                  <Card className="p-3 shadow-lg border min-w-[200px]">
                    <div className="text-xs font-medium text-muted-foreground mb-2">Filter Breakdown</div>
                    <div className="space-y-1">
                      {stats.filteredIncludeRegex > 0 && (
                        <div className="flex justify-between text-sm">
                          <span>Include regex</span>
                          <span className="font-medium">{stats.filteredIncludeRegex}</span>
                        </div>
                      )}
                      {stats.filteredExcludeRegex > 0 && (
                        <div className="flex justify-between text-sm">
                          <span>Exclude regex</span>
                          <span className="font-medium">{stats.filteredExcludeRegex}</span>
                        </div>
                      )}
                      {stats.filteredNotEvent > 0 && (
                        <div className="flex justify-between text-sm">
                          <span>Placeholders</span>
                          <span className="font-medium">{stats.filteredNotEvent}</span>
                        </div>
                      )}
                      <div className="flex justify-between text-sm font-medium pt-1 border-t">
                        <span>Total</span>
                        <span>{stats.totalFiltered}</span>
                      </div>
                    </div>
                  </Card>
                </div>
              )}
            </div>

            {/* Eligible */}
            <div className="group relative">
              <div className="bg-secondary rounded px-3 py-2 cursor-help">
                <div className="text-xl font-bold">{stats.eligible}</div>
                <div className="text-[0.65rem] text-muted-foreground uppercase tracking-wider">Eligible</div>
              </div>
              {stats.eligibleByGroup.length > 0 && (
                <div className="absolute left-0 top-full mt-1 z-50 hidden group-hover:block">
                  <Card className="p-3 shadow-lg border min-w-[200px]">
                    <div className="text-xs font-medium text-muted-foreground mb-2">By Event Group</div>
                    <div className="space-y-1 max-h-48 overflow-y-auto">
                      {stats.eligibleByGroup.slice(0, 10).map((g, i) => (
                        <div key={i} className="flex justify-between text-sm">
                          <span className="truncate max-w-[140px]">{g.name}</span>
                          <span className="font-medium ml-2">{g.count}</span>
                        </div>
                      ))}
                    </div>
                  </Card>
                </div>
              )}
            </div>

            {/* Matched */}
            <div className="group relative">
              <div className="bg-secondary rounded px-3 py-2 cursor-help">
                <div className="text-xl font-bold text-green-500">{stats.matched}</div>
                <div className="text-[0.65rem] text-muted-foreground uppercase tracking-wider">
                  Matched ({stats.matchRate}%)
                </div>
              </div>
              {stats.matchedByGroup.length > 0 && (
                <div className="absolute right-0 top-full mt-1 z-50 hidden group-hover:block">
                  <Card className="p-3 shadow-lg border min-w-[220px]">
                    <div className="text-xs font-medium text-muted-foreground mb-2">Match Rate by Group</div>
                    <div className="space-y-1 max-h-48 overflow-y-auto">
                      {stats.matchedByGroup.slice(0, 10).map((g, i) => (
                        <div key={i} className="flex justify-between text-sm">
                          <span className="truncate max-w-[120px]">{g.name}</span>
                          <span className="font-medium ml-2">{g.count} ({g.rate}%)</span>
                        </div>
                      ))}
                    </div>
                  </Card>
                </div>
              )}
            </div>
        </div>
      )}

      {/* Fixed Batch Operations Bar */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-0 left-0 right-0 z-50 border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
          <div className="container max-w-screen-xl mx-auto px-4 py-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">
                {selectedIds.size} group{selectedIds.size > 1 ? "s" : ""} selected
              </span>
              <div className="flex items-center gap-1">
                <Button variant="outline" size="sm" onClick={() => setSelectedIds(new Set())}>
                  Clear
                </Button>
                <Button variant="outline" size="sm" onClick={() => handleBulkToggle(true)}>
                  Enable
                </Button>
                <Button variant="outline" size="sm" onClick={() => handleBulkToggle(false)}>
                  Disable
                </Button>
                <Button variant="outline" size="sm" onClick={() => setShowBulkTemplate(true)}>
                  Assign Template
                </Button>
                <Button variant="destructive" size="sm" onClick={() => setShowBulkDelete(true)}>
                  <Trash2 className="h-3 w-3 mr-1" />
                  Delete
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Groups Table - No card wrapper for more compact look */}
      <div className="border border-border rounded-lg overflow-hidden">
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : sortedGroups.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              {data?.groups.length === 0
                ? "No event groups configured. Create one to get started."
                : "No groups match the current filters."}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-6"></TableHead>
                  <TableHead className="w-8">
                    <Checkbox
                      checked={selectedIds.size === sortedGroups.length && sortedGroups.length > 0}
                      onCheckedChange={toggleSelectAll}
                    />
                  </TableHead>
                  <TableHead
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => handleSort("name")}
                  >
                    <div className="flex items-center">
                      Name <SortIcon column="name" />
                    </div>
                  </TableHead>
                  <TableHead className="w-[70px] text-center">League</TableHead>
                  <TableHead
                    className="w-[50px] text-center cursor-pointer hover:bg-muted/50"
                    onClick={() => handleSort("sport")}
                  >
                    <div className="flex items-center justify-center">
                      Sport <SortIcon column="sport" />
                    </div>
                  </TableHead>
                  <TableHead
                    className="w-[100px] cursor-pointer hover:bg-muted/50"
                    onClick={() => handleSort("template")}
                  >
                    <div className="flex items-center">
                      Template <SortIcon column="template" />
                    </div>
                  </TableHead>
                  <TableHead className="text-center w-[90px]">Ch Start</TableHead>
                  <TableHead className="text-center w-[140px]">Ch Group</TableHead>
                  <TableHead
                    className="w-[75px] text-center cursor-pointer hover:bg-muted/50"
                    onClick={() => handleSort("matched")}
                  >
                    <div className="flex items-center justify-center">
                      Matched <SortIcon column="matched" />
                    </div>
                  </TableHead>
                  <TableHead
                    className="w-[50px] cursor-pointer hover:bg-muted/50"
                    onClick={() => handleSort("status")}
                  >
                    <div className="flex items-center">
                      Status <SortIcon column="status" />
                    </div>
                  </TableHead>
                  <TableHead className="w-[70px] text-right">Actions</TableHead>
                </TableRow>
                {/* Filter row - styled like V1 */}
                <TableRow className="border-b-2 border-border">
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                  <TableHead className="py-0.5 pb-1.5">
                    <FilterSelect
                      value={leagueFilter}
                      onChange={setLeagueFilter}
                      options={[
                        { value: "", label: "All" },
                        ...uniqueLeagues.map((league) => ({
                          value: league,
                          label: league.toUpperCase(),
                        })),
                      ]}
                    />
                  </TableHead>
                  <TableHead className="py-0.5 pb-1.5">
                    <FilterSelect
                      value={sportFilter}
                      onChange={setSportFilter}
                      options={[
                        { value: "", label: "All" },
                        ...uniqueSports.map((sport) => ({
                          value: sport,
                          label: sport.charAt(0).toUpperCase() + sport.slice(1),
                        })),
                      ]}
                    />
                  </TableHead>
                  <TableHead className="py-0.5 pb-1.5">
                    <FilterSelect
                      value={templateFilter === "" ? "" : String(templateFilter)}
                      onChange={(v) => setTemplateFilter(v ? Number(v) : "")}
                      options={[
                        { value: "", label: "All" },
                        { value: "0", label: "Unassigned" },
                        ...(templates?.filter(t => t.template_type === "event").map((template) => ({
                          value: String(template.id),
                          label: template.name,
                        })) || []),
                      ]}
                    />
                  </TableHead>
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                  <TableHead className="py-0.5 pb-1.5">
                    <FilterSelect
                      value={statusFilter}
                      onChange={(v) => setStatusFilter(v as typeof statusFilter)}
                      options={[
                        { value: "", label: "All" },
                        { value: "enabled", label: "Active" },
                        { value: "disabled", label: "Inactive" },
                      ]}
                    />
                  </TableHead>
                  <TableHead className="py-0.5 pb-1.5 text-right">
                    {hasActiveFilters && (
                      <Button variant="ghost" size="sm" onClick={clearFilters} className="h-5 px-1.5">
                        <X className="h-3 w-3" />
                      </Button>
                    )}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {/* AUTO Section Header - V1 style */}
                {autoGroups.length > 0 && (
                  <TableRow className="bg-secondary/50 hover:bg-secondary/50 border-b-2 border-emerald-500/40">
                    <TableCell colSpan={11} className="py-2 px-4">
                      <span className="text-xs font-semibold text-emerald-500 uppercase tracking-wide">
                        AUTO Channel Assignment
                      </span>
                      <span className="text-xs text-emerald-500/60 ml-2 italic">
                        Drag to reorder priority
                      </span>
                    </TableCell>
                  </TableRow>
                )}
                {sortedGroups.map((group, index) => {
                  const isChild = typeof group.parent_group_id === 'number'
                  const parentGroup = isChild
                    ? parentGroups.find((p) => p.id === group.parent_group_id)
                    : null
                  const isAuto = group.channel_assignment_mode === "auto"
                  const isManual = !isAuto && !isChild

                  // Insert MANUAL section header before first manual group
                  const isFirstManual = isManual && !sortedGroups.slice(0, index).some(
                    (g) => g.channel_assignment_mode !== "auto" && typeof g.parent_group_id !== 'number'
                  )

                  return (
                    <React.Fragment key={group.id}>
                      {isFirstManual && manualGroups.length > 0 && (
                        <TableRow className="bg-secondary/50 hover:bg-secondary/50 border-b-2 border-border">
                          <TableCell colSpan={11} className="py-2 px-4">
                            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                              MANUAL Channel Assignment
                            </span>
                            <span className="text-xs text-muted-foreground/60 ml-2 italic">
                              Fixed channel start numbers
                            </span>
                          </TableCell>
                        </TableRow>
                      )}
                      <TableRow
                        className={`
                          ${isChild ? "bg-purple-500/5 hover:bg-purple-500/10" : ""}
                          ${isAuto && !isChild ? "border-l-3 border-l-transparent hover:border-l-emerald-500 group/row" : ""}
                          ${draggedGroupId === group.id ? "opacity-50" : ""}
                        `}
                        draggable={isAuto && !isChild}
                        onDragStart={(e) => isAuto && !isChild && handleDragStart(e, group.id)}
                        onDragOver={handleDragOver}
                        onDrop={(e) => isAuto && !isChild && handleDrop(e, group.id)}
                        onDragEnd={handleDragEnd}
                      >
                        <TableCell className="w-8 p-0">
                          {isAuto && !isChild ? (
                            <div className="flex items-center justify-center h-full cursor-grab active:cursor-grabbing text-muted-foreground group-hover/row:text-emerald-500">
                              <GripVertical className="h-4 w-4" />
                            </div>
                          ) : null}
                        </TableCell>
                        <TableCell>
                          <Checkbox
                            checked={selectedIds.has(group.id)}
                            onCheckedChange={() => toggleSelect(group.id)}
                          />
                        </TableCell>
                        <TableCell className="font-medium">
                          {isChild ? (
                            <div className="flex items-center gap-2 pl-4">
                              <span className="text-purple-400 font-bold">└</span>
                              <span>{group.name}</span>
                              {/* Account/Provider badge for child */}
                              {group.m3u_account_name && (
                                <Badge
                                  variant="secondary"
                                  className="text-xs"
                                  title={`M3U Account: ${group.m3u_account_name}`}
                                >
                                  {group.m3u_account_name}
                                </Badge>
                              )}
                              <Badge
                                variant="outline"
                                className="bg-purple-500/20 text-purple-400 border-purple-500/30 text-xs italic"
                                title={`Child of: ${parentGroup?.name}`}
                              >
                                ↳ {parentGroup?.name ? (parentGroup.name.length > 15 ? parentGroup.name.slice(0, 15) + "…" : parentGroup.name) : "parent"}
                              </Badge>
                            </div>
                          ) : (
                            <div className="flex items-center gap-2 flex-wrap">
                              <span>{group.name}</span>
                              {/* AUTO badge */}
                              {isAuto && (
                                <Badge
                                  variant="secondary"
                                  className="bg-green-500/15 text-green-500 border-green-500/30 text-xs"
                                  title="Auto channel assignment"
                                >
                                  AUTO
                                </Badge>
                              )}
                              {/* Account name badge */}
                              {group.m3u_account_name && (
                                <Badge
                                  variant="secondary"
                                  className="text-xs"
                                  title={`M3U Account: ${group.m3u_account_name}`}
                                >
                                  {group.m3u_account_name}
                                </Badge>
                              )}
                              {/* Regex badge */}
                              {(group.custom_regex_teams_enabled ||
                                group.custom_regex_date_enabled ||
                                group.custom_regex_time_enabled ||
                                group.stream_include_regex_enabled ||
                                group.stream_exclude_regex_enabled) && (
                                <Badge
                                  variant="secondary"
                                  className="bg-blue-500/15 text-blue-400 border-blue-500/30 text-xs"
                                  title={`Custom regex: ${[
                                    group.custom_regex_teams_enabled && "teams",
                                    group.custom_regex_date_enabled && "date",
                                    group.custom_regex_time_enabled && "time",
                                    group.stream_include_regex_enabled && "include",
                                    group.stream_exclude_regex_enabled && "exclude",
                                  ].filter(Boolean).join(", ")}`}
                                >
                                  Regex
                                </Badge>
                              )}
                            </div>
                          )}
                        </TableCell>
                        {/* League Column */}
                        <TableCell className="text-center">
                          <div className="flex flex-wrap gap-1 justify-center">
                            {group.leagues.slice(0, 2).map((league) => (
                              leagueLogos[league] ? (
                                <img
                                  key={league}
                                  src={leagueLogos[league]}
                                  alt={league.toUpperCase()}
                                  title={league.toUpperCase()}
                                  className="h-6 w-auto object-contain"
                                />
                              ) : (
                                <Badge key={league} variant="secondary" className="text-[0.7rem] px-1.5">
                                  {league.toUpperCase()}
                                </Badge>
                              )
                            ))}
                            {group.leagues.length > 2 && (
                              <Badge variant="outline" className="text-[0.7rem]">
                                +{group.leagues.length - 2}
                              </Badge>
                            )}
                          </div>
                        </TableCell>
                        {/* Sport Column */}
                        <TableCell className="text-center">
                          {(() => {
                            const sports = getGroupSports(group)
                            if (sports.length === 0) {
                              return <span className="text-muted-foreground">—</span>
                            } else if (sports.length === 1) {
                              const emoji = SPORT_EMOJIS[sports[0]] || "🏆"
                              return (
                                <span title={sports[0].charAt(0).toUpperCase() + sports[0].slice(1)}>
                                  {emoji}
                                </span>
                              )
                            } else {
                              return (
                                <Badge
                                  variant="outline"
                                  className="text-xs"
                                  title={sports.map(s => s.charAt(0).toUpperCase() + s.slice(1)).join(", ")}
                                >
                                  MUL
                                </Badge>
                              )
                            }
                          })()}
                        </TableCell>
                    <TableCell>
                      {isChild ? (
                        <Badge
                          variant="outline"
                          className="bg-purple-500/15 text-purple-400 border-purple-500/30 text-xs italic"
                          title="Inherited from parent"
                        >
                          ↳
                        </Badge>
                      ) : group.template_id ? (
                        <Badge variant="success">
                          {templates?.find((t) => t.id === group.template_id)?.name ?? `#${group.template_id}`}
                        </Badge>
                      ) : (
                        <Badge variant="warning">Default</Badge>
                      )}
                    </TableCell>
                    {/* Ch Start Column */}
                    <TableCell className="text-center">
                      {isChild ? (
                        <Badge
                          variant="outline"
                          className="bg-purple-500/15 text-purple-400 border-purple-500/30 text-xs italic"
                          title="Inherited from parent"
                        >
                          ↳
                        </Badge>
                      ) : isAuto ? (
                        <Badge
                          variant="secondary"
                          className="bg-green-500/15 text-green-500 border-green-500/30 text-xs"
                          title="Auto-assigned from global range"
                        >
                          AUTO
                        </Badge>
                      ) : group.channel_start_number ? (
                        <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{group.channel_start_number}</code>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    {/* Ch Group Column */}
                    <TableCell className="text-center">
                      {isChild ? (
                        <Badge
                          variant="outline"
                          className="bg-purple-500/15 text-purple-400 border-purple-500/30 text-xs italic"
                          title="Inherited from parent"
                        >
                          ↳
                        </Badge>
                      ) : group.channel_group_id ? (
                        <Badge variant="secondary" className="text-xs" title={`ID: ${group.channel_group_id}`}>
                          {channelGroupNames[group.channel_group_id] || `#${group.channel_group_id}`}
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    {/* Matched Column with Progress Bar */}
                    <TableCell className="text-center">
                      {group.stream_count && group.stream_count > 0 ? (
                        <div className="flex flex-col items-center gap-0.5" title={`Last: ${group.last_refresh ? new Date(group.last_refresh).toLocaleString() : 'Never'}`}>
                          <div className="w-full h-1.5 bg-secondary rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full transition-all ${
                                (group.matched_count || 0) / group.stream_count >= 0.8
                                  ? 'bg-green-500'
                                  : (group.matched_count || 0) / group.stream_count >= 0.5
                                    ? 'bg-yellow-500'
                                    : 'bg-red-500'
                              }`}
                              style={{ width: `${Math.round(((group.matched_count || 0) / group.stream_count) * 100)}%` }}
                            />
                          </div>
                          <span className="text-[0.65rem]">
                            {group.matched_count}/{group.stream_count} ({Math.round(((group.matched_count || 0) / group.stream_count) * 100)}%)
                          </span>
                        </div>
                      ) : (
                        <span className="text-muted-foreground text-xs italic">—</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Switch
                        checked={group.enabled}
                        onCheckedChange={() => handleToggle(group)}
                        disabled={toggleMutation.isPending}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => handlePreview(group)}
                          disabled={previewMutation.isPending}
                          title="Preview stream matches"
                        >
                          {previewMutation.isPending &&
                          previewMutation.variables === group.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Search className="h-4 w-4" />
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
                    </React.Fragment>
                  )
                })}
              </TableBody>
            </Table>
          )}
      </div>

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

      {/* Bulk Assign Template Dialog */}
      <Dialog open={showBulkTemplate} onOpenChange={setShowBulkTemplate}>
        <DialogContent onClose={() => setShowBulkTemplate(false)}>
          <DialogHeader>
            <DialogTitle>Assign Template</DialogTitle>
            <DialogDescription>
              Assign a template to {selectedIds.size} selected group{selectedIds.size !== 1 && "s"}.
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <Select
              value={bulkTemplateId?.toString() ?? ""}
              onChange={(e) =>
                setBulkTemplateId(e.target.value ? parseInt(e.target.value) : null)
              }
            >
              <option value="">Unassigned (Default)</option>
              {eventTemplates.map((template) => (
                <option key={template.id} value={template.id.toString()}>
                  {template.name}
                </option>
              ))}
            </Select>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowBulkTemplate(false)}>
              Cancel
            </Button>
            <Button onClick={handleBulkAssignTemplate} disabled={updateMutation.isPending}>
              {updateMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Assign
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

      {/* Stream Preview Modal */}
      <Dialog open={showPreviewModal} onOpenChange={setShowPreviewModal}>
        <DialogContent onClose={() => setShowPreviewModal(false)} className="max-w-4xl max-h-[80vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle>
              Stream Preview: {previewData?.group_name}
            </DialogTitle>
            <DialogDescription>
              Preview of stream matching results. Processing is done via EPG generation.
            </DialogDescription>
          </DialogHeader>

          {previewData && (
            <div className="flex-1 overflow-hidden flex flex-col gap-4">
              {/* Summary stats */}
              <div className="flex items-center gap-4 p-3 bg-muted/50 rounded-lg text-sm">
                <span>{previewData.total_streams} streams</span>
                <span className="text-muted-foreground">|</span>
                <span className="text-green-600 dark:text-green-400">
                  {previewData.matched_count} matched
                </span>
                <span className="text-muted-foreground">|</span>
                <span className="text-amber-600 dark:text-amber-400">
                  {previewData.unmatched_count} unmatched
                </span>
                {previewData.filtered_count > 0 && (
                  <>
                    <span className="text-muted-foreground">|</span>
                    <span className="text-muted-foreground">
                      {previewData.filtered_count} filtered
                    </span>
                  </>
                )}
                {previewData.cache_hits > 0 && (
                  <>
                    <span className="text-muted-foreground">|</span>
                    <span className="text-muted-foreground">
                      {previewData.cache_hits}/{previewData.cache_hits + previewData.cache_misses} cached
                    </span>
                  </>
                )}
              </div>

              {/* Errors */}
              {previewData.errors.length > 0 && (
                <div className="p-3 bg-destructive/10 border border-destructive/20 rounded-lg text-sm text-destructive">
                  {previewData.errors.map((err, i) => (
                    <div key={i}>{err}</div>
                  ))}
                </div>
              )}

              {/* Stream table */}
              <div className="flex-1 overflow-auto border rounded-lg">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-10">Status</TableHead>
                      <TableHead className="w-[40%]">Stream Name</TableHead>
                      <TableHead>League</TableHead>
                      <TableHead>Event Match</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {previewData.streams.map((stream, i) => (
                      <TableRow key={i}>
                        <TableCell>
                          {stream.matched ? (
                            <Check className="h-4 w-4 text-green-600 dark:text-green-400" />
                          ) : (
                            <AlertCircle className="h-4 w-4 text-amber-600 dark:text-amber-400" />
                          )}
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {stream.stream_name}
                        </TableCell>
                        <TableCell>
                          {stream.league ? (
                            <Badge variant="secondary">{stream.league.toUpperCase()}</Badge>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </TableCell>
                        <TableCell>
                          {stream.matched ? (
                            <div className="text-sm">
                              <div className="font-medium">{stream.event_name}</div>
                              {stream.start_time && (
                                <div className="text-muted-foreground text-xs">
                                  {new Date(stream.start_time).toLocaleString()}
                                </div>
                              )}
                            </div>
                          ) : stream.exclusion_reason ? (
                            <span className="text-muted-foreground text-xs">
                              {stream.exclusion_reason}
                            </span>
                          ) : (
                            <span className="text-muted-foreground">No match</span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                    {previewData.streams.length === 0 && (
                      <TableRow>
                        <TableCell colSpan={4} className="text-center text-muted-foreground py-8">
                          No streams to display
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </div>
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => setShowPreviewModal(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
