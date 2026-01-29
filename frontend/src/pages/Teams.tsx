import { useState, useEffect, useMemo } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import {
  Plus,
  Trash2,
  Pencil,
  Loader2,
  ArrowUp,
  ArrowDown,
  ArrowUpDown,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Checkbox } from "@/components/ui/checkbox"
import { RichTooltip } from "@/components/ui/rich-tooltip"
import { FilterSelect } from "@/components/ui/filter-select"
import { cn, getLeagueDisplayName, getSportEmoji } from "@/lib/utils"
import {
  useTeams,
  useUpdateTeam,
  useDeleteTeam,
} from "@/hooks/useTeams"
import { useTemplates } from "@/hooks/useTemplates"
import type { Team } from "@/api/teams"
import { getLeagues } from "@/api/teams"
import { statsApi } from "@/api/stats"
import { useQuery } from "@tanstack/react-query"

type ActiveFilter = "" | "active" | "inactive"
type SortColumn = "team" | "league" | "sport" | "template" | "channel" | "status" | null
type SortDirection = "asc" | "desc"

interface TeamUpdate {
  team_name?: string
  team_abbrev?: string | null
  team_logo_url?: string | null
  channel_id?: string
  channel_logo_url?: string | null
  template_id?: number | null
  active?: boolean
}

interface EditTeamDialogProps {
  team: Team
  templates: Array<{ id: number; name: string }>
  open: boolean
  onOpenChange: (open: boolean) => void
  onSave: (data: TeamUpdate) => Promise<void>
  isSaving: boolean
}

function EditTeamDialog({ team, templates, open, onOpenChange, onSave, isSaving }: EditTeamDialogProps) {
  const [formData, setFormData] = useState<TeamUpdate>({
    team_name: team.team_name,
    team_abbrev: team.team_abbrev,
    team_logo_url: team.team_logo_url,
    channel_id: team.channel_id,
    channel_logo_url: team.channel_logo_url,
    template_id: team.template_id,
    active: team.active,
  })

  const handleSubmit = async () => {
    if (!formData.team_name?.trim()) {
      toast.error("Team name is required")
      return
    }
    if (!formData.channel_id?.trim()) {
      toast.error("Channel ID is required")
      return
    }
    await onSave(formData)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg" onClose={() => onOpenChange(false)}>
        <DialogHeader>
          <DialogTitle>Edit Team</DialogTitle>
          <DialogDescription>Update team channel settings.</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="team_name">Team Name</Label>
              <Input
                id="team_name"
                value={formData.team_name ?? ""}
                onChange={(e) => setFormData({ ...formData, team_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="team_abbrev">Abbreviation</Label>
              <Input
                id="team_abbrev"
                value={formData.team_abbrev ?? ""}
                onChange={(e) => setFormData({ ...formData, team_abbrev: e.target.value || null })}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="channel_id">Channel ID</Label>
            <Input
              id="channel_id"
              value={formData.channel_id ?? ""}
              onChange={(e) => setFormData({ ...formData, channel_id: e.target.value })}
            />
            <p className="text-xs text-muted-foreground">Unique identifier for XMLTV output</p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="template_id">Template</Label>
            <Select
              id="template_id"
              value={formData.template_id?.toString() ?? ""}
              onChange={(e) => setFormData({ ...formData, template_id: e.target.value ? parseInt(e.target.value) : null })}
            >
              <option value="">Unassigned</option>
              {templates.map((template) => (
                <option key={template.id} value={template.id.toString()}>
                  {template.name}
                </option>
              ))}
            </Select>
          </div>

          <div className="flex items-center gap-2">
            <Switch
              checked={formData.active ?? true}
              onCheckedChange={(checked) => setFormData({ ...formData, active: checked })}
            />
            <Label className="font-normal">Active</Label>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={isSaving}>
            {isSaving && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            Update
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function Teams() {
  const navigate = useNavigate()
  const { data: teams, isLoading, error, refetch } = useTeams()
  const { data: templates } = useTemplates()
  const { data: leaguesResponse } = useQuery({ queryKey: ["leagues"], queryFn: () => getLeagues() })
  const cachedLeagues = leaguesResponse?.leagues
  const { data: liveStats } = useQuery({
    queryKey: ["stats", "live", "team"],
    queryFn: () => statsApi.getLiveStats("team"),
    refetchInterval: 60000, // Refresh every minute
  })

  // Create league lookup maps (logo and display alias)
  const leagueLookup = useMemo(() => {
    const logos: Record<string, string> = {}
    const aliases: Record<string, string> = {}  // {league} variable value
    if (cachedLeagues) {
      for (const league of cachedLeagues) {
        if (league.logo_url) {
          logos[league.slug] = league.logo_url
        }
        // league_alias if set, else name (display_name), else slug uppercase
        aliases[league.slug] = getLeagueDisplayName(league, true) || league.slug.toUpperCase()
      }
    }
    return { logos, aliases }
  }, [cachedLeagues])
  const updateMutation = useUpdateTeam()
  const deleteMutation = useDeleteTeam()

  // Filter state
  const [leagueFilter, setLeagueFilter] = useState<string>("")
  const [sportFilter, setSportFilter] = useState<string>("")
  const [templateFilter, setTemplateFilter] = useState<string>("")
  const [activeFilter, setActiveFilter] = useState<ActiveFilter>("")

  // Sorting state
  const [sortColumn, setSortColumn] = useState<SortColumn>(null)
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc")

  // Bulk selection state
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [lastClickedIndex, setLastClickedIndex] = useState<number | null>(null)
  const [bulkTemplateId, setBulkTemplateId] = useState<number | null>(null)
  const [showBulkTemplate, setShowBulkTemplate] = useState(false)
  const [showBulkDelete, setShowBulkDelete] = useState(false)
  const [showBulkChannelId, setShowBulkChannelId] = useState(false)
  const [channelIdMode, setChannelIdMode] = useState<"default" | "custom">("default")
  const [customChannelIdFormat, setCustomChannelIdFormat] = useState("")
  const [isUpdatingChannelIds, setIsUpdatingChannelIds] = useState(false)
  const defaultChannelIdFormat = "{team_name_pascal}.{league_id}"

  // Edit dialog state
  const [showDialog, setShowDialog] = useState(false)
  const [editingTeam, setEditingTeam] = useState<Team | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<Team | null>(null)

  // Get unique leagues from teams (using primary_league)
  const leagues = useMemo(() => {
    if (!teams) return []
    const uniqueLeagues = [...new Set(teams.map((t) => t.primary_league))]
    return uniqueLeagues.sort()
  }, [teams])

  // Get unique sports from teams
  const sports = useMemo(() => {
    if (!teams) return []
    const uniqueSports = [...new Set(teams.map((t) => t.sport))]
    return uniqueSports.sort()
  }, [teams])

  // Filter templates to only show team templates
  const teamTemplates = useMemo(() => {
    return templates?.filter((t) => t.template_type === "team") ?? []
  }, [templates])

  // Calculate team stats for tiles
  const teamStats = useMemo(() => {
    if (!teams) return { total: 0, enabled: 0, byLeague: {} as Record<string, { total: number; enabled: number }> }

    const byLeague: Record<string, { total: number; enabled: number }> = {}

    for (const team of teams) {
      // Count by primary league
      if (!byLeague[team.primary_league]) {
        byLeague[team.primary_league] = { total: 0, enabled: 0 }
      }
      byLeague[team.primary_league].total++
      if (team.active) {
        byLeague[team.primary_league].enabled++
      }
    }

    return {
      total: teams.length,
      enabled: teams.filter((t) => t.active).length,
      byLeague,
    }
  }, [teams])

  // Filter and sort teams
  const filteredTeams = useMemo(() => {
    if (!teams) return []

    // First filter
    let result = teams.filter((team) => {
      // League filter - match if any of the team's leagues match
      if (leagueFilter && !team.leagues.includes(leagueFilter)) return false

      // Sport filter
      if (sportFilter && team.sport !== sportFilter) return false

      // Template filter
      if (templateFilter) {
        if (templateFilter === "_unassigned") {
          if (team.template_id !== null) return false
        } else {
          if (team.template_id?.toString() !== templateFilter) return false
        }
      }

      // Active filter (empty string means show all)
      if (activeFilter === "active" && !team.active) return false
      if (activeFilter === "inactive" && team.active) return false

      return true
    })

    // Then sort
    if (sortColumn) {
      result = [...result].sort((a, b) => {
        let aVal: string
        let bVal: string

        switch (sortColumn) {
          case "team":
            aVal = a.team_name.toLowerCase()
            bVal = b.team_name.toLowerCase()
            break
          case "league":
            aVal = a.primary_league.toLowerCase()
            bVal = b.primary_league.toLowerCase()
            break
          case "sport":
            aVal = a.sport.toLowerCase()
            bVal = b.sport.toLowerCase()
            break
          case "template":
            aVal = (teamTemplates.find((t) => t.id === a.template_id)?.name ?? "zzz").toLowerCase()
            bVal = (teamTemplates.find((t) => t.id === b.template_id)?.name ?? "zzz").toLowerCase()
            break
          case "channel":
            aVal = a.channel_id.toLowerCase()
            bVal = b.channel_id.toLowerCase()
            break
          case "status":
            aVal = a.active ? "active" : "inactive"
            bVal = b.active ? "active" : "inactive"
            break
          default:
            return 0
        }

        if (aVal < bVal) return sortDirection === "asc" ? -1 : 1
        if (aVal > bVal) return sortDirection === "asc" ? 1 : -1
        return 0
      })
    }

    return result
  }, [teams, leagueFilter, sportFilter, templateFilter, activeFilter, sortColumn, sortDirection, teamTemplates])

  // Clear selection when filters change
  useEffect(() => {
    setSelectedIds(new Set())
    setLastClickedIndex(null)
  }, [leagueFilter, sportFilter, templateFilter, activeFilter])

  // Handle column sort
  const handleSort = (column: SortColumn) => {
    if (sortColumn === column) {
      // Toggle direction if same column
      setSortDirection(sortDirection === "asc" ? "desc" : "asc")
    } else {
      // New column, start with ascending
      setSortColumn(column)
      setSortDirection("asc")
    }
  }

  // Render sort icon
  const renderSortIcon = (column: SortColumn) => {
    if (sortColumn !== column) {
      return <ArrowUpDown className="h-3 w-3 ml-1 opacity-50" />
    }
    return sortDirection === "asc"
      ? <ArrowUp className="h-3 w-3 ml-1" />
      : <ArrowDown className="h-3 w-3 ml-1" />
  }

  const openEdit = (team: Team) => {
    setEditingTeam(team)
    setShowDialog(true)
  }

  const handleDelete = async () => {
    if (!deleteConfirm) return

    try {
      await deleteMutation.mutateAsync(deleteConfirm.id)
      toast.success(`Deleted team "${deleteConfirm.team_name}"`)
      setDeleteConfirm(null)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete team")
    }
  }

  const handleToggleActive = async (team: Team) => {
    try {
      await updateMutation.mutateAsync({
        teamId: team.id,
        data: { active: !team.active },
      })
      toast.success(`${team.active ? "Disabled" : "Enabled"} team "${team.team_name}"`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to toggle team status")
    }
  }

  // Bulk actions
  const toggleSelectAll = () => {
    if (selectedIds.size === filteredTeams.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(filteredTeams.map((t) => t.id)))
    }
  }

  const toggleSelect = (id: number, index: number, shiftKey: boolean) => {
    if (shiftKey && lastClickedIndex !== null) {
      // Shift-click: select range
      const start = Math.min(lastClickedIndex, index)
      const end = Math.max(lastClickedIndex, index)

      setSelectedIds((prev) => {
        const next = new Set(prev)
        for (let i = start; i <= end; i++) {
          next.add(filteredTeams[i].id)
        }
        return next
      })
    } else {
      // Regular click: toggle single item
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
    setLastClickedIndex(index)
  }

  const handleBulkToggleActive = async (active: boolean) => {
    const ids = Array.from(selectedIds)
    let succeeded = 0
    for (const id of ids) {
      try {
        await updateMutation.mutateAsync({ teamId: id, data: { active } })
        succeeded++
      } catch {
        // Continue with others
      }
    }
    toast.success(`${active ? "Enabled" : "Disabled"} ${succeeded} teams`)
    setSelectedIds(new Set())
  }

  const handleBulkAssignTemplate = async () => {
    const ids = Array.from(selectedIds)
    let succeeded = 0
    for (const id of ids) {
      try {
        await updateMutation.mutateAsync({ teamId: id, data: { template_id: bulkTemplateId } })
        succeeded++
      } catch {
        // Continue with others
      }
    }
    toast.success(`Assigned template to ${succeeded} teams`)
    setSelectedIds(new Set())
    setShowBulkTemplate(false)
    setBulkTemplateId(null)
  }

  const handleBulkDelete = async () => {
    const ids = Array.from(selectedIds)
    let succeeded = 0
    for (const id of ids) {
      try {
        await deleteMutation.mutateAsync(id)
        succeeded++
      } catch {
        // Continue with others
      }
    }
    toast.success(`Deleted ${succeeded} teams`)
    setSelectedIds(new Set())
    setShowBulkDelete(false)
  }

  const handleBulkUpdateChannelIds = async () => {
    const formatTemplate = channelIdMode === "default" ? defaultChannelIdFormat : customChannelIdFormat
    if (!formatTemplate.trim()) {
      toast.error("Please enter a format template")
      return
    }

    setIsUpdatingChannelIds(true)
    try {
      const response = await fetch("/api/v1/teams/bulk-channel-id", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          team_ids: Array.from(selectedIds),
          format_template: formatTemplate,
        }),
      })

      const result = await response.json()

      if (response.ok && result.updated > 0) {
        toast.success(`Updated channel IDs for ${result.updated} team(s)`)
        refetch()
        setSelectedIds(new Set())
        setShowBulkChannelId(false)
        setChannelIdMode("default")
        setCustomChannelIdFormat("")
      } else if (result.errors?.length > 0) {
        toast.error(result.errors[0])
      } else {
        toast.error("Failed to update channel IDs")
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update channel IDs")
    } finally {
      setIsUpdatingChannelIds(false)
    }
  }

  if (error) {
    return (
      <div className="space-y-2">
        <h1 className="text-xl font-bold">Teams</h1>
        <Card className="border-destructive p-4">
          <p className="text-destructive">Error loading teams: {error.message}</p>
          <p className="text-destructive">Error loading teams: {error instanceof Error ? error.message : "An unknown error occurred"}</p>
          <Button className="mt-4" onClick={() => refetch()}>
            Retry
          </Button>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {/* Header - Compact */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">Teams</h1>
          <p className="text-sm text-muted-foreground">
            Team-based EPG channel configurations
          </p>
        </div>
        <Button size="sm" onClick={() => navigate("/teams/import")}>
          <Plus className="h-4 w-4 mr-1" />
          Import
        </Button>
      </div>

      {/* Stats Tiles - V1 Style: Grid with 4 equal columns filling width */}
      {teams && teams.length > 0 && (
        <div className="grid grid-cols-4 gap-3">
          {/* Configured */}
          <div className="group relative">
            <div className="bg-secondary rounded px-3 py-2 cursor-help">
              <div className="text-xl font-bold">{teamStats.total}</div>
              <div className="text-[0.65rem] text-muted-foreground uppercase tracking-wider">Configured</div>
            </div>
            <div className="absolute left-0 top-full mt-1 z-50 hidden group-hover:block">
              <Card className="p-3 shadow-lg border min-w-[160px]">
                <div className="text-xs font-medium text-muted-foreground mb-2">By League</div>
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {Object.entries(teamStats.byLeague)
                    .sort(([a], [b]) => (leagueLookup.aliases[a] || a).localeCompare(leagueLookup.aliases[b] || b))
                    .map(([league, counts]) => (
                      <div key={league} className="flex justify-between text-sm">
                        <span className="truncate max-w-[100px]">{leagueLookup.aliases[league] || league.toUpperCase()}</span>
                        <span className="font-medium ml-2">{counts.total}</span>
                      </div>
                    ))}
                </div>
              </Card>
            </div>
          </div>

          {/* Enabled */}
          <div className="group relative">
            <div className="bg-secondary rounded px-3 py-2 cursor-help">
              <div className="text-xl font-bold text-green-500">{teamStats.enabled}</div>
              <div className="text-[0.65rem] text-muted-foreground uppercase tracking-wider">Enabled</div>
            </div>
            <div className="absolute left-0 top-full mt-1 z-50 hidden group-hover:block">
              <Card className="p-3 shadow-lg border min-w-[160px]">
                <div className="text-xs font-medium text-muted-foreground mb-2">By League</div>
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {Object.entries(teamStats.byLeague)
                    .filter(([, counts]) => counts.enabled > 0)
                    .sort(([a], [b]) => (leagueLookup.aliases[a] || a).localeCompare(leagueLookup.aliases[b] || b))
                    .map(([league, counts]) => (
                      <div key={league} className="flex justify-between text-sm">
                        <span className="truncate max-w-[100px]">{leagueLookup.aliases[league] || league.toUpperCase()}</span>
                        <span className="font-medium ml-2">{counts.enabled}</span>
                      </div>
                    ))}
                </div>
              </Card>
            </div>
          </div>

          {/* Games Today */}
          <div className="group relative">
            <div className="bg-secondary rounded px-3 py-2 cursor-help">
              <div className={cn(
                "text-xl font-bold",
                liveStats?.team.games_today ? "" : "text-muted-foreground"
              )}>
                {liveStats?.team.games_today ?? "--"}
              </div>
              <div className="text-[0.65rem] text-muted-foreground uppercase tracking-wider">Games Today</div>
            </div>
            {liveStats?.team.by_league && liveStats.team.by_league.length > 0 && (
              <div className="absolute left-0 top-full mt-1 z-50 hidden group-hover:block">
                <Card className="p-3 shadow-lg border min-w-[120px]">
                  <div className="text-xs font-medium text-muted-foreground mb-2">By League</div>
                  <div className="space-y-1 max-h-48 overflow-y-auto">
                    {liveStats.team.by_league.map((item) => (
                      <div key={item.league} className="flex justify-between text-sm gap-3">
                        <span>{item.league}</span>
                        <span className="font-medium">{item.count}</span>
                      </div>
                    ))}
                  </div>
                </Card>
              </div>
            )}
          </div>

          {/* Live Now */}
          <div className="group relative">
            <div className={cn("bg-secondary rounded px-3 py-2", liveStats?.team.live_events?.length && "cursor-help")}>
              <div className={cn(
                "text-xl font-bold",
                liveStats?.team.live_now ? "text-green-500" : "text-muted-foreground"
              )}>
                {liveStats?.team.live_now ?? "--"}
              </div>
              <div className="text-[0.65rem] text-muted-foreground uppercase tracking-wider">Live Now</div>
            </div>
            {liveStats?.team.live_events && liveStats.team.live_events.length > 0 && (
              <div className="absolute right-0 top-full mt-1 z-50 hidden group-hover:block">
                <Card className="p-3 shadow-lg border min-w-[240px] max-w-[320px]">
                  <div className="text-xs font-medium text-muted-foreground mb-2">Live Now</div>
                  <div className="space-y-2 max-h-48 overflow-y-auto">
                    {liveStats.team.live_events.map((event, idx) => (
                      <div key={idx} className="text-sm">
                        <div className="font-medium truncate" title={event.title}>
                          {event.title}
                        </div>
                        <div className="flex items-center justify-between text-xs text-muted-foreground">
                          <span>{event.league}</span>
                          <span>Started {new Date(event.start_time).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
                        </div>
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
                {selectedIds.size} team{selectedIds.size > 1 ? "s" : ""} selected
              </span>
              <div className="flex items-center gap-1">
                <Button variant="outline" size="sm" onClick={() => setSelectedIds(new Set())}>
                  Clear
                </Button>
                <Button variant="outline" size="sm" onClick={() => handleBulkToggleActive(true)}>
                  Enable
                </Button>
                <Button variant="outline" size="sm" onClick={() => handleBulkToggleActive(false)}>
                  Disable
                </Button>
                <Button variant="outline" size="sm" onClick={() => setShowBulkTemplate(true)}>
                  Assign Template
                </Button>
                <Button variant="outline" size="sm" onClick={() => setShowBulkChannelId(true)}>
                  Channel ID
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

      {/* Teams Table - No card wrapper for more compact look */}
      <div className="border border-border rounded-lg overflow-hidden">
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : filteredTeams.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              {teams?.length === 0
                ? "No teams configured. Add a team to generate team-based EPG."
                : "No teams match the current filters."}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8">
                    <Checkbox
                      checked={
                        selectedIds.size === filteredTeams.length && filteredTeams.length > 0
                      }
                      onCheckedChange={toggleSelectAll}
                    />
                  </TableHead>
                  <TableHead
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => handleSort("team")}
                  >
                    <div className="flex items-center">
                      Team {renderSortIcon("team")}
                    </div>
                  </TableHead>
                  <TableHead
                    className="w-[100px] cursor-pointer hover:bg-muted/50"
                    onClick={() => handleSort("league")}
                  >
                    <div className="flex items-center">
                      League {renderSortIcon("league")}
                    </div>
                  </TableHead>
                  <TableHead
                    className="w-[70px] text-center cursor-pointer hover:bg-muted/50"
                    onClick={() => handleSort("sport")}
                  >
                    <div className="flex items-center justify-center">
                      Sport {renderSortIcon("sport")}
                    </div>
                  </TableHead>
                  <TableHead
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => handleSort("channel")}
                  >
                    <div className="flex items-center">
                      Channel ID {renderSortIcon("channel")}
                    </div>
                  </TableHead>
                  <TableHead
                    className="w-[120px] cursor-pointer hover:bg-muted/50"
                    onClick={() => handleSort("template")}
                  >
                    <div className="flex items-center">
                      Template {renderSortIcon("template")}
                    </div>
                  </TableHead>
                  <TableHead
                    className="w-[70px] cursor-pointer hover:bg-muted/50"
                    onClick={() => handleSort("status")}
                  >
                    <div className="flex items-center">
                      Status {renderSortIcon("status")}
                    </div>
                  </TableHead>
                  <TableHead className="w-[70px] text-right">Actions</TableHead>
                </TableRow>
                {/* Filter row - styled like V1 */}
                <TableRow className="border-b-2 border-border">
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                  <TableHead className="py-0.5 pb-1.5">
                    <FilterSelect
                      value={leagueFilter}
                      onChange={setLeagueFilter}
                      options={[
                        { value: "", label: "All" },
                        ...leagues.map((l) => ({ value: l, label: leagueLookup.aliases[l] || l })),
                      ]}
                    />
                  </TableHead>
                  <TableHead className="py-0.5 pb-1.5">
                    <FilterSelect
                      value={sportFilter}
                      onChange={setSportFilter}
                      options={[
                        { value: "", label: "All" },
                        ...sports.map((s) => ({ value: s, label: s.charAt(0).toUpperCase() + s.slice(1) })),
                      ]}
                    />
                  </TableHead>
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                  <TableHead className="py-0.5 pb-1.5">
                    <FilterSelect
                      value={templateFilter}
                      onChange={setTemplateFilter}
                      options={[
                        { value: "", label: "All" },
                        { value: "_unassigned", label: "Unassigned" },
                        ...teamTemplates.map((t) => ({ value: t.id.toString(), label: t.name })),
                      ]}
                    />
                  </TableHead>
                  <TableHead className="py-0.5 pb-1.5">
                    <FilterSelect
                      value={activeFilter}
                      onChange={(v) => setActiveFilter(v as ActiveFilter)}
                      options={[
                        { value: "", label: "All" },
                        { value: "active", label: "Active" },
                        { value: "inactive", label: "Inactive" },
                      ]}
                    />
                  </TableHead>
                  <TableHead className="py-0.5 pb-1.5"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredTeams.map((team, index) => (
                  <TableRow
                    key={team.id}
                    className={cn(selectedIds.has(team.id) && "bg-muted/50")}
                  >
                    <TableCell>
                      <Checkbox
                        checked={selectedIds.has(team.id)}
                        onClick={(e) => toggleSelect(team.id, index, e.shiftKey)}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        {team.team_logo_url && (
                          <img
                            src={team.team_logo_url}
                            alt=""
                            className="h-8 w-8 object-contain"
                          />
                        )}
                        <div>
                          <div className="font-medium">{team.team_name}</div>
                          {team.team_abbrev && (
                            <div className="text-xs text-muted-foreground">{team.team_abbrev}</div>
                          )}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      {(() => {
                        const hasMultiLeague = team.leagues.length > 1

                        const leagueDisplay = (
                          <div
                            className={cn("relative inline-block", hasMultiLeague && "cursor-help")}
                          >
                            {leagueLookup.logos[team.primary_league] ? (
                              <img
                                src={leagueLookup.logos[team.primary_league]}
                                alt={leagueLookup.aliases[team.primary_league] || team.primary_league}
                                title={leagueLookup.aliases[team.primary_league] || team.primary_league}
                                className="h-7 w-auto object-contain"
                              />
                            ) : (
                              <Badge variant="secondary">{leagueLookup.aliases[team.primary_league] || team.primary_league}</Badge>
                            )}
                            {/* Multi-league badge */}
                            {hasMultiLeague && (
                              <span className="absolute -bottom-1 -right-1 bg-primary text-primary-foreground text-[10px] font-bold w-4 h-4 rounded-full flex items-center justify-center border border-background">
                                +{team.leagues.length - 1}
                              </span>
                            )}
                          </div>
                        )

                        if (hasMultiLeague) {
                          return (
                            <RichTooltip
                              title="Competitions"
                              side="bottom"
                              align="start"
                              content={
                                <div className="space-y-1.5">
                                  {team.leagues.map((leagueSlug) => (
                                    <div key={leagueSlug} className="flex items-center gap-2 text-sm">
                                      {leagueLookup.logos[leagueSlug] && (
                                        <img
                                          src={leagueLookup.logos[leagueSlug]}
                                          alt=""
                                          className="h-5 w-5 object-contain"
                                        />
                                      )}
                                      <span className={leagueSlug === team.primary_league ? "font-medium text-foreground" : "text-muted-foreground"}>
                                        {leagueLookup.aliases[leagueSlug] || leagueSlug}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              }
                            >
                              {leagueDisplay}
                            </RichTooltip>
                          )
                        }

                        return leagueDisplay
                      })()}
                    </TableCell>
                    <TableCell>
                      <span className="text-xl" title={team.sport}>
                        {getSportEmoji(team.sport)}
                      </span>
                    </TableCell>
                    <TableCell className="font-mono text-sm">{team.channel_id}</TableCell>
                    <TableCell>
                      {team.template_id ? (
                        <Badge variant="success">
                          {templates?.find((t) => t.id === team.template_id)?.name ??
                            `#${team.template_id}`}
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="italic text-muted-foreground">
                          Unassigned
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <Switch
                        checked={team.active}
                        onCheckedChange={() => handleToggleActive(team)}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => openEdit(team)}
                          title="Edit"
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          onClick={() => setDeleteConfirm(team)}
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
      </div>

      {/* Edit Team Dialog */}
      {editingTeam && (
        <EditTeamDialog
          team={editingTeam}
          templates={teamTemplates}
          open={showDialog}
          onOpenChange={(open) => {
            if (!open) {
              setShowDialog(false)
              setEditingTeam(null)
            }
          }}
          onSave={async (data) => {
            await updateMutation.mutateAsync({ teamId: editingTeam.id, data })
            toast.success(`Updated team "${data.team_name || editingTeam.team_name}"`)
            setShowDialog(false)
            setEditingTeam(null)
          }}
          isSaving={updateMutation.isPending}
        />
      )}

      {/* Delete Confirmation */}
      <Dialog open={deleteConfirm !== null} onOpenChange={(open) => !open && setDeleteConfirm(null)}>
        <DialogContent onClose={() => setDeleteConfirm(null)}>
          <DialogHeader>
            <DialogTitle>Delete Team</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete "{deleteConfirm?.team_name}"? This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteConfirm(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleteMutation.isPending}>
              {deleteMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
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
              Assign a template to {selectedIds.size} selected team{selectedIds.size !== 1 && "s"}.
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <Select
              value={bulkTemplateId?.toString() ?? ""}
              onChange={(e) =>
                setBulkTemplateId(e.target.value ? parseInt(e.target.value) : null)
              }
            >
              <option value="">Unassigned</option>
              {teamTemplates.map((template) => (
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

      {/* Bulk Delete Confirmation */}
      <Dialog open={showBulkDelete} onOpenChange={setShowBulkDelete}>
        <DialogContent onClose={() => setShowBulkDelete(false)}>
          <DialogHeader>
            <DialogTitle>Delete Teams</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete {selectedIds.size} team{selectedIds.size !== 1 && "s"}?
              This cannot be undone.
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
              {deleteMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Delete {selectedIds.size} Team{selectedIds.size !== 1 && "s"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Change Channel ID Modal */}
      <Dialog open={showBulkChannelId} onOpenChange={setShowBulkChannelId}>
        <DialogContent className="max-w-lg" onClose={() => setShowBulkChannelId(false)}>
          <DialogHeader>
            <DialogTitle>Change Channel ID</DialogTitle>
            <DialogDescription>
              Update channel IDs for {selectedIds.size} selected team{selectedIds.size !== 1 && "s"}.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-4">
            <div className="space-y-3">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="channel_id_mode"
                  checked={channelIdMode === "default"}
                  onChange={() => setChannelIdMode("default")}
                  className="w-4 h-4"
                />
                <span>Use Global Default Format</span>
              </label>
              <p className="text-sm text-muted-foreground ml-6">
                Current: <code className="bg-muted px-1 py-0.5 rounded text-xs">{defaultChannelIdFormat}</code>
              </p>
            </div>

            <div className="space-y-3">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="channel_id_mode"
                  checked={channelIdMode === "custom"}
                  onChange={() => setChannelIdMode("custom")}
                  className="w-4 h-4"
                />
                <span>Use Custom Format</span>
              </label>

              {channelIdMode === "custom" && (
                <div className="ml-6 space-y-2">
                  <Input
                    value={customChannelIdFormat}
                    onChange={(e) => setCustomChannelIdFormat(e.target.value)}
                    placeholder="{team_name_pascal}.{league_id}"
                  />
                  <div className="text-xs text-muted-foreground space-y-1">
                    <p className="font-medium">Available variables:</p>
                    <div className="grid grid-cols-2 gap-1">
                      <span><code>{"{team_name_pascal}"}</code> - PascalCase</span>
                      <span><code>{"{team_abbrev}"}</code> - Abbreviation</span>
                      <span><code>{"{team_name}"}</code> - lowercase-dashes</span>
                      <span><code>{"{league_id}"}</code> - league code</span>
                      <span><code>{"{league}"}</code> - League Name</span>
                      <span><code>{"{sport}"}</code> - sport name</span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setShowBulkChannelId(false)}>
              Cancel
            </Button>
            <Button onClick={handleBulkUpdateChannelIds} disabled={isUpdatingChannelIds}>
              {isUpdatingChannelIds && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Apply to {selectedIds.size} Team{selectedIds.size !== 1 && "s"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
