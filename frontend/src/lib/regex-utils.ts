/**
 * Regex utilities for the test patterns modal.
 *
 * Handles validation, Python↔JS conversion, matching, and
 * range extraction for stream name highlighting.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RegexValidation {
  valid: boolean
  jsPattern: string | null
  error: string | null
}

export interface MatchRange {
  start: number
  end: number
  group?: string // named group label (e.g., "team1", "date")
}

// ---------------------------------------------------------------------------
// Python ↔ JS regex conversion
// ---------------------------------------------------------------------------

/**
 * Convert Python regex syntax to JavaScript.
 * Handles: (?P<name>...) → (?<name>...) and (?P=name) → \k<name>
 */
export function pythonToJs(pattern: string): string {
  return pattern
    .replace(/\(\?P<(\w+)>/g, "(?<$1>")
    .replace(/\(\?P=(\w+)\)/g, "\\k<$1>")
}

/**
 * Convert JavaScript regex syntax back to Python.
 * Handles: (?<name>...) → (?P<name>...) and \k<name> → (?P=name)
 */
export function jsToPython(pattern: string): string {
  return pattern
    .replace(/\(\?<(\w+)>/g, "(?P<$1>")
    .replace(/\\k<(\w+)>/g, "(?P=$1)")
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

/**
 * Validate a regex pattern (accepts Python syntax).
 * Returns the JS-converted pattern if valid.
 */
export function validateRegex(pattern: string): RegexValidation {
  if (!pattern.trim()) {
    return { valid: false, jsPattern: null, error: "Empty pattern" }
  }
  const jsPattern = pythonToJs(pattern)
  try {
    new RegExp(jsPattern, "i")
    return { valid: true, jsPattern, error: null }
  } catch (e) {
    return {
      valid: false,
      jsPattern: null,
      error: e instanceof Error ? e.message : "Invalid regex",
    }
  }
}

// ---------------------------------------------------------------------------
// Matching
// ---------------------------------------------------------------------------

/**
 * Test whether a pattern matches a string. Returns true/false.
 * Accepts Python syntax.
 */
export function testMatch(pattern: string, text: string): boolean {
  const v = validateRegex(pattern)
  if (!v.valid || !v.jsPattern) return false
  try {
    return new RegExp(v.jsPattern, "i").test(text)
  } catch {
    return false
  }
}

/**
 * Get all non-overlapping match ranges for a pattern against text.
 * Used for highlighting matched spans in stream names.
 *
 * If the regex has named groups, each range is tagged with the group name.
 * Otherwise the full match range is returned untagged.
 */
export function getMatchRanges(pattern: string, text: string): MatchRange[] {
  const v = validateRegex(pattern)
  if (!v.valid || !v.jsPattern) return []

  try {
    const re = new RegExp(v.jsPattern, "ig")
    const ranges: MatchRange[] = []
    let m: RegExpExecArray | null

    while ((m = re.exec(text)) !== null) {
      if (m[0].length === 0) {
        re.lastIndex++
        continue
      }

      // If we have named groups, emit one range per group
      if (m.groups) {
        for (const [name, value] of Object.entries(m.groups)) {
          if (value == null) continue
          const groupStart = text.indexOf(value, m.index)
          if (groupStart >= 0) {
            ranges.push({
              start: groupStart,
              end: groupStart + value.length,
              group: name,
            })
          }
        }
      } else {
        // Fall back to full match
        ranges.push({ start: m.index, end: m.index + m[0].length })
      }
    }

    return ranges
  } catch {
    return []
  }
}

/**
 * Extract named group values from a match.
 * Returns a map of group name → captured value.
 */
export function extractGroups(
  pattern: string,
  text: string
): Record<string, string> {
  const v = validateRegex(pattern)
  if (!v.valid || !v.jsPattern) return {}

  try {
    const m = new RegExp(v.jsPattern, "i").exec(text)
    if (!m?.groups) return {}

    const result: Record<string, string> = {}
    for (const [name, value] of Object.entries(m.groups)) {
      if (value != null) result[name] = value
    }
    return result
  } catch {
    return {}
  }
}
