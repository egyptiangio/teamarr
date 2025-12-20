import { useState, useEffect, useMemo } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import {
  Plus,
  Trash2,
  Pencil,
  Loader2,
  Search,
  LayoutGrid,
  List,
  Filter,
  X,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
import { cn } from "@/lib/utils"
import {
  useTeams,
  useUpdateTeam,
  useDeleteTeam,
} from "@/hooks/useTeams"
import { useTemplates } from "@/hooks/useTemplates"
import type { Team } from "@/api/teams"
import { useQuery } from "@tanstack/react-query"

// Sport emoji mapping
const SPORT_EMOJIS: Record<string, string> = {
  basketball: "🏀",
  football: "🏈",
  baseball: "⚾",
  hockey: "🏒",
  soccer: "⚽",
  mma: "🥊",
  boxing: "🥊",
  default: "🏆",
}

function getSportEmoji(sport: string): string {
  return SPORT_EMOJIS[sport.toLowerCase()] || SPORT_EMOJIS.default
}

// Fetch leagues for logo lookup
async function fetchLeagues(): Promise<{ slug: string; logo_url: string | null }[]> {
  const response = await fetch("/api/v1/cache/leagues")
  if (!response.ok) return []
  const data = await response.json()
  return data.leagues || []
}

// Fetch team's leagues (for multi-league display)
interface TeamLeagueInfo {
  slug: string
  name: string
  sport: string | null
  logo_url: string | null
}

async function fetchTeamLeagues(
  provider: string,
  providerTeamId: string
): Promise<TeamLeagueInfo[]> {
  const response = await fetch(`/api/v1/cache/team-leagues/${provider}/${providerTeamId}`)
  if (!response.ok) return []
  const data = await response.json()
  return data.leagues || []
}

type ViewMode = "table" | "cards"
type ActiveFilter = "all" | "active" | "inactive"

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
              <option value="">Default Template</option>
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
  const { data: cachedLeagues } = useQuery({ queryKey: ["leagues"], queryFn: fetchLeagues })

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
  const updateMutation = useUpdateTeam()
  const deleteMutation = useDeleteTeam()

  // View and filter state
  const [viewMode, setViewMode] = useState<ViewMode>("table")
  const [searchFilter, setSearchFilter] = useState("")
  const [leagueFilter, setLeagueFilter] = useState<string>("")
  const [activeFilter, setActiveFilter] = useState<ActiveFilter>("all")
  const [showFilters, setShowFilters] = useState(false)

  // Bulk selection state
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [bulkTemplateId, setBulkTemplateId] = useState<number | null>(null)
  const [showBulkTemplate, setShowBulkTemplate] = useState(false)
  const [showBulkDelete, setShowBulkDelete] = useState(false)

  // Edit dialog state
  const [showDialog, setShowDialog] = useState(false)
  const [editingTeam, setEditingTeam] = useState<Team | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<Team | null>(null)

  // Multi-league state for soccer teams
  const [teamLeaguesCache, setTeamLeaguesCache] = useState<Record<string, TeamLeagueInfo[]>>({})
  const [loadingTeamLeagues, setLoadingTeamLeagues] = useState<Set<string>>(new Set())

  const loadTeamLeagues = async (provider: string, providerTeamId: string) => {
    const cacheKey = `${provider}:${providerTeamId}`
    if (teamLeaguesCache[cacheKey] || loadingTeamLeagues.has(cacheKey)) return

    setLoadingTeamLeagues((prev) => new Set(prev).add(cacheKey))
    try {
      const leagues = await fetchTeamLeagues(provider, providerTeamId)
      setTeamLeaguesCache((prev) => ({ ...prev, [cacheKey]: leagues }))
    } finally {
      setLoadingTeamLeagues((prev) => {
        const next = new Set(prev)
        next.delete(cacheKey)
        return next
      })
    }
  }

  // Get unique leagues from teams
  const leagues = useMemo(() => {
    if (!teams) return []
    const uniqueLeagues = [...new Set(teams.map((t) => t.league))]
    return uniqueLeagues.sort()
  }, [teams])

  // Calculate team stats for tiles
  const teamStats = useMemo(() => {
    if (!teams) return { total: 0, enabled: 0, byLeague: {} as Record<string, { total: number; enabled: number }> }

    const byLeague: Record<string, { total: number; enabled: number }> = {}

    for (const team of teams) {
      if (!byLeague[team.league]) {
        byLeague[team.league] = { total: 0, enabled: 0 }
      }
      byLeague[team.league].total++
      if (team.active) {
        byLeague[team.league].enabled++
      }
    }

    return {
      total: teams.length,
      enabled: teams.filter((t) => t.active).length,
      byLeague,
    }
  }, [teams])

  // Filter teams
  const filteredTeams = useMemo(() => {
    if (!teams) return []
    return teams.filter((team) => {
      // Search filter
      if (searchFilter) {
        const q = searchFilter.toLowerCase()
        const matches =
          team.team_name.toLowerCase().includes(q) ||
          team.team_abbrev?.toLowerCase().includes(q) ||
          team.channel_id.toLowerCase().includes(q) ||
          team.league.toLowerCase().includes(q)
        if (!matches) return false
      }

      // League filter
      if (leagueFilter && team.league !== leagueFilter) return false

      // Active filter
      if (activeFilter === "active" && !team.active) return false
      if (activeFilter === "inactive" && team.active) return false

      return true
    })
  }, [teams, searchFilter, leagueFilter, activeFilter])

  // Clear selection when filters change
  useEffect(() => {
    setSelectedIds(new Set())
  }, [searchFilter, leagueFilter, activeFilter])

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

  const hasActiveFilters = searchFilter || leagueFilter || activeFilter !== "all"

  const clearFilters = () => {
    setSearchFilter("")
    setLeagueFilter("")
    setActiveFilter("all")
  }

  if (error) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">Teams</h1>
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-destructive">Error loading teams: {error.message}</p>
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
          <h1 className="text-2xl font-bold">Teams</h1>
          <p className="text-muted-foreground">Team-based EPG channel configurations</p>
        </div>
        <Button onClick={() => navigate("/teams/import")}>
          <Plus className="h-4 w-4 mr-1.5" />
          Import Teams
        </Button>
      </div>

      {/* Stats Tiles */}
      {teams && teams.length > 0 && (
        <div className="grid grid-cols-4 gap-3">
          {/* Configured */}
          <div className="group relative">
            <Card className="p-3 cursor-help">
              <div className="text-2xl font-bold">{teamStats.total}</div>
              <div className="text-xs text-muted-foreground uppercase tracking-wide">Configured</div>
            </Card>
            <div className="absolute left-0 top-full mt-1 z-50 hidden group-hover:block">
              <Card className="p-3 shadow-lg min-w-[160px]">
                <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2 pb-1 border-b">
                  By League
                </div>
                <div className="space-y-1">
                  {Object.entries(teamStats.byLeague)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([league, counts]) => (
                      <div key={league} className="flex justify-between text-sm">
                        <span className="text-muted-foreground">{league.toUpperCase()}</span>
                        <span className="font-medium">{counts.total}</span>
                      </div>
                    ))}
                </div>
              </Card>
            </div>
          </div>

          {/* Enabled */}
          <div className="group relative">
            <Card className="p-3 cursor-help">
              <div className="text-2xl font-bold">{teamStats.enabled}</div>
              <div className="text-xs text-muted-foreground uppercase tracking-wide">Enabled</div>
            </Card>
            <div className="absolute left-0 top-full mt-1 z-50 hidden group-hover:block">
              <Card className="p-3 shadow-lg min-w-[160px]">
                <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2 pb-1 border-b">
                  By League
                </div>
                <div className="space-y-1">
                  {Object.entries(teamStats.byLeague)
                    .filter(([, counts]) => counts.enabled > 0)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([league, counts]) => (
                      <div key={league} className="flex justify-between text-sm">
                        <span className="text-muted-foreground">{league.toUpperCase()}</span>
                        <span className="font-medium">{counts.enabled}</span>
                      </div>
                    ))}
                </div>
              </Card>
            </div>
          </div>

          {/* Games Today - placeholder */}
          <Card className="p-3">
            <div className="text-2xl font-bold text-muted-foreground">--</div>
            <div className="text-xs text-muted-foreground uppercase tracking-wide">Games Today</div>
          </Card>

          {/* Live Now - placeholder */}
          <Card className="p-3">
            <div className="text-2xl font-bold text-muted-foreground">--</div>
            <div className="text-xs text-muted-foreground uppercase tracking-wide">Live Now</div>
          </Card>
        </div>
      )}

      {/* Filters and View Toggle */}
      <Card>
        <CardContent className="py-3">
          <div className="flex items-center gap-3">
            {/* Search */}
            <div className="relative flex-1 max-w-xs">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                value={searchFilter}
                onChange={(e) => setSearchFilter(e.target.value)}
                placeholder="Search teams..."
                className="pl-10"
              />
            </div>

            {/* Filter toggle */}
            <Button
              variant={showFilters ? "secondary" : "outline"}
              size="sm"
              onClick={() => setShowFilters(!showFilters)}
            >
              <Filter className="h-4 w-4 mr-1" />
              Filters
              {hasActiveFilters && (
                <Badge variant="secondary" className="ml-1 h-5 w-5 p-0 justify-center">
                  {(leagueFilter ? 1 : 0) + (activeFilter !== "all" ? 1 : 0)}
                </Badge>
              )}
            </Button>

            {hasActiveFilters && (
              <Button variant="ghost" size="sm" onClick={clearFilters}>
                <X className="h-4 w-4 mr-1" />
                Clear
              </Button>
            )}

            <div className="flex-1" />

            {/* View toggle */}
            <div className="flex border rounded-md">
              <Button
                variant={viewMode === "table" ? "secondary" : "ghost"}
                size="sm"
                className="rounded-r-none"
                onClick={() => setViewMode("table")}
              >
                <List className="h-4 w-4" />
              </Button>
              <Button
                variant={viewMode === "cards" ? "secondary" : "ghost"}
                size="sm"
                className="rounded-l-none"
                onClick={() => setViewMode("cards")}
              >
                <LayoutGrid className="h-4 w-4" />
              </Button>
            </div>
          </div>

          {/* Expanded filters */}
          {showFilters && (
            <div className="flex items-center gap-3 mt-3 pt-3 border-t">
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">League</Label>
                <Select
                  value={leagueFilter}
                  onChange={(e) => setLeagueFilter(e.target.value)}
                  className="w-40"
                >
                  <option value="">All leagues</option>
                  {leagues.map((league) => (
                    <option key={league} value={league}>
                      {league}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="space-y-1">
                <Label className="text-xs text-muted-foreground">Status</Label>
                <Select
                  value={activeFilter}
                  onChange={(e) => setActiveFilter(e.target.value as ActiveFilter)}
                  className="w-32"
                >
                  <option value="all">All</option>
                  <option value="active">Active</option>
                  <option value="inactive">Inactive</option>
                </Select>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Bulk Actions Bar */}
      {selectedIds.size > 0 && (
        <Card className="bg-primary/5 border-primary/20">
          <CardContent className="py-3">
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium">
                {selectedIds.size} team{selectedIds.size !== 1 && "s"} selected
              </span>
              <div className="flex-1" />
              <Button variant="outline" size="sm" onClick={() => handleBulkToggleActive(true)}>
                Enable
              </Button>
              <Button variant="outline" size="sm" onClick={() => handleBulkToggleActive(false)}>
                Disable
              </Button>
              <Button variant="outline" size="sm" onClick={() => setShowBulkTemplate(true)}>
                Assign Template
              </Button>
              <Button variant="destructive" size="sm" onClick={() => setShowBulkDelete(true)}>
                <Trash2 className="h-4 w-4 mr-1" />
                Delete
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setSelectedIds(new Set())}>
                <X className="h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Teams List */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle>
            Teams ({filteredTeams.length}
            {filteredTeams.length !== teams?.length && ` of ${teams?.length}`})
          </CardTitle>
        </CardHeader>
        <CardContent>
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
          ) : viewMode === "table" ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10">
                    <Checkbox
                      checked={
                        selectedIds.size === filteredTeams.length && filteredTeams.length > 0
                      }
                      onCheckedChange={toggleSelectAll}
                    />
                  </TableHead>
                  <TableHead>Team</TableHead>
                  <TableHead className="w-16">League</TableHead>
                  <TableHead className="w-14">Sport</TableHead>
                  <TableHead>Channel ID</TableHead>
                  <TableHead>Template</TableHead>
                  <TableHead className="w-16">Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredTeams.map((team) => (
                  <TableRow
                    key={team.id}
                    className={cn(selectedIds.has(team.id) && "bg-muted/50")}
                  >
                    <TableCell>
                      <Checkbox
                        checked={selectedIds.has(team.id)}
                        onCheckedChange={() => toggleSelect(team.id)}
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
                        const cacheKey = `${team.provider}:${team.provider_team_id}`
                        const teamLeagues = teamLeaguesCache[cacheKey]
                        const hasMultiLeague = team.sport === "soccer" && teamLeagues && teamLeagues.length > 1

                        const leagueDisplay = (
                          <div
                            className={cn("relative inline-block", hasMultiLeague && "cursor-help")}
                            onMouseEnter={() => team.sport === "soccer" && loadTeamLeagues(team.provider, team.provider_team_id)}
                          >
                            {leagueLogos[team.league] ? (
                              <img
                                src={leagueLogos[team.league]}
                                alt={team.league.toUpperCase()}
                                title={team.league.toUpperCase()}
                                className="h-7 w-auto object-contain"
                              />
                            ) : (
                              <Badge variant="secondary">{team.league}</Badge>
                            )}
                            {/* Multi-league badge */}
                            {hasMultiLeague && (
                              <span className="absolute -bottom-1 -right-1 bg-primary text-primary-foreground text-[10px] font-bold w-4 h-4 rounded-full flex items-center justify-center border border-background">
                                +{teamLeagues.length - 1}
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
                                  {teamLeagues.map((league) => (
                                    <div key={league.slug} className="flex items-center gap-2 text-sm">
                                      {league.logo_url && (
                                        <img
                                          src={league.logo_url}
                                          alt=""
                                          className="h-5 w-5 object-contain"
                                        />
                                      )}
                                      <span className={league.slug === team.league ? "font-medium text-foreground" : "text-muted-foreground"}>
                                        {league.name || league.slug}
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
                        <span className="text-muted-foreground">
                          {templates?.find((t) => t.id === team.template_id)?.name ??
                            `#${team.template_id}`}
                        </span>
                      ) : (
                        <span className="text-muted-foreground italic">Default</span>
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
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
              {filteredTeams.map((team) => (
                <div
                  key={team.id}
                  className={cn(
                    "border rounded-lg p-3 cursor-pointer hover:border-primary/50 transition-colors",
                    selectedIds.has(team.id) && "border-primary bg-primary/5"
                  )}
                  onClick={() => toggleSelect(team.id)}
                >
                  <div className="flex items-start gap-3">
                    <Checkbox
                      checked={selectedIds.has(team.id)}
                      onCheckedChange={() => toggleSelect(team.id)}
                      onClick={(e) => e.stopPropagation()}
                    />
                    {team.team_logo_url ? (
                      <img
                        src={team.team_logo_url}
                        alt=""
                        className="h-10 w-10 object-contain bg-white rounded p-0.5"
                      />
                    ) : (
                      <div className="h-10 w-10 bg-muted rounded flex items-center justify-center text-xs text-muted-foreground">
                        {team.team_abbrev || "?"}
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="font-medium truncate">{team.team_name}</div>
                      <div className="flex items-center gap-2 mt-1">
                        <Badge variant="secondary" className="text-xs">
                          {team.league}
                        </Badge>
                        <Badge
                          variant={team.active ? "default" : "outline"}
                          className={cn("text-xs", team.active && "bg-green-500/20 text-green-600")}
                        >
                          {team.active ? "Active" : "Inactive"}
                        </Badge>
                      </div>
                      <div className="text-xs text-muted-foreground mt-1 truncate font-mono">
                        {team.channel_id}
                      </div>
                    </div>
                    <div className="flex flex-col gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={(e) => {
                          e.stopPropagation()
                          openEdit(team)
                        }}
                        title="Edit"
                      >
                        <Pencil className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={(e) => {
                          e.stopPropagation()
                          setDeleteConfirm(team)
                        }}
                        title="Delete"
                      >
                        <Trash2 className="h-3 w-3 text-destructive" />
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Edit Team Dialog */}
      {editingTeam && (
        <EditTeamDialog
          team={editingTeam}
          templates={templates ?? []}
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
              <option value="">Default Template</option>
              {templates?.map((template) => (
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
    </div>
  )
}
