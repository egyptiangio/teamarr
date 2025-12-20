"""Fuzzy string matching for team names.

Uses rapidfuzz for fast, maintenance-free fuzzy matching.
Provides pattern generation and matching utilities for the codebase.
"""

from dataclasses import dataclass

from rapidfuzz import fuzz

from teamarr.core import Team

# Common abbreviations to expand for better matching
# Key: abbreviation (lowercase), Value: expansion
ABBREVIATIONS = {
    # UFC/MMA
    "fn": "fight night",
    "ufc fn": "ufc fight night",
    "ppv": "pay per view",
    # Sports generic
    "vs": "versus",
    "v": "versus",
}

# Common mascot/suffix words to strip for partial matching
MASCOT_WORDS = {
    # Generic
    "team",
    "club",
    "fc",
    "sc",
    "cf",
    "united",
    "city",
    # Animals
    "eagles",
    "owls",
    "lions",
    "tigers",
    "bears",
    "wolves",
    "hawks",
    "falcons",
    "panthers",
    "jaguars",
    "bengals",
    "colts",
    "broncos",
    "chargers",
    "raiders",
    "ravens",
    "cardinals",
    "seahawks",
    "dolphins",
    "bills",
    "jets",
    "giants",
    "patriots",
    "steelers",
    "browns",
    "packers",
    "vikings",
    "saints",
    "buccaneers",
    "cowboys",
    "commanders",
    "49ers",
    "rams",
    "chiefs",
    "texans",
    "titans",
    "cavaliers",
    "celtics",
    "bulls",
    "pistons",
    "pacers",
    "heat",
    "magic",
    "hornets",
    "wizards",
    "knicks",
    "nets",
    "76ers",
    "sixers",
    "raptors",
    "bucks",
    "timberwolves",
    "thunder",
    "blazers",
    "warriors",
    "kings",
    "lakers",
    "clippers",
    "suns",
    "nuggets",
    "jazz",
    "grizzlies",
    "pelicans",
    "spurs",
    "mavericks",
    "rockets",
    "bruins",
    "canadiens",
    "red wings",
    "blackhawks",
    "blues",
    "avalanche",
    "stars",
    "wild",
    "predators",
    "hurricanes",
    "lightning",
    "rangers",
    "islanders",
    "devils",
    "flyers",
    "penguins",
    "capitals",
    "blue jackets",
    "senators",
    "maple leafs",
    "sabres",
    "kraken",
    "golden knights",
    "flames",
    "oilers",
    "canucks",
    "sharks",
    "ducks",
    "coyotes",  # College
    "bulldogs",
    "wildcats",
    "huskies",
    "cougars",
    "badgers",
    "gophers",
    "wolverines",
    "buckeyes",
    "spartans",
    "hoosiers",
    "boilermakers",
    "hawkeyes",
    "cornhuskers",
    "cyclones",
    "jayhawks",
    "sooners",
    "longhorns",
    "aggies",
    "razorbacks",
    "volunteers",
    "commodores",
    "crimson tide",
    "gators",
    "seminoles",
    "yellow jackets",
    "tar heels",
    "wolfpack",
    "hokies",
    "terrapins",
    "nittany lions",
    "orange",
    "mountaineers",
    "red raiders",
    "horned frogs",
    "mustangs",
    "golden eagles",
    "blue devils",
    "demon deacons",
    "fighting irish",
    "trojans",
    "beavers",
    "sun devils",
    "buffaloes",
    "utes",
    "rebels",
    "aztecs",
    "rainbow warriors",
    "retrievers",
    "black knights",
    "musketeers",
    "beacons",
    "lancers",
    "governors",
    "skyhawks",
    "tornados",
    "runnin' bulldogs",
    # Soccer
    "rovers",
    "wanderers",
    "albion",
    "athletic",
    "sporting",
    "real",
    "dynamo",
    "racing",
    "deportivo",
    "atletico",
    "inter",
    "ac",
    "as",
    "ss",
    "us",
    # Misc
    "mammoth",
    "roar",
    "glory",
    "phoenix",
    "rush",
    "black bears",
}


@dataclass
class FuzzyMatchResult:
    """Result of a fuzzy match."""

    matched: bool
    score: float
    pattern_used: str | None = None


class FuzzyMatcher:
    """Fuzzy string matcher for team/event names.

    Uses rapidfuzz for fast matching with configurable thresholds.
    """

    def __init__(
        self,
        threshold: float = 85.0,
        partial_threshold: float = 90.0,
    ):
        """Initialize matcher.

        Args:
            threshold: Minimum score for full string match (0-100)
            partial_threshold: Minimum score for partial/token match (0-100)
        """
        self.threshold = threshold
        self.partial_threshold = partial_threshold

    def generate_team_patterns(self, team: Team) -> list[str]:
        """Generate all searchable patterns for a team.

        Returns patterns in priority order (most specific first).
        """
        patterns = []
        seen = set()

        def add(value: str | None) -> None:
            if value:
                lower = value.lower().strip()
                if lower and lower not in seen and len(lower) >= 2:
                    seen.add(lower)
                    patterns.append(lower)

        # Full name: "Florida Atlantic Owls"
        add(team.name)

        # Name without mascot: "Florida Atlantic"
        if team.name:
            stripped = self._strip_mascot(team.name)
            add(stripped)

        # Short name: "FAU" or "Fla Atlantic"
        add(team.short_name)

        # Abbreviation: "FAU"
        add(team.abbreviation)

        return patterns

    def _strip_mascot(self, name: str) -> str:
        """Strip common mascot/suffix words from team name.

        "Florida Atlantic Owls" -> "Florida Atlantic"
        "Chicago Blackhawks" -> "Chicago"
        """
        words = name.lower().split()
        result = []

        for word in words:
            # Clean punctuation for comparison
            clean_word = word.strip("'\".,")
            if clean_word not in MASCOT_WORDS:
                result.append(word)

        # Return original case if possible
        if result:
            # Reconstruct with original words (preserving original case)
            original_words = name.split()
            kept = []
            for orig in original_words:
                if orig.lower().strip("'\".,") not in MASCOT_WORDS:
                    kept.append(orig)
            return " ".join(kept) if kept else name

        return name

    def _expand_abbreviations(self, text: str) -> str:
        """Expand known abbreviations in text for better matching.

        E.g., "UFC FN Prelims" -> "UFC Fight Night Prelims"
        """
        import re

        result = text.lower()

        # Sort by length descending to match longer abbreviations first
        # (e.g., "ufc fn" before "fn")
        for abbrev in sorted(ABBREVIATIONS.keys(), key=len, reverse=True):
            expansion = ABBREVIATIONS[abbrev]
            # Use word boundaries to avoid partial matches
            pattern = r"\b" + re.escape(abbrev) + r"\b"
            result = re.sub(pattern, expansion, result, flags=re.IGNORECASE)

        return result

    def matches_any(
        self,
        patterns: list[str],
        text: str,
    ) -> FuzzyMatchResult:
        """Check if any pattern matches within text.

        Uses multiple strategies:
        1. Exact substring match (fastest)
        2. Token set ratio (handles word order, extra words)
        3. Partial ratio (handles substrings)

        Abbreviations are expanded before matching (e.g., "FN" -> "Fight Night").

        Args:
            patterns: List of patterns to search for
            text: Text to search within

        Returns:
            FuzzyMatchResult with match status and score
        """
        # Expand abbreviations before matching
        text_lower = self._expand_abbreviations(text)

        # Strategy 1: Exact substring match (fastest)
        for pattern in patterns:
            if pattern in text_lower:
                return FuzzyMatchResult(matched=True, score=100.0, pattern_used=pattern)

        # Strategy 2: Token set ratio (handles word order, extra words)
        # Good for "Atlanta Falcons" matching "Falcons @ Atlanta"
        for pattern in patterns:
            score = fuzz.token_set_ratio(pattern, text_lower)
            if score >= self.partial_threshold:
                return FuzzyMatchResult(matched=True, score=score, pattern_used=pattern)

        # Strategy 3: Partial ratio (handles substrings)
        # Good for "Florida Atlantic" matching "Florida Atlantic Owls"
        for pattern in patterns:
            score = fuzz.partial_ratio(pattern, text_lower)
            if score >= self.partial_threshold:
                return FuzzyMatchResult(matched=True, score=score, pattern_used=pattern)

        return FuzzyMatchResult(matched=False, score=0.0)

    def best_match(
        self,
        pattern: str,
        candidates: list[str],
    ) -> tuple[str | None, float]:
        """Find the best matching candidate for a pattern.

        Args:
            pattern: Pattern to match
            candidates: List of candidate strings

        Returns:
            Tuple of (best_match, score) or (None, 0) if no match
        """
        best_candidate = None
        best_score = 0.0

        pattern_lower = pattern.lower()

        for candidate in candidates:
            candidate_lower = candidate.lower()

            # Try different scoring methods, take the best
            scores = [
                fuzz.ratio(pattern_lower, candidate_lower),
                fuzz.token_set_ratio(pattern_lower, candidate_lower),
                fuzz.partial_ratio(pattern_lower, candidate_lower),
            ]
            score = max(scores)

            if score > best_score:
                best_score = score
                best_candidate = candidate

        if best_score >= self.threshold:
            return best_candidate, best_score

        return None, 0.0


# Default singleton for convenience
_default_matcher: FuzzyMatcher | None = None


def get_matcher() -> FuzzyMatcher:
    """Get the default FuzzyMatcher instance."""
    global _default_matcher
    if _default_matcher is None:
        _default_matcher = FuzzyMatcher()
    return _default_matcher
