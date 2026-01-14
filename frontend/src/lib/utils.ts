import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Sport display names - handles special cases and formatting.
 * Used for consistent sport name formatting across the UI.
 */
const SPORT_DISPLAY_NAMES: Record<string, string> = {
  football: "Football (American)",
  soccer: "Soccer",
  basketball: "Basketball",
  hockey: "Hockey",
  baseball: "Baseball",
  softball: "Softball",
  mma: "MMA / Combat Sports",
  boxing: "Boxing",
  lacrosse: "Lacrosse",
  cricket: "Cricket",
  rugby_league: "Rugby League",
  rugby_union: "Rugby Union",
  volleyball: "Volleyball",
}

/**
 * Get display name for a sport.
 * Returns special-cased names or capitalizes the first letter.
 */
export function getSportDisplayName(sport: string): string {
  const lower = sport.toLowerCase()
  return SPORT_DISPLAY_NAMES[lower] ?? sport.charAt(0).toUpperCase() + sport.slice(1).replace(/_/g, ' ')
}

/**
 * Get display name for a league.
 * @param league - League object with name and optional league_alias
 * @param short - If true, prefer league_alias for short display (e.g., "EPL" instead of "English Premier League")
 * @returns Display name string
 */
export function getLeagueDisplayName(
  league: { name: string; league_alias?: string | null },
  short = false
): string {
  if (short && league.league_alias) {
    return league.league_alias
  }
  return league.name
}

/**
 * Get unique sports from leagues, normalized and sorted alphabetically.
 */
export function getUniqueSports(leagues: { sport: string | null }[]): string[] {
  const sportSet = new Set<string>()
  for (const league of leagues) {
    if (league.sport) {
      sportSet.add(getSportDisplayName(league.sport))
    }
  }
  return [...sportSet].sort()
}

/**
 * Sort leagues: import_enabled first (alphabetically), then rest (alphabetically).
 */
export function sortLeaguesImportFirst<T extends { name: string; import_enabled?: boolean }>(
  leagues: T[]
): T[] {
  return [...leagues].sort((a, b) => {
    const aEnabled = a.import_enabled ?? false
    const bEnabled = b.import_enabled ?? false

    if (aEnabled !== bEnabled) {
      return bEnabled ? 1 : -1
    }

    return a.name.localeCompare(b.name)
  })
}

/**
 * Filter leagues by sport (case-insensitive) and sort with import_enabled first.
 */
export function filterLeaguesBySport<T extends { sport: string | null; name: string; import_enabled?: boolean }>(
  leagues: T[],
  sport: string
): T[] {
  const normalizedSport = getSportDisplayName(sport)
  const filtered = leagues.filter(
    (l) => l.sport && getSportDisplayName(l.sport) === normalizedSport
  )
  return sortLeaguesImportFirst(filtered)
}
