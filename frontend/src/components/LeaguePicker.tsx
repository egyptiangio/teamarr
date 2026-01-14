import { useState, useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { X, Loader2, Check, ChevronRight, ChevronDown } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { cn, getSportDisplayName, getLeagueDisplayName } from "@/lib/utils"
import type { CachedLeague } from "@/api/teams"
import { getLeagues } from "@/api/teams"

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
  const { data: leaguesResponse, isLoading } = useQuery({
    queryKey: ["cached-leagues"],
    queryFn: () => getLeagues(),
  })
  const cachedLeagues = leaguesResponse?.leagues

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

  // Check if all leagues in a sport are selected
  const isSportFullySelected = (sport: string) => {
    const sportLeagues = leaguesBySport[sport] || []
    return sportLeagues.length > 0 && sportLeagues.every(l => selectedSet.has(l.slug))
  }

  // Check if some (but not all) leagues in a sport are selected
  const isSportPartiallySelected = (sport: string) => {
    const sportLeagues = leaguesBySport[sport] || []
    const selected = sportLeagues.filter(l => selectedSet.has(l.slug))
    return selected.length > 0 && selected.length < sportLeagues.length
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
            const someSelected = isSportPartiallySelected(sport)

            // Soccer in multi-select mode: show as single consolidated checkbox (too many leagues)
            if (!singleSelect && sport.toLowerCase() === "soccer") {
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
                      {getSportDisplayName(sport)}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      All {leagues.length} leagues (EPL, La Liga, Bundesliga, Serie A, Ligue 1, MLS, Champions League, etc.)
                    </div>
                  </div>
                </label>
              )
            }

            // Render leagues for this sport
            const isExpanded = expandedSports.has(sport) || !!search
            const selectedCount = displayLeagues.filter(l => selectedSet.has(l.slug)).length

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
                      {getSportDisplayName(sport)} ({displayLeagues.length})
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
                        allSelected ? clearAllInSport(sport) : selectAllInSport(sport)
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
                        <div
                          key={league.slug}
                          className={cn(
                            "flex items-center gap-2 px-2 py-1.5 rounded text-sm cursor-pointer hover:bg-accent",
                            isSelected && "bg-primary/10"
                          )}
                          onClick={() => selectLeague(league.slug)}
                        >
                          {singleSelect ? (
                            // Single select: show checkmark for selected
                            <div className="w-4 h-4 flex items-center justify-center">
                              {isSelected && <Check className="h-4 w-4 text-primary" />}
                            </div>
                          ) : (
                            // Multi select: show checkbox
                            <Checkbox
                              checked={isSelected}
                              onCheckedChange={() => selectLeague(league.slug)}
                            />
                          )}
                          {league.logo_url && (
                            <img src={league.logo_url} alt="" className="h-4 w-4 object-contain" />
                          )}
                          <span className="truncate">{getLeagueDisplayName(league, true)}</span>
                        </div>
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
