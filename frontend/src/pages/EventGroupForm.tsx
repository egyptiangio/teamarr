import { useState, useEffect, useMemo } from "react"
import { useNavigate, useParams, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { ArrowLeft, Loader2, Save, ChevronRight, Check, X } from "lucide-react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { cn } from "@/lib/utils"
import {
  useGroup,
  useGroups,
  useCreateGroup,
  useUpdateGroup,
} from "@/hooks/useGroups"
import { useTemplates } from "@/hooks/useTemplates"
import type { EventGroupCreate, EventGroupUpdate } from "@/api/types"

// Step type
type Step = "mode" | "leagues" | "settings"

// Group mode
type GroupMode = "single" | "multi" | null

// Fetch leagues from cache grouped by sport
interface CachedLeague {
  slug: string
  name: string
  sport: string
  logo_url: string | null
  team_count?: number
}

async function fetchLeagues(): Promise<CachedLeague[]> {
  const response = await fetch("/api/v1/cache/leagues")
  if (!response.ok) return []
  const data = await response.json()
  return data.leagues || []
}

// Common single-league options
const COMMON_LEAGUES = [
  { sport: "football", leagues: [
    { slug: "nfl", name: "NFL" },
    { slug: "ncaaf", name: "College Football" },
  ]},
  { sport: "basketball", leagues: [
    { slug: "nba", name: "NBA" },
    { slug: "ncaam", name: "College Basketball (M)" },
    { slug: "ncaaw", name: "College Basketball (W)" },
    { slug: "wnba", name: "WNBA" },
  ]},
  { sport: "hockey", leagues: [
    { slug: "nhl", name: "NHL" },
  ]},
  { sport: "baseball", leagues: [
    { slug: "mlb", name: "MLB" },
  ]},
  { sport: "soccer", leagues: [
    { slug: "usa.1", name: "MLS" },
    { slug: "eng.1", name: "Premier League" },
    { slug: "esp.1", name: "La Liga" },
    { slug: "ger.1", name: "Bundesliga" },
    { slug: "ita.1", name: "Serie A" },
    { slug: "fra.1", name: "Ligue 1" },
    { slug: "uefa.champions", name: "Champions League" },
    { slug: "uefa.europa", name: "Europa League" },
  ]},
  { sport: "mma", leagues: [
    { slug: "ufc", name: "UFC" },
  ]},
]

// Sport display names
const SPORT_NAMES: Record<string, string> = {
  football: "Football",
  basketball: "Basketball",
  hockey: "Hockey",
  baseball: "Baseball",
  soccer: "Soccer",
  mma: "MMA",
  boxing: "Boxing",
  tennis: "Tennis",
  golf: "Golf",
  racing: "Racing",
  cricket: "Cricket",
  rugby: "Rugby",
}

export function EventGroupForm() {
  const { groupId } = useParams<{ groupId: string }>()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const isEdit = groupId && groupId !== "new"

  // M3U group info from URL params (when coming from Import)
  const m3uGroupId = searchParams.get("m3u_group_id")
  const m3uGroupName = searchParams.get("m3u_group_name")
  const m3uAccountId = searchParams.get("m3u_account_id")
  const m3uAccountName = searchParams.get("m3u_account_name")

  // Step state
  const [currentStep, setCurrentStep] = useState<Step>(isEdit ? "settings" : "mode")
  const [groupMode, setGroupMode] = useState<GroupMode>(null)

  // Form state
  const [formData, setFormData] = useState<EventGroupCreate>({
    name: m3uGroupName || "",
    leagues: [],
    template_id: null,
    channel_start_number: null,
    channel_assignment_mode: "auto",
    create_timing: "same_day",
    delete_timing: "same_day",
    duplicate_event_handling: "consolidate",
    sort_order: 0,
    total_stream_count: 0,
    m3u_group_id: m3uGroupId ? Number(m3uGroupId) : null,
    m3u_group_name: m3uGroupName || null,
    m3u_account_id: m3uAccountId ? Number(m3uAccountId) : null,
    m3u_account_name: m3uAccountName || null,
    enabled: true,
  })

  // Single-league selection
  const [selectedSport, setSelectedSport] = useState<string | null>(null)
  const [selectedLeague, setSelectedLeague] = useState<string | null>(null)
  const [parentGroupId, setParentGroupId] = useState<number | null>(null)

  // Multi-league selection
  const [selectedLeagues, setSelectedLeagues] = useState<Set<string>>(new Set())
  const [leagueSearch, setLeagueSearch] = useState("")

  // Fetch existing group if editing
  const { data: group, isLoading: isLoadingGroup } = useGroup(
    isEdit ? Number(groupId) : 0
  )

  // Fetch all groups for parent selection
  const { data: groupsData } = useGroups(true)

  // Fetch templates (event type only)
  const { data: templates } = useTemplates()
  const eventTemplates = templates?.filter(t => t.template_type === "event") || []

  // Fetch leagues
  const { data: cachedLeagues, isLoading: isLoadingLeagues } = useQuery({
    queryKey: ["leagues"],
    queryFn: fetchLeagues,
  })

  // Mutations
  const createMutation = useCreateGroup()
  const updateMutation = useUpdateGroup()

  // Populate form when editing
  useEffect(() => {
    if (group) {
      setFormData({
        name: group.name,
        leagues: group.leagues,
        template_id: group.template_id,
        channel_start_number: group.channel_start_number,
        channel_group_id: group.channel_group_id,
        stream_profile_id: group.stream_profile_id,
        channel_profile_ids: group.channel_profile_ids || [],
        create_timing: group.create_timing,
        delete_timing: group.delete_timing,
        duplicate_event_handling: group.duplicate_event_handling,
        channel_assignment_mode: group.channel_assignment_mode,
        sort_order: group.sort_order,
        total_stream_count: group.total_stream_count,
        m3u_group_id: group.m3u_group_id,
        m3u_group_name: group.m3u_group_name,
        m3u_account_id: group.m3u_account_id,
        m3u_account_name: group.m3u_account_name,
        // Stream filtering
        stream_include_regex: group.stream_include_regex,
        stream_include_regex_enabled: group.stream_include_regex_enabled,
        stream_exclude_regex: group.stream_exclude_regex,
        stream_exclude_regex_enabled: group.stream_exclude_regex_enabled,
        custom_regex_teams: group.custom_regex_teams,
        custom_regex_teams_enabled: group.custom_regex_teams_enabled,
        skip_builtin_filter: group.skip_builtin_filter,
        enabled: group.enabled,
      })

      // Determine mode from leagues
      if (group.leagues.length === 1) {
        setGroupMode("single")
        setSelectedLeague(group.leagues[0])
        // Try to find sport from cached leagues
        const league = cachedLeagues?.find(l => l.slug === group.leagues[0])
        if (league) setSelectedSport(league.sport)
      } else if (group.leagues.length > 1) {
        setGroupMode("multi")
        setSelectedLeagues(new Set(group.leagues))
      }
    }
  }, [group, cachedLeagues])

  // Group leagues by sport
  const leaguesBySport = useMemo(() => {
    if (!cachedLeagues) return {}
    const grouped: Record<string, CachedLeague[]> = {}
    for (const league of cachedLeagues) {
      // Skip leagues without names
      if (!league.name) continue
      const sport = league.sport || "other"
      if (!grouped[sport]) grouped[sport] = []
      grouped[sport].push(league)
    }
    // Sort leagues within each sport
    for (const sport of Object.keys(grouped)) {
      grouped[sport].sort((a, b) => (a.name || "").localeCompare(b.name || ""))
    }
    return grouped
  }, [cachedLeagues])

  // Eligible parent groups (single-league only, not multi-sport)
  const eligibleParents = useMemo(() => {
    if (!groupsData?.groups) return []
    return groupsData.groups.filter(g => {
      // Can't be own parent
      if (isEdit && g.id === Number(groupId)) return false
      // Must be single-league
      if (g.leagues.length !== 1) return false
      // Must match our league
      if (selectedLeague && g.leagues[0] !== selectedLeague) return false
      return true
    })
  }, [groupsData, isEdit, groupId, selectedLeague])

  const handleModeSelect = (mode: GroupMode) => {
    setGroupMode(mode)
    setCurrentStep("leagues")
  }

  const handleLeaguesContinue = () => {
    if (groupMode === "single" && !selectedLeague) {
      toast.error("Please select a league")
      return
    }
    if (groupMode === "multi" && selectedLeagues.size === 0) {
      toast.error("Please select at least one league")
      return
    }

    // Update formData with selected leagues
    if (groupMode === "single" && selectedLeague) {
      setFormData(prev => ({ ...prev, leagues: [selectedLeague] }))
    } else if (groupMode === "multi") {
      setFormData(prev => ({ ...prev, leagues: Array.from(selectedLeagues) }))
    }

    setCurrentStep("settings")
  }

  const handleSubmit = async () => {
    if (!formData.name.trim()) {
      toast.error("Group name is required")
      return
    }
    if (formData.leagues.length === 0) {
      toast.error("At least one league is required")
      return
    }

    try {
      if (isEdit) {
        const updateData: EventGroupUpdate = { ...formData }
        await updateMutation.mutateAsync({ groupId: Number(groupId), data: updateData })
        toast.success(`Updated group "${formData.name}"`)
      } else {
        await createMutation.mutateAsync(formData)
        toast.success(`Created group "${formData.name}"`)
      }
      navigate("/event-groups")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save group")
    }
  }

  const toggleLeague = (slug: string) => {
    setSelectedLeagues(prev => {
      const next = new Set(prev)
      if (next.has(slug)) {
        next.delete(slug)
      } else {
        next.add(slug)
      }
      return next
    })
  }

  const selectAllInSport = (sport: string) => {
    const sportLeagues = leaguesBySport[sport] || []
    setSelectedLeagues(prev => {
      const next = new Set(prev)
      for (const league of sportLeagues) {
        next.add(league.slug)
      }
      return next
    })
  }

  const clearAllInSport = (sport: string) => {
    const sportLeagues = leaguesBySport[sport] || []
    const slugs = new Set(sportLeagues.map(l => l.slug))
    setSelectedLeagues(prev => {
      const next = new Set(prev)
      for (const slug of slugs) {
        next.delete(slug)
      }
      return next
    })
  }

  if (isEdit && isLoadingGroup) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  const isPending = createMutation.isPending || updateMutation.isPending

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Button variant="ghost" size="icon" onClick={() => navigate("/event-groups")}>
          <ArrowLeft className="h-5 w-5" />
        </Button>
        <div className="flex-1">
          <h1 className="text-2xl font-bold">
            {isEdit ? "Edit Event Group" : "Configure Event Group"}
          </h1>
          {m3uGroupName && !isEdit && (
            <p className="text-muted-foreground">
              Importing: <span className="font-medium">{m3uGroupName}</span>
            </p>
          )}
        </div>
      </div>

      {/* Step Indicator */}
      <div className="flex items-center gap-2 text-sm">
        <button
          onClick={() => !isEdit && setCurrentStep("mode")}
          className={cn(
            "px-3 py-1.5 rounded-full transition-colors",
            currentStep === "mode"
              ? "bg-primary text-primary-foreground"
              : groupMode ? "bg-green-500/20 text-green-600" : "bg-muted text-muted-foreground"
          )}
          disabled={!!isEdit}
        >
          1. Mode
        </button>
        <ChevronRight className="h-4 w-4 text-muted-foreground" />
        <button
          onClick={() => groupMode && setCurrentStep("leagues")}
          className={cn(
            "px-3 py-1.5 rounded-full transition-colors",
            currentStep === "leagues"
              ? "bg-primary text-primary-foreground"
              : (formData.leagues.length > 0 || selectedLeagues.size > 0 || selectedLeague)
                ? "bg-green-500/20 text-green-600"
                : "bg-muted text-muted-foreground"
          )}
          disabled={!groupMode}
        >
          2. Leagues
        </button>
        <ChevronRight className="h-4 w-4 text-muted-foreground" />
        <span
          className={cn(
            "px-3 py-1.5 rounded-full",
            currentStep === "settings"
              ? "bg-primary text-primary-foreground"
              : "bg-muted text-muted-foreground"
          )}
        >
          3. Settings
        </span>
      </div>

      {/* Step 1: Mode Selection */}
      {currentStep === "mode" && (
        <div className="grid grid-cols-2 gap-6">
          <Card
            className={cn(
              "cursor-pointer transition-all hover:border-primary/50",
              groupMode === "single" && "border-primary ring-2 ring-primary/20"
            )}
            onClick={() => handleModeSelect("single")}
          >
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <span className="text-2xl">🎯</span>
                Single League
              </CardTitle>
              <CardDescription>
                Match streams to events in ONE specific league
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="text-sm text-muted-foreground space-y-1">
                <li>• Best for dedicated league streams (NFL, NBA, etc.)</li>
                <li>• Can be a child of another group</li>
                <li>• Simpler configuration</li>
              </ul>
            </CardContent>
          </Card>

          <Card
            className={cn(
              "cursor-pointer transition-all hover:border-primary/50",
              groupMode === "multi" && "border-primary ring-2 ring-primary/20"
            )}
            onClick={() => handleModeSelect("multi")}
          >
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <span className="text-2xl">🌐</span>
                Multi-Sport
              </CardTitle>
              <CardDescription>
                Match streams across MULTIPLE leagues and sports
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="text-sm text-muted-foreground space-y-1">
                <li>• Best for aggregator streams (ESPN+, etc.)</li>
                <li>• Matches across all selected leagues</li>
                <li>• Advanced league detection</li>
              </ul>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Step 2: League Selection */}
      {currentStep === "leagues" && groupMode === "single" && (
        <Card>
          <CardHeader>
            <CardTitle>Select League</CardTitle>
            <CardDescription>
              Choose the league to match streams against
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* Sport Selection */}
            <div className="space-y-2">
              <Label>Sport</Label>
              <div className="flex flex-wrap gap-2">
                {COMMON_LEAGUES.map(({ sport }) => (
                  <Button
                    key={sport}
                    variant={selectedSport === sport ? "default" : "outline"}
                    size="sm"
                    onClick={() => {
                      setSelectedSport(sport)
                      setSelectedLeague(null)
                    }}
                  >
                    {SPORT_NAMES[sport] || sport}
                  </Button>
                ))}
              </div>
            </div>

            {/* League Selection */}
            {selectedSport && (
              <div className="space-y-2">
                <Label>League</Label>
                <div className="grid grid-cols-3 gap-2">
                  {COMMON_LEAGUES.find(s => s.sport === selectedSport)?.leagues.map(league => (
                    <Button
                      key={league.slug}
                      variant={selectedLeague === league.slug ? "default" : "outline"}
                      className="justify-start"
                      onClick={() => setSelectedLeague(league.slug)}
                    >
                      {selectedLeague === league.slug && <Check className="h-4 w-4 mr-2" />}
                      {league.name}
                    </Button>
                  ))}
                </div>

                {/* Show more leagues from cache */}
                {leaguesBySport[selectedSport] && leaguesBySport[selectedSport].length > 0 && (
                  <div className="pt-4 border-t">
                    <Label className="text-muted-foreground">More {SPORT_NAMES[selectedSport]} Leagues</Label>
                    <div className="mt-2 max-h-48 overflow-y-auto grid grid-cols-3 gap-1">
                      {leaguesBySport[selectedSport]
                        .filter(l => !COMMON_LEAGUES.find(s => s.sport === selectedSport)?.leagues.some(cl => cl.slug === l.slug))
                        .map(league => (
                          <button
                            key={league.slug}
                            className={cn(
                              "flex items-center gap-2 px-2 py-1.5 rounded text-sm text-left hover:bg-accent",
                              selectedLeague === league.slug && "bg-primary text-primary-foreground"
                            )}
                            onClick={() => setSelectedLeague(league.slug)}
                          >
                            {league.logo_url && (
                              <img src={league.logo_url} alt="" className="h-4 w-4 object-contain" />
                            )}
                            <span className="truncate">{league.name}</span>
                          </button>
                        ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Parent Group Selection */}
            {selectedLeague && eligibleParents.length > 0 && (
              <div className="space-y-2 pt-4 border-t">
                <Label>Parent Group (Optional)</Label>
                <p className="text-xs text-muted-foreground mb-2">
                  Child groups add their streams to the parent's channels instead of creating new ones.
                </p>
                <Select
                  value={parentGroupId?.toString() || ""}
                  onChange={(e) => setParentGroupId(e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">No parent (independent group)</option>
                  {eligibleParents.map(g => (
                    <option key={g.id} value={g.id}>{g.name}</option>
                  ))}
                </Select>
              </div>
            )}

            <div className="flex justify-end gap-2 pt-4">
              <Button variant="outline" onClick={() => setCurrentStep("mode")}>
                Back
              </Button>
              <Button onClick={handleLeaguesContinue} disabled={!selectedLeague}>
                Continue
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {currentStep === "leagues" && groupMode === "multi" && (
        <Card>
          <CardHeader>
            <CardTitle>Select Leagues</CardTitle>
            <CardDescription>
              Choose which leagues to match streams against. Streams will be matched to events in any selected league.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Search */}
            <Input
              placeholder="Search leagues..."
              value={leagueSearch}
              onChange={(e) => setLeagueSearch(e.target.value)}
            />

            {/* Selected count */}
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">
                {selectedLeagues.size} leagues selected
              </span>
              {selectedLeagues.size > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSelectedLeagues(new Set())}
                >
                  Clear All
                </Button>
              )}
            </div>

            {/* Selected badges */}
            {selectedLeagues.size > 0 && (
              <div className="flex flex-wrap gap-1">
                {Array.from(selectedLeagues).slice(0, 10).map(slug => {
                  const league = cachedLeagues?.find(l => l.slug === slug)
                  return (
                    <Badge key={slug} variant="secondary" className="gap-1">
                      {league?.logo_url && (
                        <img src={league.logo_url} alt="" className="h-3 w-3 object-contain" />
                      )}
                      {league?.name || slug}
                      <button onClick={() => toggleLeague(slug)} className="ml-1 hover:bg-muted rounded">
                        <X className="h-3 w-3" />
                      </button>
                    </Badge>
                  )
                })}
                {selectedLeagues.size > 10 && (
                  <Badge variant="outline">+{selectedLeagues.size - 10} more</Badge>
                )}
              </div>
            )}

            {/* League picker by sport */}
            {isLoadingLeagues ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin" />
              </div>
            ) : (
              <div className="max-h-96 overflow-y-auto border rounded-md divide-y">
                {Object.entries(leaguesBySport)
                  .filter(([sport]) =>
                    !leagueSearch ||
                    leaguesBySport[sport].some(l =>
                      l.slug.toLowerCase().includes(leagueSearch.toLowerCase()) ||
                      l.name.toLowerCase().includes(leagueSearch.toLowerCase())
                    )
                  )
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([sport, leagues]) => {
                    const sportLeaguesFiltered = leagueSearch
                      ? leagues.filter(l =>
                          l.slug.toLowerCase().includes(leagueSearch.toLowerCase()) ||
                          l.name.toLowerCase().includes(leagueSearch.toLowerCase())
                        )
                      : leagues

                    if (sportLeaguesFiltered.length === 0) return null

                    const allSelected = sportLeaguesFiltered.every(l => selectedLeagues.has(l.slug))

                    return (
                      <div key={sport}>
                        <div className="flex items-center justify-between px-3 py-2 bg-muted/50 sticky top-0">
                          <span className="font-medium text-sm">
                            {SPORT_NAMES[sport] || sport} ({sportLeaguesFiltered.length})
                          </span>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 text-xs"
                            onClick={() => allSelected ? clearAllInSport(sport) : selectAllInSport(sport)}
                          >
                            {allSelected ? "Clear" : "Select All"}
                          </Button>
                        </div>
                        <div className="grid grid-cols-2 md:grid-cols-3 gap-1 p-2">
                          {sportLeaguesFiltered.map(league => (
                            <label
                              key={league.slug}
                              className={cn(
                                "flex items-center gap-2 px-2 py-1.5 rounded text-sm cursor-pointer hover:bg-accent",
                                selectedLeagues.has(league.slug) && "bg-primary/10"
                              )}
                            >
                              <Checkbox
                                checked={selectedLeagues.has(league.slug)}
                                onCheckedChange={() => toggleLeague(league.slug)}
                              />
                              {league.logo_url && (
                                <img src={league.logo_url} alt="" className="h-4 w-4 object-contain" />
                              )}
                              <span className="truncate">{league.name}</span>
                            </label>
                          ))}
                        </div>
                      </div>
                    )
                  })}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-4">
              <Button variant="outline" onClick={() => setCurrentStep("mode")}>
                Back
              </Button>
              <Button onClick={handleLeaguesContinue} disabled={selectedLeagues.size === 0}>
                Continue ({selectedLeagues.size} leagues)
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Step 3: Settings */}
      {currentStep === "settings" && (
        <div className="space-y-6">
          {/* Basic Info */}
          <Card>
            <CardHeader>
              <CardTitle>Basic Settings</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="name">Group Name</Label>
                  <Input
                    id="name"
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    placeholder="e.g., NFL Sunday Ticket"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="template">Event Template</Label>
                  <Select
                    id="template"
                    value={formData.template_id?.toString() || ""}
                    onChange={(e) =>
                      setFormData({
                        ...formData,
                        template_id: e.target.value ? Number(e.target.value) : null,
                      })
                    }
                  >
                    <option value="">Default Template</option>
                    {eventTemplates.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    Only event-type templates are shown
                  </p>
                </div>
              </div>

              {/* Show selected leagues */}
              <div className="space-y-2">
                <Label>Matching Leagues</Label>
                <div className="flex flex-wrap gap-1.5">
                  {formData.leagues.map(slug => {
                    const league = cachedLeagues?.find(l => l.slug === slug)
                    return (
                      <Badge key={slug} variant="secondary">
                        {league?.logo_url && (
                          <img src={league.logo_url} alt="" className="h-3 w-3 object-contain mr-1" />
                        )}
                        {league?.name || slug}
                      </Badge>
                    )
                  })}
                </div>
                <Button
                  variant="link"
                  size="sm"
                  className="h-auto p-0 text-xs"
                  onClick={() => setCurrentStep("leagues")}
                >
                  Change leagues
                </Button>
              </div>

              <div className="flex items-center gap-2">
                <Switch
                  checked={formData.enabled}
                  onCheckedChange={(checked) => setFormData({ ...formData, enabled: checked })}
                />
                <Label className="font-normal">Enabled</Label>
              </div>
            </CardContent>
          </Card>

          {/* Channel Settings */}
          <Card>
            <CardHeader>
              <CardTitle>Channel Settings</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="assignment_mode">Channel Assignment</Label>
                  <Select
                    id="assignment_mode"
                    value={formData.channel_assignment_mode}
                    onChange={(e) =>
                      setFormData({ ...formData, channel_assignment_mode: e.target.value })
                    }
                  >
                    <option value="auto">Automatic</option>
                    <option value="manual">Manual</option>
                  </Select>
                </div>
                {formData.channel_assignment_mode === "manual" && (
                  <div className="space-y-2">
                    <Label htmlFor="channel_start">Starting Channel Number</Label>
                    <Input
                      id="channel_start"
                      type="number"
                      min={1}
                      value={formData.channel_start_number || ""}
                      onChange={(e) =>
                        setFormData({
                          ...formData,
                          channel_start_number: e.target.value ? Number(e.target.value) : null,
                        })
                      }
                    />
                  </div>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="duplicate_handling">Duplicate Event Handling</Label>
                <Select
                  id="duplicate_handling"
                  value={formData.duplicate_event_handling}
                  onChange={(e) =>
                    setFormData({ ...formData, duplicate_event_handling: e.target.value })
                  }
                >
                  <option value="consolidate">Consolidate (merge into one channel)</option>
                  <option value="separate">Separate (one channel per stream)</option>
                  <option value="ignore">Ignore (skip duplicates)</option>
                </Select>
                <p className="text-xs text-muted-foreground">
                  How to handle multiple streams matching the same event
                </p>
              </div>
            </CardContent>
          </Card>

          {/* Stream Filtering */}
          <Card>
            <CardHeader>
              <CardTitle>Stream Filtering</CardTitle>
              <CardDescription>
                Use regex patterns to filter which streams are processed
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Include Regex */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="include_regex">Include Pattern</Label>
                  <div className="flex items-center gap-2">
                    <Switch
                      checked={formData.stream_include_regex_enabled || false}
                      onCheckedChange={(checked) =>
                        setFormData({ ...formData, stream_include_regex_enabled: checked })
                      }
                    />
                    <span className="text-xs text-muted-foreground">Enabled</span>
                  </div>
                </div>
                <Input
                  id="include_regex"
                  value={formData.stream_include_regex || ""}
                  onChange={(e) =>
                    setFormData({ ...formData, stream_include_regex: e.target.value || null })
                  }
                  placeholder="e.g., (HD|1080p|720p)"
                  disabled={!formData.stream_include_regex_enabled}
                  className={cn(!formData.stream_include_regex_enabled && "opacity-50")}
                />
                <p className="text-xs text-muted-foreground">
                  Only streams matching this pattern will be processed
                </p>
              </div>

              {/* Exclude Regex */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="exclude_regex">Exclude Pattern</Label>
                  <div className="flex items-center gap-2">
                    <Switch
                      checked={formData.stream_exclude_regex_enabled || false}
                      onCheckedChange={(checked) =>
                        setFormData({ ...formData, stream_exclude_regex_enabled: checked })
                      }
                    />
                    <span className="text-xs text-muted-foreground">Enabled</span>
                  </div>
                </div>
                <Input
                  id="exclude_regex"
                  value={formData.stream_exclude_regex || ""}
                  onChange={(e) =>
                    setFormData({ ...formData, stream_exclude_regex: e.target.value || null })
                  }
                  placeholder="e.g., (Spanish|French|German)"
                  disabled={!formData.stream_exclude_regex_enabled}
                  className={cn(!formData.stream_exclude_regex_enabled && "opacity-50")}
                />
                <p className="text-xs text-muted-foreground">
                  Streams matching this pattern will be skipped
                </p>
              </div>

              {/* Custom Teams Regex */}
              <div className="space-y-2 pt-4 border-t">
                <div className="flex items-center justify-between">
                  <Label htmlFor="teams_regex">Custom Team Extraction</Label>
                  <div className="flex items-center gap-2">
                    <Switch
                      checked={formData.custom_regex_teams_enabled || false}
                      onCheckedChange={(checked) =>
                        setFormData({ ...formData, custom_regex_teams_enabled: checked })
                      }
                    />
                    <span className="text-xs text-muted-foreground">Enabled</span>
                  </div>
                </div>
                <Input
                  id="teams_regex"
                  value={formData.custom_regex_teams || ""}
                  onChange={(e) =>
                    setFormData({ ...formData, custom_regex_teams: e.target.value || null })
                  }
                  placeholder="e.g., (.+?) vs (.+)"
                  disabled={!formData.custom_regex_teams_enabled}
                  className={cn(!formData.custom_regex_teams_enabled && "opacity-50")}
                />
                <p className="text-xs text-muted-foreground">
                  Custom regex to extract team names from stream names. Use capture groups or named groups (?P&lt;team1&gt;...) (?P&lt;team2&gt;...).
                </p>
              </div>

              {/* Skip Builtin Filter */}
              <div className="flex items-center justify-between pt-4 border-t">
                <div>
                  <Label>Skip Builtin Patterns</Label>
                  <p className="text-xs text-muted-foreground">
                    Disable automatic "Team A vs Team B" pattern detection
                  </p>
                </div>
                <Switch
                  checked={formData.skip_builtin_filter || false}
                  onCheckedChange={(checked) =>
                    setFormData({ ...formData, skip_builtin_filter: checked })
                  }
                />
              </div>
            </CardContent>
          </Card>

          {/* M3U Source */}
          {formData.m3u_group_name && (
            <Card>
              <CardHeader>
                <CardTitle>Stream Source</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between p-3 border rounded-md bg-muted/30">
                  <div>
                    <div className="font-medium">{formData.m3u_group_name}</div>
                    <div className="text-sm text-muted-foreground">
                      {formData.m3u_account_name && (
                        <span>Account: {formData.m3u_account_name} · </span>
                      )}
                      Group ID: {formData.m3u_group_id}
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Actions */}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => navigate("/event-groups")}>
              Cancel
            </Button>
            <Button variant="outline" onClick={() => setCurrentStep("leagues")}>
              Back
            </Button>
            <Button onClick={handleSubmit} disabled={isPending}>
              {isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              <Save className="h-4 w-4 mr-2" />
              {isEdit ? "Update Group" : "Create Group"}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
