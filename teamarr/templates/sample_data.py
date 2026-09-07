"""Sample data for template variable previews.

Sport-specific sample values for the template variable picker.
Used for live preview in the UI.

Organization: Variables grouped by category, with base/.next/.last variants together.
"""
from teamarr.config import get_show_timezone, get_time_format, get_user_timezone_str
from teamarr.templates.variables import get_registry
from teamarr.templates.variables.identity import construct_league_abbrev
from teamarr.utilities.sports import get_sport_from_league

# Available sample "profiles" — the three shape bases. Every league previews
# against one of three generic, fictitious shapes ("team" / "combat" / "racing")
# whose sport-agnostic values are borrowed from these base profiles (NBA / UFC /
# F1). Leagues map onto a shape via resolve_profile_for_league / resolve_shape.
# The ``sport=`` query param accepts these names; anything else falls back to NBA.
AVAILABLE_SPORTS = [
    "NBA",
    "UFC",
    "F1",
]

# Sample data organized by variable name and sport
# Each variable can have different sample values per sport
SAMPLE_DATA: dict[str, dict[str, str]] = {
    "team_name": {
        "NBA": "Detroit Pistons",
    },
    "team_abbrev": {
        "NBA": "DET",
    },
    "team_short": {
        "NBA": "Pistons",
    },
    "opponent": {
        "NBA": "Chicago Bulls",
    },
    "opponent.next": {
        "NBA": "Milwaukee Bucks",
    },
    "opponent.last": {
        "NBA": "Cleveland Cavaliers",
    },
    "team_name_the": {
        "NBA": "the Detroit Pistons",
    },
    "opponent_the": {
        "NBA": "the Chicago Bulls",
    },
    "opponent_the.next": {
        "NBA": "the Milwaukee Bucks",
    },
    "opponent_the.last": {
        "NBA": "the Cleveland Cavaliers",
    },
    "home_team_the": {
        "NBA": "the Detroit Pistons",
    },
    "away_team_the": {
        "NBA": "the Chicago Bulls",
    },
    "at_vs": {
        "NBA": "at",
    },
    "at_vs.next": {
        "NBA": "at",
    },
    "at_vs.last": {
        "NBA": "at",
    },
    "home_away_verb": {
        "NBA": "host",
    },
    "home_away_verb.next": {
        "NBA": "visit",
    },
    "home_away_verb.last": {
        "NBA": "visit",
    },
    "opponent_abbrev": {
        "NBA": "CHI",
    },
    "opponent_abbrev.next": {
        "NBA": "MIL",
    },
    "opponent_abbrev.last": {
        "NBA": "CLE",
    },
    "opponent_short": {
        "NBA": "Bulls",
    },
    "opponent_short.next": {
        "NBA": "Bucks",
    },
    "opponent_short.last": {
        "NBA": "Cavaliers",
    },
    "matchup": {
        "NBA": "Chicago Bulls @ Detroit Pistons",
        "UFC": "Diego Lopes @ Alex Volkanovski",
    },
    "matchup.next": {
        "NBA": "Detroit Pistons @ Milwaukee Bucks",
        "UFC": "Arman Tsarukyan @ Islam Makhachev",
    },
    "matchup.last": {
        "NBA": "Detroit Pistons @ Cleveland Cavaliers",
        "UFC": "Stipe Miocic @ Jon Jones",
    },
    "matchup_combat": {
        "UFC": "Alex Volkanovski vs Diego Lopes",
    },
    "matchup_abbrev": {
        "NBA": "CHI @ DET",
    },
    "matchup_abbrev.next": {
        "NBA": "DET @ MIL",
    },
    "matchup_abbrev.last": {
        "NBA": "DET @ CLE",
    },
    "matchup_short": {
        "NBA": "Bulls @ Pistons",
    },
    "matchup_short.next": {
        "NBA": "Pistons @ Bucks",
    },
    "matchup_short.last": {
        "NBA": "Pistons @ Cavaliers",
    },
    "league": {
        "NBA": "NBA",
        "UFC": "UFC",
        "F1": "F1",
    },
    "league_name": {
        "NBA": "National Basketball Association",
        "UFC": "Ultimate Fighting Championship",
        "F1": "Formula 1",
    },
    "league_id": {
        "NBA": "nba",
        "F1": "f1",
    },
    "league_code": {
        "NBA": "nba",
        "F1": "f1",
    },
    "sport": {
        "NBA": "Basketball",
        "F1": "Racing",
    },
    "gracenote_category": {
        "NBA": "NBA Basketball",
        "F1": "Formula 1 Racing",
    },
    "exception_keyword": {
        "NBA": "4K",
    },
    "home_team": {
        "NBA": "Detroit Pistons",
    },
    "home_team.next": {
        "NBA": "Milwaukee Bucks",
    },
    "home_team.last": {
        "NBA": "Cleveland Cavaliers",
    },
    "home_team_short": {
        "NBA": "Pistons",
    },
    "home_team_short.next": {
        "NBA": "Bucks",
    },
    "home_team_short.last": {
        "NBA": "Cavaliers",
    },
    "home_team_abbrev": {
        "NBA": "DET",
    },
    "home_team_abbrev.next": {
        "NBA": "MIL",
    },
    "home_team_abbrev.last": {
        "NBA": "CLE",
    },
    "home_team_logo": {
        "NBA": "https://a.espncdn.com/i/teamlogos/nba/500/8.png",
    },
    "home_team_logo.next": {
        "NBA": "https://a.espncdn.com/i/teamlogos/nba/500/8.png",
    },
    "home_team_logo.last": {
        "NBA": "https://a.espncdn.com/i/teamlogos/nba/500/8.png",
    },
    "away_team": {
        "NBA": "Chicago Bulls",
    },
    "away_team.next": {
        "NBA": "Detroit Pistons",
    },
    "away_team.last": {
        "NBA": "Detroit Pistons",
    },
    "away_team_short": {
        "NBA": "Bulls",
    },
    "away_team_short.next": {
        "NBA": "Pistons",
    },
    "away_team_short.last": {
        "NBA": "Pistons",
    },
    "away_team_abbrev": {
        "NBA": "CHI",
    },
    "away_team_abbrev.next": {
        "NBA": "DET",
    },
    "away_team_abbrev.last": {
        "NBA": "DET",
    },
    "away_team_logo": {
        "NBA": "https://a.espncdn.com/i/teamlogos/nba/500/4.png",
    },
    "away_team_logo.next": {
        "NBA": "https://a.espncdn.com/i/teamlogos/nba/500/4.png",
    },
    "away_team_logo.last": {
        "NBA": "https://a.espncdn.com/i/teamlogos/nba/500/4.png",
    },
    "is_home": {
        "NBA": "true",
    },
    "is_home.next": {
        "NBA": "false",
    },
    "is_home.last": {
        "NBA": "false",
    },
    "is_away": {
        "NBA": "false",
    },
    "is_away.next": {
        "NBA": "true",
    },
    "is_away.last": {
        "NBA": "true",
    },
    "home_away_text": {
        "NBA": "at home",
    },
    "home_away_text.next": {
        "NBA": "on the road",
    },
    "home_away_text.last": {
        "NBA": "on the road",
    },
    "vs_at": {
        "NBA": "vs",
    },
    "vs_at.next": {
        "NBA": "at",
    },
    "vs_at.last": {
        "NBA": "at",
    },
    "vs_@": {
        "NBA": "vs",
    },
    "vs_@.next": {
        "NBA": "@",
    },
    "vs_@.last": {
        "NBA": "@",
    },
    "game_date": {
        "NBA": "Sunday, December 22, 2024",
    },
    "game_date.next": {
        "NBA": "Tuesday, December 24, 2024",
    },
    "game_date.last": {
        "NBA": "Friday, December 20, 2024",
    },
    "game_date_short": {
        "NBA": "Dec 22",
    },
    "game_date_short.next": {
        "NBA": "Dec 24",
    },
    "game_date_short.last": {
        "NBA": "Dec 20",
    },
    "game_day": {
        "NBA": "Sunday",
    },
    "game_day.next": {
        "NBA": "Tuesday",
    },
    "game_day.last": {
        "NBA": "Friday",
    },
    "game_day_short": {
        "NBA": "Sun",
    },
    "game_day_short.next": {
        "NBA": "Tue",
    },
    "game_day_short.last": {
        "NBA": "Fri",
    },
    "game_time": {
        "NBA": "7:00 PM EST",
    },
    "game_time.next": {
        "NBA": "8:00 PM EST",
    },
    "game_time.last": {
        "NBA": "7:30 PM EST",
    },
    "days_until": {
        "NBA": "0",
    },
    "days_until.next": {
        "NBA": "2",
    },
    "days_until.last": {
        "NBA": "0",
    },
    "today_tonight": {
        "NBA": "tonight",
    },
    "today_tonight.next": {
        "NBA": "tonight",
    },
    "today_tonight.last": {
        "NBA": "tonight",
    },
    "today_tonight_title": {
        "NBA": "Tonight",
    },
    "today_tonight_title.next": {
        "NBA": "Tonight",
    },
    "today_tonight_title.last": {
        "NBA": "Tonight",
    },
    "relative_day": {
        "NBA": "tonight",
    },
    "relative_day.next": {
        "NBA": "friday",
    },
    "relative_day_title": {
        "NBA": "Tonight",
    },
    "relative_day_title.next": {
        "NBA": "Friday",
    },
    "venue": {
        "NBA": "Little Caesars Arena",
    },
    "venue.next": {
        "NBA": "Fiserv Forum",
    },
    "venue.last": {
        "NBA": "Rocket Mortgage FieldHouse",
    },
    "venue_city": {
        "NBA": "Detroit",
    },
    "venue_city.next": {
        "NBA": "Milwaukee",
    },
    "venue_city.last": {
        "NBA": "Cleveland",
    },
    "venue_state": {
        "NBA": "MI",
    },
    "venue_state.next": {
        "NBA": "WI",
    },
    "venue_state.last": {
        "NBA": "OH",
    },
    "venue_full": {
        "NBA": "Little Caesars Arena, Detroit, MI",
    },
    "venue_full.next": {
        "NBA": "Fiserv Forum, Milwaukee, WI",
    },
    "venue_full.last": {
        "NBA": "Rocket Mortgage FieldHouse, Cleveland, OH",
    },
    "team_record": {
        "NBA": "25-15",
    },
    "team_wins": {
        "NBA": "25",
    },
    "team_losses": {
        "NBA": "15",
    },
    "team_ties": {
        "NBA": "",
    },
    "team_win_pct": {
        "NBA": ".625",
    },
    "home_record": {
        "NBA": "15-5",
    },
    "home_win_pct": {
        "NBA": ".750",
    },
    "away_record": {
        "NBA": "10-10",
    },
    "away_win_pct": {
        "NBA": ".500",
    },
    "opponent_record": {
        "NBA": "22-18",
    },
    "opponent_record.next": {
        "NBA": "20-20",
    },
    "opponent_record.last": {
        "NBA": "24-16",
    },
    "opponent_wins": {
        "NBA": "22",
    },
    "opponent_wins.next": {
        "NBA": "20",
    },
    "opponent_wins.last": {
        "NBA": "24",
    },
    "opponent_losses": {
        "NBA": "18",
    },
    "opponent_losses.next": {
        "NBA": "20",
    },
    "opponent_losses.last": {
        "NBA": "16",
    },
    "opponent_ties": {
        "NBA": "",
    },
    "opponent_ties.next": {
        "NBA": "",
    },
    "opponent_ties.last": {
        "NBA": "",
    },
    "opponent_win_pct": {
        "NBA": ".550",
    },
    "opponent_win_pct.next": {
        "NBA": ".500",
    },
    "opponent_win_pct.last": {
        "NBA": ".600",
    },
    "home_team_record": {
        "NBA": "25-15",
    },
    "home_team_record.next": {
        "NBA": "26-15",
    },
    "home_team_record.last": {
        "NBA": "24-16",
    },
    "away_team_record": {
        "NBA": "22-18",
    },
    "away_team_record.next": {
        "NBA": "20-20",
    },
    "away_team_record.last": {
        "NBA": "24-16",
    },
    "home_team_seed": {
        "NBA": "4",
    },
    "home_team_seed.next": {
        "NBA": "3",
    },
    "home_team_seed.last": {
        "NBA": "5",
    },
    "away_team_seed": {
        "NBA": "6",
    },
    "away_team_seed.next": {
        "NBA": "4",
    },
    "away_team_seed.last": {
        "NBA": "4",
    },
    "streak": {
        "NBA": "W3",
    },
    "streak_length": {
        "NBA": "3",
    },
    "streak_type": {
        "NBA": "win",
    },
    "win_streak": {
        "NBA": "3",
    },
    "loss_streak": {
        "NBA": "",
    },
    "opponent_streak": {
        "NBA": "L3",
    },
    "opponent_streak.next": {
        "NBA": "W2",
    },
    "opponent_streak.last": {
        "NBA": "W1",
    },
    "opponent_streak_length": {
        "NBA": "3",
    },
    "opponent_streak_length.next": {
        "NBA": "2",
    },
    "opponent_streak_length.last": {
        "NBA": "1",
    },
    "opponent_streak_type": {
        "NBA": "loss",
    },
    "opponent_streak_type.next": {
        "NBA": "win",
    },
    "opponent_streak_type.last": {
        "NBA": "win",
    },
    "opponent_win_streak": {
        "NBA": "",
    },
    "opponent_win_streak.next": {
        "NBA": "2",
    },
    "opponent_win_streak.last": {
        "NBA": "1",
    },
    "opponent_loss_streak": {
        "NBA": "3",
    },
    "opponent_loss_streak.next": {
        "NBA": "",
    },
    "opponent_loss_streak.last": {
        "NBA": "",
    },
    "home_team_streak": {
        "NBA": "W3",
    },
    "home_team_streak.next": {
        "NBA": "W4",
    },
    "home_team_streak.last": {
        "NBA": "W2",
    },
    "home_team_streak_length": {
        "NBA": "3",
    },
    "home_team_streak_length.next": {
        "NBA": "4",
    },
    "home_team_streak_length.last": {
        "NBA": "2",
    },
    "home_team_win_streak": {
        "NBA": "3",
    },
    "home_team_win_streak.next": {
        "NBA": "4",
    },
    "home_team_win_streak.last": {
        "NBA": "2",
    },
    "home_team_loss_streak": {
        "NBA": "",
    },
    "home_team_loss_streak.next": {
        "NBA": "",
    },
    "home_team_loss_streak.last": {
        "NBA": "",
    },
    "away_team_streak": {
        "NBA": "L3",
    },
    "away_team_streak.next": {
        "NBA": "L2",
    },
    "away_team_streak.last": {
        "NBA": "L4",
    },
    "away_team_streak_length": {
        "NBA": "3",
    },
    "away_team_streak_length.next": {
        "NBA": "2",
    },
    "away_team_streak_length.last": {
        "NBA": "4",
    },
    "away_team_win_streak": {
        "NBA": "",
    },
    "away_team_win_streak.next": {
        "NBA": "",
    },
    "away_team_win_streak.last": {
        "NBA": "",
    },
    "away_team_loss_streak": {
        "NBA": "3",
    },
    "away_team_loss_streak.next": {
        "NBA": "2",
    },
    "away_team_loss_streak.last": {
        "NBA": "4",
    },
    "team_score": {
        "NBA": "112",
    },
    "team_score.next": {
        "NBA": "",
    },
    "team_score.last": {
        "NBA": "108",
    },
    "opponent_score": {
        "NBA": "105",
    },
    "opponent_score.next": {
        "NBA": "",
    },
    "opponent_score.last": {
        "NBA": "102",
    },
    "score": {
        "NBA": "112-105",
    },
    "score.next": {
        "NBA": "",
    },
    "score.last": {
        "NBA": "108-102",
    },
    "final_score": {
        "NBA": "112-105",
    },
    "final_score.next": {
        "NBA": "",
    },
    "final_score.last": {
        "NBA": "108-102",
    },
    "home_team_score": {
        "NBA": "112",
    },
    "home_team_score.next": {
        "NBA": "",
    },
    "home_team_score.last": {
        "NBA": "102",
    },
    "away_team_score": {
        "NBA": "105",
    },
    "away_team_score.next": {
        "NBA": "",
    },
    "away_team_score.last": {
        "NBA": "108",
    },
    "score_diff": {
        "NBA": "+7",
    },
    "score_diff.next": {
        "NBA": "",
    },
    "score_diff.last": {
        "NBA": "+6",
    },
    "score_differential": {
        "NBA": "7",
    },
    "score_differential.next": {
        "NBA": "",
    },
    "score_differential.last": {
        "NBA": "6",
    },
    "score_differential_text": {
        "NBA": "by 7",
    },
    "score_differential_text.next": {
        "NBA": "",
    },
    "score_differential_text.last": {
        "NBA": "by 6",
    },
    "event_result": {
        "NBA": "Detroit Pistons 112 - Chicago Bulls 105",
    },
    "event_result.next": {
        "NBA": "",
    },
    "event_result.last": {
        "NBA": "Detroit Pistons 108 - Cleveland Cavaliers 102",
    },
    "event_result_abbrev": {
        "NBA": "DET 112 - CHI 105",
    },
    "event_result_abbrev.next": {
        "NBA": "",
    },
    "event_result_abbrev.last": {
        "NBA": "DET 108 - CLE 102",
    },
    "winner": {
        "NBA": "Detroit Pistons",
    },
    "winner.next": {
        "NBA": "",
    },
    "winner.last": {
        "NBA": "Detroit Pistons",
    },
    "winner_abbrev": {
        "NBA": "DET",
    },
    "winner_abbrev.next": {
        "NBA": "",
    },
    "winner_abbrev.last": {
        "NBA": "DET",
    },
    "loser": {
        "NBA": "Chicago Bulls",
    },
    "loser.next": {
        "NBA": "",
    },
    "loser.last": {
        "NBA": "Cleveland Cavaliers",
    },
    "loser_abbrev": {
        "NBA": "CHI",
    },
    "loser_abbrev.next": {
        "NBA": "",
    },
    "loser_abbrev.last": {
        "NBA": "CLE",
    },
    "result": {
        "NBA": "W",
    },
    "result.next": {
        "NBA": "",
    },
    "result.last": {
        "NBA": "W",
    },
    "result_text": {
        "NBA": "defeated",
    },
    "result_text.next": {
        "NBA": "",
    },
    "result_text.last": {
        "NBA": "defeated",
    },
    "overtime_text": {
        "NBA": "",
    },
    "overtime_text.next": {
        "NBA": "",
    },
    "overtime_text.last": {
        "NBA": "",
    },
    "overtime_short": {
        "NBA": "",
    },
    "overtime_short.next": {
        "NBA": "",
    },
    "overtime_short.last": {
        "NBA": "",
    },
    "playoff_seed": {
        "NBA": "4",
    },
    "games_back": {
        "NBA": "3.5",
    },
    "opponent_playoff_seed": {
        "NBA": "6",
    },
    "opponent_playoff_seed.next": {
        "NBA": "3",
    },
    "opponent_playoff_seed.last": {
        "NBA": "5",
    },
    "opponent_games_back": {
        "NBA": "7",
    },
    "opponent_games_back.next": {
        "NBA": "2",
    },
    "opponent_games_back.last": {
        "NBA": "5",
    },
    "team_ppg": {
        "NBA": "112.5",
    },
    "team_papg": {
        "NBA": "108.2",
    },
    "opponent_ppg": {
        "NBA": "109.8",
    },
    "opponent_ppg.next": {
        "NBA": "115.2",
    },
    "opponent_ppg.last": {
        "NBA": "110.8",
    },
    "opponent_papg": {
        "NBA": "110.5",
    },
    "opponent_papg.next": {
        "NBA": "112.8",
    },
    "opponent_papg.last": {
        "NBA": "108.5",
    },
    "home_team_ppg": {
        "NBA": "112.5",
    },
    "home_team_ppg.next": {
        "NBA": "115.2",
    },
    "home_team_ppg.last": {
        "NBA": "110.8",
    },
    "away_team_ppg": {
        "NBA": "109.8",
    },
    "away_team_ppg.next": {
        "NBA": "112.5",
    },
    "away_team_ppg.last": {
        "NBA": "112.5",
    },
    "season_type": {
        "NBA": "Regular Season",
    },
    "season_type.next": {
        "NBA": "Regular Season",
    },
    "season_type.last": {
        "NBA": "Regular Season",
    },
    "is_playoff": {
        "NBA": "",
    },
    "is_playoff.next": {
        "NBA": "",
    },
    "is_playoff.last": {
        "NBA": "",
    },
    "is_preseason": {
        "NBA": "",
    },
    "is_preseason.next": {
        "NBA": "",
    },
    "is_preseason.last": {
        "NBA": "",
    },
    "is_regular_season": {
        "NBA": "true",
    },
    "is_regular_season.next": {
        "NBA": "true",
    },
    "is_regular_season.last": {
        "NBA": "true",
    },
    "odds_spread": {
        "NBA": "-4.5",
    },
    "odds_spread.next": {
        "NBA": "+2.5",
    },
    "odds_moneyline": {
        "NBA": "-180",
    },
    "odds_moneyline.next": {
        "NBA": "+120",
    },
    "odds_over_under": {
        "NBA": "225.5",
    },
    "odds_over_under.next": {
        "NBA": "228.5",
    },
    "odds_provider": {
        "NBA": "ESPN BET",
    },
    "odds_provider.next": {
        "NBA": "ESPN BET",
    },
    "odds_details": {
        "NBA": "DET -4.5, O/U 225.5",
    },
    "odds_details.next": {
        "NBA": "DET +2.5, O/U 228.5",
    },
    "odds_opponent_moneyline": {
        "NBA": "+160",
    },
    "odds_opponent_moneyline.next": {
        "NBA": "-140",
    },
    "has_odds": {
        "NBA": "true",
    },
    "has_odds.next": {
        "NBA": "true",
    },
    "broadcast_network": {
        "NBA": "ESPN",
    },
    "broadcast_network.next": {
        "NBA": "TNT",
    },
    "broadcast_network.last": {
        "NBA": "Bally Sports Detroit",
    },
    "broadcast_simple": {
        "NBA": "ESPN, Bally Sports Detroit",
    },
    "broadcast_simple.next": {
        "NBA": "TNT",
    },
    "broadcast_simple.last": {
        "NBA": "Bally Sports Detroit",
    },
    "broadcast_national_network": {
        "NBA": "ESPN",
    },
    "broadcast_national_network.next": {
        "NBA": "TNT",
    },
    "broadcast_national_network.last": {
        "NBA": "",
    },
    "is_national_broadcast": {
        "NBA": "true",
    },
    "is_national_broadcast.next": {
        "NBA": "true",
    },
    "is_national_broadcast.last": {
        "NBA": "false",
    },
    "team_rank": {
        "NBA": "",
    },
    "team_rank_display": {
        "NBA": "",
    },
    "is_ranked": {
        "NBA": "",
    },
    "opponent_rank": {
        "NBA": "",
    },
    "opponent_rank.next": {
        "NBA": "",
    },
    "opponent_rank.last": {
        "NBA": "",
    },
    "opponent_rank_display": {
        "NBA": "",
    },
    "opponent_rank_display.next": {
        "NBA": "",
    },
    "opponent_rank_display.last": {
        "NBA": "",
    },
    "opponent_is_ranked": {
        "NBA": "",
    },
    "opponent_is_ranked.next": {
        "NBA": "",
    },
    "opponent_is_ranked.last": {
        "NBA": "",
    },
    "is_ranked_matchup": {
        "NBA": "",
    },
    "is_ranked_matchup.next": {
        "NBA": "",
    },
    "is_ranked_matchup.last": {
        "NBA": "",
    },
    "home_team_rank": {
        "NBA": "",
    },
    "home_team_rank.next": {
        "NBA": "",
    },
    "home_team_rank.last": {
        "NBA": "",
    },
    "away_team_rank": {
        "NBA": "",
    },
    "away_team_rank.next": {
        "NBA": "",
    },
    "away_team_rank.last": {
        "NBA": "",
    },
    "college_conference": {
        "NBA": "",
    },
    "college_conference_abbrev": {
        "NBA": "",
    },
    "opponent_college_conference": {
        "NBA": "",
    },
    "opponent_college_conference.next": {
        "NBA": "",
    },
    "opponent_college_conference.last": {
        "NBA": "",
    },
    "opponent_college_conference_abbrev": {
        "NBA": "",
    },
    "opponent_college_conference_abbrev.next": {
        "NBA": "",
    },
    "opponent_college_conference_abbrev.last": {
        "NBA": "",
    },
    "home_team_college_conference": {
        "NBA": "",
    },
    "home_team_college_conference.next": {
        "NBA": "",
    },
    "home_team_college_conference.last": {
        "NBA": "",
    },
    "home_team_college_conference_abbrev": {
        "NBA": "",
    },
    "home_team_college_conference_abbrev.next": {
        "NBA": "",
    },
    "home_team_college_conference_abbrev.last": {
        "NBA": "",
    },
    "away_team_college_conference": {
        "NBA": "",
    },
    "away_team_college_conference.next": {
        "NBA": "",
    },
    "away_team_college_conference.last": {
        "NBA": "",
    },
    "away_team_college_conference_abbrev": {
        "NBA": "",
    },
    "away_team_college_conference_abbrev.next": {
        "NBA": "",
    },
    "away_team_college_conference_abbrev.last": {
        "NBA": "",
    },
    "pro_conference": {
        "NBA": "Eastern",
    },
    "pro_conference_abbrev": {
        "NBA": "East",
    },
    "pro_division": {
        "NBA": "Central",
    },
    "opponent_pro_conference": {
        "NBA": "Eastern",
    },
    "opponent_pro_conference.next": {
        "NBA": "Eastern",
    },
    "opponent_pro_conference.last": {
        "NBA": "Eastern",
    },
    "opponent_pro_conference_abbrev": {
        "NBA": "East",
    },
    "opponent_pro_conference_abbrev.next": {
        "NBA": "East",
    },
    "opponent_pro_conference_abbrev.last": {
        "NBA": "East",
    },
    "opponent_pro_division": {
        "NBA": "Central",
    },
    "opponent_pro_division.next": {
        "NBA": "Central",
    },
    "opponent_pro_division.last": {
        "NBA": "Central",
    },
    "home_team_pro_conference": {
        "NBA": "Eastern",
    },
    "home_team_pro_conference.next": {
        "NBA": "Eastern",
    },
    "home_team_pro_conference.last": {
        "NBA": "Eastern",
    },
    "home_team_pro_conference_abbrev": {
        "NBA": "East",
    },
    "home_team_pro_conference_abbrev.next": {
        "NBA": "East",
    },
    "home_team_pro_conference_abbrev.last": {
        "NBA": "East",
    },
    "home_team_pro_division": {
        "NBA": "Central",
    },
    "home_team_pro_division.next": {
        "NBA": "Central",
    },
    "home_team_pro_division.last": {
        "NBA": "Central",
    },
    "away_team_pro_conference": {
        "NBA": "Eastern",
    },
    "away_team_pro_conference.next": {
        "NBA": "Eastern",
    },
    "away_team_pro_conference.last": {
        "NBA": "Eastern",
    },
    "away_team_pro_conference_abbrev": {
        "NBA": "East",
    },
    "away_team_pro_conference_abbrev.next": {
        "NBA": "East",
    },
    "away_team_pro_conference_abbrev.last": {
        "NBA": "East",
    },
    "away_team_pro_division": {
        "NBA": "Central",
    },
    "away_team_pro_division.next": {
        "NBA": "Central",
    },
    "away_team_pro_division.last": {
        "NBA": "Central",
    },
    "soccer_primary_league": {
        "NBA": "",
    },
    "soccer_primary_league_id": {
        "NBA": "",
    },
    "soccer_match_league": {
        "NBA": "",
    },
    "soccer_match_league.next": {
        "NBA": "",
    },
    "soccer_match_league.last": {
        "NBA": "",
    },
    "soccer_match_league_name": {
        "NBA": "",
    },
    "soccer_match_league_name.next": {
        "NBA": "",
    },
    "soccer_match_league_name.last": {
        "NBA": "",
    },
    "soccer_match_league_id": {
        "NBA": "",
    },
    "soccer_match_league_id.next": {
        "NBA": "",
    },
    "soccer_match_league_id.last": {
        "NBA": "",
    },
    "soccer_match_league_logo": {
        "NBA": "",
    },
    "soccer_match_league_logo.next": {
        "NBA": "",
    },
    "soccer_match_league_logo.last": {
        "NBA": "",
    },
    "fighter1": {
        "UFC": "Alex Volkanovski",
    },
    "fighter1_last": {
        "UFC": "Volkanovski",
    },
    "fighter2_last": {
        "UFC": "Lopes",
    },
    "fighter2": {
        "UFC": "Diego Lopes",
    },
    "event_number": {
        "UFC": "314",
    },
    "event_title": {
        "UFC": "UFC 314: Volkanovski vs Lopes",
    },
    "card_segment": {
        "UFC": "main_card",
    },
    "card_segment_display": {
        "UFC": "Main Card",
    },
    "main_card_time": {
        "UFC": "10:00 PM EST",
    },
    "prelims_time": {
        "UFC": "8:00 PM EST",
    },
    "early_prelims_time": {
        "UFC": "6:00 PM EST",
    },
    "bout_count": {
        "UFC": "14",
    },
    "fight_card": {
        "UFC": (
            "Alex Volkanovski vs Diego Lopes\n"
            "Merab Dvalishvili vs Umar Nurmagomedov\n"
            "Renato Moicano vs Beneil Dariush"
        ),
    },
    "main_card_bouts": {
        "UFC": "Alex Volkanovski vs Diego Lopes\nMerab Dvalishvili vs Umar Nurmagomedov",
    },
    "prelims_bouts": {
        "UFC": "Sean Brady vs Kelvin Gastelum\nChris Weidman vs Eryk Anders",
    },
    "early_prelims_bouts": {
        "UFC": "Mauricio Ruffy vs Jamie Mullarkey\nOtar Kentchadze vs Ismael Bonfim",
    },
    "fight_result": {
        "UFC": "TKO",
    },
    "fight_result_short": {
        "UFC": "TKO",
    },
    "finish_round": {
        "UFC": "2",
    },
    "finish_time": {
        "UFC": "4:31",
    },
    "finish_info": {
        "UFC": "R2 4:31",
    },
    "weight_class": {
        "UFC": "Featherweight",
    },
    "weight_class_short": {
        "UFC": "FW",
    },
    "fighter1_record": {
        "UFC": "28-4-0",
    },
    "fighter2_record": {
        "UFC": "27-8-0",
    },
    "judge_scores": {
        "UFC": "48-47",
    },
    "fight_summary": {
        "UFC": "TKO R2 4:31",
    },
    "feed_team": {
        "NBA": "Detroit Pistons",
    },
    "feed_team_short": {
        "NBA": "Pistons",
    },
    "feed_team_abbrev": {
        "NBA": "DET",
    },
    "feed_team_logo": {
        "NBA": "https://a.espncdn.com/i/teamlogos/nba/500/8.png",
    },
    "is_home_feed": {
        "NBA": "true",
    },
    "is_away_feed": {
        "NBA": "false",
    },
    "feed_home_away": {
        "NBA": "Home",
    },
    "broadcast_feed": {
        "NBA": "Home Team Feed",
    },
    "broadcast_feed_team": {
        "NBA": "Detroit Pistons",
    },
    # ==========================================================================
    # MOTORSPORTS - F1/NASCAR/IndyCar/MotoGP specific variables (event EPG only)
    # ==========================================================================
    "race_name": {
        "F1": "Monaco Grand Prix",
    },
    "circuit_name": {
        "F1": "Circuit de Monaco",
    },
    "session_name": {
        "F1": "Qualifying",
    },
    "session_type": {
        "F1": "qualifying",
    },
    "next_session_name": {
        "F1": "Race",
    },
    "next_session_time": {
        "F1": "1:00 PM EST",
    },
    "pole_position": {
        "F1": "Charles Leclerc",
    },
    "pole_team": {
        "F1": "Ferrari",
    },
    "grid": {
        "F1": (
            "1. Charles Leclerc (Ferrari)\n"
            "2. Max Verstappen (Red Bull Racing)\n"
            "3. Lando Norris (McLaren)"
        ),
    },
    "race_winner": {
        "F1": "Max Verstappen",
    },
    "podium_2": {
        "F1": "Charles Leclerc",
    },
    "podium_3": {
        "F1": "Lando Norris",
    },
    "podium": {
        "F1": "1. Max Verstappen, 2. Charles Leclerc, 3. Lando Norris",
    },
    "results": {
        "F1": (
            "1. Max Verstappen (Red Bull Racing)\n"
            "2. Charles Leclerc (Ferrari)\n"
            "3. Lando Norris (McLaren)"
        ),
    },
    "fastest_lap_driver": {
        "F1": "Max Verstappen",
    },
}


# Derive league_abbrev samples from each profile's league sample so the preview
# matches what the live extractor produces (same construction rule).
def _seed_league_abbrev_samples() -> None:

    abbrev = SAMPLE_DATA.setdefault("league_abbrev", {})
    for _profile, _league in SAMPLE_DATA.get("league", {}).items():
        abbrev.setdefault(_profile, construct_league_abbrev(_league))


_seed_league_abbrev_samples()


# --------------------------------------------------------------------------
# SHAPES — generic, FICTITIOUS sample identities (epic gruy .1/.2)
#
# Instead of previewing against a real league's identity (which looks wrong
# whenever the league guess is off), every league resolves to one of three
# generic *shapes* — "team", "combat", "racing" — each populated with funny,
# obviously-fake placeholder identities. A shape borrows all sport-agnostic
# values (dates, broadcast timeslots, generic flags, odds, statistics) from a
# base profile (NBA / UFC / F1) and overrides every variable that would
# otherwise read as a real league/sport identity.
#
# The shape data here LAYERS on top of the legacy 18 profiles, which are left
# intact for now (their removal is gruy.7). Direct profile names ("NBA", ...)
# still resolve exactly as before.
# --------------------------------------------------------------------------

# Each shape borrows non-identity values (dates/broadcast/flags) from this base.
_SHAPE_TO_BASE: dict[str, str] = {"team": "NBA", "combat": "UFC", "racing": "F1"}
_SHAPES = frozenset(_SHAPE_TO_BASE)


def resolve_shape(sport: str | None) -> str:
    """Map a league's sport onto one of the three generic sample shapes."""
    s = (sport or "").lower()
    if s in {"boxing", "mma"}:
        return "combat"
    if s == "racing":
        return "racing"
    return "team"


# Fictitious, in-theme identity/score/standings/venue overrides per shape.
# Anything NOT listed here falls through to the shape's base profile (so generic
# categories stay realistic). The team shape deliberately carries BOTH pro-style
# fields (conference/division) AND college-style fields (AP rank/ranking) so pro
# and college templates both preview fully — no single real league has both.
_SHAPE_OVERRIDES: dict[str, dict[str, str]] = {
    # ===================== TEAM (base NBA) =====================
    "team": {
        # --- focus team / opponent identity ---
        "team_name": "Flint Tropics",
        "team_short": "Tropics",
        "team_abbrev": "FLT",
        "opponent": "Greenwich Mean Time",
        "opponent.next": "Baltimore Pinchy Crabs",
        "opponent.last": "Denver Mile High Club",
        "opponent_short": "Mean Time",
        "opponent_short.next": "Pinchy Crabs",
        "opponent_short.last": "Mile High Club",
        "opponent_abbrev": "GMT",
        "opponent_abbrev.next": "CRB",
        "opponent_abbrev.last": "MHC",
        "matchup": "Greenwich Mean Time @ Flint Tropics",
        "matchup.next": "Flint Tropics @ Baltimore Pinchy Crabs",
        "matchup.last": "Flint Tropics @ Denver Mile High Club",
        "matchup_abbrev": "GMT @ FLT",
        "matchup_abbrev.next": "FLT @ CRB",
        "matchup_abbrev.last": "FLT @ MHC",
        "matchup_short": "Mean Time @ Tropics",
        "matchup_short.next": "Tropics @ Pinchy Crabs",
        "matchup_short.last": "Tropics @ Mile High Club",
        "team_name_the": "the Flint Tropics",
        "opponent_the": "the Greenwich Mean Time",
        "opponent_the.next": "the Baltimore Pinchy Crabs",
        "opponent_the.last": "the Denver Mile High Club",
        "home_team_the": "the Flint Tropics",
        "home_team_the.next": "the Baltimore Pinchy Crabs",
        "home_team_the.last": "the Denver Mile High Club",
        "away_team_the": "the Greenwich Mean Time",
        "away_team_the.next": "the Flint Tropics",
        "away_team_the.last": "the Flint Tropics",
        # --- league / sport identity ---
        "league": "Placeholder Premier League",
        "league_name": "Placeholder Premier League",
        "league_code": "ppl",
        "league_id": "ppl",
        "sport": "Placeholderball",
        "gracenote_category": "Placeholder Premier League",
        # --- home / away identity ---
        "home_team": "Flint Tropics",
        "home_team.next": "Baltimore Pinchy Crabs",
        "home_team.last": "Denver Mile High Club",
        "home_team_short": "Tropics",
        "home_team_short.next": "Pinchy Crabs",
        "home_team_short.last": "Mile High Club",
        "home_team_abbrev": "FLT",
        "home_team_abbrev.next": "CRB",
        "home_team_abbrev.last": "MHC",
        "away_team": "Greenwich Mean Time",
        "away_team.next": "Flint Tropics",
        "away_team.last": "Flint Tropics",
        "away_team_short": "Mean Time",
        "away_team_short.next": "Tropics",
        "away_team_short.last": "Tropics",
        "away_team_abbrev": "GMT",
        "away_team_abbrev.next": "FLT",
        "away_team_abbrev.last": "FLT",
        "feed_team": "Flint Tropics",
        "feed_team_short": "Tropics",
        "feed_team_abbrev": "FLT",
        "broadcast_feed_team": "Flint Tropics",
        # --- venue ---
        "venue": "The Coconut Coliseum",
        "venue.next": "The Crab Pot Pavilion",
        "venue.last": "The Thin Air Arena",
        "venue_city": "Flint",
        "venue_city.next": "Baltimore",
        "venue_city.last": "Denver",
        "venue_state": "ZZ",
        "venue_state.next": "ZZ",
        "venue_state.last": "ZZ",
        "venue_full": "The Coconut Coliseum, Flint, ZZ",
        "venue_full.next": "The Crab Pot Pavilion, Baltimore, ZZ",
        "venue_full.last": "The Thin Air Arena, Denver, ZZ",
        # --- summary / context copy ---
        "game_recap": "The Tropics rode a 40-point fourth quarter to bury the Mean Time.",
        "game_recap.last": "Flint edged the Mile High Club in double overtime last week.",
        "game_event_note": "Placeholder Premier League Finals - Game 4",
        "game_event_note.next": "Placeholder Premier League Finals - Game 5",
        "game_event_note.last": "Placeholder Premier League Semifinals - Game 7",
        "soccer_match_note": "Placeholder Premier League, Group Z",
        "game_preview": "The Tropics look to close out the series against the Mean Time.",
        "game_preview.next": "Flint visits the Pinchy Crabs to open the next round.",
        "generated_preview": (
            "The Greenwich Mean Time visit the Flint Tropics at The Coconut Coliseum. "
            "Greenwich enters at 34-28 after winning two of its last five games. "
            "Flint enters at 42-20 after winning four of its last five games."
        ),
        "generated_preview.next": (
            "The Flint Tropics visit the Pinchy Crabs at The Crab Pot Pavilion."
        ),
        "series_summary": "Tropics lead series 3-1",
        "home_last_five": "4-1",
        "away_last_five": "2-3",
        "last_five_summary": (
            "the Greenwich Mean Time have won 2 of their last five; "
            "the Flint Tropics have won 4 of their last five."
        ),
        "series_summary.last": "Tropics won series 4-2",
        # --- scores / outcome ---
        "team_score": "3",
        "team_score.last": "3",
        "opponent_score": "1",
        "opponent_score.last": "1",
        "home_team_score": "3",
        "home_team_score.last": "3",
        "away_team_score": "1",
        "away_team_score.last": "1",
        "score": "3-1",
        "score.last": "3-1",
        "final_score": "3-1",
        "final_score.last": "3-1",
        "winner": "Flint Tropics",
        "winner.last": "Flint Tropics",
        "winner_abbrev": "FLT",
        "winner_abbrev.last": "FLT",
        "loser": "Greenwich Mean Time",
        "loser.last": "Greenwich Mean Time",
        "loser_abbrev": "GMT",
        "loser_abbrev.last": "GMT",
        "event_result": "Flint Tropics 3 - Greenwich Mean Time 1",
        "event_result.last": "Flint Tropics 3 - Greenwich Mean Time 1",
        "event_result_abbrev": "FLT 3 - GMT 1",
        "event_result_abbrev.last": "FLT 3 - GMT 1",
        # --- records ---
        "team_record": "10-2",
        "team_wins": "10",
        "team_losses": "2",
        "opponent_record": "8-4",
        "opponent_record.next": "9-3",
        "opponent_record.last": "7-5",
        "opponent_wins": "8",
        "opponent_losses": "4",
        "home_record": "6-0",
        "away_record": "4-2",
        "home_team_record": "10-2",
        "home_team_record.next": "9-3",
        "home_team_record.last": "7-5",
        "away_team_record": "8-4",
        "away_team_record.next": "10-2",
        "away_team_record.last": "10-2",
        "home_team_seed": "1",
        "home_team_seed.next": "3",
        "home_team_seed.last": "5",
        "away_team_seed": "2",
        "away_team_seed.next": "1",
        "away_team_seed.last": "1",
        # --- streaks ---
        "streak": "W3",
        "win_streak": "3",
        "home_team_streak": "W3",
        "home_team_streak.next": "W4",
        "home_team_streak.last": "W2",
        "away_team_streak": "L1",
        "away_team_streak.next": "W3",
        "away_team_streak.last": "W3",
        "opponent_streak": "L1",
        "opponent_streak.next": "W2",
        "opponent_streak.last": "W1",
        # --- standings ---
        "playoff_seed": "1",
        "opponent_playoff_seed": "2",
        "opponent_playoff_seed.next": "1",
        "opponent_playoff_seed.last": "3",
        # --- conference / division (pro-style) ---
        "pro_conference": "Cryptid Conference",
        "pro_conference_abbrev": "Cryptid",
        "pro_division": "Folklore Division",
        "college_conference": "Tall Tales Conference",
        "college_conference_abbrev": "TTC",
        "opponent_pro_conference": "Cryptid Conference",
        "opponent_pro_conference.next": "Cryptid Conference",
        "opponent_pro_conference.last": "Cryptid Conference",
        "opponent_pro_conference_abbrev": "Cryptid",
        "opponent_pro_conference_abbrev.next": "Cryptid",
        "opponent_pro_conference_abbrev.last": "Cryptid",
        "opponent_pro_division": "Folklore Division",
        "opponent_pro_division.next": "Folklore Division",
        "opponent_pro_division.last": "Folklore Division",
        "opponent_college_conference": "Tall Tales Conference",
        "opponent_college_conference_abbrev": "TTC",
        "home_team_pro_conference": "Cryptid Conference",
        "home_team_pro_conference.next": "Cryptid Conference",
        "home_team_pro_conference.last": "Cryptid Conference",
        "home_team_pro_conference_abbrev": "Cryptid",
        "home_team_pro_conference_abbrev.next": "Cryptid",
        "home_team_pro_conference_abbrev.last": "Cryptid",
        "home_team_pro_division": "Folklore Division",
        "home_team_pro_division.next": "Folklore Division",
        "home_team_pro_division.last": "Folklore Division",
        "home_team_college_conference": "Tall Tales Conference",
        "home_team_college_conference_abbrev": "TTC",
        "away_team_pro_conference": "Cryptid Conference",
        "away_team_pro_conference.next": "Cryptid Conference",
        "away_team_pro_conference.last": "Cryptid Conference",
        "away_team_pro_conference_abbrev": "Cryptid",
        "away_team_pro_conference_abbrev.next": "Cryptid",
        "away_team_pro_conference_abbrev.last": "Cryptid",
        "away_team_pro_division": "Folklore Division",
        "away_team_pro_division.next": "Folklore Division",
        "away_team_pro_division.last": "Folklore Division",
        "away_team_college_conference": "Tall Tales Conference",
        "away_team_college_conference_abbrev": "TTC",
        # --- rankings (college/AP-style, so college templates render) ---
        "team_rank": "7",
        "team_rank_display": "#7",
        "is_ranked": "true",
        "opponent_rank": "14",
        "opponent_rank_display": "#14",
        "opponent_is_ranked": "true",
        "is_ranked_matchup": "true",
        "home_team_rank": "7",
        "home_team_rank.next": "5",
        "home_team_rank.last": "9",
        "away_team_rank": "14",
        "away_team_rank.next": "7",
        "away_team_rank.last": "7",
        "home_team_rank_display": "No. 7",
        "home_team_rank_display.next": "No. 5",
        "home_team_rank_display.last": "No. 9",
        "away_team_rank_display": "No. 14",
        "away_team_rank_display.next": "No. 7",
        "away_team_rank_display.last": "No. 7",
        # Composed rank+article names (#359). The .next opponent is deliberately
        # unranked so the preview demonstrates the graceful article fallback.
        "home_team_ranked_the": "the No. 7 Flint Tropics",
        "home_team_ranked_the.next": "the Baltimore Pinchy Crabs",
        "home_team_ranked_the.last": "the No. 9 Denver Mile High Club",
        "away_team_ranked_the": "the No. 14 Greenwich Mean Time",
        "away_team_ranked_the.next": "the No. 7 Flint Tropics",
        "away_team_ranked_the.last": "the No. 7 Flint Tropics",
        "team_name_ranked_the": "the No. 7 Flint Tropics",
        "opponent_ranked_the": "the No. 14 Greenwich Mean Time",
        "opponent_ranked_the.next": "the Baltimore Pinchy Crabs",
        "opponent_ranked_the.last": "the No. 9 Denver Mile High Club",
        # --- odds (de-NBA the spread/details so no DET sneaks in) ---
        "odds_details": "FLT -3.5, O/U 210.5",
        "odds_details.next": "FLT -1.5, O/U 214.5",
        # --- broadcast (generic so no real RSN/city leaks) ---
        "broadcast_network": "Sample Sports Network",
        "broadcast_network.next": "Sample Sports Network",
        "broadcast_network.last": "Sample Sports Network",
        "broadcast_simple": "Sample Sports Network",
        "broadcast_simple.next": "Sample Sports Network",
        "broadcast_simple.last": "Sample Sports Network",
    },
    # ================= COMBAT (base UFC) — Punch-Out!! roster =================
    "combat": {
        "fighter1": "Little Mac",
        "fighter2": "Super Macho Man",
        "fighter1_record": "12-0-0",
        "fighter2_record": "27-1-0",
        "event_title": "WVBA Title Night",
        "event_number": "1",
        "matchup": "Super Macho Man @ Little Mac",
        "matchup.next": "Bald Bull @ King Hippo",
        "matchup.last": "Glass Joe @ Soda Popinski",
        "matchup_combat": "Little Mac vs Super Macho Man",
        "weight_class": "Heavyweight",
        "weight_class_short": "HW",
        "fight_result": "KO",
        "fight_result_short": "KO",
        "fight_summary": "KO R2 0:45",
        "finish_round": "2",
        "finish_time": "0:45",
        "finish_info": "R2 0:45",
        "judge_scores": "—",
        "fight_card": (
            "Little Mac vs Super Macho Man\nMr. Sandman vs King Hippo\nBald Bull vs Soda Popinski"
        ),
        "main_card_bouts": ("Little Mac vs Super Macho Man\nMr. Sandman vs King Hippo"),
        "prelims_bouts": ("Great Tiger vs Don Flamenco\nPiston Honda vs Bald Bull"),
        "early_prelims_bouts": ("Glass Joe vs Von Kaiser\nKing Hippo vs Soda Popinski"),
        "bout_count": "11",
        "league": "World Video Boxing Association",
        "league_name": "World Video Boxing Association",
        "league_code": "wvba",
        "league_id": "wvba",
        "sport": "Boxing",
        "gracenote_category": "World Video Boxing Association",
        "venue": "Madison Square Pixels",
        "venue_city": "New York",
        "venue_state": "ZZ",
        "venue_full": "Madison Square Pixels, New York, ZZ",
        "exception_keyword": "PPV",
        "game_recap": "Little Mac dropped Super Macho Man in the third to take the title.",
        "game_event_note": "WVBA Title Night - Main Event",
        "game_preview": "Little Mac takes on Super Macho Man for the WVBA title tonight.",
    },
    # ============== RACING (base F1) — Ricky Bobby x Cars (Pixar) ==============
    "racing": {
        "race_name": "Piston Cup 500",
        "race_winner": "Ricky Bobby",
        "pole_position": "Lightning McQueen",
        "pole_team": "Rust-eze",
        "podium": "1. Ricky Bobby, 2. Lightning McQueen, 3. Cal Naughton Jr.",
        "podium_2": "Lightning McQueen",
        "podium_3": "Cal Naughton Jr.",
        "fastest_lap_driver": "Jackson Storm",
        "circuit_name": "Radiator Springs Speedway",
        "session_name": "Qualifying",
        "session_type": "qualifying",
        "next_session_name": "Race",
        "grid": (
            "1. Lightning McQueen (Rust-eze)\n"
            "2. Ricky Bobby (Wonder Bread)\n"
            "3. Jackson Storm (Team IGNTR)"
        ),
        "results": (
            "1. Ricky Bobby (Wonder Bread)\n"
            "2. Lightning McQueen (Rust-eze)\n"
            "3. Cal Naughton Jr. (Magic Man)"
        ),
        "league": "Piston Cup Series",
        "league_name": "Piston Cup Series",
        "league_code": "pcs",
        "league_id": "pcs",
        "sport": "Stock Car Racing",
        "gracenote_category": "Piston Cup Series",
        "venue": "Radiator Springs Speedway",
        "venue_city": "Radiator Springs",
        "venue_state": "ZZ",
        "venue_full": "Radiator Springs Speedway, Radiator Springs, ZZ",
        "exception_keyword": "4K",
        "game_recap": "Ricky Bobby held off Lightning McQueen on the final lap.",
        "game_event_note": "Piston Cup 500 - Championship Race",
        "game_preview": "Ricky Bobby starts second as the Piston Cup field rolls out.",
    },
}


def _seed_shape_league_abbrevs() -> None:
    """Derive each shape's ``league_abbrev`` from its ``league`` override.

    Mirrors the live extractor's construction rule (e.g. "Placeholder Premier
    League" -> "PPL"), keeping the shape preview consistent with real output.
    """

    for overrides in _SHAPE_OVERRIDES.values():
        league = overrides.get("league")
        if league:
            overrides.setdefault("league_abbrev", construct_league_abbrev(league))


_seed_shape_league_abbrevs()


# --------------------------------------------------------------------------
# League -> shape resolution. Every league previews against one of three
# generic, fictitious shapes ("team" / "combat" / "racing"), keyed off its
# sport (from the `leagues` record). Runtime-added custom leagues resolve for
# free since the mapping is intrinsic to the sport, not a hardcoded code list.
# --------------------------------------------------------------------------


def resolve_profile_for_league(
    league_code: str, sport: str | None = None, provider: str | None = None
) -> str:
    """Resolve the sample SHAPE a league should preview against.

    Every league previews against one of three generic, fictitious shapes
    ("team" / "combat" / "racing"), keyed off its sport. When the sport is
    unknown (no DB record), the name heuristic still resolves to a shape via the
    underlying sport guess, defaulting to "team".
    """
    if sport:
        return resolve_shape(sport)
    # Name-heuristic fallback: derive the sport, then the shape (defaults team).

    return resolve_shape(get_sport_from_league(league_code).lower())


# --------------------------------------------------------------------------
# Resolution precedence: curated SAMPLE_DATA -> inline registry sample ->
# category auto-default. Iterating the registry (not just SAMPLE_DATA keys)
# means a newly-registered variable is auto-adopted into previews with a
# sensible value even before anyone curates it here.
# --------------------------------------------------------------------------

# Categories whose curated values are sport-agnostic enough to borrow from
# another profile when the requested profile has no curated value (e.g. a
# generic date/broadcast). Identity/venue/score/etc. are NOT here because
# borrowing them would leak another sport's team or venue into the preview.
_GENERIC_FALLBACK_CATEGORIES = frozenset({"DATETIME", "BROADCAST", "PLAYOFFS"})


def _suffix_variants(suffix_rules_name: str) -> list[str]:
    """Suffix strings a variable supports, given its SuffixRules enum name."""
    if suffix_rules_name == "ALL":
        return ["", ".next", ".last"]
    if suffix_rules_name == "BASE_NEXT_ONLY":
        return ["", ".next"]
    if suffix_rules_name == "LAST_ONLY":
        return [".last"]
    return [""]  # BASE_ONLY


def _curated_value(full_name: str, profile: str, generic_ok: bool) -> str | None:
    """Curated sample for a variable+profile, or None if not curated.

    Tries exact name then the base name (without .next/.last), looking the
    profile (one of the shape bases NBA/UFC/F1) up directly. For generic
    categories, falls back to any profile's curated value so shared values
    (dates, broadcast networks) stay realistic without per-profile curation.
    """
    for name in (full_name, full_name.replace(".next", "").replace(".last", "")):
        sport_data = SAMPLE_DATA.get(name)
        if not sport_data:
            continue
        if profile in sport_data:
            return sport_data[profile]
        if generic_ok:
            return next(iter(sport_data.values()), None)
    return None


def _category_default(category_name: str, full_name: str) -> str:
    """Synthesize a deterministic, sport-neutral placeholder.

    Last-resort value so any registered variable always previews as something
    plausible and never leaks another sport's identity. Name heuristics take
    priority over the category fallback.
    """
    name = full_name.lower()

    # Name heuristics (cut across categories)
    if name.startswith(("is_", "has_")) or name.endswith(("_flag", "_bool")):
        return "true"
    if "time" in name:
        return "7:00 PM"
    if "date" in name:
        return "Saturday, January 18"
    if "pct" in name or "percentage" in name:
        return ".625"
    if name.endswith("_abbrev") or name.endswith("_abbreviation"):
        return "SAM"
    if name.endswith("_lower"):
        return "sample"
    if "logo" in name or "url" in name or "image" in name or name.endswith("_art"):
        return ""

    defaults = {
        "IDENTITY": "Sample Team",
        "DATETIME": "Saturday, January 18",
        "VENUE": "Sample Arena",
        "HOME_AWAY": "vs",
        "RECORDS": "20-10",
        "STREAKS": "W3",
        "SCORES": "3",
        "OUTCOME": "Win",
        "STANDINGS": "1",
        "STATISTICS": "100.0",
        "PLAYOFFS": "Regular Season",
        "ODDS": "-3.5",
        "BROADCAST": "ESPN",
        "RANKINGS": "10",
        "CONFERENCE": "Sample Conference",
        "SOCCER": "Premier League",
        "COMBAT": "Sample Fighter",
        "MOTORSPORTS": "Sample Grand Prix",
    }
    return defaults.get(category_name, "Sample")


def _shape_override(shape: str, full_name: str) -> str | None:
    """Funny per-shape override for a variable, or None if not overridden.

    Tries the exact name then the suffix-stripped base name, so a single base
    override (e.g. ``team_record``) covers ``.next``/``.last`` unless those are
    overridden explicitly.
    """
    overrides = _SHAPE_OVERRIDES.get(shape, {})
    base = full_name.replace(".next", "").replace(".last", "")
    for name in (full_name, base):
        if name in overrides:
            return overrides[name]
    return None


def _resolve_one(var_def, full_name: str, profile: str) -> str:
    """Resolve a single variable for a profile/shape via the precedence chain.

    For the three generic shapes, funny shape overrides win first; everything
    else borrows from the shape's base profile (NBA/UFC/F1) so sport-agnostic
    values (dates, broadcast, flags) stay realistic.
    """
    category_name = var_def.category.name
    generic_ok = category_name in _GENERIC_FALLBACK_CATEGORIES

    if profile in _SHAPES:
        override = _shape_override(profile, full_name)
        if override is not None:
            return override
        # Borrow non-identity values from the shape's base profile.
        profile = _SHAPE_TO_BASE[profile]

    curated = _curated_value(full_name, profile, generic_ok)
    if curated is not None:
        return curated
    if var_def.sample is not None:
        return var_def.sample
    return _category_default(category_name, full_name)


def get_sample_value(var_name: str, sport: str) -> str:
    """Get the sample value for a single variable and sport/profile.

    Resolves via curated SAMPLE_DATA -> inline registry sample -> category
    default, so unknown/new variables still return a plausible placeholder.
    """

    registry = get_registry()
    base_var = var_name.replace(".next", "").replace(".last", "")
    var_def = registry.get(base_var)
    if var_def is not None:
        return _resolve_one(var_def, var_name, sport)

    # Not a registered variable - fall back to raw curated lookup.
    if sport in _SHAPES:
        override = _shape_override(sport, var_name)
        if override is not None:
            return override
        sport = _SHAPE_TO_BASE[sport]
    value = _curated_value(var_name, sport, generic_ok=True)
    return value if value is not None else ""


def get_all_sample_data(sport: str) -> dict[str, str]:
    """Get all sample values for a given sport/profile.

    Iterates the variable registry (the source of truth for what variables
    exist) and resolves each via the precedence chain, so a newly-registered
    variable is auto-adopted with a sensible value. Any extra curated keys not
    backed by a registered variable are included too, for safety.

    Time-related variables are formatted according to user's display settings
    (12h/24h format, show/hide timezone).
    """

    registry = get_registry()
    result: dict[str, str] = {}

    for var_def in registry.all_variables():
        for suffix in _suffix_variants(var_def.suffix_rules.name):
            full_name = f"{var_def.name}{suffix}"
            result[full_name] = _resolve_one(var_def, full_name, sport)

    # Include any curated keys not backed by a registered variable (defensive).
    lookup = _SHAPE_TO_BASE.get(sport, sport)
    for name, sport_data in SAMPLE_DATA.items():
        if name not in result and sport_data:
            override = _shape_override(sport, name) if sport in _SHAPES else None
            if override is not None:
                result[name] = override
            else:
                result[name] = sport_data.get(lookup) or next(iter(sport_data.values()))

    # Post-process time-related variables to honor user settings
    result = _format_time_samples(result)
    return result


def get_all_sample_data_for_league(
    league_code: str, sport: str | None = None, provider: str | None = None
) -> dict[str, str]:
    """Get all sample values for a specific league.

    Resolves the league's shape from its sport/provider (passed from the league
    record) or, when those are absent, a name heuristic, then builds the full
    sample set for that shape. Shapes use fictitious identities by design (epic
    gruy), so a preview never looks like a real (and likely wrong-league) game.
    """
    shape = resolve_profile_for_league(league_code, sport, provider)
    return get_all_sample_data(shape)


# Variables that contain time values needing format conversion
_TIME_VARIABLES = {
    "game_time",
    "game_time.next",
    "game_time.last",
    # UFC segment times (base only, no .next/.last for event EPG)
    "main_card_time",
    "prelims_time",
    "early_prelims_time",
    # Motorsports next-session time (base only, no .next/.last for event EPG)
    "next_session_time",
}


def _format_time_samples(samples: dict[str, str]) -> dict[str, str]:
    """Format time sample values according to user display settings.

    Converts hardcoded time strings like "7:00 PM EST" to user's preferred format.
    """

    time_format = get_time_format()
    show_tz = get_show_timezone()
    tz_str = get_user_timezone_str()

    # Get timezone abbreviation for display
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_str)
        tz_abbrev = datetime.now(tz).strftime("%Z")
    except Exception:
        tz_abbrev = "EST"

    for var_name in _TIME_VARIABLES:
        if var_name not in samples:
            continue

        # Parse the existing time (format: "7:00 PM EST" or similar)
        original = samples[var_name]
        parsed = _parse_sample_time(original)
        if parsed is None:
            continue

        hour, minute = parsed

        # Format according to user settings
        if time_format == "24h":
            time_str = f"{hour:02d}:{minute:02d}"
        else:
            # 12-hour format
            display_hour = hour % 12
            if display_hour == 0:
                display_hour = 12
            am_pm = "AM" if hour < 12 else "PM"
            time_str = f"{display_hour}:{minute:02d} {am_pm}"

        # Add timezone if enabled
        if show_tz:
            time_str = f"{time_str} {tz_abbrev}"

        samples[var_name] = time_str

    return samples


def _parse_sample_time(time_str: str) -> tuple[int, int] | None:
    """Parse a sample time string like '7:00 PM EST' into (hour, minute) in 24h format."""
    import re

    # Match patterns like "7:00 PM", "19:00", "7:00 PM EST"
    match = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", time_str, re.IGNORECASE)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))
    am_pm = match.group(3)

    # Convert to 24h if AM/PM present
    if am_pm:
        am_pm = am_pm.upper()
        if am_pm == "PM" and hour < 12:
            hour += 12
        elif am_pm == "AM" and hour == 12:
            hour = 0

    return (hour, minute)
