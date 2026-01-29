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
  Plus,
  RotateCcw,
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
import { Input } from "@/components/ui/input"
import {
  useBulkUpdateGroups,
  useClearGroupMatchCache,
  useClearGroupsMatchCache,
  useGroups,
  useDeleteGroup,
  useToggleGroup,
  usePreviewGroup,
  useReorderGroups,
} from "@/hooks/useGroups"
import { useTemplates } from "@/hooks/useTemplates"
import type { EventGroup, PreviewGroupResponse, TeamFilterEntry } from "@/api/types"
import { getLeagues, getSports } from "@/api/teams"
import { TeamPicker } from "@/components/TeamPicker"
import { LeaguePicker } from "@/components/LeaguePicker"
import { ChannelProfileSelector } from "@/components/ChannelProfileSelector"
import { getUniqueSports, filterLeaguesBySport, getLeagueDisplayName, SPORT_EMOJIS } from "@/lib/utils"

// Fetch Dispatcharr channel groups for name lookup
async function fetchChannelGroups(): Promise<{ id: number; name: string }[]> {
  const response = await fetch("/api/v1/groups/dispatcharr/channel-groups")
  if (!response.ok) return []
  const data = await response.json()
  return data.groups || []
}

// ============================================================================
// Team Alias Types and API Functions
// ============================================================================

interface TeamAlias {
  id: number
  alias: string
  league: string
  provider: string
  team_id: string
  team_name: string
  created_at: string | null
}

async function fetchAliases(): Promise<TeamAlias[]> {
  const response = await fetch("/api/v1/aliases")
  if (!response.ok) return []
  const data = await response.json()
  return data.aliases || []
}

async function createAlias(alias: { alias: string; league: string; team_id: string; team_name: string; provider?: string }): Promise<void> {
  const response = await fetch("/api/v1/aliases", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...alias, provider: alias.provider || "espn" }),
  })
  if (!response.ok) {
    const data = await response.json()
    throw new Error(data.detail || "Failed to create alias")
  }
}

async function deleteAlias(id: number): Promise<void> {
  const response = await fetch(`/api/v1/aliases/${id}`, { method: "DELETE" })
  if (!response.ok) {
    throw new Error("Failed to delete alias")
  }
}

// Helper to get display name (prefer display_name over name)
const getDisplayName = (group: EventGroup) => group.display_name || group.name

export function EventGroups() {
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useGroups(true)
  const { data: templates } = useTemplates()
  const { data: leaguesResponse } = useQuery({ queryKey: ["leagues"], queryFn: () => getLeagues() })
  const cachedLeagues = leaguesResponse?.leagues
  const { data: sportsResponse } = useQuery({ queryKey: ["sports"], queryFn: getSports, staleTime: 1000 * 60 * 60 })
  const sportsMap = sportsResponse?.sports
  const { data: channelGroups } = useQuery({ queryKey: ["dispatcharr-channel-groups"], queryFn: fetchChannelGroups })
  const deleteMutation = useDeleteGroup()
  const toggleMutation = useToggleGroup()
  const bulkUpdateMutation = useBulkUpdateGroups()
  const previewMutation = usePreviewGroup()
  const reorderMutation = useReorderGroups()
  const clearCacheMutation = useClearGroupMatchCache()
  const clearCachesBulkMutation = useClearGroupsMatchCache()

  // Drag-and-drop state for AUTO groups
  const [draggedGroupId, setDraggedGroupId] = useState<number | null>(null)

  // Preview modal state
  const [previewData, setPreviewData] = useState<PreviewGroupResponse | null>(null)
  const [showPreviewModal, setShowPreviewModal] = useState(false)

  // Clear cache confirmation state
  const [clearCacheConfirm, setClearCacheConfirm] = useState<EventGroup | null>(null)
  const [showBulkClearCache, setShowBulkClearCache] = useState(false)

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
  const [showBulkEdit, setShowBulkEdit] = useState(false)
  // Bulk edit form state - checkboxes control which fields to update
  const [bulkEditLeaguesEnabled, setBulkEditLeaguesEnabled] = useState(false)
  const [bulkEditLeagues, setBulkEditLeagues] = useState<string[]>([])
  const [bulkEditTemplateEnabled, setBulkEditTemplateEnabled] = useState(false)
  const [bulkEditTemplateId, setBulkEditTemplateId] = useState<number | null>(null)
  const [bulkEditClearTemplate, setBulkEditClearTemplate] = useState(false)
  const [bulkEditChannelGroupEnabled, setBulkEditChannelGroupEnabled] = useState(false)
  const [bulkEditChannelGroupId, setBulkEditChannelGroupId] = useState<number | null>(null)
  const [bulkEditChannelGroupMode, setBulkEditChannelGroupMode] = useState<'static' | 'sport' | 'league'>('static')
  const [bulkEditClearChannelGroup, setBulkEditClearChannelGroup] = useState(false)
  const [bulkEditProfilesEnabled, setBulkEditProfilesEnabled] = useState(false)
  const [bulkEditProfileIds, setBulkEditProfileIds] = useState<(number | string)[]>([])
  const [bulkEditUseDefaultProfiles, setBulkEditUseDefaultProfiles] = useState(true)
  const [bulkEditSortOrderEnabled, setBulkEditSortOrderEnabled] = useState(false)
  const [bulkEditSortOrder, setBulkEditSortOrder] = useState<string>("time")
  const [bulkEditOverlapHandlingEnabled, setBulkEditOverlapHandlingEnabled] = useState(false)
  const [bulkEditOverlapHandling, setBulkEditOverlapHandling] = useState<string>("add_stream")

  // Column sorting state
  type SortColumn = "name" | "sport" | "template" | "matched" | "status" | null
  type SortDirection = "asc" | "desc"
  const [sortColumn, setSortColumn] = useState<SortColumn>(null)
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc")

  // Team Aliases state
  const { data: aliases, refetch: refetchAliases } = useQuery({
    queryKey: ["aliases"],
    queryFn: fetchAliases,
  })
  const [showAliasModal, setShowAliasModal] = useState(false)
  const [aliasForm, setAliasForm] = useState({ alias: "", league: "", team_id: "", team_name: "" })
  const [aliasSport, setAliasSport] = useState("")
  const [aliasSelectedTeams, setAliasSelectedTeams] = useState<TeamFilterEntry[]>([])
  const [aliasSubmitting, setAliasSubmitting] = useState(false)
  const [aliasDeleting, setAliasDeleting] = useState<number | null>(null)

  // Get unique sports from cached leagues (normalized, sorted)
  const aliasSports = useMemo(() => {
    if (!cachedLeagues) return []
    return getUniqueSports(cachedLeagues, sportsMap)
  }, [cachedLeagues, sportsMap])

  // Filter leagues by selected sport (import_enabled first, then alphabetical)
  const aliasFilteredLeagues = useMemo(() => {
    if (!aliasSport || !cachedLeagues) return []
    return filterLeaguesBySport(cachedLeagues, aliasSport, sportsMap)
  }, [cachedLeagues, aliasSport, sportsMap])

  // Handle sport change in alias modal
  const handleAliasSportChange = (sport: string) => {
    setAliasSport(sport)
    setAliasForm({ ...aliasForm, league: "", team_id: "", team_name: "" })
    setAliasSelectedTeams([])
  }

  // Handle league change in alias modal
  const handleAliasLeagueChange = (league: string) => {
    setAliasForm({ ...aliasForm, league, team_id: "", team_name: "" })
    setAliasSelectedTeams([])
  }

  // Handle team selection from TeamPicker
  const handleAliasTeamSelect = (teams: TeamFilterEntry[]) => {
    setAliasSelectedTeams(teams)
    const team = teams[0]
    if (team) {
      setAliasForm({ ...aliasForm, team_id: team.team_id, team_name: team.name || "" })
    } else {
      setAliasForm({ ...aliasForm, team_id: "", team_name: "" })
    }
  }

  const handleCreateAlias = async () => {
    if (!aliasForm.alias.trim() || !aliasForm.league || !aliasForm.team_id) {
      toast.error("Please fill in all fields")
      return
    }

    setAliasSubmitting(true)
    try {
      await createAlias({
        alias: aliasForm.alias.trim().toLowerCase(),
        league: aliasForm.league,
        team_id: aliasForm.team_id,
        team_name: aliasForm.team_name,
      })
      toast.success(`Alias "${aliasForm.alias}" created`)
      setShowAliasModal(false)
      setAliasForm({ alias: "", league: "", team_id: "", team_name: "" })
      setAliasSport("")
      setAliasSelectedTeams([])
      refetchAliases()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create alias")
    } finally {
      setAliasSubmitting(false)
    }
  }

  const handleDeleteAlias = async (id: number) => {
    setAliasDeleting(id)
    try {
      await deleteAlias(id)
      toast.success("Alias deleted")
      refetchAliases()
    } catch {
      toast.error("Failed to delete alias")
    } finally {
      setAliasDeleting(null)
    }
  }

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
          cmp = getDisplayName(a).localeCompare(getDisplayName(b))
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
      filteredNotEvent: 0,
      failedCount: 0,
      streamsExcluded: 0,
      excludedEventFinal: 0,
      excludedEventPast: 0,
      excludedBeforeWindow: 0,
      excludedLeagueNotIncluded: 0,
      matched: 0,
      matchRate: 0,
      // Per-group breakdowns for tooltips
      streamsByGroup: [] as { name: string; count: number }[],
    }

    // Sum all groups (parents + children) - each has distinct streams from different M3U accounts
    const groups = data.groups
    const totalStreams = groups.reduce((sum, g) => sum + (g.total_stream_count || 0), 0)
    const filteredIncludeRegex = groups.reduce((sum, g) => sum + (g.filtered_include_regex || 0), 0)
    const filteredExcludeRegex = groups.reduce((sum, g) => sum + (g.filtered_exclude_regex || 0), 0)
    const filteredNotEvent = groups.reduce((sum, g) => sum + (g.filtered_not_event || 0), 0)
    const filteredTeam = groups.reduce((sum, g) => sum + (g.filtered_team || 0), 0)
    const streamsExcluded = groups.reduce((sum, g) => sum + (g.streams_excluded || 0), 0)
    const excludedEventFinal = groups.reduce((sum, g) => sum + (g.excluded_event_final || 0), 0)
    const excludedEventPast = groups.reduce((sum, g) => sum + (g.excluded_event_past || 0), 0)
    const excludedBeforeWindow = groups.reduce((sum, g) => sum + (g.excluded_before_window || 0), 0)
    const excludedLeagueNotIncluded = groups.reduce((sum, g) => sum + (g.excluded_league_not_included || 0), 0)
    const totalFiltered = filteredIncludeRegex + filteredExcludeRegex + filteredNotEvent + filteredTeam
    const matched = groups.reduce((sum, g) => sum + (g.matched_count || 0), 0)
    const failedCount = groups.reduce((sum, g) => sum + (g.failed_count || 0), 0)
    // Match rate = matched / (matched + failed) - percentage of match attempts that succeeded
    const totalAttempted = matched + failedCount
    const matchRate = totalAttempted > 0 ? Math.round((matched / totalAttempted) * 100) : 0

    // Per-group breakdowns for tooltips (all groups, not just parents)
    const streamsByGroup = groups
      .filter(g => (g.total_stream_count || 0) > 0)
      .map(g => ({ name: getDisplayName(g), count: g.total_stream_count || 0 }))
      .sort((a, b) => b.count - a.count)

    return {
      totalStreams,
      totalFiltered,
      filteredIncludeRegex,
      filteredExcludeRegex,
      filteredNotEvent,
      filteredTeam,
      failedCount,
      streamsExcluded,
      excludedEventFinal,
      excludedEventPast,
      excludedBeforeWindow,
      excludedLeagueNotIncluded,
      matched,
      matchRate,
      streamsByGroup,
    }
  }, [data?.groups])

  // League slug -> display name lookup (uses {league} variable resolution: alias first, then name)
  const getLeagueDisplay = useMemo(() => {
    const map = new Map<string, string>()
    for (const league of cachedLeagues ?? []) {
      // {league} variable uses league_alias if available, otherwise name
      map.set(league.slug, getLeagueDisplayName(league, true))
    }
    return (slug: string | null | undefined) => {
      if (!slug) return "-"
      return map.get(slug) ?? slug.toUpperCase()
    }
  }, [cachedLeagues])

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
      toast.success(`${group.enabled ? "Disabled" : "Enabled"} group "${getDisplayName(group)}"`)
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

  const handleClearCache = async (group: EventGroup) => {
    try {
      const result = await clearCacheMutation.mutateAsync(group.id)
      toast.success(`Cleared ${result.entries_cleared} cache entries for "${getDisplayName(group)}"`)
      setClearCacheConfirm(null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to clear cache")
    }
  }

  const handleBulkClearCache = async () => {
    try {
      const result = await clearCachesBulkMutation.mutateAsync(Array.from(selectedIds))
      toast.success(`Cleared ${result.total_cleared} cache entries across ${result.by_group?.length || 0} groups`)
      setShowBulkClearCache(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to clear cache")
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

  // Check if selection has mixed group_modes (single vs multi)
  const hasMixedModes = useMemo(() => {
    if (!data?.groups || selectedIds.size === 0) return false
    const selectedGroups = data.groups.filter(g => selectedIds.has(g.id))
    const modes = new Set(selectedGroups.map(g => g.group_mode))
    return modes.size > 1
  }, [data?.groups, selectedIds])

  // Reset bulk edit form state
  const resetBulkEditForm = () => {
    setBulkEditLeaguesEnabled(false)
    setBulkEditLeagues([])
    setBulkEditTemplateEnabled(false)
    setBulkEditTemplateId(null)
    setBulkEditClearTemplate(false)
    setBulkEditChannelGroupEnabled(false)
    setBulkEditChannelGroupId(null)
    setBulkEditChannelGroupMode('static')
    setBulkEditClearChannelGroup(false)
    setBulkEditProfilesEnabled(false)
    setBulkEditProfileIds([])
    setBulkEditUseDefaultProfiles(true)
    setBulkEditSortOrderEnabled(false)
    setBulkEditSortOrder("time")
    setBulkEditOverlapHandlingEnabled(false)
    setBulkEditOverlapHandling("add_stream")
  }

  const handleBulkEdit = async () => {
    const ids = Array.from(selectedIds)

    // Build request with only enabled fields
    const request: {
      group_ids: number[]
      leagues?: string[]
      template_id?: number | null
      channel_group_id?: number | null
      channel_group_mode?: 'static' | 'sport' | 'league'
      channel_profile_ids?: (number | string)[]
      channel_sort_order?: string
      overlap_handling?: string
      clear_template?: boolean
      clear_channel_group_id?: boolean
      clear_channel_profile_ids?: boolean
    } = { group_ids: ids }

    if (bulkEditLeaguesEnabled && bulkEditLeagues.length > 0) {
      request.leagues = bulkEditLeagues
    }
    if (bulkEditTemplateEnabled) {
      if (bulkEditClearTemplate) {
        request.clear_template = true
      } else if (bulkEditTemplateId) {
        request.template_id = bulkEditTemplateId
      }
    }
    if (bulkEditChannelGroupEnabled) {
      if (bulkEditClearChannelGroup) {
        request.clear_channel_group_id = true
      } else {
        request.channel_group_mode = bulkEditChannelGroupMode
        if (bulkEditChannelGroupMode === 'static' && bulkEditChannelGroupId) {
          request.channel_group_id = bulkEditChannelGroupId
        }
      }
    }
    if (bulkEditProfilesEnabled) {
      if (bulkEditUseDefaultProfiles) {
        // Use default = clear and fall back to global setting (null)
        request.clear_channel_profile_ids = true
      } else {
        // Custom selection (could be empty [] for "no profiles" or specific ids)
        request.channel_profile_ids = bulkEditProfileIds
      }
    }
    if (bulkEditSortOrderEnabled) {
      request.channel_sort_order = bulkEditSortOrder
    }
    if (bulkEditOverlapHandlingEnabled) {
      request.overlap_handling = bulkEditOverlapHandling
    }

    try {
      const result = await bulkUpdateMutation.mutateAsync(request)
      if (result.total_failed > 0) {
        toast.warning(`Updated ${result.total_updated} groups, ${result.total_failed} failed`)
      } else {
        toast.success(`Updated ${result.total_updated} groups`)
      }
      setSelectedIds(new Set())
      setShowBulkEdit(false)
      resetBulkEditForm()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update groups")
    }
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
            <p className="text-destructive">Error loading groups: {error instanceof Error ? error.message : "An unknown error occurred"}</p>
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
                      {stats.filteredNotEvent > 0 && (
                        <div className="flex justify-between text-sm">
                          <span>Not Event Stream</span>
                          <span className="font-medium">{stats.filteredNotEvent}</span>
                        </div>
                      )}
                      {stats.filteredIncludeRegex > 0 && (
                        <div className="flex justify-between text-sm">
                          <span>Include Regex not Matched</span>
                          <span className="font-medium">{stats.filteredIncludeRegex}</span>
                        </div>
                      )}
                      {stats.filteredExcludeRegex > 0 && (
                        <div className="flex justify-between text-sm">
                          <span>Exclude Regex Matched</span>
                          <span className="font-medium">{stats.filteredExcludeRegex}</span>
                        </div>
                      )}
                      {(stats.filteredTeam ?? 0) > 0 && (
                        <div className="flex justify-between text-sm">
                          <span>Team Filter</span>
                          <span className="font-medium">{stats.filteredTeam}</span>
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

            {/* Excluded */}
            <div className="group relative">
              <div className="bg-secondary rounded px-3 py-2 cursor-help">
                <div className={`text-xl font-bold ${stats.streamsExcluded > 0 ? 'text-yellow-500' : ''}`}>{stats.streamsExcluded}</div>
                <div className="text-[0.65rem] text-muted-foreground uppercase tracking-wider">Excluded</div>
              </div>
              {stats.streamsExcluded > 0 && (
                <div className="absolute left-0 top-full mt-1 z-50 hidden group-hover:block">
                  <Card className="p-3 shadow-lg border min-w-[200px]">
                    <div className="text-xs font-medium text-muted-foreground mb-2">Exclusion Breakdown</div>
                    <div className="space-y-1">
                      {stats.excludedLeagueNotIncluded > 0 && (
                        <div className="flex justify-between text-sm">
                          <span>League Not Enabled</span>
                          <span className="font-medium">{stats.excludedLeagueNotIncluded}</span>
                        </div>
                      )}
                      {stats.excludedEventFinal > 0 && (
                        <div className="flex justify-between text-sm">
                          <span>Event Final</span>
                          <span className="font-medium">{stats.excludedEventFinal}</span>
                        </div>
                      )}
                      {stats.excludedEventPast > 0 && (
                        <div className="flex justify-between text-sm">
                          <span>Event in Past</span>
                          <span className="font-medium">{stats.excludedEventPast}</span>
                        </div>
                      )}
                      {stats.excludedBeforeWindow > 0 && (
                        <div className="flex justify-between text-sm">
                          <span>Event in Future</span>
                          <span className="font-medium">{stats.excludedBeforeWindow}</span>
                        </div>
                      )}
                      <div className="flex justify-between text-sm font-medium pt-1 border-t">
                        <span>Total</span>
                        <span>{stats.streamsExcluded}</span>
                      </div>
                    </div>
                  </Card>
                </div>
              )}
            </div>

            {/* Matched - color based on match rate */}
            <div className="bg-secondary rounded px-3 py-2">
              <div className={`text-xl font-bold ${
                stats.matchRate >= 85 ? 'text-green-500' :
                stats.matchRate >= 60 ? 'text-orange-500' :
                stats.matchRate > 0 ? 'text-red-500' : ''
              }`}>
                {stats.matched}/{stats.matched + stats.failedCount}
              </div>
              <div className="text-[0.65rem] text-muted-foreground uppercase tracking-wider">
                Matched ({stats.matchRate}%)
              </div>
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
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowBulkClearCache(true)}
                  disabled={clearCachesBulkMutation.isPending}
                >
                  <RotateCcw className="h-3 w-3 mr-1" />
                  Clear Cache
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    if (selectedIds.size === 1) {
                      const groupId = Array.from(selectedIds)[0]
                      navigate(`/event-groups/${groupId}`)
                    } else {
                      setShowBulkEdit(true)
                    }
                  }}
                  disabled={hasMixedModes}
                  title={hasMixedModes ? "Cannot edit groups with different modes (single/multi)" : selectedIds.size === 1 ? "Edit group" : "Edit selected groups"}
                >
                  <Pencil className="h-3 w-3 mr-1" />
                  Edit
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
                              <span>{getDisplayName(group)}</span>
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
                                title={`Child of: ${parentGroup ? getDisplayName(parentGroup) : 'parent'}`}
                              >
                                ↳ {parentGroup ? ((() => {
                                  const name = getDisplayName(parentGroup)
                                  const chars = [...name] // Properly handles Unicode/emojis
                                  return chars.length > 15 ? chars.slice(0, 15).join("") + "…" : name
                                })()) : "parent"}
                              </Badge>
                            </div>
                          ) : (
                            <div className="flex items-center gap-2 flex-wrap">
                              <span>{getDisplayName(group)}</span>
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
                        <Badge variant="outline" className="italic text-muted-foreground">
                          Unassigned
                        </Badge>
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
                          onClick={() => setClearCacheConfirm(group)}
                          disabled={clearCacheMutation.isPending}
                          title="Clear match cache"
                        >
                          {clearCacheMutation.isPending &&
                          clearCacheMutation.variables === group.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <RotateCcw className="h-4 w-4" />
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

      {/* Team Aliases Section - Compact like V1 */}
      <div className="mt-4">
        <div className="flex items-center gap-2 mb-1.5">
          <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Team Aliases</h3>
          <span className="text-[0.65rem] text-muted-foreground/70">({aliases?.length ?? 0})</span>
          <Button
            variant="ghost"
            size="sm"
            className="h-5 px-1.5 text-xs ml-auto"
            onClick={() => {
              setAliasForm({ alias: "", league: "", team_id: "", team_name: "" })
              setAliasSelectedTeams([])
              setShowAliasModal(true)
            }}
          >
            <Plus className="h-3 w-3 mr-0.5" />
            Add
          </Button>
        </div>

        {aliases && aliases.length > 0 ? (
          <div className="border rounded overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow className="h-6 bg-muted/30">
                  <TableHead className="text-[0.65rem] py-1 font-medium">Alias</TableHead>
                  <TableHead className="text-[0.65rem] py-1 font-medium">League</TableHead>
                  <TableHead className="text-[0.65rem] py-1 font-medium">Maps To</TableHead>
                  <TableHead className="text-[0.65rem] py-1 w-8"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {aliases.map((alias) => (
                  <TableRow key={alias.id} className="h-7">
                    <TableCell className="py-1 font-mono text-xs">{alias.alias}</TableCell>
                    <TableCell className="py-1">
                      <Badge variant="outline" className="text-[0.6rem] px-1 py-0 h-4">
                        {alias.league.toUpperCase()}
                      </Badge>
                    </TableCell>
                    <TableCell className="py-1 text-xs">{alias.team_name}</TableCell>
                    <TableCell className="py-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-5 w-5"
                        onClick={() => handleDeleteAlias(alias.id)}
                        disabled={aliasDeleting === alias.id}
                        title="Delete"
                      >
                        {aliasDeleting === alias.id ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Trash2 className="h-3 w-3 text-muted-foreground hover:text-destructive" />
                        )}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground/70 italic">
            No aliases defined. Aliases help match stream names to teams.
          </p>
        )}
      </div>

      {/* Add Alias Modal */}
      <Dialog open={showAliasModal} onOpenChange={(open) => {
        setShowAliasModal(open)
        if (!open) {
          setAliasForm({ alias: "", league: "", team_id: "", team_name: "" })
          setAliasSport("")
          setAliasSelectedTeams([])
        }
      }}>
        <DialogContent onClose={() => setShowAliasModal(false)}>
          <DialogHeader>
            <DialogTitle>Add Team Alias</DialogTitle>
            <DialogDescription>
              Create a new alias to map stream names to teams.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Alias Text</label>
              <Input
                value={aliasForm.alias}
                onChange={(e) => setAliasForm({ ...aliasForm, alias: e.target.value })}
                placeholder="e.g., spurs, man u"
              />
              <p className="text-xs text-muted-foreground">
                The text that appears in stream names (case-insensitive)
              </p>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Sport</label>
              <Select
                value={aliasSport}
                onChange={(e) => handleAliasSportChange(e.target.value)}
              >
                <option value="">Select sport...</option>
                {aliasSports.map((sport) => (
                  <option key={sport} value={sport}>
                    {sport}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">League</label>
              {!aliasSport ? (
                <p className="text-sm text-muted-foreground py-2">Select a sport first</p>
              ) : (
                <Select
                  value={aliasForm.league}
                  onChange={(e) => handleAliasLeagueChange(e.target.value)}
                >
                  <option value="">Select league...</option>
                  {aliasFilteredLeagues.map((league) => (
                    <option key={league.slug} value={league.slug}>
                      {getLeagueDisplayName(league, true)}
                    </option>
                  ))}
                </Select>
              )}
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Team</label>
              {!aliasForm.league ? (
                <p className="text-sm text-muted-foreground py-2">Select a league first</p>
              ) : (
                <TeamPicker
                  leagues={[aliasForm.league]}
                  selectedTeams={aliasSelectedTeams}
                  onSelectionChange={handleAliasTeamSelect}
                  placeholder="Search teams..."
                  singleSelect
                />
              )}
              <p className="text-xs text-muted-foreground">
                The actual team this alias should map to
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowAliasModal(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleCreateAlias}
              disabled={aliasSubmitting || !aliasForm.alias.trim() || !aliasForm.team_id}
            >
              {aliasSubmitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Save Alias
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={deleteConfirm !== null}
        onOpenChange={(open) => !open && setDeleteConfirm(null)}
      >
        <DialogContent onClose={() => setDeleteConfirm(null)}>
          <DialogHeader>
            <DialogTitle>Delete Event Group</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete "{deleteConfirm ? getDisplayName(deleteConfirm) : ''}"? This will
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

      {/* Clear Cache Confirmation Dialog */}
      <Dialog
        open={clearCacheConfirm !== null}
        onOpenChange={(open) => !open && setClearCacheConfirm(null)}
      >
        <DialogContent onClose={() => setClearCacheConfirm(null)}>
          <DialogHeader>
            <DialogTitle>Clear Match Cache</DialogTitle>
            <DialogDescription>
              Clear the stream match cache for "{clearCacheConfirm ? getDisplayName(clearCacheConfirm) : ''}"?
              This will force re-matching on the next EPG generation run.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setClearCacheConfirm(null)}>
              Cancel
            </Button>
            <Button
              onClick={() => clearCacheConfirm && handleClearCache(clearCacheConfirm)}
              disabled={clearCacheMutation.isPending}
            >
              {clearCacheMutation.isPending && (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              )}
              Clear Cache
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Bulk Clear Cache Confirmation Dialog */}
      <Dialog open={showBulkClearCache} onOpenChange={setShowBulkClearCache}>
        <DialogContent onClose={() => setShowBulkClearCache(false)}>
          <DialogHeader>
            <DialogTitle>Clear Match Cache for {selectedIds.size} Groups</DialogTitle>
            <DialogDescription>
              Clear the stream match cache for {selectedIds.size} selected groups?
              This will force re-matching on the next EPG generation run.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowBulkClearCache(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleBulkClearCache}
              disabled={clearCachesBulkMutation.isPending}
            >
              {clearCachesBulkMutation.isPending && (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              )}
              Clear Cache for {selectedIds.size} Groups
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Bulk Edit Dialog */}
      <Dialog open={showBulkEdit} onOpenChange={(open) => {
        setShowBulkEdit(open)
        if (!open) resetBulkEditForm()
      }}>
        <DialogContent onClose={() => setShowBulkEdit(false)} className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>Bulk Edit ({selectedIds.size} groups)</DialogTitle>
            <DialogDescription>
              Only checked fields will be updated. Use "Clear" to remove values.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4 px-1 max-h-[60vh] overflow-y-auto">
            {/* Leagues */}
            <div className="space-y-2">
              <div
                className="flex items-center gap-2 cursor-pointer"
                onClick={() => setBulkEditLeaguesEnabled(!bulkEditLeaguesEnabled)}
              >
                <Checkbox
                  checked={bulkEditLeaguesEnabled}
                  onCheckedChange={(checked) => setBulkEditLeaguesEnabled(!!checked)}
                />
                <span className="text-sm font-medium">Leagues</span>
              </div>
              {bulkEditLeaguesEnabled && (
                <LeaguePicker
                  selectedLeagues={bulkEditLeagues}
                  onSelectionChange={setBulkEditLeagues}
                  maxHeight="max-h-48"
                  maxBadges={5}
                />
              )}
            </div>

            {/* Template */}
            <div className="space-y-2">
              <div
                className="flex items-center gap-2 cursor-pointer"
                onClick={() => {
                  const newVal = !bulkEditTemplateEnabled
                  setBulkEditTemplateEnabled(newVal)
                  if (!newVal) {
                    setBulkEditTemplateId(null)
                    setBulkEditClearTemplate(false)
                  }
                }}
              >
                <Checkbox
                  checked={bulkEditTemplateEnabled}
                  onCheckedChange={(checked) => {
                    setBulkEditTemplateEnabled(!!checked)
                    if (!checked) {
                      setBulkEditTemplateId(null)
                      setBulkEditClearTemplate(false)
                    }
                  }}
                />
                <span className="text-sm font-medium">Template</span>
              </div>
              {bulkEditTemplateEnabled && (
                <>
                  <Select
                    value={bulkEditClearTemplate ? "" : (bulkEditTemplateId?.toString() ?? "")}
                    onChange={(e) => {
                      setBulkEditTemplateId(e.target.value ? parseInt(e.target.value) : null)
                      setBulkEditClearTemplate(false)
                    }}
                    disabled={bulkEditClearTemplate}
                  >
                    <option value="">Select template...</option>
                    {eventTemplates.map((template) => (
                      <option key={template.id} value={template.id.toString()}>
                        {template.name}
                      </option>
                    ))}
                  </Select>
                  <div
                    className="flex items-center gap-2 cursor-pointer"
                    onClick={() => {
                      const newVal = !bulkEditClearTemplate
                      setBulkEditClearTemplate(newVal)
                      if (newVal) setBulkEditTemplateId(null)
                    }}
                  >
                    <Checkbox
                      checked={bulkEditClearTemplate}
                      onCheckedChange={(checked) => {
                        setBulkEditClearTemplate(!!checked)
                        if (checked) setBulkEditTemplateId(null)
                      }}
                    />
                    <span className="text-xs text-muted-foreground">Clear (unassign template)</span>
                  </div>
                </>
              )}
            </div>

            {/* Channel Group */}
            <div className="space-y-2">
              <div
                className="flex items-center gap-2 cursor-pointer"
                onClick={() => {
                  const newVal = !bulkEditChannelGroupEnabled
                  setBulkEditChannelGroupEnabled(newVal)
                  if (!newVal) {
                    setBulkEditChannelGroupId(null)
                    setBulkEditChannelGroupMode('static')
                    setBulkEditClearChannelGroup(false)
                  }
                }}
              >
                <Checkbox
                  checked={bulkEditChannelGroupEnabled}
                  onCheckedChange={(checked) => {
                    setBulkEditChannelGroupEnabled(!!checked)
                    if (!checked) {
                      setBulkEditChannelGroupId(null)
                      setBulkEditChannelGroupMode('static')
                      setBulkEditClearChannelGroup(false)
                    }
                  }}
                />
                <span className="text-sm font-medium">Channel Group</span>
              </div>
              {bulkEditChannelGroupEnabled && (
                <div className="space-y-3">
                  {/* Clear option */}
                  <div
                    className="flex items-center gap-2 cursor-pointer"
                    onClick={() => {
                      const newVal = !bulkEditClearChannelGroup
                      setBulkEditClearChannelGroup(newVal)
                      if (newVal) {
                        setBulkEditChannelGroupId(null)
                        setBulkEditChannelGroupMode('static')
                      }
                    }}
                  >
                    <Checkbox
                      checked={bulkEditClearChannelGroup}
                      onCheckedChange={(checked) => {
                        setBulkEditClearChannelGroup(!!checked)
                        if (checked) {
                          setBulkEditChannelGroupId(null)
                          setBulkEditChannelGroupMode('static')
                        }
                      }}
                    />
                    <span className="text-xs text-muted-foreground">Clear (remove from channel group)</span>
                  </div>

                  {!bulkEditClearChannelGroup && (
                    <div className="space-y-2">
                      {/* Static group option */}
                      <div>
                        <label className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="radio"
                            name="bulk_channel_group_mode"
                            checked={bulkEditChannelGroupMode === "static"}
                            onChange={() => setBulkEditChannelGroupMode("static")}
                            className="accent-primary"
                          />
                          <span className="text-sm">Existing group</span>
                        </label>
                        <div className={`mt-2 ml-6 ${bulkEditChannelGroupMode !== "static" ? "opacity-40 pointer-events-none" : ""}`}>
                          <Select
                            value={bulkEditChannelGroupId?.toString() ?? ""}
                            onChange={(e) => setBulkEditChannelGroupId(e.target.value ? parseInt(e.target.value) : null)}
                            disabled={bulkEditChannelGroupMode !== "static"}
                          >
                            <option value="">Select channel group...</option>
                            {channelGroups?.map((group) => (
                              <option key={group.id} value={group.id.toString()}>
                                {group.name}
                              </option>
                            ))}
                          </Select>
                        </div>
                      </div>

                      {/* Dynamic group options */}
                      <div className="border rounded-md bg-muted/30">
                        <div className="px-3 py-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wide">
                          Dynamic Groups
                        </div>
                        <div className="divide-y">
                          <label className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-accent">
                            <input
                              type="radio"
                              name="bulk_channel_group_mode"
                              checked={bulkEditChannelGroupMode === "sport"}
                              onChange={() => {
                                setBulkEditChannelGroupMode("sport")
                                setBulkEditChannelGroupId(null)
                              }}
                              className="accent-primary"
                            />
                            <div className="flex-1">
                              <code className="text-sm font-medium bg-muted px-1 rounded">{"{sport}"}</code>
                              <p className="text-xs text-muted-foreground mt-0.5">Assign channels to a group by sport name</p>
                            </div>
                          </label>
                          <label className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-accent">
                            <input
                              type="radio"
                              name="bulk_channel_group_mode"
                              checked={bulkEditChannelGroupMode === "league"}
                              onChange={() => {
                                setBulkEditChannelGroupMode("league")
                                setBulkEditChannelGroupId(null)
                              }}
                              className="accent-primary"
                            />
                            <div className="flex-1">
                              <code className="text-sm font-medium bg-muted px-1 rounded">{"{league}"}</code>
                              <p className="text-xs text-muted-foreground mt-0.5">Assign channels to a group by league name</p>
                            </div>
                          </label>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Channel Profiles */}
            <div className="space-y-2">
              <div
                className="flex items-center gap-2 cursor-pointer"
                onClick={() => {
                  const newVal = !bulkEditProfilesEnabled
                  setBulkEditProfilesEnabled(newVal)
                  if (!newVal) {
                    setBulkEditProfileIds([])
                    setBulkEditUseDefaultProfiles(true)
                  }
                }}
              >
                <Checkbox
                  checked={bulkEditProfilesEnabled}
                  onCheckedChange={(checked) => {
                    setBulkEditProfilesEnabled(!!checked)
                    if (!checked) {
                      setBulkEditProfileIds([])
                      setBulkEditUseDefaultProfiles(true)
                    }
                  }}
                />
                <span className="text-sm font-medium">Channel Profiles</span>
              </div>
              {bulkEditProfilesEnabled && (
                <div className="space-y-2">
                  <div
                    className="flex items-center gap-2 cursor-pointer"
                    onClick={() => {
                      const newVal = !bulkEditUseDefaultProfiles
                      setBulkEditUseDefaultProfiles(newVal)
                      if (newVal) {
                        setBulkEditProfileIds([])
                      }
                    }}
                  >
                    <Checkbox
                      checked={bulkEditUseDefaultProfiles}
                      onCheckedChange={(checked) => {
                        setBulkEditUseDefaultProfiles(!!checked)
                        if (checked) {
                          setBulkEditProfileIds([])
                        }
                      }}
                    />
                    <span className="text-sm font-normal">
                      Use default channel profiles
                    </span>
                  </div>
                  <ChannelProfileSelector
                    selectedIds={bulkEditProfileIds}
                    onChange={setBulkEditProfileIds}
                    disabled={bulkEditUseDefaultProfiles}
                  />
                  <p className="text-xs text-muted-foreground">
                    {bulkEditUseDefaultProfiles
                      ? "Using default profiles from global settings"
                      : "Select specific profiles for these groups"}
                  </p>
                </div>
              )}
            </div>

            {/* Channel Sort Order (multi-league only) */}
            <div className="space-y-2">
              <div
                className="flex items-center gap-2 cursor-pointer"
                onClick={() => setBulkEditSortOrderEnabled(!bulkEditSortOrderEnabled)}
              >
                <Checkbox
                  checked={bulkEditSortOrderEnabled}
                  onCheckedChange={(checked) => setBulkEditSortOrderEnabled(!!checked)}
                />
                <span className="text-sm font-medium">Channel Sort Order</span>
                <span className="text-xs text-muted-foreground">(multi-league groups)</span>
              </div>
              {bulkEditSortOrderEnabled && (
                <Select
                  value={bulkEditSortOrder}
                  onChange={(e) => setBulkEditSortOrder(e.target.value)}
                >
                  <option value="time">Time</option>
                  <option value="sport_time">Sport → Time</option>
                  <option value="league_time">League → Time</option>
                </Select>
              )}
            </div>

            {/* Overlap Handling (multi-league only) */}
            <div className="space-y-2">
              <div
                className="flex items-center gap-2 cursor-pointer"
                onClick={() => setBulkEditOverlapHandlingEnabled(!bulkEditOverlapHandlingEnabled)}
              >
                <Checkbox
                  checked={bulkEditOverlapHandlingEnabled}
                  onCheckedChange={(checked) => setBulkEditOverlapHandlingEnabled(!!checked)}
                />
                <span className="text-sm font-medium">Overlap Handling</span>
                <span className="text-xs text-muted-foreground">(multi-league groups)</span>
              </div>
              {bulkEditOverlapHandlingEnabled && (
                <Select
                  value={bulkEditOverlapHandling}
                  onChange={(e) => setBulkEditOverlapHandling(e.target.value)}
                >
                  <option value="add_stream">Add stream to existing channel</option>
                  <option value="add_only">Add only (skip if no existing)</option>
                  <option value="create_all">Create all (ignore overlap)</option>
                  <option value="skip">Skip overlapping streams</option>
                </Select>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowBulkEdit(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleBulkEdit}
              disabled={bulkUpdateMutation.isPending || (!bulkEditLeaguesEnabled && !bulkEditTemplateEnabled && !bulkEditChannelGroupEnabled && !bulkEditProfilesEnabled && !bulkEditSortOrderEnabled && !bulkEditOverlapHandlingEnabled)}
            >
              {bulkUpdateMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Apply to {selectedIds.size} groups
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
                            <Badge variant="secondary">{getLeagueDisplay(stream.league)}</Badge>
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
