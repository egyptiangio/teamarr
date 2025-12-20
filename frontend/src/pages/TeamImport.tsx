import { useState, useMemo } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { cn } from "@/lib/utils"
import { toast } from "sonner"
import { ChevronRight, Loader2, Check } from "lucide-react"

// Types
interface League {
  slug: string
  name: string | null
  sport: string
  logo_url: string | null
  team_count: number
  provider: string
  import_enabled: boolean
}

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
  league: string
}

// Fetch leagues from cache (import-enabled only)
async function fetchLeagues(): Promise<League[]> {
  const data = await api.get<{ count: number; leagues: League[] }>("/cache/leagues?import_only=true")
  // Defensive: ensure we have a valid response with leagues array
  if (!data || !Array.isArray(data.leagues)) {
    console.error("Invalid leagues response:", data)
    return []
  }
  return data.leagues
}

// Fetch teams for a league
async function fetchTeamsForLeague(league: string): Promise<CacheTeam[]> {
  const result = await api.get<CacheTeam[]>(`/cache/leagues/${league}/teams`)
  return Array.isArray(result) ? result : []
}

// Fetch already imported teams
async function fetchImportedTeams(): Promise<ImportedTeam[]> {
  const teams = await api.get<Array<{ provider_team_id: string; league: string }>>("/teams")
  // Defensive: ensure we have a valid array
  if (!Array.isArray(teams)) {
    console.error("Invalid teams response:", teams)
    return []
  }
  return teams.map(t => ({ provider_team_id: t.provider_team_id, league: t.league }))
}

// Bulk import teams
async function bulkImportTeams(teams: CacheTeam[]): Promise<{ imported: number; skipped: number }> {
  return api.post("/teams/bulk-import", { teams })
}

// Helper to get display name for league (use slug if name is null)
function getLeagueName(league: League): string {
  return league.name || league.slug.toUpperCase()
}

export function TeamImport() {
  const queryClient = useQueryClient()
  const [selectedLeague, setSelectedLeague] = useState<League | null>(null)
  const [selectedTeamIds, setSelectedTeamIds] = useState<Set<string>>(new Set())
  const [expandedSports, setExpandedSports] = useState<Set<string>>(new Set())

  // Fetch leagues
  const leaguesQuery = useQuery({
    queryKey: ["cache-leagues"],
    queryFn: fetchLeagues,
  })

  // Fetch teams for selected league
  const teamsQuery = useQuery({
    queryKey: ["cache-league-teams", selectedLeague?.slug],
    queryFn: () => fetchTeamsForLeague(selectedLeague!.slug),
    enabled: !!selectedLeague,
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
      toast.success(`Imported ${result.imported} teams${result.skipped > 0 ? `, ${result.skipped} skipped` : ""}`)
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

    const grouped: Record<string, League[]> = {}
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

  // Get set of imported team keys
  const importedSet = useMemo(() => {
    if (!importedQuery.data) return new Set<string>()
    return new Set(importedQuery.data.map((t) => `${t.provider_team_id}:${t.league}`))
  }, [importedQuery.data])

  // Filter out already imported teams
  const availableTeams = useMemo(() => {
    if (!teamsQuery.data) return []
    return teamsQuery.data.filter(
      (t) => !importedSet.has(`${t.provider_team_id}:${t.league}`)
    )
  }, [teamsQuery.data, importedSet])

  const importedTeams = useMemo(() => {
    if (!teamsQuery.data) return []
    return teamsQuery.data.filter(
      (t) => importedSet.has(`${t.provider_team_id}:${t.league}`)
    )
  }, [teamsQuery.data, importedSet])

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

  const selectLeague = (league: League) => {
    setSelectedLeague(league)
    setSelectedTeamIds(new Set())
  }

  const toggleTeam = (teamId: string) => {
    setSelectedTeamIds((prev) => {
      const next = new Set(prev)
      if (next.has(teamId)) {
        next.delete(teamId)
      } else {
        next.add(teamId)
      }
      return next
    })
  }

  const selectAll = () => {
    setSelectedTeamIds(new Set(availableTeams.map((t) => t.provider_team_id)))
  }

  const selectNone = () => {
    setSelectedTeamIds(new Set())
  }

  const handleImport = () => {
    if (!teamsQuery.data) return
    const teamsToImport = teamsQuery.data.filter((t) =>
      selectedTeamIds.has(t.provider_team_id)
    )
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
                            {league.logo_url && (
                              <img
                                src={league.logo_url}
                                alt=""
                                className="h-5 w-5 object-contain"
                                onError={(e) => {
                                  e.currentTarget.style.display = "none"
                                }}
                              />
                            )}
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
            <div className="flex-1 flex items-center justify-center text-muted-foreground">
              <div className="text-center">
                <h3 className="text-lg font-medium mb-1">Select a league</h3>
                <p className="text-sm">
                  Choose a league from the sidebar to view and import teams
                </p>
              </div>
            </div>
          ) : (
            <>
              {/* Header */}
              <div className="border-b p-4 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  {selectedLeague.logo_url && (
                    <img
                      src={selectedLeague.logo_url}
                      alt=""
                      className="h-10 w-10 object-contain"
                    />
                  )}
                  <div>
                    <h1 className="text-xl font-bold">{getLeagueName(selectedLeague)}</h1>
                    <p className="text-sm text-muted-foreground">
                      {teamsQuery.data?.length ?? 0} teams
                      {importedTeams.length > 0 &&
                        ` • ${importedTeams.length} already imported`}
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
                ) : (
                  <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-2">
                    {teamsQuery.data?.map((team) => {
                      const isImported = importedSet.has(
                        `${team.provider_team_id}:${team.league}`
                      )
                      const isSelected = selectedTeamIds.has(team.provider_team_id)

                      return (
                        <div
                          key={`${team.provider_team_id}-${team.league}`}
                          onClick={() =>
                            !isImported && toggleTeam(team.provider_team_id)
                          }
                          className={cn(
                            "flex items-center gap-2 p-2 rounded-md border cursor-pointer transition-colors",
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
                            onCheckedChange={() => toggleTeam(team.provider_team_id)}
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

              {/* Footer */}
              {selectedTeamIds.size > 0 && (
                <div className="border-t p-4 flex items-center justify-between bg-muted/30">
                  <span className="text-sm font-medium">
                    {selectedTeamIds.size} team{selectedTeamIds.size !== 1 && "s"}{" "}
                    selected
                  </span>
                  <Button
                    onClick={handleImport}
                    disabled={importMutation.isPending}
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
            </>
          )}
        </div>
      </div>
    </div>
  )
}
