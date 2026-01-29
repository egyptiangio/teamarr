"""Matching constants for stream-to-event matching.

Contains hardcoded translations, aliases, and patterns that can't be solved
through fuzzy matching alone. Keep this minimal - prefer user-defined aliases
in the database when possible.
"""

# =============================================================================
# CITY NAME TRANSLATIONS
# ESPN uses English city names, but stream names may use native spellings.
# rapidfuzz won't match "München" to "Munich" - need explicit translations.
#
# Format: normalized_variant -> english_name
# All keys should be lowercase, already normalized (no accents via unidecode)
# =============================================================================

CITY_TRANSLATIONS: dict[str, str] = {
    # German cities
    "munchen": "munich",
    "koln": "cologne",
    "nurnberg": "nuremberg",
    "dusseldorf": "dusseldorf",  # Already English spelling
    "frankfurt": "frankfurt",
    "hannover": "hanover",
    "braunschweig": "brunswick",
    # Italian cities
    "milano": "milan",
    # "roma", "napoli", "torino" removed — conflicts with team aliases
    # (AS Roma, SSC Napoli, Torino FC). Both sides of fuzzy match go through
    # the same normalizer so translation is redundant, but it breaks alias lookup.
    "firenze": "florence",
    "venezia": "venice",
    "genova": "genoa",
    # Spanish cities
    "sevilla": "seville",
    # Brazilian cities (Portuguese)
    "sao paulo": "sao paulo",  # Keep as-is
    # Russian cities (transliterated)
    "moskva": "moscow",
    "sankt peterburg": "st petersburg",
    # Other
    "wien": "vienna",
    "praha": "prague",
    "warszawa": "warsaw",
    "kobenhavn": "copenhagen",
    "goteborg": "gothenburg",
}


# =============================================================================
# BUILT-IN TEAM NAME ALIASES
# Common abbreviations/nicknames that fuzzy matching won't catch.
# User-defined aliases in team_aliases table take precedence.
#
# Format: alias -> canonical_name
# All keys should be lowercase
# =============================================================================

TEAM_ALIASES: dict[str, str] = {
    # English Premier League
    "man u": "manchester united",
    "man utd": "manchester united",
    "man united": "manchester united",
    "man city": "manchester city",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "wolves": "wolverhampton wanderers",
    "west ham": "west ham united",
    "brighton": "brighton and hove albion",
    "newcastle": "newcastle united",
    "nottm forest": "nottingham forest",
    "nott forest": "nottingham forest",
    "nottingham": "nottingham forest",
    # German Bundesliga
    "bayern": "bayern munich",
    "bayern munchen": "bayern munich",
    "fc bayern": "bayern munich",
    "dortmund": "borussia dortmund",
    "bvb": "borussia dortmund",
    "gladbach": "borussia monchengladbach",
    "monchengladbach": "borussia monchengladbach",
    "leverkusen": "bayer leverkusen",
    "bayer 04": "bayer leverkusen",
    "leipzig": "rb leipzig",
    "rb leipzig": "rb leipzig",
    "frankfurt": "eintracht frankfurt",
    "wolfsburg": "vfl wolfsburg",
    # Spanish La Liga
    "barca": "barcelona",
    "real": "real madrid",
    "atletico": "atletico madrid",
    "atleti": "atletico madrid",
    "athletic": "athletic bilbao",
    "athletic club": "athletic bilbao",
    "sevilla fc": "sevilla",
    "real sociedad": "real sociedad",
    "villarreal cf": "villarreal",
    # Italian Serie A
    "inter": "inter milan",
    "inter milan": "internazionale",
    "ac milan": "milan",
    "juve": "juventus",
    "napoli": "ssc napoli",
    "lazio": "ss lazio",
    "roma": "as roma",
    "atalanta": "atalanta bc",
    "fiorentina": "acf fiorentina",
    # French Ligue 1
    "psg": "paris saint germain",
    "paris sg": "paris saint germain",
    "paris": "paris saint germain",
    "marseille": "olympique marseille",
    "om": "olympique marseille",
    "lyon": "olympique lyonnais",
    "ol": "olympique lyonnais",
    "monaco": "as monaco",
    "lille": "lille osc",
    # MLS
    "la galaxy": "los angeles galaxy",
    "galaxy": "los angeles galaxy",
    "lafc": "los angeles fc",
    "nycfc": "new york city fc",
    "nyc fc": "new york city fc",
    "nyrb": "new york red bulls",
    "ny red bulls": "new york red bulls",
    "atlanta": "atlanta united",
    "inter miami": "inter miami cf",
    "miami": "inter miami cf",
    "seattle": "seattle sounders",
    "sounders": "seattle sounders",
    "portland": "portland timbers",
    "timbers": "portland timbers",
    # NFL
    "pats": "new england patriots",
    "niners": "san francisco 49ers",
    "9ers": "san francisco 49ers",
    "philly": "philadelphia eagles",
    "big d": "dallas cowboys",
    # NBA
    "lakers": "los angeles lakers",
    "clippers": "la clippers",
    "knicks": "new york knicks",
    "nets": "brooklyn nets",
    "sixers": "philadelphia 76ers",
    "celts": "boston celtics",
    "dubs": "golden state warriors",
    "heat": "miami heat",
    # NHL
    "habs": "montreal canadiens",
    "leafs": "toronto maple leafs",
    "bruins": "boston bruins",
    "wings": "detroit red wings",
    "hawks": "chicago blackhawks",
    "pens": "pittsburgh penguins",
    "caps": "washington capitals",
    "bolts": "tampa bay lightning",
    "avs": "colorado avalanche",
    "canucks": "vancouver canucks",
    "oilers": "edmonton oilers",
    "flames": "calgary flames",
    "sens": "ottawa senators",
    "jets": "winnipeg jets",
    # MLB
    "yanks": "new york yankees",
    "bosox": "boston red sox",
    "redsox": "boston red sox",
    "whitesox": "chicago white sox",
    "dodgers": "los angeles dodgers",
    "cards": "st louis cardinals",
    "jays": "toronto blue jays",
    # College Basketball - ESPN uses short forms
    "appalachian state": "app state",
    "miami-oh": "miami oh",
    "miami (oh)": "miami oh",
    "utrgv": "ut rio grande valley",
    "ut rio grande": "ut rio grande valley",
    "tamu-cc": "texas a m corpus christi",
    "tamu cc": "texas a m corpus christi",
    "texas a&m-cc": "texas a m corpus christi",
    "texas a&m-corpus christi": "texas a m corpus christi",
}


# =============================================================================
# PROVIDER PREFIXES TO STRIP
# Stream names often start with provider identifiers that should be removed
# before matching. Order matters - longer prefixes first to avoid partial matches.
# =============================================================================

PROVIDER_PREFIXES: list[str] = [
    # Streaming services (with common suffixes)
    "espn+ ",
    "espn plus ",
    "espn+ - ",
    "espn +",
    "espn:",
    "espn -",
    "espn",
    "paramount+ ",
    "paramount+: ",
    "paramount plus ",
    "peacock ",
    "peacock: ",
    "max ",
    "max: ",
    "apple tv+ ",
    "apple tv ",
    "amazon prime ",
    "prime video ",
    "dazn ",
    "dazn: ",
    "fubo ",
    "fubotv ",
    "directv ",
    "directv stream ",
    # Sports networks
    "fox sports ",
    "fs1 ",
    "fs2 ",
    "fsn ",
    "nbc sports ",
    "nbcsn ",
    "cbs sports ",
    "tnt ",
    "tbs ",
    "usa network ",
    "nfl network ",
    "nba tv ",
    "nhl network ",
    "mlb network ",
    "bein sports ",
    "bein ",
    "sky sports ",
    "bt sport ",
    "tsn ",
    "sportsnet ",
    # Regional sports networks
    "nesn ",
    "msg ",
    "yes network ",
    "masn ",
    "root sports ",
    "bally sports ",
    "at&t sportsnet ",
    "altitude ",
]


# =============================================================================
# PLACEHOLDER PATTERNS
# Regex patterns that identify placeholder/filler streams with no event info.
# These streams should be classified as PLACEHOLDER and skipped.
# =============================================================================

PLACEHOLDER_PATTERNS: list[str] = [
    # Provider prefix + number with no event info
    r"^espn\+?\s*\d+\s*[-:]?\s*$",
    r"^dazn\s*\d+\s*[-:]?\s*$",
    r"^paramount\+?\s*\d+\s*[-:]?\s*$",
    # Generic numbered channels
    r"^channel\s*\d+\s*$",
    r"^ch\s*\d+\s*$",
    # "Coming Soon" / "TBD" / "TBA" patterns
    r"^coming\s+soon",
    r"^to\s+be\s+announced",
    r"^to\s+be\s+determined",
    r"^tba\s*$",
    r"^tbd\s*$",
    # Maintenance / Off-air
    r"^off\s*air",
    r"^no\s+signal",
    r"^please\s+stand\s+by",
    r"^technical\s+difficulties",
]


# =============================================================================
# GAME SEPARATORS
# Patterns that indicate a stream contains team vs team matchup.
# Used by classifier to determine stream category.
# Order: more specific patterns first
# =============================================================================

GAME_SEPARATORS: list[str] = [
    " vs. ",
    " vs ",
    " v. ",
    " v ",
    " @ ",
    " at ",
    " x ",  # Portuguese/Spanish style
    " contre ",  # French
    " gegen ",  # German
    " contra ",  # Spanish/Portuguese
]


# =============================================================================
# BROADCAST NETWORKS
# Network names to strip from team names during matching normalization.
# These appear in stream names like "MIL Bucks ( ESPN Feed )" and add noise
# that reduces fuzzy match scores.
# =============================================================================

BROADCAST_NETWORKS: list[str] = [
    # Major US networks
    "ESPN",
    "ESPN2",
    "ESPNU",
    "ESPN+",
    "ABC",
    "CBS",
    "NBC",
    "FOX",
    "TNT",
    "TBS",
    "USA",
    # Sports-specific
    "FS1",
    "FS2",
    "NBCSN",
    "CBSSN",
    "NFLN",
    "MLBN",
    "NHLN",
    "SECN",
    "BTN",
    "ACCN",
    "PAC12",
    "LHN",
    # Streaming
    "PEACOCK",
    "PARAMOUNT",
    "AMAZON",
    "PRIME",
    "APPLE",
    "DAZN",
    "FUBO",
    # International
    "SKY",
    "BT",
    "BEIN",
    "TSN",
    "SPORTSNET",
    # Common suffixes
    "FEED",
    "STREAM",
]


# =============================================================================
# LEAGUE HINT PATTERNS
# Patterns to detect league from stream name for multi-league groups.
# Returns league_code(s) if detected.
#
# Format: (pattern, league_code) where league_code is str or list[str]
# Use list for umbrella brands (e.g., "EFL" covers Championship, League One, League Two)
# Patterns are case-insensitive, checked in order
# =============================================================================

LEAGUE_HINT_PATTERNS: list[tuple[str, str | list[str]]] = [
    # ==========================================================================
    # Major US/Canadian Pro Leagues
    # ==========================================================================
    (r"\bnfl[:\s-]", "nfl"),
    (r"\bnba[:\s-]", "nba"),
    (r"\bnhl[:\s-]", "nhl"),
    (r"\bmlb[:\s-]", "mlb"),
    (r"\bmls[:\s-]", "usa.1"),
    (r"\bwnba[:\s-]", "wnba"),
    (r"\bnwsl[:\s-]", "usa.nwsl"),
    (r"\bg[\s-]?league[:\s-]", "nba-development"),
    # ==========================================================================
    # US College Sports
    # ==========================================================================
    (r"\bncaaf[:\s-]", "college-football"),
    (r"\bncaam[:\s-]", "mens-college-basketball"),
    (r"\bncaaw[:\s-]", "womens-college-basketball"),
    (r"\bncaab[:\s-]", "mens-college-basketball"),  # Alternate abbreviation
    # ==========================================================================
    # Soccer / Football - Multi-league umbrella brands first
    # ==========================================================================
    # EFL = English Football League (Championship, League One, League Two)
    # Include FA Cup since providers often mislabel FA Cup matches as "EFL"
    # Use \b word boundary instead of ^ to match after channel prefixes like "03: "
    (r"\befl[:\s-]", ["eng.2", "eng.3", "eng.4", "eng.fa"]),
    (r"\benglish\s+football\s+league[:\s-]", ["eng.2", "eng.3", "eng.4", "eng.fa"]),
    # Specific EFL divisions
    (r"\befl\s+championship[:\s-]", "eng.2"),
    (r"\bchampionship[:\s-]", "eng.2"),
    (r"\befl\s+league\s+one[:\s-]", "eng.3"),
    (r"\bleague\s+one[:\s-]", "eng.3"),
    (r"\befl\s+league\s+two[:\s-]", "eng.4"),
    (r"\bleague\s+two[:\s-]", "eng.4"),
    # EFL Cup (Carabao Cup)
    (r"\befl\s+cup[:\s-]", "eng.league_cup"),
    (r"\bcarabao\s+cup[:\s-]", "eng.league_cup"),
    (r"\bleague\s+cup[:\s-]", "eng.league_cup"),
    # FA Cup
    (r"\bfa\s+cup[:\s-]", "eng.fa"),
    # Premier League
    (r"\bepl[:\s-]", "eng.1"),
    (r"\bpremier\s+league[:\s-]", "eng.1"),
    # Other top European leagues
    (r"\bla\s+liga[:\s-]", "esp.1"),
    (r"\bbundesliga[:\s-]", "ger.1"),
    (r"\bserie\s+a[:\s-]", "ita.1"),
    (r"\bligue\s+1[:\s-]", "fra.1"),
    (r"\buefa\s+champions\s+league[:\s-]", "uefa.champions"),
    (r"\bucl[:\s-]", "uefa.champions"),
    (r"\bchampions\s+league[:\s-]", "uefa.champions"),
    (r"\bspl[:\s-]", "ksa.1"),  # Saudi Pro League
    # ==========================================================================
    # Hockey - Multi-league umbrella brands first
    # ==========================================================================
    # CHL = Canadian Hockey League (OHL, WHL, QMJHL)
    (r"\bchl[:\s-]", ["ohl", "whl", "qmjhl"]),
    (r"\bcanadian\s+hockey\s+league[:\s-]", ["ohl", "whl", "qmjhl"]),
    # Specific CHL leagues
    (r"\bpwhl[:\s-]", "pwhl"),
    (r"\bahl[:\s-]", "ahl"),
    (r"\bohl[:\s-]", "ohl"),
    (r"\bwhl[:\s-]", "whl"),
    (r"\bqmjhl[:\s-]", "qmjhl"),
    (r"\bushl[:\s-]", "ushl"),
    # ==========================================================================
    # Combat Sports (event_card types)
    # ==========================================================================
    (r"\bufc\s*\d+", "ufc"),
    (r"\bufc\b", "ufc"),
    (r"\bfight\s+night\b", "ufc"),
    (r"\bboxing[:\s-]", "boxing"),
    (r"\bpbc[:\s-]", "boxing"),  # Premier Boxing Champions
    (r"\btop\s+rank\b", "boxing"),
    (r"\bmatchroom\b", "boxing"),
    # ==========================================================================
    # Cricket
    # ==========================================================================
    (r"\bipl[:\s-]", "ipl"),
    (r"\bcpl[:\s-]", "cpl"),
    (r"\bbbl[:\s-]", "bbl"),  # Big Bash League
    (r"\bsa20[:\s-]", "sa20"),
    # ==========================================================================
    # Lacrosse
    # ==========================================================================
    (r"\bnll[:\s-]", "nll"),
    (r"\bpll[:\s-]", "pll"),
    # ==========================================================================
    # Rugby
    # ==========================================================================
    (r"\bnrl[:\s-]", "nrl"),
    (r"\bsuper\s+rugby[:\s-]", "super-rugby"),
]


# =============================================================================
# SPORT HINT PATTERNS
# Patterns to detect sport type from stream name.
# Unlike league hints which are start-anchored, these can match anywhere.
# Returns sport name matching leagues.sport column values.
#
# Format: (pattern, sport_name)
# Patterns are case-insensitive, checked in order
# =============================================================================

SPORT_HINT_PATTERNS: list[tuple[str, str]] = [
    # Hockey variants - must come before generic patterns
    (r"\b(ice\s+)?hockey\b", "Hockey"),
    (r"\bnhl\b", "Hockey"),
    (r"\bahl\b", "Hockey"),
    (r"\bpwhl\b", "Hockey"),
    # Football variants
    (r"\b(american\s+)?football\b", "Football"),
    (r"\bnfl\b", "Football"),
    (r"\bncaaf\b", "Football"),
    # Basketball
    (r"\bbasketball\b", "Basketball"),
    (r"\bnba\b", "Basketball"),
    (r"\bncaab\b", "Basketball"),
    (r"\bncaam\b", "Basketball"),
    (r"\bncaaw\b", "Basketball"),
    # Soccer/Football (association)
    (r"\bsoccer\b", "Soccer"),
    (
        r"\bfootball\b(?!\s*(nfl|american|college))",
        "Soccer",
    ),  # "Football" without NFL context = Soccer
    # Baseball
    (r"\bbaseball\b", "Baseball"),
    (r"\bmlb\b", "Baseball"),
    # Lacrosse
    (r"\blacrosse\b", "Lacrosse"),
    (r"\bnll\b", "Lacrosse"),
    (r"\bpll\b", "Lacrosse"),
    # Cricket
    (r"\bcricket\b", "Cricket"),
    (r"\bipl\b", "Cricket"),
    (r"\bt20\b", "Cricket"),
    # Volleyball
    (r"\bvolleyball\b", "Volleyball"),
    # Swimming & Diving (not currently supported)
    (r"\bswimming\b", "Swimming"),
    (r"\bswim\b", "Swimming"),
    (r"\bdiving\b", "Diving"),
    (r"\bdive\b", "Diving"),
    # Gymnastics (not currently supported)
    (r"\bgymnastics\b", "Gymnastics"),
    # Wrestling (not currently supported)
    (r"\bwrestling\b", "Wrestling"),
    # Track & Field (not currently supported)
    (r"\btrack\s*(?:&|and)?\s*field\b", "Track and Field"),
    # Tennis (not currently supported)
    (r"\btennis\b", "Tennis"),
    # Golf (not currently supported)
    (r"\bgolf\b", "Golf"),
]


# =============================================================================
# EVENT CARD KEYWORDS
# Keywords that identify event card streams (UFC, boxing) within their league.
# Used by EventCardMatcher to validate streams.
# =============================================================================

EVENT_CARD_KEYWORDS: dict[str, list[str]] = {
    "ufc": [
        "ufc",
        "fight night",
        "ufc fn",
        "main card",
        "prelims",
        "early prelims",
        "dana white",
        "contender series",
        "dwcs",
    ],
    "boxing": [
        "boxing",
        "main event",
        "undercard",
        "premier boxing",
        "top rank",
        "matchroom",
        "dazn boxing",
        "showtime boxing",
        "golden boy",
    ],
}


# =============================================================================
# CARD SEGMENT PATTERNS
# Patterns to detect card segments (Early Prelims, Prelims, Main Card) from
# UFC/MMA stream names. Order matters - check specific patterns before general.
#
# Returns: "early_prelims", "prelims", "main_card", "combined", or None
# =============================================================================

CARD_SEGMENT_PATTERNS: list[tuple[str, str]] = [
    # Combined streams (check first - should match all segment channels)
    (r"\bprelims?\s*\+\s*mains?\b", "combined"),
    (r"\bprelims?\s*&\s*mains?\b", "combined"),
    # Early prelims (check before general prelims)
    (r"\bearly\s*prelims?\b", "early_prelims"),
    (r"\bpre-?show\b", "early_prelims"),
    # Prelims - parenthetical is more specific
    (r"\(prelims?\)", "prelims"),
    (r"\bpreliminary\s*card\b", "prelims"),
    (r"\bprelims?\b", "prelims"),
    # Main card variants
    (r"\(main\s*card(?:\s*\d+)?\)", "main_card"),  # (Main Card), (Main Card 1)
    (r"\bmain\s*card\b", "main_card"),
    (r"\bmain\s*event\b", "main_card"),
    (r"\b:?\s*main\s+english\b", "main_card"),  # "UFC 324: Main English"
    (r"\bmain\b(?!\s*(?:st|street))", "main_card"),  # "Main" but not "Main St"
]


# =============================================================================
# UFC EXCLUDE PATTERNS
# Stream name patterns that should NOT be matched to UFC events.
# These are related content (weigh-ins, press conferences) not actual fights.
# =============================================================================

UFC_EXCLUDE_PATTERNS: list[str] = [
    r"\bweigh[\s-]?in\b",
    r"\bpress\s*conference\b",
    r"\bcountdown\b",
    r"\bembedded\b",
    r"\bpost[\s-]?fight\b",
    r"\bface[\s-]?off\b",
    r"\bfree\s*fight\b",
    r"\bclassic\s*fight\b",
    r"\breplay\b",
    r"\bencore\b",
]
