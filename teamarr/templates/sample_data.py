"""Sample data for template variable previews.

Sport-specific sample values for the template variable picker.
Used for live preview in the UI.
"""

# Available sports for the dropdown
AVAILABLE_SPORTS = ["NBA", "NFL", "MLB", "NHL", "NCAAM", "NCAAF", "Soccer"]

# Sample data organized by variable name and sport
# Each variable can have different sample values per sport
SAMPLE_DATA: dict[str, dict[str, str]] = {
    # Team Identity
    "team_name": {
        "NBA": "Detroit Pistons",
        "NFL": "Detroit Lions",
        "MLB": "Detroit Tigers",
        "NHL": "Detroit Red Wings",
        "NCAAM": "Michigan Wolverines",
        "NCAAF": "Michigan Wolverines",
        "Soccer": "Liverpool",
    },
    "team_abbrev": {
        "NBA": "DET",
        "NFL": "DET",
        "MLB": "DET",
        "NHL": "DET",
        "NCAAM": "MICH",
        "NCAAF": "MICH",
        "Soccer": "LIV",
    },
    "league": {
        "NBA": "NBA",
        "NFL": "NFL",
        "MLB": "MLB",
        "NHL": "NHL",
        "NCAAM": "NCAAM",
        "NCAAF": "NCAAF",
        "Soccer": "Premier League",
    },
    "sport": {
        "NBA": "Basketball",
        "NFL": "Football",
        "MLB": "Baseball",
        "NHL": "Hockey",
        "NCAAM": "Basketball",
        "NCAAF": "Football",
        "Soccer": "Soccer",
    },
    # Opponent/Matchup
    "opponent": {
        "NBA": "Chicago Bulls",
        "NFL": "Chicago Bears",
        "MLB": "Chicago White Sox",
        "NHL": "Chicago Blackhawks",
        "NCAAM": "Ohio State Buckeyes",
        "NCAAF": "Ohio State Buckeyes",
        "Soccer": "Manchester City",
    },
    "opponent.next": {
        "NBA": "Milwaukee Bucks",
        "NFL": "Green Bay Packers",
        "MLB": "Cleveland Guardians",
        "NHL": "Columbus Blue Jackets",
        "NCAAM": "Indiana Hoosiers",
        "NCAAF": "Penn State Nittany Lions",
        "Soccer": "Arsenal",
    },
    "opponent.last": {
        "NBA": "Cleveland Cavaliers",
        "NFL": "Minnesota Vikings",
        "MLB": "Minnesota Twins",
        "NHL": "Minnesota Wild",
        "NCAAM": "Michigan State Spartans",
        "NCAAF": "Michigan State Spartans",
        "Soccer": "Chelsea",
    },
    "matchup": {
        "NBA": "Detroit Pistons vs Chicago Bulls",
        "NFL": "Detroit Lions vs Chicago Bears",
        "MLB": "Detroit Tigers vs Chicago White Sox",
        "NHL": "Detroit Red Wings vs Chicago Blackhawks",
        "NCAAM": "Michigan Wolverines vs Ohio State Buckeyes",
        "NCAAF": "Michigan Wolverines vs Ohio State Buckeyes",
        "Soccer": "Liverpool vs Manchester City",
    },
    "home_team": {
        "NBA": "Detroit Pistons",
        "NFL": "Detroit Lions",
        "MLB": "Detroit Tigers",
        "NHL": "Detroit Red Wings",
        "NCAAM": "Michigan Wolverines",
        "NCAAF": "Michigan Wolverines",
        "Soccer": "Liverpool",
    },
    "away_team": {
        "NBA": "Chicago Bulls",
        "NFL": "Chicago Bears",
        "MLB": "Chicago White Sox",
        "NHL": "Chicago Blackhawks",
        "NCAAM": "Ohio State Buckeyes",
        "NCAAF": "Ohio State Buckeyes",
        "Soccer": "Manchester City",
    },
    # Records
    "team_record": {
        "NBA": "25-15",
        "NFL": "10-4",
        "MLB": "82-65",
        "NHL": "28-18-6",
        "NCAAM": "18-5",
        "NCAAF": "11-1",
        "Soccer": "15-3-2",
    },
    "opponent_record": {
        "NBA": "22-18",
        "NFL": "8-6",
        "MLB": "75-72",
        "NHL": "25-20-7",
        "NCAAM": "16-7",
        "NCAAF": "10-2",
        "Soccer": "14-4-2",
    },
    "home_team_record": {
        "NBA": "25-15",
        "NFL": "10-4",
        "MLB": "82-65",
        "NHL": "28-18-6",
        "NCAAM": "18-5",
        "NCAAF": "11-1",
        "Soccer": "15-3-2",
    },
    "away_team_record": {
        "NBA": "22-18",
        "NFL": "8-6",
        "MLB": "75-72",
        "NHL": "25-20-7",
        "NCAAM": "16-7",
        "NCAAF": "10-2",
        "Soccer": "14-4-2",
    },
    "home_team_record.next": {
        "NBA": "26-15",
        "NFL": "11-4",
        "MLB": "83-65",
        "NHL": "29-18-6",
        "NCAAM": "19-5",
        "NCAAF": "12-1",
        "Soccer": "16-3-2",
    },
    "away_team_record.next": {
        "NBA": "20-20",
        "NFL": "9-6",
        "MLB": "70-77",
        "NHL": "22-22-8",
        "NCAAM": "14-9",
        "NCAAF": "8-4",
        "Soccer": "12-5-3",
    },
    # Venue
    "venue": {
        "NBA": "Little Caesars Arena",
        "NFL": "Ford Field",
        "MLB": "Comerica Park",
        "NHL": "Little Caesars Arena",
        "NCAAM": "Crisler Center",
        "NCAAF": "Michigan Stadium",
        "Soccer": "Anfield",
    },
    "venue_city": {
        "NBA": "Detroit",
        "NFL": "Detroit",
        "MLB": "Detroit",
        "NHL": "Detroit",
        "NCAAM": "Ann Arbor",
        "NCAAF": "Ann Arbor",
        "Soccer": "Liverpool",
    },
    "venue_state": {
        "NBA": "MI",
        "NFL": "MI",
        "MLB": "MI",
        "NHL": "MI",
        "NCAAM": "MI",
        "NCAAF": "MI",
        "Soccer": "England",
    },
    "venue_full": {
        "NBA": "Little Caesars Arena, Detroit, MI",
        "NFL": "Ford Field, Detroit, MI",
        "MLB": "Comerica Park, Detroit, MI",
        "NHL": "Little Caesars Arena, Detroit, MI",
        "NCAAM": "Crisler Center, Ann Arbor, MI",
        "NCAAF": "Michigan Stadium, Ann Arbor, MI",
        "Soccer": "Anfield, Liverpool, England",
    },
    # Date/Time
    "game_date": {
        "NBA": "Sunday, Dec 22",
        "NFL": "Sunday, Dec 22",
        "MLB": "Saturday, Jul 12",
        "NHL": "Saturday, Dec 21",
        "NCAAM": "Saturday, Jan 11",
        "NCAAF": "Saturday, Nov 30",
        "Soccer": "Saturday, Dec 21",
    },
    "game_date.next": {
        "NBA": "Tuesday, Dec 24",
        "NFL": "Sunday, Dec 29",
        "MLB": "Sunday, Jul 13",
        "NHL": "Monday, Dec 23",
        "NCAAM": "Tuesday, Jan 14",
        "NCAAF": "Saturday, Dec 7",
        "Soccer": "Wednesday, Dec 25",
    },
    "game_date.last": {
        "NBA": "Friday, Dec 20",
        "NFL": "Sunday, Dec 15",
        "MLB": "Friday, Jul 11",
        "NHL": "Thursday, Dec 19",
        "NCAAM": "Wednesday, Jan 8",
        "NCAAF": "Saturday, Nov 23",
        "Soccer": "Sunday, Dec 15",
    },
    "game_time": {
        "NBA": "7:00 PM EST",
        "NFL": "1:00 PM EST",
        "MLB": "4:10 PM EDT",
        "NHL": "7:00 PM EST",
        "NCAAM": "2:00 PM EST",
        "NCAAF": "12:00 PM EST",
        "Soccer": "10:00 AM EST",
    },
    "game_time.next": {
        "NBA": "8:00 PM EST",
        "NFL": "4:25 PM EST",
        "MLB": "1:10 PM EDT",
        "NHL": "7:30 PM EST",
        "NCAAM": "7:00 PM EST",
        "NCAAF": "3:30 PM EST",
        "Soccer": "2:30 PM EST",
    },
    # Home/Away
    "vs_at": {
        "NBA": "vs",
        "NFL": "vs",
        "MLB": "vs",
        "NHL": "vs",
        "NCAAM": "vs",
        "NCAAF": "vs",
        "Soccer": "vs",
    },
    "vs_at.next": {
        "NBA": "@",
        "NFL": "vs",
        "MLB": "@",
        "NHL": "@",
        "NCAAM": "@",
        "NCAAF": "vs",
        "Soccer": "@",
    },
    "is_home": {
        "NBA": "true",
        "NFL": "true",
        "MLB": "true",
        "NHL": "true",
        "NCAAM": "true",
        "NCAAF": "true",
        "Soccer": "true",
    },
    # Scores/Results (only .last)
    "final_score": {
        "NBA": "112-105",
        "NFL": "31-24",
        "MLB": "5-3",
        "NHL": "4-2",
        "NCAAM": "78-65",
        "NCAAF": "42-27",
        "Soccer": "2-1",
    },
    "final_score.last": {
        "NBA": "108-102",
        "NFL": "28-21",
        "MLB": "7-4",
        "NHL": "3-2",
        "NCAAM": "85-72",
        "NCAAF": "35-28",
        "Soccer": "3-0",
    },
    "result_text": {
        "NBA": "defeated",
        "NFL": "defeated",
        "MLB": "defeated",
        "NHL": "defeated",
        "NCAAM": "defeated",
        "NCAAF": "defeated",
        "Soccer": "defeated",
    },
    "result_text.last": {
        "NBA": "defeated",
        "NFL": "beat",
        "MLB": "beat",
        "NHL": "beat",
        "NCAAM": "defeated",
        "NCAAF": "defeated",
        "Soccer": "beat",
    },
    # Broadcast
    "broadcast": {
        "NBA": "ESPN, Bally Sports Detroit",
        "NFL": "FOX, NFL Network",
        "MLB": "Bally Sports Detroit",
        "NHL": "ESPN+, Bally Sports Detroit",
        "NCAAM": "CBS, ESPN+",
        "NCAAF": "ABC, ESPN",
        "Soccer": "NBC, Peacock",
    },
    "broadcast_simple": {
        "NBA": "ESPN",
        "NFL": "FOX",
        "MLB": "Bally Sports",
        "NHL": "ESPN+",
        "NCAAM": "CBS",
        "NCAAF": "ABC",
        "Soccer": "NBC",
    },
    # Odds
    "odds_spread": {
        "NBA": "-4.5",
        "NFL": "-3.0",
        "MLB": "-1.5",
        "NHL": "-1.5",
        "NCAAM": "-7.0",
        "NCAAF": "-14.5",
        "Soccer": "-0.5",
    },
    "odds_moneyline": {
        "NBA": "-180",
        "NFL": "-150",
        "MLB": "-130",
        "NHL": "-140",
        "NCAAM": "-275",
        "NCAAF": "-500",
        "Soccer": "-120",
    },
    "odds_over_under": {
        "NBA": "225.5",
        "NFL": "48.5",
        "MLB": "8.5",
        "NHL": "6.5",
        "NCAAM": "145.5",
        "NCAAF": "54.5",
        "Soccer": "2.5",
    },
    # Streaks
    "streak": {
        "NBA": "W3",
        "NFL": "W2",
        "MLB": "W4",
        "NHL": "L2",
        "NCAAM": "W5",
        "NCAAF": "W8",
        "Soccer": "W4",
    },
    "streak_description": {
        "NBA": "Won 3 straight",
        "NFL": "Won 2 straight",
        "MLB": "Won 4 straight",
        "NHL": "Lost 2 straight",
        "NCAAM": "Won 5 straight",
        "NCAAF": "Won 8 straight",
        "Soccer": "Won 4 straight",
    },
    # Rankings (college)
    "ranking": {
        "NBA": "",
        "NFL": "",
        "MLB": "",
        "NHL": "",
        "NCAAM": "#8",
        "NCAAF": "#3",
        "Soccer": "",
    },
    "opponent_ranking": {
        "NBA": "",
        "NFL": "",
        "MLB": "",
        "NHL": "",
        "NCAAM": "#12",
        "NCAAF": "#7",
        "Soccer": "",
    },
    # Playoff/Season type
    "season_type": {
        "NBA": "Regular Season",
        "NFL": "Regular Season",
        "MLB": "Regular Season",
        "NHL": "Regular Season",
        "NCAAM": "Regular Season",
        "NCAAF": "Bowl Season",
        "Soccer": "Premier League",
    },
    "playoff_round": {
        "NBA": "",
        "NFL": "",
        "MLB": "",
        "NHL": "",
        "NCAAM": "",
        "NCAAF": "Rose Bowl",
        "Soccer": "",
    },
}


def get_sample_value(var_name: str, sport: str) -> str:
    """Get sample value for a variable and sport.

    Falls back through: exact match -> base var -> default
    """
    # Try exact match first
    if var_name in SAMPLE_DATA:
        sport_data = SAMPLE_DATA[var_name]
        if sport in sport_data:
            return sport_data[sport]
        # Fall back to first available sport
        return next(iter(sport_data.values()), "")

    # Try base variable (without .next/.last suffix)
    base_var = var_name.replace(".next", "").replace(".last", "")
    if base_var in SAMPLE_DATA:
        sport_data = SAMPLE_DATA[base_var]
        if sport in sport_data:
            return sport_data[sport]
        return next(iter(sport_data.values()), "")

    return ""


def get_all_sample_data(sport: str) -> dict[str, str]:
    """Get all sample values for a given sport."""
    result = {}
    for var_name, sport_data in SAMPLE_DATA.items():
        if sport in sport_data:
            result[var_name] = sport_data[sport]
        elif sport_data:
            # Fall back to first available
            result[var_name] = next(iter(sport_data.values()))
    return result
