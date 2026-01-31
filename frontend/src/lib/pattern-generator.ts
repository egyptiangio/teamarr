/**
 * Pattern generator for the interactive selection feature.
 *
 * When a user selects text in a stream name and labels it (team1, date, etc.),
 * this module generates a regex pattern that captures that text across streams.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TextSelection {
  text: string
  field: "team1" | "team2" | "date" | "time" | "league"
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Escape special regex characters in a literal string. */
export function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

/**
 * Attempt to generalize a literal selection into a broader pattern.
 *
 * For example, if the user selects "Arsenal" as team1, we want to match
 * any team name in that position — not just "Arsenal". This analyzes the
 * surrounding context to pick an appropriate capture pattern.
 */
function generalizeForField(
  field: TextSelection["field"],
  text: string
): string {
  switch (field) {
    case "team1":
    case "team2":
      // Team names: word characters, spaces, dots, hyphens, apostrophes
      return "([\\w][\\w .'-]+[\\w.])"

    case "date":
      // Date: digits, slashes, dashes, spaces, month names
      if (/\d{4}-\d{2}-\d{2}/.test(text)) {
        return "(\\d{4}-\\d{2}-\\d{2})"
      }
      if (/\d{1,2}\/\d{1,2}/.test(text)) {
        return "(\\d{1,2}/\\d{1,2}(?:/\\d{2,4})?)"
      }
      // Generic date-like
      return "([\\d/\\-.]+)"

    case "time":
      // Time: HH:MM with optional seconds, AM/PM
      if (/\d{1,2}:\d{2}:\d{2}/.test(text)) {
        return "(\\d{1,2}:\\d{2}:\\d{2})"
      }
      if (/\d{1,2}:\d{2}\s*[AaPp][Mm]/.test(text)) {
        return "(\\d{1,2}:\\d{2}\\s*[AaPp][Mm])"
      }
      return "(\\d{1,2}:\\d{2}(?::\\d{2})?\\s*(?:[AaPp][Mm])?)"

    case "league":
      // League codes tend to be short uppercase or known names
      if (/^[A-Z]{2,6}$/.test(text)) {
        return "([A-Z]{2,6})"
      }
      // Multi-word league name — capture word characters and spaces
      return "([\\w][\\w ]+[\\w])"
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Generate a regex pattern from a user's text selection in a stream name.
 *
 * @param selection - What the user selected and labeled
 * @param streamName - The full stream name the selection came from
 * @returns A Python-syntax regex string with named group, or null if generation fails
 */
export function generatePattern(
  selection: TextSelection,
  streamName: string
): string | null {
  const { text, field } = selection
  if (!text || !streamName.includes(text)) return null

  const idx = streamName.indexOf(text)
  const before = streamName.slice(0, idx)
  const after = streamName.slice(idx + text.length)

  // Build an anchor from the immediate surrounding context
  const captureGroup = generalizeForField(field, text)
  const namedGroup = `(?P<${field}>${captureGroup.slice(1, -1)})`

  // Find a stable anchor before the selection
  // Look for the nearest separator or keyword before the text
  let anchorBefore = ""
  const beforeTrimmed = before.trimEnd()
  if (beforeTrimmed.length > 0) {
    // Use the last few non-space characters as anchor (e.g., "vs.", ":", "|", "@")
    const separatorMatch = beforeTrimmed.match(
      /(?:vs\.?|v\.?|@|at|\||:|-|–|—)\s*$/i
    )
    if (separatorMatch) {
      anchorBefore = escapeRegex(separatorMatch[0])
    }
  }

  // Find a stable anchor after the selection
  let anchorAfter = ""
  const afterTrimmed = after.trimStart()
  if (afterTrimmed.length > 0) {
    const separatorMatch = afterTrimmed.match(
      /^\s*(?:vs\.?|v\.?|@|at|\||:|-|–|—|\()/i
    )
    if (separatorMatch) {
      anchorAfter = escapeRegex(separatorMatch[0])
    }
  }

  // Assemble: anchor + whitespace + named capture + whitespace + anchor
  let pattern = ""
  if (anchorBefore) {
    pattern += anchorBefore + "\\s*"
  }
  pattern += namedGroup
  if (anchorAfter) {
    pattern += "\\s*" + anchorAfter
  }

  return pattern
}

/**
 * Build a combined teams regex from two separate selections.
 * Produces: (?P<team1>...) separator (?P<team2>...)
 */
export function generateTeamsPattern(
  team1Text: string,
  team2Text: string,
  streamName: string
): string | null {
  if (!team1Text || !team2Text) return null

  const idx1 = streamName.indexOf(team1Text)
  const idx2 = streamName.indexOf(team2Text)
  if (idx1 < 0 || idx2 < 0 || idx1 >= idx2) return null

  // Find what separates team1 and team2
  const between = streamName.slice(idx1 + team1Text.length, idx2)
  const sepMatch = between.match(/^\s*(vs\.?|v\.?|@|at|-|–|—)\s*$/i)
  const separator = sepMatch
    ? "\\s*" + escapeRegex(sepMatch[1]) + "\\s*"
    : "\\s+(?:vs\\.?|v\\.?|@|at)\\s+"

  const team1Group = "(?P<team1>[\\w][\\w .'-]+[\\w.])"
  const team2Group = "(?P<team2>[\\w][\\w .'-]+[\\w.])"

  return team1Group + separator + team2Group
}
