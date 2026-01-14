import { useState, useMemo, useDeferredValue } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { cn, getLeagueDisplayName } from "@/lib/utils"
import { toast } from "sonner"
import type { CachedLeague } from "@/api/teams"
import { getLeagues } from "@/api/teams"
import { ChevronRight, Loader2, Check, Search, X } from "lucide-react"
import { useThemedLogo } from "@/hooks/useTheme"

// Types
interface CacheTeam {
  id: number
  team_name: string
  team_abbrev: string | null
  team_short_name: string | null
  provider: string
  provider_team_id: string
  league: string
  sport: string
  logo_url: string | null
}

interface ImportedTeam {
  provider_team_id: string
  sport: string
  leagues: string[]
}

// Fetch teams for a league
async function fetchTeamsForLeague(league: string): Promise<CacheTeam[]> {
  const result = await api.get<CacheTeam[]>(`/cache/leagues/${league}/teams`)
  return Array.isArray(result) ? result : []
}

// Fetch already imported teams
async function fetchImportedTeams(): Promise<ImportedTeam[]> {
  const teams = await api.get<Array<{ provider_team_id: string; sport: string; leagues: string[] }>>("/teams")
  // Defensive: ensure we have a valid array
  if (!Array.isArray(teams)) {
    console.error("Invalid teams response:", teams)
    return []
  }
  return teams.map(t => ({ provider_team_id: t.provider_team_id, sport: t.sport, leagues: t.leagues }))
}

// Bulk import teams
async function bulkImportTeams(teams: CacheTeam[]): Promise<{ imported: number; updated: number; skipped: number }> {
  return api.post("/teams/bulk-import", { teams })
}

// Search teams across all leagues
interface SearchResult {
  name: string
  abbrev: string | null
  short_name: string | null
  provider: string
  team_id: string
  league: string
  sport: string
  logo_url: string | null
}

async function searchTeams(query: string, league?: string): Promise<CacheTeam[]> {
  const params = new URLSearchParams({ q: query })
  if (league) params.append("league", league)
  const result = await api.get<{ teams: SearchResult[] }>(`/cache/teams/search?${params}`)
  // Map to CacheTeam format
  return (result.teams || []).map((t) => ({
    id: 0,
    team_name: t.name,
    team_abbrev: t.abbrev,
    team_short_name: t.short_name,
    provider: t.provider,
    provider_team_id: t.team_id,
    league: t.league,
    sport: t.sport,
    logo_url: t.logo_url,
  }))
}

// Helper to get display name for league (use slug if name is null)
function getLeagueName(league: CachedLeague): string {
  return getLeagueDisplayName(league) || league.slug.toUpperCase()
}

// Theme-aware league logo component
function LeagueLogo({ league, className }: { league: CachedLeague; className?: string }) {
  const logoUrl = useThemedLogo(league.logo_url, league.logo_url_dark)
  if (!logoUrl) return null
  return <img src={logoUrl} alt="" className={className} />
}

// Group teams by provider:provider_team_id for consolidated display
interface GroupedTeam {
  team: CacheTeam
  leagues: string[]
  allImported: boolean
  someImported: boolean
}

function groupTeamsByProvider(teams: CacheTeam[], importedSet: Set<string>): GroupedTeam[] {
  const grouped = new Map<string, { team: CacheTeam; leagues: string[]; importedLeagues: string[] }>()

  for (const team of teams) {
    // Soccer teams play in multiple leagues (EPL + Champions League + FA Cup) - group by team ID
    // All other sports: include league to prevent grouping different programs
    // (e.g., Michigan Men's Basketball ≠ Michigan Women's Basketball even if they share school ID)
    const key = team.sport === "soccer"
      ? `${team.provider}:${team.sport}:${team.provider_team_id}`
      : `${team.provider}:${team.sport}:${team.provider_team_id}:${team.league}`
    // Check if this specific team+league combination is imported
    const isImported = importedSet.has(`${team.provider_team_id}:${team.sport}:${team.league}`)

    if (!grouped.has(key)) {
      grouped.set(key, {
        team,
        leagues: [team.league],
        importedLeagues: isImported ? [team.league] : []
      })
    } else {
      const entry = grouped.get(key)!
      entry.leagues.push(team.league)
      if (isImported) {
        entry.importedLeagues.push(team.league)
      }
    }
  }

  return Array.from(grouped.values()).map(({ team, leagues, importedLeagues }) => ({
    team,
    leagues,
    allImported: importedLeagues.length === leagues.length,
    someImported: importedLeagues.length > 0,
  }))
}

export function TeamImport() {
  const queryClient = useQueryClient()
  const [selectedLeague, setSelectedLeague] = useState<CachedLeague | null>(null)
  const [selectedTeamIds, setSelectedTeamIds] = useState<Set<string>>(new Set())
  const [expandedSports, setExpandedSports] = useState<Set<string>>(new Set())
  const [searchQuery, setSearchQuery] = useState("")
  const deferredSearchQuery = useDeferredValue(searchQuery)
  const [lastClickedIndex, setLastClickedIndex] = useState<number | null>(null)

  // Fetch leagues (import-enabled only)
  const leaguesQuery = useQuery({
    queryKey: ["cache-leagues"],
    queryFn: () => getLeagues(true).then(r => r.leagues),
  })

  // Fetch teams for selected league
  const teamsQuery = useQuery({
    queryKey: ["cache-league-teams", selectedLeague?.slug],
    queryFn: () => fetchTeamsForLeague(selectedLeague!.slug),
    enabled: !!selectedLeague,
  })

  // Search teams (when no league selected and query >= 2 chars)
  const searchTeamsQuery = useQuery({
    queryKey: ["search-teams", deferredSearchQuery],
    queryFn: () => searchTeams(deferredSearchQuery),
    enabled: !selectedLeague && deferredSearchQuery.length >= 2,
  })

  // Fetch imported teams
  const importedQuery = useQuery({
    queryKey: ["imported-teams"],
    queryFn: fetchImportedTeams,
  })

  // Import mutation
  const importMutation = useMutation({
    mutationFn: bulkImportTeams,
    onSuccess: (result) => {
      const parts = []
      if (result.imported > 0) parts.push(`${result.imported} imported`)
      if (result.updated > 0) parts.push(`${result.updated} updated`)
      if (result.skipped > 0) parts.push(`${result.skipped} skipped`)
      toast.success(parts.join(", ") || "No changes")
      setSelectedTeamIds(new Set())
      queryClient.invalidateQueries({ queryKey: ["imported-teams"] })
      queryClient.invalidateQueries({ queryKey: ["teams"] })
    },
    onError: (error) => {
      toast.error(`Import failed: ${error instanceof Error ? error.message : "Unknown error"}`)
    },
  })

  // Group leagues by sport - only include leagues with import_enabled flag
  const leaguesBySport = useMemo(() => {
    if (!leaguesQuery.data) return {}

    const grouped: Record<string, CachedLeague[]> = {}
    leaguesQuery.data.forEach((league) => {
      // Only show leagues with import_enabled flag
      if (!league.import_enabled) {
        return
      }

      const sport = league.sport || "Other"
      if (!grouped[sport]) grouped[sport] = []
      grouped[sport].push(league)
    })
    // Sort leagues within each sport - handle null names
    Object.values(grouped).forEach((leagues) => {
      leagues.sort((a, b) => getLeagueName(a).localeCompare(getLeagueName(b)))
    })
    return grouped
  }, [leaguesQuery.data])

  // Get set of imported team+league keys (provider_team_id:sport:league format)
  const importedSet = useMemo(() => {
    if (!importedQuery.data) return new Set<string>()
    const set = new Set<string>()
    for (const team of importedQuery.data) {
      for (const league of team.leagues) {
        set.add(`${team.provider_team_id}:${team.sport}:${league}`)
      }
    }
    return set
  }, [importedQuery.data])

  // Get the teams to display - either from league selection or search
  const displayTeams = useMemo(() => {
    if (selectedLeague) {
      // When a league is selected, use teams from that league
      let teams = teamsQuery.data || []
      // Apply search filter if there's a query
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase()
        teams = teams.filter(
          (t) =>
            t.team_name.toLowerCase().includes(q) ||
            t.team_abbrev?.toLowerCase() === q ||
            t.team_short_name?.toLowerCase().includes(q)
        )
      }
      return teams
    } else if (searchQuery.length >= 2) {
      // When no league selected but searching, use search results
      return searchTeamsQuery.data || []
    }
    return []
  }, [selectedLeague, teamsQuery.data, searchQuery, searchTeamsQuery.data])

  // Filter out already imported teams
  const availableTeams = useMemo(() => {
    return displayTeams.filter(
      (t) => !importedSet.has(`${t.provider_team_id}:${t.sport}:${t.league}`)
    )
  }, [displayTeams, importedSet])

  const importedTeamsInView = useMemo(() => {
    return displayTeams.filter(
      (t) => importedSet.has(`${t.provider_team_id}:${t.sport}:${t.league}`)
    )
  }, [displayTeams, importedSet])

  // Grouped teams for search results (when no league selected)
  const groupedTeams = useMemo(() => {
    if (selectedLeague) return [] // Don't group when viewing a specific league
    return groupTeamsByProvider(displayTeams, importedSet)
  }, [selectedLeague, displayTeams, importedSet])

  const toggleSport = (sport: string) => {
    setExpandedSports((prev) => {
      const next = new Set(prev)
      if (next.has(sport)) {
        next.delete(sport)
      } else {
        next.add(sport)
      }
      return next
    })
  }

  const selectLeague = (league: CachedLeague) => {
    setSelectedLeague(league)
    setSelectedTeamIds(new Set())
    setSearchQuery("")
  }

  // Selection key: only soccer teams play in multiple leagues (EPL + Champions League + FA Cup)
  // All other sports: use league to prevent grouping (Michigan football ≠ Michigan basketball)
  const getSelectionKey = (team: CacheTeam) => {
    if (team.sport === "soccer") {
      return `${team.provider_team_id}:${team.sport}`
    }
    return `${team.provider_team_id}:${team.league}`
  }

  const toggleTeam = (team: CacheTeam, index: number, shiftKey: boolean) => {
    const selectionKey = getSelectionKey(team)

    if (shiftKey && lastClickedIndex !== null) {
      // Shift-click: select range
      const start = Math.min(lastClickedIndex, index)
      const end = Math.max(lastClickedIndex, index)

      setSelectedTeamIds((prev) => {
        const next = new Set(prev)
        // Get the teams in the display order and select the range
        for (let i = start; i <= end; i++) {
          const t = displayTeams[i]
          if (t && !importedSet.has(`${t.provider_team_id}:${t.sport}:${t.league}`)) {
            next.add(getSelectionKey(t))
          }
        }
        return next
      })
    } else {
      // Normal click: toggle single item
      setSelectedTeamIds((prev) => {
        const next = new Set(prev)
        if (next.has(selectionKey)) {
          next.delete(selectionKey)
        } else {
          next.add(selectionKey)
        }
        return next
      })
    }

    setLastClickedIndex(index)
  }

  const selectAll = () => {
    setSelectedTeamIds(new Set(availableTeams.map(getSelectionKey)))
  }

  const selectNone = () => {
    setSelectedTeamIds(new Set())
  }

  const handleImport = () => {
    const teamsToImport = displayTeams.filter((t) =>
      selectedTeamIds.has(getSelectionKey(t))
    )
    if (teamsToImport.length === 0) return
    importMutation.mutate(teamsToImport)
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">Import Teams</h1>
        <p className="text-muted-foreground">Select teams from the cache to import</p>
      </div>
      <div className="flex h-[calc(100vh-14rem)] overflow-hidden border rounded-lg">
        {/* Left Sidebar - Leagues */}
        <div className="w-64 border-r bg-muted/30 overflow-y-auto flex-shrink-0">
          <div className="p-3 border-b">
            <h2 className="text-xs font-semibold uppercase text-muted-foreground">
              Leagues
            </h2>
          </div>

          {leaguesQuery.isLoading ? (
            <div className="flex items-center justify-center p-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : leaguesQuery.error ? (
            <div className="p-4 text-sm text-destructive">
              Failed to load leagues
            </div>
          ) : Object.keys(leaguesBySport).length === 0 ? (
            <div className="p-4 text-sm text-muted-foreground">
              <p className="mb-2">No leagues cached.</p>
              <p>Go to Settings → Cache to refresh the team/league cache.</p>
            </div>
          ) : (
            <div className="py-1">
              {Object.entries(leaguesBySport)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([sport, leagues]) => (
                  <div key={sport} className="border-b last:border-b-0">
                    <button
                      onClick={() => toggleSport(sport)}
                      className="w-full flex items-center gap-2 px-3 py-2 text-xs font-semibold uppercase text-muted-foreground hover:bg-muted/50"
                    >
                      <ChevronRight
                        className={cn(
                          "h-3 w-3 transition-transform",
                          expandedSports.has(sport) && "rotate-90"
                        )}
                      />
                      {sport}
                      <span className="ml-auto text-[10px] font-normal">
                        {leagues.length}
                      </span>
                    </button>

                    {expandedSports.has(sport) && (
                      <div className="pb-1">
                        {leagues.map((league) => (
                          <button
                            key={league.slug}
                            onClick={() => selectLeague(league)}
                            className={cn(
                              "w-full flex items-center gap-2 px-3 py-1.5 text-sm hover:bg-muted/50 border-l-2 border-transparent",
                              selectedLeague?.slug === league.slug &&
                                "bg-muted border-l-primary"
                            )}
                          >
                            <LeagueLogo league={league} className="h-5 w-5 object-contain" />
                            <span className="truncate flex-1 text-left">
                              {getLeagueName(league)}
                            </span>
                            <span className="text-xs text-muted-foreground">
                              {league.team_count}
                            </span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
            </div>
          )}
        </div>

        {/* Main Content */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {!selectedLeague ? (
            <div className="flex-1 flex flex-col overflow-hidden">
              {/* Search header when no league selected */}
              <div className="border-b p-4">
                <div className="relative max-w-md">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search teams across all leagues..."
                    className="pl-9 pr-9"
                  />
                  {searchQuery && (
                    <button
                      onClick={() => setSearchQuery("")}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  )}
                </div>
                {searchQuery.length > 0 && searchQuery.length < 2 && (
                  <p className="text-xs text-muted-foreground mt-2">
                    Type at least 2 characters to search
                  </p>
                )}
              </div>

              {/* Search results or empty state */}
              {searchQuery.length >= 2 ? (
                <div className="flex-1 overflow-y-auto p-4">
                  {searchTeamsQuery.isLoading ? (
                    <div className="flex items-center justify-center p-8">
                      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                    </div>
                  ) : groupedTeams.length === 0 ? (
                    <div className="text-center text-muted-foreground py-8">
                      No teams found matching "{searchQuery}"
                    </div>
                  ) : (
                    <>
                      <div className="flex items-center justify-between mb-4">
                        <p className="text-sm text-muted-foreground">
                          {groupedTeams.length} unique team{groupedTeams.length !== 1 && "s"} found
                          {groupedTeams.filter(g => g.allImported).length > 0 &&
                            ` • ${groupedTeams.filter(g => g.allImported).length} already imported`}
                        </p>
                        <div className="flex items-center gap-2">
                          <Button variant="outline" size="sm" onClick={selectAll}>
                            Select All
                          </Button>
                          <Button variant="outline" size="sm" onClick={selectNone}>
                            Deselect All
                          </Button>
                        </div>
                      </div>
                      <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-2">
                        {groupedTeams.map((grouped, index) => {
                          const { team, leagues, allImported, someImported } = grouped
                          const isSelected = selectedTeamIds.has(getSelectionKey(team))
                          return (
                            <div
                              key={`${team.provider}:${team.sport}:${team.provider_team_id}`}
                              onClick={(e) => !allImported && toggleTeam(team, index, e.shiftKey)}
                              className={cn(
                                "flex items-center gap-2 p-2 rounded-md border cursor-pointer transition-colors select-none",
                                allImported
                                  ? "opacity-50 cursor-not-allowed bg-muted/30"
                                  : isSelected
                                    ? "border-primary bg-primary/5"
                                    : "hover:border-primary/50 hover:bg-muted/30"
                              )}
                            >
                              <Checkbox
                                checked={isSelected}
                                disabled={allImported}
                                onCheckedChange={() => toggleTeam(team, index, false)}
                                onClick={(e) => e.stopPropagation()}
                              />
                              {team.logo_url ? (
                                <img
                                  src={team.logo_url}
                                  alt=""
                                  className="h-8 w-8 object-contain bg-white rounded p-0.5"
                                  onError={(e) => { e.currentTarget.style.display = "none" }}
                                />
                              ) : (
                                <div className="h-8 w-8" />
                              )}
                              <div className="flex-1 min-w-0">
                                <div className="text-sm font-medium truncate">{team.team_name}</div>
                                <div className="text-xs text-muted-foreground flex items-center gap-1 flex-wrap">
                                  <span className="uppercase">{leagues[0]}</span>
                                  {leagues.length > 1 && (
                                    <span className="inline-flex items-center text-[10px] bg-muted px-1.5 py-0.5 rounded" title={leagues.slice(1).join(', ')}>
                                      +{leagues.length - 1}
                                    </span>
                                  )}
                                  {allImported && (
                                    <span className="inline-flex items-center gap-0.5 text-[10px] bg-green-500/20 text-green-600 px-1 rounded">
                                      <Check className="h-2.5 w-2.5" />
                                      Imported
                                    </span>
                                  )}
                                  {someImported && !allImported && (
                                    <span className="inline-flex items-center gap-0.5 text-[10px] bg-yellow-500/20 text-yellow-600 px-1 rounded">
                                      Partial
                                    </span>
                                  )}
                                </div>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </>
                  )}
                </div>
              ) : (
                <div className="flex-1 flex items-center justify-center text-muted-foreground">
                  <div className="text-center">
                    <h3 className="text-lg font-medium mb-1">Select a league or search</h3>
                    <p className="text-sm">
                      Choose a league from the sidebar, or search for teams across all leagues
                    </p>
                  </div>
                </div>
              )}

              {/* Footer for search results - always visible */}
              {searchQuery.length >= 2 && (
                <div className={cn(
                  "border-t p-4 flex items-center justify-between",
                  selectedTeamIds.size > 0 ? "bg-muted/30" : "bg-muted/10"
                )}>
                  <span className={cn(
                    "text-sm font-medium",
                    selectedTeamIds.size === 0 && "text-muted-foreground"
                  )}>
                    {selectedTeamIds.size > 0
                      ? `${selectedTeamIds.size} team${selectedTeamIds.size !== 1 ? "s" : ""} selected`
                      : "No teams selected"}
                  </span>
                  <Button
                    onClick={handleImport}
                    disabled={importMutation.isPending || selectedTeamIds.size === 0}
                  >
                    {importMutation.isPending ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin mr-2" />
                        Importing...
                      </>
                    ) : (
                      <>Import Selected Teams</>
                    )}
                  </Button>
                </div>
              )}
            </div>
          ) : (
            <>
              {/* Header */}
              <div className="border-b p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <LeagueLogo league={selectedLeague} className="h-10 w-10 object-contain" />
                    <div>
                      <h1 className="text-xl font-bold">{getLeagueName(selectedLeague)}</h1>
                      <p className="text-sm text-muted-foreground">
                        {displayTeams.length} of {teamsQuery.data?.length ?? 0} teams
                        {importedTeamsInView.length > 0 &&
                          ` • ${importedTeamsInView.length} already imported`}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={selectAll}>
                      Select All
                    </Button>
                    <Button variant="outline" size="sm" onClick={selectNone}>
                      Deselect All
                    </Button>
                  </div>
                </div>
                <div className="relative max-w-sm">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Filter teams..."
                    className="pl-9 pr-9"
                  />
                  {searchQuery && (
                    <button
                      onClick={() => setSearchQuery("")}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  )}
                </div>
              </div>

              {/* Teams Grid */}
              <div className="flex-1 overflow-y-auto p-4">
                {teamsQuery.isLoading ? (
                  <div className="flex items-center justify-center p-8">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                  </div>
                ) : teamsQuery.error ? (
                  <div className="text-center text-destructive p-8">
                    Failed to load teams
                  </div>
                ) : !teamsQuery.data?.length ? (
                  <div className="flex-1 flex items-center justify-center text-muted-foreground">
                    <div className="text-center">
                      <h3 className="text-lg font-medium mb-1">No teams cached</h3>
                      <p className="text-sm">
                        The team cache is empty. Go to Settings → Cache to refresh.
                      </p>
                    </div>
                  </div>
                ) : displayTeams.length === 0 && searchQuery ? (
                  <div className="text-center text-muted-foreground py-8">
                    No teams found matching "{searchQuery}"
                  </div>
                ) : (
                  <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-2">
                    {displayTeams.map((team, index) => {
                      const isImported = importedSet.has(
                        `${team.provider_team_id}:${team.sport}:${team.league}`
                      )
                      const isSelected = selectedTeamIds.has(getSelectionKey(team))

                      return (
                        <div
                          key={`${team.provider_team_id}-${team.sport}-${team.league}`}
                          onClick={(e) =>
                            !isImported && toggleTeam(team, index, e.shiftKey)
                          }
                          className={cn(
                            "flex items-center gap-2 p-2 rounded-md border cursor-pointer transition-colors select-none",
                            isImported
                              ? "opacity-50 cursor-not-allowed bg-muted/30"
                              : isSelected
                                ? "border-primary bg-primary/5"
                                : "hover:border-primary/50 hover:bg-muted/30"
                          )}
                        >
                          <Checkbox
                            checked={isSelected}
                            disabled={isImported}
                            onCheckedChange={() => toggleTeam(team, index, false)}
                            onClick={(e) => e.stopPropagation()}
                          />
                          {team.logo_url ? (
                            <img
                              src={team.logo_url}
                              alt=""
                              className="h-8 w-8 object-contain bg-white rounded p-0.5"
                              onError={(e) => {
                                e.currentTarget.style.display = "none"
                              }}
                            />
                          ) : (
                            <div className="h-8 w-8" />
                          )}
                          <div className="flex-1 min-w-0">
                            <div className="text-sm font-medium truncate">
                              {team.team_name}
                            </div>
                            <div className="text-xs text-muted-foreground flex items-center gap-1">
                              {team.team_abbrev}
                              {isImported && (
                                <span className="inline-flex items-center gap-0.5 text-[10px] bg-green-500/20 text-green-600 px-1 rounded">
                                  <Check className="h-2.5 w-2.5" />
                                  Imported
                                </span>
                              )}
                            </div>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>

              {/* Footer - always visible when league selected */}
              <div className={cn(
                "border-t p-4 flex items-center justify-between",
                selectedTeamIds.size > 0 ? "bg-muted/30" : "bg-muted/10"
              )}>
                <span className={cn(
                  "text-sm font-medium",
                  selectedTeamIds.size === 0 && "text-muted-foreground"
                )}>
                  {selectedTeamIds.size > 0
                    ? `${selectedTeamIds.size} team${selectedTeamIds.size !== 1 ? "s" : ""} selected`
                    : "No teams selected"}
                </span>
                <Button
                  onClick={handleImport}
                  disabled={importMutation.isPending || selectedTeamIds.size === 0}
                >
                  {importMutation.isPending ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin mr-2" />
                      Importing...
                    </>
                  ) : (
                    <>Import Selected Teams</>
                  )}
                </Button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
