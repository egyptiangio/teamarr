"""Static league logo and name mappings.

Known league logos and display names for common leagues.
"""

# =============================================================================
# KNOWN LEAGUE LOGOS (static mapping like V1)
# =============================================================================

KNOWN_LEAGUE_LOGOS: dict[str, str] = {
    # Major US leagues
    "nfl": "https://a.espncdn.com/i/teamlogos/leagues/500/nfl.png",
    "nba": "https://a.espncdn.com/i/teamlogos/leagues/500/nba.png",
    "mlb": "https://a.espncdn.com/i/teamlogos/leagues/500/mlb.png",
    "nhl": "https://a.espncdn.com/i/teamlogos/leagues/500/nhl.png",
    "mls": "https://a.espncdn.com/i/leaguelogos/soccer/500/19.png",
    "wnba": "https://a.espncdn.com/i/teamlogos/leagues/500/wnba.png",
    # College (NCAA.com sport banners)
    "college-football": "https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/football.png",
    "mens-college-basketball": "https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/basketball.png",
    "womens-college-basketball": "https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/basketball.png",
    "mens-college-hockey": "https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/icehockey.png",
    "womens-college-hockey": "https://www.ncaa.com/modules/custom/casablanca_core/img/sportbanners/icehockey.png",
    # Soccer - Major
    "eng.1": "https://a.espncdn.com/i/leaguelogos/soccer/500/23.png",
    "eng.2": "https://a.espncdn.com/i/leaguelogos/soccer/500/24.png",
    "esp.1": "https://a.espncdn.com/i/leaguelogos/soccer/500/15.png",
    "ger.1": "https://a.espncdn.com/i/leaguelogos/soccer/500/10.png",
    "ita.1": "https://a.espncdn.com/i/leaguelogos/soccer/500/12.png",
    "fra.1": "https://a.espncdn.com/i/leaguelogos/soccer/500/9.png",
    "uefa.champions": "https://a.espncdn.com/i/leaguelogos/soccer/500/2.png",
    "uefa.europa": "https://a.espncdn.com/i/leaguelogos/soccer/500/2310.png",
    "usa.1": "https://a.espncdn.com/i/leaguelogos/soccer/500/19.png",
    "usa.nwsl": "https://a.espncdn.com/i/leaguelogos/soccer/500/2323.png",
    # Other
    "ufc": "https://a.espncdn.com/i/teamlogos/leagues/500/ufc.png",
}

KNOWN_LEAGUE_NAMES: dict[str, str] = {
    "nfl": "NFL",
    "nba": "NBA",
    "mlb": "MLB",
    "nhl": "NHL",
    "mls": "MLS",
    "wnba": "WNBA",
    "college-football": "NCAA Football",
    "mens-college-basketball": "NCAA Men's Basketball",
    "womens-college-basketball": "NCAA Women's Basketball",
    "eng.1": "Premier League",
    "eng.2": "EFL Championship",
    "esp.1": "La Liga",
    "ger.1": "Bundesliga",
    "ita.1": "Serie A",
    "fra.1": "Ligue 1",
    "uefa.champions": "UEFA Champions League",
    "uefa.europa": "UEFA Europa League",
    "usa.1": "MLS",
    "usa.nwsl": "NWSL",
    "ufc": "UFC",
}

# Sport inference patterns
SPORT_PATTERNS: dict[str, str] = {
    "nfl": "football",
    "nba": "basketball",
    "nhl": "hockey",
    "mlb": "baseball",
    "wnba": "basketball",
    "mls": "soccer",
    "ufc": "mma",
    "college-football": "football",
    "mens-college-basketball": "basketball",
    "womens-college-basketball": "basketball",
    "mens-college-hockey": "hockey",
    "womens-college-hockey": "hockey",
}
