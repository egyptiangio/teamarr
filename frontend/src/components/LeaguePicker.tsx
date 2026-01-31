import { useState, useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { X, Loader2, Check, ChevronRight, ChevronDown } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { cn, getSportDisplayName, getLeagueDisplayName } from "@/lib/utils"
import type { CachedLeague } from "@/api/teams"
import { getLeagues, getSports } from "@/api/teams"

const SOCCER_TOP_LEAGUE_SLUGS = new Set([
  'eng.1',
  'esp.1',
  'ger.1',
  'ita.1',
  'fra.1',
  'uefa.champions',
  'uefa.europa',
  'uefa.europa.conf',
]);

interface LeaguePickerProps {
  selectedLeagues: string[]
  onSelectionChange: (leagues: string[]) => void
  /** Single select mode - only one league can be selected */
  singleSelect?: boolean
  maxHeight?: string
  showSearch?: boolean
  showSelectedBadges?: boolean
  maxBadges?: number
}

export function LeaguePicker({
  selectedLeagues,
  onSelectionChange,
  singleSelect = false,
  maxHeight = "max-h-64",
  showSearch = true,
  showSelectedBadges = true,
  maxBadges = 10,
}: LeaguePickerProps) {
  const [search, setSearch] = useState("")
  const [expandedSports, setExpandedSports] = useState<Set<string>>(new Set())
  const [expandedSoccerOther, setExpandedSoccerOther] = useState(false)
  const { data: leaguesResponse, isLoading } = useQuery({
    queryKey: ["cached-leagues"],
    queryFn: () => getLeagues(),
  })
  const cachedLeagues = leaguesResponse?.leagues

  // Fetch sport display names from database (single source of truth)
  const { data: sportsResponse } = useQuery({
    queryKey: ["sports"],
    queryFn: getSports,
    staleTime: 1000 * 60 * 60, // 1 hour - sports rarely change
  })
  const sportsMap = sportsResponse?.sports

  // Convert to Set for easier operations
  const selectedSet = useMemo(() => new Set(selectedLeagues), [selectedLeagues])

  // Group leagues by sport (normalize to lowercase for consistent grouping)
  const leaguesBySport = useMemo(() => {
    if (!cachedLeagues) return {}
    const grouped: Record<string, CachedLeague[]> = {}
    for (const league of cachedLeagues) {
      const sport = (league.sport || "other").toLowerCase()
      if (!grouped[sport]) grouped[sport] = []
      grouped[sport].push(league)
    }
    // Sort leagues within each sport
    for (const sport of Object.keys(grouped)) {
      grouped[sport].sort((a, b) => a.name.localeCompare(b.name))
    }
    return grouped
  }, [cachedLeagues])

  const sports = Object.keys(leaguesBySport).sort()

  // Select a league (single or multi mode)
  const selectLeague = (slug: string) => {
    if (singleSelect) {
      // Single select: replace current selection
      onSelectionChange([slug])
    } else {
      // Multi select: toggle
      const next = new Set(selectedSet)
      if (next.has(slug)) {
        next.delete(slug)
      } else {
        next.add(slug)
      }
      onSelectionChange(Array.from(next))
    }
  }

  // Global select/clear all (multi-select only)
  const selectAllLeagues = () => {
    const allSlugs = cachedLeagues?.map(l => l.slug) || []
    onSelectionChange(allSlugs)
  }

  const clearAllLeagues = () => {
    onSelectionChange([])
  }

  // Per-sport select/clear (multi-select only)
  const selectAllInSport = (sport: string) => {
    const sportLeagues = leaguesBySport[sport] || []
    const next = new Set(selectedSet)
    for (const league of sportLeagues) {
      next.add(league.slug)
    }
    onSelectionChange(Array.from(next))
  }

  const clearAllInSport = (sport: string) => {
    const sportSlugs = new Set((leaguesBySport[sport] || []).map(l => l.slug))
    const next = new Set(selectedSet)
    for (const slug of sportSlugs) {
      next.delete(slug)
    }
    onSelectionChange(Array.from(next))
  }

  const setLeagueSelection = (slugs: string[], select: boolean) => {
    const next = new Set(selectedSet)
    for (const slug of slugs) {
      if (select) {
        next.add(slug)
      } else {
        next.delete(slug)
      }
    }
    onSelectionChange(Array.from(next))
  }

  // Check if all leagues in a sport are selected
  const isSportFullySelected = (sport: string) => {
    const sportLeagues = leaguesBySport[sport] || []
    return sportLeagues.length > 0 && sportLeagues.every(l => selectedSet.has(l.slug))
  }

  // Toggle entire sport (multi-select only)
  const toggleSport = (sport: string) => {
    if (isSportFullySelected(sport)) {
      clearAllInSport(sport)
    } else {
      selectAllInSport(sport)
    }
  }

  // Toggle sport expand/collapse
  const toggleExpanded = (sport: string) => {
    setExpandedSports(prev => {
      const next = new Set(prev)
      if (next.has(sport)) {
        next.delete(sport)
      } else {
        next.add(sport)
      }
      return next
    })
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {/* Search */}
      {showSearch && (
        <Input
          placeholder="Search leagues..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      )}

      {/* Selected count and global actions (multi-select only) */}
      {!singleSelect && (
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">
            {selectedSet.size} league{selectedSet.size !== 1 ? "s" : ""} selected
          </span>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="sm" onClick={selectAllLeagues}>
              Select All
            </Button>
            {selectedSet.size > 0 && (
              <Button variant="ghost" size="sm" onClick={clearAllLeagues}>
                Clear All
              </Button>
            )}
          </div>
        </div>
      )}

      {/* Selected badges (multi-select only, or single with showSelectedBadges) */}
      {showSelectedBadges && selectedSet.size > 0 && !singleSelect && (
        <div className="flex flex-wrap gap-1">
          {Array.from(selectedSet).slice(0, maxBadges).map(slug => {
            const league = cachedLeagues?.find(l => l.slug === slug)
            return (
              <Badge key={slug} variant="secondary" className="gap-1">
                {league?.logo_url && (
                  <img src={league.logo_url} alt="" className="h-3 w-3 object-contain" />
                )}
                {league ? getLeagueDisplayName(league, true) : slug}
                <button onClick={() => selectLeague(slug)} className="ml-1 hover:bg-muted rounded">
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            )
          })}
          {selectedSet.size > maxBadges && (
            <Badge variant="outline">+{selectedSet.size - maxBadges} more</Badge>
          )}
        </div>
      )}

      {/* League picker by sport */}
      <div className={cn("overflow-y-auto border rounded-md divide-y", maxHeight)}>
        {sports
          .filter((sport) =>
            !search ||
            sport.toLowerCase().includes(search.toLowerCase()) ||
            leaguesBySport[sport].some(l =>
              l.slug.toLowerCase().includes(search.toLowerCase()) ||
              l.name.toLowerCase().includes(search.toLowerCase())
            )
          )
          .map((sport) => {
            const leagues = leaguesBySport[sport]
            const filteredLeagues = search
              ? leagues.filter(l =>
                  l.slug.toLowerCase().includes(search.toLowerCase()) ||
                  l.name.toLowerCase().includes(search.toLowerCase())
                )
              : leagues

            // For search, if no individual leagues match but sport name matches, show all
            const displayLeagues = search && filteredLeagues.length === 0 &&
              sport.toLowerCase().includes(search.toLowerCase())
              ? leagues
              : filteredLeagues

            if (displayLeagues.length === 0) return null

            const allSelected = isSportFullySelected(sport)
            const selectedCount = displayLeagues.filter(l => selectedSet.has(l.slug)).length

            const isSoccer = sport.toLowerCase() === 'soccer'
            if (!singleSelect && isSoccer && !search) {
              const soccerLeagues = leaguesBySport[sport] || []
              const topSoccerLeagues = soccerLeagues.filter(l => SOCCER_TOP_LEAGUE_SLUGS.has(l.slug))
              const otherSoccerLeagues = soccerLeagues.filter(l => !SOCCER_TOP_LEAGUE_SLUGS.has(l.slug))
              const topSelectedCount = topSoccerLeagues.filter(l => selectedSet.has(l.slug)).length
              const otherSelectedCount = otherSoccerLeagues.filter(l => selectedSet.has(l.slug)).length
              const allTopSelected = topSoccerLeagues.length > 0 && topSelectedCount === topSoccerLeagues.length
              const allOtherSelected = otherSoccerLeagues.length > 0 && otherSelectedCount === otherSoccerLeagues.length
              const otherSlugs = otherSoccerLeagues.map(l => l.slug)

              return (
                <div key={sport}>
                  <div className="flex items-center justify-between px-3 py-2 bg-muted/50 sticky top-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm">
                        {getSportDisplayName(sport, sportsMap)} ({soccerLeagues.length})
                      </span>
                      {selectedCount > 0 && (
                        <Badge variant="secondary" className="text-xs h-5">
                          {selectedCount} selected
                        </Badge>
                      )}
                    </div>
                  </div>
                  <div className="space-y-2 p-2">
                    <label
                      className={cn(
                        "flex items-center gap-3 px-2 py-2 rounded cursor-pointer hover:bg-accent",
                        allSelected && "bg-primary/10"
                      )}
                    >
                      <Checkbox
                        checked={allSelected}
                        onCheckedChange={() => toggleSport(sport)}
                      />
                      <div className="flex-1">
                        <div className="font-medium text-sm">All Soccer Leagues</div>
                        <div className="text-xs text-muted-foreground">
                          Select or clear every soccer league in this group
                        </div>
                      </div>
                    </label>
                    <label
                      className={cn(
                        "flex items-center gap-3 px-2 py-2 rounded cursor-pointer hover:bg-accent",
                        allTopSelected && "bg-primary/10"
                      )}
                    >
                      <Checkbox
                        checked={allTopSelected}
                        onCheckedChange={() =>
                          setLeagueSelection(
                            topSoccerLeagues.map(l => l.slug),
                            !allTopSelected
                          )
                        }
                      />
                      <div className="flex-1">
                        <div className="font-medium text-sm">Top Domestic + Champions Leagues</div>
                        <div className="text-xs text-muted-foreground">
                          EPL, La Liga, Bundesliga, Serie A, Ligue 1, UCL, UEL, UECL
                        </div>
                      </div>
                    </label>
                    <div className="rounded border border-border/50">
                      <div className="flex items-center justify-between px-2 py-2">
                        <label
                          className={cn(
                            "flex items-center gap-3 cursor-pointer",
                            allOtherSelected && "text-primary"
                          )}
                        >
                          <Checkbox
                            checked={allOtherSelected}
                            onCheckedChange={() => setLeagueSelection(otherSlugs, !allOtherSelected)}
                          />
                          <div className="flex-1">
                            <div className="font-medium text-sm">Other Soccer Leagues</div>
                            <div className="text-xs text-muted-foreground">
                              {otherSoccerLeagues.length} leagues (expand for individual picks)
                            </div>
                          </div>
                        </label>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 p-0"
                          onClick={() => setExpandedSoccerOther((prev) => !prev)}
                        >
                          {expandedSoccerOther ? (
                            <ChevronDown className="h-4 w-4 text-muted-foreground" />
                          ) : (
                            <ChevronRight className="h-4 w-4 text-muted-foreground" />
                          )}
                        </Button>
                      </div>
                      {expandedSoccerOther && (
                        <div className="grid grid-cols-2 md:grid-cols-3 gap-1 p-2 pt-0">
                          {otherSoccerLeagues.map((league) => {
                            const isSelected = selectedSet.has(league.slug)
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
                                  onCheckedChange={() => selectLeague(league.slug)}
                                />
                                {league.logo_url && (
                                  <img src={league.logo_url} alt="" className="h-4 w-4 object-contain" />
                                )}
                                <span className="truncate">{getLeagueDisplayName(league, true)}</span>
                              </label>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )
            }

            // Render leagues for this sport
            const isExpanded = expandedSports.has(sport) || !!search

            return (
              <div key={sport}>
                <div
                  className="flex items-center justify-between px-3 py-2 bg-muted/50 sticky top-0 cursor-pointer hover:bg-muted/70"
                  onClick={() => toggleExpanded(sport)}
                >
                  <div className="flex items-center gap-2">
                    {isExpanded ? (
                      <ChevronDown className="h-4 w-4 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="h-4 w-4 text-muted-foreground" />
                    )}
                    <span className="font-medium text-sm">
                      {getSportDisplayName(sport, sportsMap)} ({displayLeagues.length})
                    </span>
                    {selectedCount > 0 && !isExpanded && (
                      <Badge variant="secondary" className="text-xs h-5">
                        {selectedCount} selected
                      </Badge>
                    )}
                  </div>
                  {/* Select All button only in multi-select mode */}
                  {!singleSelect && isExpanded && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 text-xs"
                      onClick={(e) => {
                        e.stopPropagation()
                        if (allSelected) {
                          clearAllInSport(sport)
                        } else {
                          selectAllInSport(sport)
                        }
                      }}
                    >
                      {allSelected ? "Clear" : "Select All"}
                    </Button>
                  )}
                </div>
                {isExpanded && (
                  <div className={cn(
                    "gap-1 p-2",
                    singleSelect ? "space-y-0.5" : "grid grid-cols-2 md:grid-cols-3"
                  )}>
                    {displayLeagues.map(league => {
                      const isSelected = selectedSet.has(league.slug)
                      return (
                        <label
                          key={league.slug}
                          className={cn(
                            "flex items-center gap-2 px-2 py-1.5 rounded text-sm cursor-pointer hover:bg-accent",
                            isSelected && "bg-primary/10"
                          )}
                        >
                          {singleSelect ? (
                            // Single select: custom checkmark icon (no checkbox)
                            <button
                              type="button"
                              className="w-4 h-4 flex items-center justify-center"
                              onClick={() => selectLeague(league.slug)}
                            >
                              {isSelected && <Check className="h-4 w-4 text-primary" />}
                            </button>
                          ) : (
                            // Multi select: standard checkbox
                            <Checkbox
                              checked={isSelected}
                              onCheckedChange={() => selectLeague(league.slug)}
                            />
                          )}
                          {league.logo_url && (
                            <img src={league.logo_url} alt="" className="h-4 w-4 object-contain" />
                          )}
                          <span className="truncate">{getLeagueDisplayName(league, true)}</span>
                        </label>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
      </div>
    </div>
  )
}
