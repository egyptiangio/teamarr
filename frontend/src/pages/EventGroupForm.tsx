import { useState, useEffect, useMemo } from "react"
import { useNavigate, useParams, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { ArrowLeft, Loader2, Save, ChevronRight, ChevronDown, X, Plus, Check } from "lucide-react"
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
import { TeamPicker } from "@/components/TeamPicker"

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

// Dispatcharr channel group
interface ChannelGroup {
  id: number
  name: string
}

// Dispatcharr channel profile
interface ChannelProfile {
  id: number
  name: string
}

async function fetchChannelGroups(): Promise<ChannelGroup[]> {
  const response = await fetch("/api/v1/dispatcharr/channel-groups")
  if (!response.ok) return []
  return response.json()
}

async function fetchChannelProfiles(): Promise<ChannelProfile[]> {
  const response = await fetch("/api/v1/dispatcharr/channel-profiles")
  if (!response.ok) return []
  return response.json()
}

async function createChannelGroup(name: string): Promise<ChannelGroup | null> {
  const response = await fetch(`/api/v1/dispatcharr/channel-groups?name=${encodeURIComponent(name)}`, {
    method: "POST",
  })
  if (!response.ok) return null
  return response.json()
}

async function createChannelProfile(name: string): Promise<ChannelProfile | null> {
  const response = await fetch(`/api/v1/dispatcharr/channel-profiles?name=${encodeURIComponent(name)}`, {
    method: "POST",
  })
  if (!response.ok) return null
  return response.json()
}

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

  const [groupMode, setGroupMode] = useState<GroupMode>(null)

  // Form state
  const [formData, setFormData] = useState<EventGroupCreate>({
    name: m3uGroupName || "",
    display_name: null,  // Optional display name override
    leagues: [],
    parent_group_id: null,
    template_id: null,
    channel_start_number: null,
    channel_assignment_mode: "auto",
    duplicate_event_handling: "consolidate",
    sort_order: 0,
    total_stream_count: 0,
    m3u_group_id: m3uGroupId ? Number(m3uGroupId) : null,
    m3u_group_name: m3uGroupName || null,
    m3u_account_id: m3uAccountId ? Number(m3uAccountId) : null,
    m3u_account_name: m3uAccountName || null,
    // Multi-sport enhancements (Phase 3)
    channel_sort_order: "time",
    overlap_handling: "add_stream",
    enabled: true,
    // Team filtering
    include_teams: null,
    exclude_teams: null,
    team_filter_mode: "include",
  })

  // Single-league selection
  const [selectedSport, setSelectedSport] = useState<string | null>(null)
  const [selectedLeague, setSelectedLeague] = useState<string | null>(null)

  // Track if this is a child group (inherits settings from parent)
  const isChildGroup = formData.parent_group_id != null

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

  // Fetch channel groups and profiles from Dispatcharr
  const { data: channelGroups, refetch: refetchChannelGroups } = useQuery({
    queryKey: ["dispatcharr-channel-groups"],
    queryFn: fetchChannelGroups,
  })
  const { data: channelProfiles, refetch: refetchChannelProfiles } = useQuery({
    queryKey: ["dispatcharr-channel-profiles"],
    queryFn: fetchChannelProfiles,
  })

  // Inline create state
  const [showCreateGroup, setShowCreateGroup] = useState(false)
  const [newGroupName, setNewGroupName] = useState("")
  const [creatingGroup, setCreatingGroup] = useState(false)
  const [showCreateProfile, setShowCreateProfile] = useState(false)
  const [newProfileName, setNewProfileName] = useState("")
  const [creatingProfile, setCreatingProfile] = useState(false)

  // Filter state for channel groups
  const [channelGroupFilter, setChannelGroupFilter] = useState("")

  // Collapsible section states
  const [regexExpanded, setRegexExpanded] = useState(false)
  const [teamFilterExpanded, setTeamFilterExpanded] = useState(false)

  // Mutations
  const createMutation = useCreateGroup()
  const updateMutation = useUpdateGroup()

  // Populate form when editing
  useEffect(() => {
    if (group) {
      setFormData({
        name: group.name,
        display_name: group.display_name,
        leagues: group.leagues,
        parent_group_id: group.parent_group_id,
        template_id: group.template_id,
        channel_start_number: group.channel_start_number,
        channel_group_id: group.channel_group_id,
        stream_profile_id: group.stream_profile_id,
        channel_profile_ids: group.channel_profile_ids || [],
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
        custom_regex_date: group.custom_regex_date,
        custom_regex_date_enabled: group.custom_regex_date_enabled,
        custom_regex_time: group.custom_regex_time,
        custom_regex_time_enabled: group.custom_regex_time_enabled,
        skip_builtin_filter: group.skip_builtin_filter,
        // Team filtering
        include_teams: group.include_teams,
        exclude_teams: group.exclude_teams,
        team_filter_mode: group.team_filter_mode || "include",
        // Multi-sport enhancements (Phase 3)
        channel_sort_order: group.channel_sort_order || "time",
        overlap_handling: group.overlap_handling || "add_stream",
        enabled: group.enabled,
      })

      // Use stored group_mode (not derived from league count) to preserve user intent
      const mode = group.group_mode as GroupMode || (group.leagues.length > 1 ? "multi" : "single")
      setGroupMode(mode)

      if (mode === "single") {
        // Single league mode - use first league
        if (group.leagues.length > 0) {
          setSelectedLeague(group.leagues[0])
          // Try to find sport from cached leagues
          const league = cachedLeagues?.find(l => l.slug === group.leagues[0])
          if (league) setSelectedSport(league.sport)
        }
      } else {
        // Multi league mode
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

  // Filtered channel groups based on search
  const filteredChannelGroups = useMemo(() => {
    if (!channelGroups) return []
    if (!channelGroupFilter) return channelGroups
    const filter = channelGroupFilter.toLowerCase()
    return channelGroups.filter(g => g.name.toLowerCase().includes(filter))
  }, [channelGroups, channelGroupFilter])

  // Eligible parent groups (single-league only, not multi-sport, not already a child)
  const eligibleParents = useMemo(() => {
    if (!groupsData?.groups) return []
    return groupsData.groups.filter(g => {
      // Can't be own parent
      if (isEdit && g.id === Number(groupId)) return false
      // Must be single-league
      if (g.leagues.length !== 1) return false
      // Must match our league
      if (selectedLeague && g.leagues[0] !== selectedLeague) return false
      // Can't be a child group (groups with parents can't be parents themselves)
      if (g.parent_group_id != null) return false
      return true
    })
  }, [groupsData, isEdit, groupId, selectedLeague])

  const handleModeSelect = (mode: GroupMode) => {
    setGroupMode(mode)
  }

  // handleLeaguesContinue removed - V1-style single-page flow uses inline league selection

  const handleSubmit = async () => {
    if (!formData.name.trim()) {
      toast.error("Group name is required")
      return
    }

    // Update leagues from selection state if not already set (single-page flow)
    let leagues = formData.leagues
    if (leagues.length === 0) {
      if (groupMode === "single" && selectedLeague) {
        leagues = [selectedLeague]
      } else if (groupMode === "multi" && selectedLeagues.size > 0) {
        leagues = Array.from(selectedLeagues)
      }
    }

    if (leagues.length === 0) {
      toast.error("At least one league is required")
      return
    }

    try {
      const submitData = {
        ...formData,
        leagues,
        // Only include group_mode if it's set (not null)
        ...(groupMode && { group_mode: groupMode })
      }

      if (isEdit) {
        const updateData: EventGroupUpdate = { ...submitData }

        // Compute clear flags for nullable fields that were changed from a value to null/undefined
        // This is required because the backend only clears fields when explicit clear_* flags are set
        if (group) {
          // Helper to check if field should be cleared (had value, now doesn't)
          const shouldClear = (original: unknown, current: unknown) =>
            original != null && (current == null || current === undefined)

          if (shouldClear(group.channel_group_id, formData.channel_group_id)) {
            updateData.clear_channel_group_id = true
          }
          if (shouldClear(group.stream_profile_id, formData.stream_profile_id)) {
            updateData.clear_stream_profile_id = true
          }
          if (shouldClear(group.template_id, formData.template_id)) {
            updateData.clear_template = true
          }
          if (shouldClear(group.channel_start_number, formData.channel_start_number)) {
            updateData.clear_channel_start_number = true
          }
          if (shouldClear(group.parent_group_id, formData.parent_group_id)) {
            updateData.clear_parent_group_id = true
          }
          if (shouldClear(group.display_name, formData.display_name)) {
            updateData.clear_display_name = true
          }
        }

        await updateMutation.mutateAsync({ groupId: Number(groupId), data: updateData })
        toast.success(`Updated group "${formData.name}"`)
      } else {
        await createMutation.mutateAsync(submitData)
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

  // Global select/clear all
  const selectAllLeagues = () => {
    const allSlugs = cachedLeagues?.map(l => l.slug) || []
    setSelectedLeagues(new Set(allSlugs))
  }

  const clearAllLeagues = () => {
    setSelectedLeagues(new Set())
  }

  // Check if all leagues in a sport are selected
  const isSportFullySelected = (sport: string) => {
    const sportLeagues = leaguesBySport[sport] || []
    return sportLeagues.length > 0 && sportLeagues.every(l => selectedLeagues.has(l.slug))
  }

  // Toggle entire sport (for consolidated sports like Soccer)
  const toggleSport = (sport: string) => {
    if (isSportFullySelected(sport)) {
      clearAllInSport(sport)
    } else {
      selectAllInSport(sport)
    }
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

      {/* Add Mode: Group Type Selector (V1 style) */}
      {!isEdit && !groupMode && (
        <Card className="bg-muted/30">
          <CardHeader>
            <CardTitle>Group Type</CardTitle>
            <CardDescription>Select the type of event group to create</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <button
                type="button"
                onClick={() => handleModeSelect("single")}
                className={cn(
                  "flex flex-col items-start p-4 rounded-lg border-2 text-left transition-all",
                  "border-border hover:border-primary/50"
                )}
              >
                <span className="font-semibold">Single League</span>
                <span className="text-xs text-muted-foreground mt-1">
                  Match streams to events in one specific league (e.g., NFL, NBA, EPL)
                </span>
              </button>
              <button
                type="button"
                onClick={() => handleModeSelect("multi")}
                className={cn(
                  "flex flex-col items-start p-4 rounded-lg border-2 text-left transition-all",
                  "border-border hover:border-primary/50"
                )}
              >
                <span className="font-semibold">Multi-Sport / Multi-League</span>
                <span className="text-xs text-muted-foreground mt-1">
                  Match streams across multiple sports and leagues (e.g., ESPN+)
                </span>
              </button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* League Selection - Single League Mode (add mode only) */}
      {groupMode === "single" && !isEdit && (
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
              {isLoadingLeagues ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading sports...
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {Object.keys(leaguesBySport).sort((a, b) =>
                    (SPORT_NAMES[a] || a).localeCompare(SPORT_NAMES[b] || b)
                  ).map((sport) => (
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
              )}
            </div>

            {/* League Selection */}
            {selectedSport && leaguesBySport[selectedSport] && (
              <div className="space-y-2">
                <Label>League ({leaguesBySport[selectedSport].length} available)</Label>
                <div className="max-h-72 overflow-y-auto grid grid-cols-3 gap-1 border rounded-md p-2">
                  {leaguesBySport[selectedSport].map(league => (
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

            {/* Parent Group Selection */}
            {selectedLeague && eligibleParents.length > 0 && (
              <div className="space-y-2 pt-4 border-t">
                <Label>Parent Group (Optional)</Label>
                <p className="text-xs text-muted-foreground mb-2">
                  Child groups inherit all settings from parent and add streams to parent's channels.
                </p>
                <Select
                  value={formData.parent_group_id?.toString() || ""}
                  onChange={(e) => setFormData({
                    ...formData,
                    parent_group_id: e.target.value ? Number(e.target.value) : null
                  })}
                >
                  <option value="">No parent (independent group)</option>
                  {eligibleParents.map(g => (
                    <option key={g.id} value={g.id}>{g.name}</option>
                  ))}
                </Select>
              </div>
            )}

          </CardContent>
        </Card>
      )}

      {/* League Selection - Multi-Sport Mode (add mode only) */}
      {groupMode === "multi" && !isEdit && (
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

            {/* Selected count and global actions */}
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted-foreground">
                {selectedLeagues.size} league{selectedLeagues.size !== 1 ? "s" : ""} selected
              </span>
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={selectAllLeagues}
                >
                  Select All
                </Button>
                {selectedLeagues.size > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={clearAllLeagues}
                  >
                    Clear All
                  </Button>
                )}
              </div>
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
                    sport.toLowerCase().includes(leagueSearch.toLowerCase()) ||
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

                    // For search, if no individual leagues match but sport name matches, show all
                    const displayLeagues = leagueSearch && sportLeaguesFiltered.length === 0 &&
                      sport.toLowerCase().includes(leagueSearch.toLowerCase())
                      ? leagues
                      : sportLeaguesFiltered

                    if (displayLeagues.length === 0) return null

                    const allSelected = isSportFullySelected(sport)
                    const someSelected = leagues.some(l => selectedLeagues.has(l.slug)) && !allSelected

                    // Soccer: show as single consolidated checkbox (too many leagues)
                    if (sport === "soccer") {
                      return (
                        <label
                          key={sport}
                          className={cn(
                            "flex items-center gap-3 px-3 py-3 cursor-pointer hover:bg-accent",
                            allSelected && "bg-primary/10"
                          )}
                          onClick={(e) => {
                            e.preventDefault()
                            toggleSport(sport)
                          }}
                        >
                          <Checkbox
                            checked={allSelected}
                            // @ts-expect-error - indeterminate is valid but not in types
                            indeterminate={someSelected}
                            onCheckedChange={() => toggleSport(sport)}
                          />
                          <div className="flex-1">
                            <div className="font-medium text-sm">
                              {SPORT_NAMES[sport] || sport}
                            </div>
                            <div className="text-xs text-muted-foreground">
                              All {leagues.length} leagues (EPL, La Liga, Bundesliga, Serie A, Ligue 1, MLS, Champions League, etc.)
                            </div>
                          </div>
                        </label>
                      )
                    }

                    // Other sports: show individual leagues
                    return (
                      <div key={sport}>
                        <div className="flex items-center justify-between px-3 py-2 bg-muted/50 sticky top-0">
                          <span className="font-medium text-sm">
                            {SPORT_NAMES[sport] || sport} ({displayLeagues.length})
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
                          {displayLeagues.map(league => (
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

          </CardContent>
        </Card>
      )}

      {/* Settings Section - shown when leagues selected or in edit mode */}
      {(isEdit || formData.leagues.length > 0 || selectedLeague || selectedLeagues.size > 0) && (
        <div className="space-y-6">
          {/* Child Group Notice */}
          {isChildGroup && (
            <Card className="border-blue-500/50 bg-blue-500/5">
              <CardContent className="py-4">
                <div className="flex items-start gap-3">
                  <span className="text-2xl">👶</span>
                  <div>
                    <p className="font-medium">Child Group</p>
                    <p className="text-sm text-muted-foreground">
                      This group inherits league, template, and channel settings from its parent.
                      Only enabled status and custom regex patterns can be configured here.
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Group Type Indicator - hidden for child groups */}
          {!isChildGroup && (
            <div className="flex items-center gap-2 px-1 py-2">
              <Badge variant="secondary" className="font-normal">
                {formData.leagues.length > 1 ? "Multi-Sport / Multi-League" : "Single League"}
              </Badge>
            </div>
          )}

          {/* Child Group Basic Settings - only name and enabled */}
          {isChildGroup && (
            <Card>
              <CardHeader>
                <CardTitle>Basic Settings</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="name">Group Name</Label>
                  <Input
                    id="name"
                    value={formData.name}
                    readOnly
                    className="bg-muted"
                  />
                  <p className="text-xs text-muted-foreground">Name from M3U group</p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="display_name_child">Display Name (Optional)</Label>
                  <Input
                    id="display_name_child"
                    value={formData.display_name || ""}
                    onChange={(e) => setFormData({ ...formData, display_name: e.target.value || null })}
                    placeholder="Override name for display in UI"
                  />
                  <p className="text-xs text-muted-foreground">
                    If set, this name will be shown instead of the M3U group name
                  </p>
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
          )}

          {/* Basic Info - hidden for child groups (inherited from parent) */}
          {!isChildGroup && <Card>
            <CardHeader>
              <CardTitle>Basic Settings</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className={cn("grid gap-4", isChildGroup ? "grid-cols-1" : "grid-cols-2")}>
                <div className="space-y-2">
                  <Label htmlFor="name">Group Name</Label>
                  <Input
                    id="name"
                    value={formData.name}
                    readOnly
                    className="bg-muted"
                  />
                  <p className="text-xs text-muted-foreground">Name from M3U group</p>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="display_name">Display Name (Optional)</Label>
                  <Input
                    id="display_name"
                    value={formData.display_name || ""}
                    onChange={(e) => setFormData({ ...formData, display_name: e.target.value || null })}
                    placeholder="Override name for display in UI"
                  />
                  <p className="text-xs text-muted-foreground">
                    If set, shown instead of M3U group name
                  </p>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                {!isChildGroup && (
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
                      <option value="">Unassigned</option>
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
                )}
              </div>

              {/* Show selected leagues in edit mode */}
              {isEdit && groupMode === "single" && (
                <div className="space-y-2">
                  <Label>League</Label>
                  <div className="flex items-center gap-2">
                    {formData.leagues.map(slug => {
                      const league = cachedLeagues?.find(l => l.slug === slug)
                      return (
                        <Badge key={slug} variant="secondary" className="gap-1.5 py-1.5 px-3">
                          {league?.logo_url && (
                            <img src={league.logo_url} alt="" className="h-4 w-4 object-contain" />
                          )}
                          {league?.name || slug}
                        </Badge>
                      )
                    })}
                    <span className="text-xs text-muted-foreground ml-2">
                      (set on import)
                    </span>
                  </div>
                </div>
              )}

              {/* Full league picker for multi-league groups in edit mode */}
              {isEdit && groupMode === "multi" && (
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <Label>Matching Leagues</Label>
                    <div className="flex items-center gap-1">
                      <span className="text-xs text-muted-foreground mr-2">
                        {formData.leagues.length} selected
                      </span>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-6 text-xs"
                        onClick={() => {
                          const allSlugs = cachedLeagues?.map(l => l.slug) || []
                          setFormData({ ...formData, leagues: allSlugs })
                        }}
                      >
                        Select All
                      </Button>
                      {formData.leagues.length > 0 && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-6 text-xs"
                          onClick={() => setFormData({ ...formData, leagues: [] })}
                        >
                          Clear
                        </Button>
                      )}
                    </div>
                  </div>

                  {/* Search */}
                  <Input
                    placeholder="Search leagues..."
                    value={leagueSearch}
                    onChange={(e) => setLeagueSearch(e.target.value)}
                  />

                  {/* Selected badges */}
                  {formData.leagues.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {formData.leagues.slice(0, 10).map(slug => {
                        const league = cachedLeagues?.find(l => l.slug === slug)
                        return (
                          <Badge key={slug} variant="secondary" className="gap-1">
                            {league?.logo_url && (
                              <img src={league.logo_url} alt="" className="h-3 w-3 object-contain" />
                            )}
                            {league?.name || slug}
                            <button
                              type="button"
                              onClick={() => setFormData({
                                ...formData,
                                leagues: formData.leagues.filter(l => l !== slug)
                              })}
                              className="ml-1 hover:bg-muted rounded"
                            >
                              <X className="h-3 w-3" />
                            </button>
                          </Badge>
                        )
                      })}
                      {formData.leagues.length > 10 && (
                        <Badge variant="outline">+{formData.leagues.length - 10} more</Badge>
                      )}
                    </div>
                  )}

                  {/* League picker by sport */}
                  {isLoadingLeagues ? (
                    <div className="flex items-center justify-center py-8">
                      <Loader2 className="h-6 w-6 animate-spin" />
                    </div>
                  ) : (
                    <div className="max-h-72 overflow-y-auto border rounded-md divide-y">
                      {Object.entries(leaguesBySport)
                        .filter(([sport]) =>
                          !leagueSearch ||
                          sport.toLowerCase().includes(leagueSearch.toLowerCase()) ||
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

                          const displayLeagues = leagueSearch && sportLeaguesFiltered.length === 0 &&
                            sport.toLowerCase().includes(leagueSearch.toLowerCase())
                            ? leagues
                            : sportLeaguesFiltered

                          if (displayLeagues.length === 0) return null

                          const allInSportSelected = leagues.every(l => formData.leagues.includes(l.slug))
                          const someInSportSelected = leagues.some(l => formData.leagues.includes(l.slug)) && !allInSportSelected

                          // Soccer: consolidated checkbox
                          if (sport === "soccer") {
                            return (
                              <label
                                key={sport}
                                className={cn(
                                  "flex items-center gap-3 px-3 py-3 cursor-pointer hover:bg-accent",
                                  allInSportSelected && "bg-primary/10"
                                )}
                                onClick={(e) => {
                                  e.preventDefault()
                                  const sportSlugs = leagues.map(l => l.slug)
                                  if (allInSportSelected) {
                                    setFormData({ ...formData, leagues: formData.leagues.filter(l => !sportSlugs.includes(l)) })
                                  } else {
                                    const newLeagues = [...new Set([...formData.leagues, ...sportSlugs])]
                                    setFormData({ ...formData, leagues: newLeagues })
                                  }
                                }}
                              >
                                <Checkbox
                                  checked={allInSportSelected}
                                  // @ts-expect-error - indeterminate is valid
                                  indeterminate={someInSportSelected}
                                />
                                <div className="flex-1">
                                  <div className="font-medium text-sm">{SPORT_NAMES[sport] || sport}</div>
                                  <div className="text-xs text-muted-foreground">
                                    All {leagues.length} leagues (EPL, La Liga, Bundesliga, etc.)
                                  </div>
                                </div>
                              </label>
                            )
                          }

                          // Other sports: individual leagues
                          return (
                            <div key={sport}>
                              <div className="flex items-center justify-between px-3 py-2 bg-muted/50 sticky top-0">
                                <span className="font-medium text-sm">
                                  {SPORT_NAMES[sport] || sport} ({displayLeagues.length})
                                </span>
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="sm"
                                  className="h-6 text-xs"
                                  onClick={() => {
                                    const sportSlugs = leagues.map(l => l.slug)
                                    if (allInSportSelected) {
                                      setFormData({ ...formData, leagues: formData.leagues.filter(l => !sportSlugs.includes(l)) })
                                    } else {
                                      const newLeagues = [...new Set([...formData.leagues, ...sportSlugs])]
                                      setFormData({ ...formData, leagues: newLeagues })
                                    }
                                  }}
                                >
                                  {allInSportSelected ? "Clear" : "Select All"}
                                </Button>
                              </div>
                              <div className="grid grid-cols-2 md:grid-cols-3 gap-1 p-2">
                                {displayLeagues.map(league => {
                                  const isSelected = formData.leagues.includes(league.slug)
                                  return (
                                    <label
                                      key={league.slug}
                                      className={cn(
                                        "flex items-center gap-2 px-2 py-1.5 rounded text-sm cursor-pointer hover:bg-accent",
                                        isSelected && "bg-primary/10"
                                      )}
                                    >
                                      <Checkbox
                                        checked={isSelected}
                                        onCheckedChange={() => {
                                          if (isSelected) {
                                            setFormData({ ...formData, leagues: formData.leagues.filter(l => l !== league.slug) })
                                          } else {
                                            setFormData({ ...formData, leagues: [...formData.leagues, league.slug] })
                                          }
                                        }}
                                      />
                                      {league.logo_url && (
                                        <img src={league.logo_url} alt="" className="h-4 w-4 object-contain" />
                                      )}
                                      <span className="truncate">{league.name}</span>
                                    </label>
                                  )
                                })}
                              </div>
                            </div>
                          )
                        })}
                    </div>
                  )}
                </div>
              )}

              <div className="flex items-center gap-2">
                <Switch
                  checked={formData.enabled}
                  onCheckedChange={(checked) => setFormData({ ...formData, enabled: checked })}
                />
                <Label className="font-normal">Enabled</Label>
              </div>
            </CardContent>
          </Card>}

          {/* Channel Settings - hidden for child groups */}
          {!isChildGroup && <Card>
            <CardHeader>
              <CardTitle>Channel Settings</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Channel Assignment Mode - V1 style tile cards */}
              <div className="space-y-2">
                <Label>Channel Assignment Mode</Label>
                <div className="grid grid-cols-2 gap-3">
                  {/* AUTO Card */}
                  <button
                    type="button"
                    onClick={() => setFormData({ ...formData, channel_assignment_mode: "auto", channel_start_number: null })}
                    className={cn(
                      "flex flex-col items-start p-4 rounded-lg border-2 text-left transition-all",
                      formData.channel_assignment_mode === "auto"
                        ? "border-green-500 bg-green-500/10"
                        : "border-border hover:border-muted-foreground/50"
                    )}
                  >
                    <span className={cn(
                      "font-semibold text-sm",
                      formData.channel_assignment_mode === "auto" && "text-green-500"
                    )}>
                      AUTO
                    </span>
                    <span className="text-xs text-muted-foreground mt-1">
                      Auto-assign from global range. Drag to set priority on Event Groups page.
                    </span>
                  </button>

                  {/* MANUAL Card */}
                  <button
                    type="button"
                    onClick={() => setFormData({ ...formData, channel_assignment_mode: "manual" })}
                    className={cn(
                      "flex flex-col items-start p-4 rounded-lg border-2 text-left transition-all",
                      formData.channel_assignment_mode === "manual"
                        ? "border-primary bg-primary/10"
                        : "border-border hover:border-muted-foreground/50"
                    )}
                  >
                    <span className={cn(
                      "font-semibold text-sm",
                      formData.channel_assignment_mode === "manual" && "text-primary"
                    )}>
                      MANUAL
                    </span>
                    <span className="text-xs text-muted-foreground mt-1">
                      Specify a fixed channel start number below.
                    </span>
                  </button>
                </div>
              </div>

              {/* Channel Start Number - only shown for manual */}
              {formData.channel_assignment_mode === "manual" && (
                <div className="space-y-2">
                  <Label htmlFor="channel_start">Channel Start Number</Label>
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
                    placeholder="Required for MANUAL mode"
                  />
                  <p className="text-xs text-muted-foreground">
                    First channel number for created channels
                  </p>
                </div>
              )}

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
          </Card>}

          {/* Dispatcharr Settings - hidden for child groups */}
          {!isChildGroup && <Card>
            <CardHeader>
              <CardTitle>Dispatcharr Settings</CardTitle>
              <CardDescription>
                Channel group and profile assignments in Dispatcharr
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Channel Group - V1 style with filter and list */}
              <div className="space-y-2">
                <Label>Channel Group</Label>
                <div className="flex gap-2 items-center">
                  <Input
                    placeholder="Filter groups..."
                    value={channelGroupFilter}
                    onChange={(e) => setChannelGroupFilter(e.target.value)}
                    className="flex-1"
                  />
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => setShowCreateGroup(!showCreateGroup)}
                  >
                    <Plus className="h-3.5 w-3.5 mr-1" />
                    New
                  </Button>
                </div>
                {showCreateGroup && (
                  <div className="flex gap-2 p-2 bg-muted/50 rounded-md">
                    <Input
                      placeholder="New group name..."
                      value={newGroupName}
                      onChange={(e) => setNewGroupName(e.target.value)}
                      className="flex-1"
                    />
                    <Button
                      type="button"
                      size="sm"
                      disabled={creatingGroup || !newGroupName.trim()}
                      onClick={async () => {
                        setCreatingGroup(true)
                        const created = await createChannelGroup(newGroupName.trim())
                        setCreatingGroup(false)
                        if (created) {
                          toast.success(`Created group "${created.name}"`)
                          setFormData({ ...formData, channel_group_id: created.id })
                          setNewGroupName("")
                          setShowCreateGroup(false)
                          setChannelGroupFilter("")
                          refetchChannelGroups()
                        } else {
                          toast.error("Failed to create group")
                        }
                      }}
                    >
                      {creatingGroup ? <Loader2 className="h-4 w-4 animate-spin" /> : "Create"}
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setShowCreateGroup(false)
                        setNewGroupName("")
                      }}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                )}
                <div className="border rounded-md max-h-36 overflow-y-auto">
                  {/* "None" option */}
                  <button
                    type="button"
                    className={cn(
                      "w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-accent border-b",
                      !formData.channel_group_id && "bg-primary/10"
                    )}
                    onClick={() => setFormData({ ...formData, channel_group_id: undefined })}
                  >
                    <div className={cn(
                      "w-4 h-4 border rounded flex items-center justify-center",
                      !formData.channel_group_id && "bg-primary border-primary"
                    )}>
                      {!formData.channel_group_id && <Check className="h-3 w-3 text-primary-foreground" />}
                    </div>
                    <span className="text-muted-foreground italic">&lt;No group assignment&gt;</span>
                  </button>
                  {filteredChannelGroups.length === 0 ? (
                    <div className="p-3 text-sm text-muted-foreground text-center">
                      {channelGroupFilter ? "No matching groups" : "No groups found"}
                    </div>
                  ) : (
                    filteredChannelGroups.map((g) => {
                      const isSelected = formData.channel_group_id === g.id
                      return (
                        <button
                          key={g.id}
                          type="button"
                          className={cn(
                            "w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-accent border-b last:border-b-0",
                            isSelected && "bg-primary/10"
                          )}
                          onClick={() => setFormData({ ...formData, channel_group_id: g.id })}
                        >
                          <div className={cn(
                            "w-4 h-4 border rounded flex items-center justify-center",
                            isSelected && "bg-primary border-primary"
                          )}>
                            {isSelected && <Check className="h-3 w-3 text-primary-foreground" />}
                          </div>
                          <span className="flex-1">{g.name}</span>
                        </button>
                      )
                    })
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  Dispatcharr group to assign created channels to
                </p>
              </div>

              {/* Channel Profiles */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label>Channel Profiles</Label>
                  <div className="flex gap-1">
                    {(formData.channel_profile_ids?.length || 0) > 0 && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-muted-foreground"
                        onClick={() => setFormData({ ...formData, channel_profile_ids: [] })}
                      >
                        Clear
                      </Button>
                    )}
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2"
                      onClick={() => setShowCreateProfile(!showCreateProfile)}
                    >
                      <Plus className="h-3.5 w-3.5 mr-1" />
                      New
                    </Button>
                  </div>
                </div>
                {showCreateProfile && (
                  <div className="flex gap-2 p-2 bg-muted/50 rounded-md">
                    <Input
                      placeholder="New profile name..."
                      value={newProfileName}
                      onChange={(e) => setNewProfileName(e.target.value)}
                      className="flex-1"
                    />
                    <Button
                      type="button"
                      size="sm"
                      disabled={creatingProfile || !newProfileName.trim()}
                      onClick={async () => {
                        setCreatingProfile(true)
                        const created = await createChannelProfile(newProfileName.trim())
                        setCreatingProfile(false)
                        if (created) {
                          toast.success(`Created profile "${created.name}"`)
                          setFormData({
                            ...formData,
                            channel_profile_ids: [...(formData.channel_profile_ids || []), created.id],
                          })
                          setNewProfileName("")
                          setShowCreateProfile(false)
                          refetchChannelProfiles()
                        } else {
                          toast.error("Failed to create profile")
                        }
                      }}
                    >
                      {creatingProfile ? <Loader2 className="h-4 w-4 animate-spin" /> : "Create"}
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setShowCreateProfile(false)
                        setNewProfileName("")
                      }}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                )}
                <div className="border rounded-md max-h-40 overflow-y-auto">
                  {channelProfiles?.length === 0 ? (
                    <div className="p-3 text-sm text-muted-foreground text-center">
                      No profiles found
                    </div>
                  ) : (
                    channelProfiles?.map((p) => {
                      const isSelected = formData.channel_profile_ids?.includes(p.id) || false
                      return (
                        <button
                          key={p.id}
                          type="button"
                          className={cn(
                            "w-full flex items-center gap-2 px-3 py-2 text-sm text-left hover:bg-accent border-b last:border-b-0",
                            isSelected && "bg-primary/10"
                          )}
                          onClick={() => {
                            const current = formData.channel_profile_ids || []
                            if (isSelected) {
                              setFormData({
                                ...formData,
                                channel_profile_ids: current.filter((id) => id !== p.id),
                              })
                            } else {
                              setFormData({
                                ...formData,
                                channel_profile_ids: [...current, p.id],
                              })
                            }
                          }}
                        >
                          <div className={cn(
                            "w-4 h-4 border rounded flex items-center justify-center",
                            isSelected && "bg-primary border-primary"
                          )}>
                            {isSelected && <Check className="h-3 w-3 text-primary-foreground" />}
                          </div>
                          <span className="flex-1">{p.name}</span>
                        </button>
                      )
                    })
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  Select profiles to add created channels to (click to toggle)
                </p>
              </div>
            </CardContent>
          </Card>}

          {/* Custom Regex - Collapsible section (available for all groups including children) */}
          <Card>
            <button
              type="button"
              onClick={() => setRegexExpanded(!regexExpanded)}
              className="w-full"
            >
              <CardHeader className="flex flex-row items-center justify-between py-3 cursor-pointer hover:bg-muted/50 rounded-t-lg">
                <div className="flex items-center gap-2">
                  {regexExpanded ? (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                  )}
                  <CardTitle className="text-base">Custom Regex</CardTitle>
                </div>
              </CardHeader>
            </button>

            {regexExpanded && (
              <CardContent className="space-y-6 pt-0">
                {/* Stream Filtering Subsection */}
                <div className="space-y-4">
                  <div className="border-b pb-2">
                    <h4 className="font-medium text-sm">Stream Filtering</h4>
                    <p className="text-xs text-muted-foreground mt-1">
                      Streams are automatically filtered to only include game streams (those with vs, @, or at).
                    </p>
                  </div>

                  {/* Skip Builtin Filter */}
                  <div className="flex items-center gap-3">
                    <Checkbox
                      id="skip_builtin"
                      checked={formData.skip_builtin_filter || false}
                      onClick={() =>
                        setFormData({ ...formData, skip_builtin_filter: !formData.skip_builtin_filter })
                      }
                    />
                    <div>
                      <Label htmlFor="skip_builtin" className="font-normal cursor-pointer">
                        Skip built-in game detection
                      </Label>
                      <p className="text-xs text-muted-foreground">
                        Disable automatic filtering when stream names don't use standard separators (vs, @, at).
                      </p>
                    </div>
                  </div>

                  {/* Inclusion Pattern */}
                  <div className="space-y-2">
                    <div className="flex items-center gap-3">
                      <Checkbox
                        id="include_enabled"
                        checked={formData.stream_include_regex_enabled || false}
                        onClick={() =>
                          setFormData({ ...formData, stream_include_regex_enabled: !formData.stream_include_regex_enabled })
                        }
                      />
                      <Label htmlFor="include_enabled" className="font-normal cursor-pointer">
                        Inclusion Pattern
                      </Label>
                    </div>
                    <Input
                      value={formData.stream_include_regex || ""}
                      onChange={(e) =>
                        setFormData({ ...formData, stream_include_regex: e.target.value || null })
                      }
                      placeholder="e.g., Gonzaga|Washington State|Eastern Washington"
                      disabled={!formData.stream_include_regex_enabled}
                      className={cn("font-mono text-sm", !formData.stream_include_regex_enabled && "opacity-50")}
                    />
                    <p className="text-xs text-muted-foreground">
                      Only streams matching this pattern will be processed.
                    </p>
                  </div>

                  {/* Exclusion Pattern */}
                  <div className="space-y-2">
                    <div className="flex items-center gap-3">
                      <Checkbox
                        id="exclude_enabled"
                        checked={formData.stream_exclude_regex_enabled || false}
                        onClick={() =>
                          setFormData({ ...formData, stream_exclude_regex_enabled: !formData.stream_exclude_regex_enabled })
                        }
                      />
                      <Label htmlFor="exclude_enabled" className="font-normal cursor-pointer">
                        Exclusion Pattern
                      </Label>
                    </div>
                    <Input
                      value={formData.stream_exclude_regex || ""}
                      onChange={(e) =>
                        setFormData({ ...formData, stream_exclude_regex: e.target.value || null })
                      }
                      placeholder="e.g., \(ES\)|\(ALT\)|All.?Star"
                      disabled={!formData.stream_exclude_regex_enabled}
                      className={cn("font-mono text-sm", !formData.stream_exclude_regex_enabled && "opacity-50")}
                    />
                    <p className="text-xs text-muted-foreground">
                      Streams matching this pattern will be excluded.
                    </p>
                  </div>
                </div>

                {/* Team Matching Subsection */}
                <div className="space-y-4">
                  <div className="border-b pb-2">
                    <h4 className="font-medium text-sm">Team Matching</h4>
                    <p className="text-xs text-muted-foreground mt-1">
                      Override built-in matching with custom regex patterns. Enable individual fields as needed.
                    </p>
                  </div>

                  {/* Teams Pattern */}
                  <div className="space-y-2">
                    <div className="flex items-center gap-3">
                      <Checkbox
                        id="teams_enabled"
                        checked={formData.custom_regex_teams_enabled || false}
                        onClick={() =>
                          setFormData({ ...formData, custom_regex_teams_enabled: !formData.custom_regex_teams_enabled })
                        }
                      />
                      <Label htmlFor="teams_enabled" className="font-normal cursor-pointer">
                        Teams Pattern
                      </Label>
                    </div>
                    <Input
                      value={formData.custom_regex_teams || ""}
                      onChange={(e) =>
                        setFormData({ ...formData, custom_regex_teams: e.target.value || null })
                      }
                      placeholder="(?P<team1>[A-Z]{2,3})\s*[@vs]+\s*(?P<team2>[A-Z]{2,3})"
                      disabled={!formData.custom_regex_teams_enabled}
                      className={cn("font-mono text-sm", !formData.custom_regex_teams_enabled && "opacity-50")}
                    />
                  </div>

                  {/* Date Pattern */}
                  <div className="space-y-2">
                    <div className="flex items-center gap-3">
                      <Checkbox
                        id="date_enabled"
                        checked={formData.custom_regex_date_enabled || false}
                        onClick={() =>
                          setFormData({ ...formData, custom_regex_date_enabled: !formData.custom_regex_date_enabled })
                        }
                      />
                      <Label htmlFor="date_enabled" className="font-normal cursor-pointer">
                        Date Pattern
                      </Label>
                    </div>
                    <Input
                      value={formData.custom_regex_date || ""}
                      onChange={(e) =>
                        setFormData({ ...formData, custom_regex_date: e.target.value || null })
                      }
                      placeholder="(?P<date>\d{1,2}/\d{1,2})"
                      disabled={!formData.custom_regex_date_enabled}
                      className={cn("font-mono text-sm", !formData.custom_regex_date_enabled && "opacity-50")}
                    />
                    <p className="text-xs text-muted-foreground">
                      Extract date from stream name. Use named group: (?P&lt;date&gt;...)
                    </p>
                  </div>

                  {/* Time Pattern */}
                  <div className="space-y-2">
                    <div className="flex items-center gap-3">
                      <Checkbox
                        id="time_enabled"
                        checked={formData.custom_regex_time_enabled || false}
                        onClick={() =>
                          setFormData({ ...formData, custom_regex_time_enabled: !formData.custom_regex_time_enabled })
                        }
                      />
                      <Label htmlFor="time_enabled" className="font-normal cursor-pointer">
                        Time Pattern
                      </Label>
                    </div>
                    <Input
                      value={formData.custom_regex_time || ""}
                      onChange={(e) =>
                        setFormData({ ...formData, custom_regex_time: e.target.value || null })
                      }
                      placeholder="(?P<time>\d{1,2}:\d{2}\s*(?:AM|PM)?)"
                      disabled={!formData.custom_regex_time_enabled}
                      className={cn("font-mono text-sm", !formData.custom_regex_time_enabled && "opacity-50")}
                    />
                    <p className="text-xs text-muted-foreground">
                      Extract time from stream name. Use named group: (?P&lt;time&gt;...)
                    </p>
                  </div>

                  {/* Test Patterns Button - only in edit mode */}
                  {isEdit && (
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={() => toast.info("Test Patterns feature coming soon")}
                    >
                      Test Patterns
                    </Button>
                  )}
                </div>
              </CardContent>
            )}
          </Card>

          {/* Team Filtering - only show for parent groups */}
          {!isChildGroup && (
            <Card>
              <button
                type="button"
                onClick={() => setTeamFilterExpanded(!teamFilterExpanded)}
                className="w-full"
              >
                <CardHeader className="flex flex-row items-center justify-between py-3 cursor-pointer hover:bg-muted/50 rounded-t-lg">
                  <div className="flex items-center gap-2">
                    {teamFilterExpanded ? (
                      <ChevronDown className="h-4 w-4 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="h-4 w-4 text-muted-foreground" />
                    )}
                    <CardTitle className="text-base">Team Filtering</CardTitle>
                    {((formData.include_teams?.length ?? 0) > 0 || (formData.exclude_teams?.length ?? 0) > 0) && (
                      <Badge variant="secondary" className="text-xs">
                        {(formData.include_teams?.length ?? 0) + (formData.exclude_teams?.length ?? 0)} teams
                      </Badge>
                    )}
                  </div>
                </CardHeader>
              </button>

              {teamFilterExpanded && (
                <CardContent className="space-y-4 pt-0">
                  {/* Enable/Disable toggle */}
                  <div className="flex items-center justify-between">
                    <p className="text-sm text-muted-foreground">
                      Filter events by specific teams. Child groups inherit this filter.
                    </p>
                    <div className="flex items-center gap-2">
                      <Label htmlFor="group-team-filter-enabled" className="text-sm">
                        {(formData.include_teams?.length || formData.exclude_teams?.length) ? "Enabled" : "Disabled"}
                      </Label>
                      <Switch
                        id="group-team-filter-enabled"
                        checked={!!(formData.include_teams?.length || formData.exclude_teams?.length)}
                        onCheckedChange={(checked) => {
                          if (!checked) {
                            // Disable - clear all teams (send [] to clear, not null)
                            setFormData({
                              ...formData,
                              include_teams: [],
                              exclude_teams: [],
                              team_filter_mode: "include",
                            })
                          }
                          // If enabling, user will add teams below
                        }}
                      />
                    </div>
                  </div>

                  {/* Mode selector */}
                  <div className="flex gap-4">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="team_filter_mode"
                        value="include"
                        checked={formData.team_filter_mode === "include"}
                        onChange={() => {
                          // Move teams to include list when switching modes
                          const teams = formData.exclude_teams || []
                          setFormData({
                            ...formData,
                            team_filter_mode: "include",
                            include_teams: teams.length > 0 ? teams : formData.include_teams,
                            exclude_teams: [],  // Send [] to clear
                          })
                        }}
                        className="text-primary"
                      />
                      <span className="text-sm">Include only selected teams</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="team_filter_mode"
                        value="exclude"
                        checked={formData.team_filter_mode === "exclude"}
                        onChange={() => {
                          // Move teams to exclude list when switching modes
                          const teams = formData.include_teams || []
                          setFormData({
                            ...formData,
                            team_filter_mode: "exclude",
                            exclude_teams: teams.length > 0 ? teams : formData.exclude_teams,
                            include_teams: [],  // Send [] to clear
                          })
                        }}
                        className="text-primary"
                      />
                      <span className="text-sm">Exclude selected teams</span>
                    </label>
                  </div>

                  {/* Team picker */}
                  <TeamPicker
                    leagues={formData.leagues}
                    selectedTeams={
                      formData.team_filter_mode === "include"
                        ? (formData.include_teams || [])
                        : (formData.exclude_teams || [])
                    }
                    onSelectionChange={(teams) => {
                      if (formData.team_filter_mode === "include") {
                        setFormData({
                          ...formData,
                          include_teams: teams,  // Send [] to clear, not null
                          exclude_teams: [],
                        })
                      } else {
                        setFormData({
                          ...formData,
                          exclude_teams: teams,  // Send [] to clear, not null
                          include_teams: [],
                        })
                      }
                    }}
                  />

                  <p className="text-xs text-muted-foreground">
                    {!(formData.include_teams?.length || formData.exclude_teams?.length)
                      ? "No filter active. All events will be matched."
                      : formData.team_filter_mode === "include"
                        ? `Only events involving ${formData.include_teams?.length} selected team(s) will be matched.`
                        : `Events involving ${formData.exclude_teams?.length} selected team(s) will be excluded.`}
                  </p>
                </CardContent>
              )}
            </Card>
          )}

          {/* Multi-Sport Settings - only show for multi-sport parent groups */}
          {!isChildGroup && formData.leagues.length > 1 && (
            <Card>
              <CardHeader>
                <CardTitle>Multi-Sport Settings</CardTitle>
                <CardDescription>
                  Configure how events from multiple leagues are handled
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="channel_sort_order">Channel Sort Order</Label>
                    <Select
                      id="channel_sort_order"
                      value={formData.channel_sort_order || "time"}
                      onChange={(e) =>
                        setFormData({ ...formData, channel_sort_order: e.target.value })
                      }
                    >
                      <option value="time">By Time (default)</option>
                      <option value="sport_time">By Sport, then Time</option>
                      <option value="league_time">By League, then Time</option>
                    </Select>
                    <p className="text-xs text-muted-foreground">
                      How to order channels when multiple events are scheduled
                    </p>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="overlap_handling">Overlap Handling</Label>
                    <Select
                      id="overlap_handling"
                      value={formData.overlap_handling || "add_stream"}
                      onChange={(e) =>
                        setFormData({ ...formData, overlap_handling: e.target.value })
                      }
                    >
                      <option value="add_stream">Add Stream (default)</option>
                      <option value="add_only">Add Only</option>
                      <option value="create_all">Create All</option>
                      <option value="skip">Skip</option>
                    </Select>
                    <p className="text-xs text-muted-foreground">
                      How to handle events that overlap in time
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          {/* M3U Source - hidden for child groups */}
          {!isChildGroup && formData.m3u_group_name && (
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
