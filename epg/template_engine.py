"""Template Variable Resolution Engine for Teamarr"""
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import random
import json
import re

from utils import to_pascal_case
from utils.time_format import format_time as fmt_time, get_time_settings

class TemplateEngine:
    """Resolves template variables in user-defined strings"""

    def __init__(self):
        pass

    def _determine_home_away(self, event: dict, our_team_id: str, use_name_fallback: bool = True) -> tuple[bool, dict, dict]:
        """
        Determine if our team is home/away and identify opponent

        Args:
            event: Event with home_team and away_team
            our_team_id: Our team's ESPN ID
            use_name_fallback: If True, also check team name as fallback (default True for template compatibility)

        Returns:
            (is_home, our_team, opponent) tuple
        """
        home_team = event.get('home_team', {})
        away_team = event.get('away_team', {})

        # Determine if our team is home
        is_home = str(home_team.get('id')) == str(our_team_id)

        # Apply name fallback if requested
        if use_name_fallback and not is_home:
            is_home = home_team.get('name', '').lower().replace(' ', '-') == our_team_id

        # Determine our team and opponent
        our_team = home_team if is_home else away_team
        opponent = away_team if is_home else home_team

        return (is_home, our_team, opponent)

    def resolve(self, template: str, context: Dict[str, Any]) -> str:
        """
        Resolve all template variables in a string with support for .next and .last suffixes

        Supports three variable formats:
        - {variable} - base variable from current game
        - {variable.next} - variable from next scheduled game
        - {variable.last} - variable from last completed game

        Args:
            template: String with {variable} or {variable.suffix} placeholders
            context: Dictionary containing all data needed for resolution

        Returns:
            String with all variables replaced with actual values
        """
        if not template:
            return ""

        # Build all variables (base + .next + .last)
        variables = self._build_variable_dict(context)

        # Use regex to find and replace all {variable} or {variable.suffix} patterns
        # Pattern matches: {variable_name} or {variable_name.next} or {variable_name.last}
        # Note: @ is allowed to support {vs_@} variable
        pattern = r'\{([a-z_][a-z0-9_@]*(?:\.[a-z]+)?)\}'

        def replace_variable(match):
            """Replace function for re.sub()"""
            var_name = match.group(1)  # e.g., "opponent" or "opponent.next"
            var_value = variables.get(var_name, '')  # Get value or empty string if not found
            return str(var_value)

        result = re.sub(pattern, replace_variable, template, flags=re.IGNORECASE)

        return result

    def _build_variables_from_game_context(
        self,
        game: dict,
        team_config: dict,
        team_stats: dict,
        opponent_stats: dict,
        h2h: dict,
        streaks: dict,
        head_coach: str,
        player_leaders: dict,
        epg_timezone: str,
        time_format_settings: dict = None
    ) -> Dict[str, str]:
        """
        Generate all 227 variables from a single game context

        This helper is called 3 times by _build_variable_dict() to generate:
        - Base variables (no suffix) - from current game
        - .next variables - from next scheduled game
        - .last variables - from last completed game

        Args:
            game: ESPN event data (or empty dict for filler programs)
            team_config: Team configuration and identity
            team_stats: Team season statistics
            opponent_stats: Opponent season statistics
            h2h: Head-to-head data (season series, previous matchup)
            streaks: Calculated streak data (home/away/last5/last10)
            head_coach: Head coach name
            player_leaders: Sport-specific player leaders
            epg_timezone: Timezone for date/time formatting

        Returns:
            Dictionary of 227 variable_name: value pairs
        """
        variables = {}

        # Ensure game is never None (use empty dict for filler programs)
        game = game or {}

        # =====================================================================
        # BASIC GAME INFORMATION
        # =====================================================================

        home_team = game.get('home_team', {})
        away_team = game.get('away_team', {})
        venue = game.get('venue', {})

        # Determine which team is "ours"
        our_team_id = team_config.get('espn_team_id', '')
        is_home, our_team, opponent = self._determine_home_away(game, our_team_id)

        # Use team_config as fallback when game data is not available
        variables['team_name'] = our_team.get('name', '') or team_config.get('team_name', '')
        variables['team_abbrev'] = our_team.get('abbrev', '') or team_config.get('team_abbrev', '')
        variables['team_abbrev_lower'] = variables['team_abbrev'].lower()

        # Team name in PascalCase for channel IDs
        variables['team_name_pascal'] = to_pascal_case(variables['team_name'])

        variables['opponent'] = opponent.get('name', '')
        variables['opponent_abbrev'] = opponent.get('abbrev', '')
        variables['opponent_abbrev_lower'] = variables['opponent_abbrev'].lower()
        variables['matchup_abbrev'] = f"{away_team.get('abbrev', '')} @ {home_team.get('abbrev', '')}"
        variables['matchup'] = f"{away_team.get('name', '')} @ {home_team.get('name', '')}"

        # Rankings (primarily for college sports - NFL/NBA don't have rankings)
        # Rank comes from team_stats/opponent_stats (fetched from team info API)
        # Game/schedule data doesn't include rank
        our_team_rank = team_stats.get('rank', 99)
        opponent_rank = opponent_stats.get('rank', 99)

        # Team rank variables (clean fallback - empty if unranked)
        is_team_ranked = our_team_rank <= 25
        variables['team_rank'] = f"#{our_team_rank}" if is_team_ranked else ''
        variables['is_ranked'] = 'true' if is_team_ranked else 'false'

        # Opponent rank variables (clean fallback - empty if unranked)
        is_opponent_ranked = opponent_rank <= 25
        variables['opponent_rank'] = f"#{opponent_rank}" if is_opponent_ranked else ''
        variables['opponent_is_ranked'] = 'true' if is_opponent_ranked else 'false'

        # Ranked matchup (legacy - both teams ranked)
        variables['is_ranked_matchup'] = 'true' if (is_team_ranked and is_opponent_ranked) else 'false'

        # Sport and League from team config
        # Map API sport codes to display names
        sport_display_names = {
            'basketball': 'Basketball',
            'football': 'Football',
            'hockey': 'Hockey',
            'baseball': 'Baseball',
            'soccer': 'Soccer'
        }
        sport_code = team_config.get('sport', '')
        variables['sport'] = sport_display_names.get(sport_code, sport_code.capitalize())
        variables['sport_lower'] = variables['sport'].lower()
        # Use league_name (e.g., "NBA") instead of league code (e.g., "nba")
        variables['league'] = team_config.get('league_name', '') or team_config.get('league', '').upper()
        variables['league_name'] = team_config.get('league_name', '')
        # League code - convert ESPN slug to friendly alias for display
        # e.g., 'womens-college-basketball' -> 'ncaaw'
        from database import get_league_alias, get_gracenote_category
        league_code = team_config.get('league', '').lower()
        variables['league_slug'] = league_code  # Always raw ESPN slug
        variables['league_id'] = get_league_alias(league_code)

        # Gracenote-compatible category (e.g., "College Basketball", "NFL Football")
        # Uses curated value from league_config, falls back to "{league_name} {Sport}"
        variables['gracenote_category'] = get_gracenote_category(
            league_code,
            variables['league_name'],
            sport_code
        )

        # Soccer Match League (for multi-league soccer teams)
        # These track which specific competition THIS GAME is from (changes per match)
        # Falls back to team's primary league if not set (non-soccer or single league)
        variables['soccer_match_league'] = game.get('_source_league_name', '') or variables['league_name']
        source_league = game.get('_source_league', '') or league_code
        variables['soccer_match_league_id'] = get_league_alias(source_league)
        variables['soccer_match_league_logo'] = game.get('_source_league_logo', '')
        # Primary league (constant - team's home league, doesn't change per game)
        # Equivalent to league_name/league_id but with soccer_* naming for consistency
        variables['soccer_primary_league'] = team_config.get('league_name', '') or team_config.get('league', '').upper()
        variables['soccer_primary_league_id'] = get_league_alias(league_code)

        # Conference/Division variables
        # - college_conference: Conference name for college sports (e.g., "Sun Belt", "ACC")
        # - college_conference_abbrev: Conference abbreviation for college sports (e.g., "big10", "acc")
        # - pro_conference: Conference name for pro sports (e.g., "National Football Conference", "Eastern Conference")
        # - pro_conference_abbrev: Conference abbreviation for pro sports (e.g., "NFC", "AFC")
        # - pro_division: Division name for pro sports (e.g., "NFC North", "Southeast Division")
        variables['college_conference'] = team_stats.get('conference_name', '') if 'college' in team_config.get('league', '').lower() else ''
        variables['college_conference_abbrev'] = team_stats.get('conference_abbrev', '') if 'college' in team_config.get('league', '').lower() else ''
        variables['pro_conference'] = team_stats.get('conference_name', '') if 'college' not in team_config.get('league', '').lower() else ''
        variables['pro_conference_abbrev'] = team_stats.get('conference_abbrev', '') if 'college' not in team_config.get('league', '').lower() else ''
        variables['pro_division'] = team_stats.get('division_name', '')

        # Opponent Conference/Division variables (same logic as team, but from opponent_stats)
        variables['opponent_college_conference'] = opponent_stats.get('conference_name', '') if 'college' in team_config.get('league', '').lower() else ''
        variables['opponent_college_conference_abbrev'] = opponent_stats.get('conference_abbrev', '') if 'college' in team_config.get('league', '').lower() else ''
        variables['opponent_pro_conference'] = opponent_stats.get('conference_name', '') if 'college' not in team_config.get('league', '').lower() else ''
        variables['opponent_pro_conference_abbrev'] = opponent_stats.get('conference_abbrev', '') if 'college' not in team_config.get('league', '').lower() else ''
        variables['opponent_pro_division'] = opponent_stats.get('division_name', '')

        # Home/Away Team Conference/Division variables (positional - based on which team is home/away)
        # Use team_stats or opponent_stats based on home/away position
        home_team_stats = team_stats if is_home else opponent_stats
        away_team_stats = opponent_stats if is_home else team_stats
        is_college = 'college' in team_config.get('league', '').lower()

        variables['home_team_college_conference'] = home_team_stats.get('conference_name', '') if is_college else ''
        variables['home_team_college_conference_abbrev'] = home_team_stats.get('conference_abbrev', '') if is_college else ''
        variables['home_team_pro_conference'] = home_team_stats.get('conference_name', '') if not is_college else ''
        variables['home_team_pro_conference_abbrev'] = home_team_stats.get('conference_abbrev', '') if not is_college else ''
        variables['home_team_pro_division'] = home_team_stats.get('division_name', '')

        variables['away_team_college_conference'] = away_team_stats.get('conference_name', '') if is_college else ''
        variables['away_team_college_conference_abbrev'] = away_team_stats.get('conference_abbrev', '') if is_college else ''
        variables['away_team_pro_conference'] = away_team_stats.get('conference_name', '') if not is_college else ''
        variables['away_team_pro_conference_abbrev'] = away_team_stats.get('conference_abbrev', '') if not is_college else ''
        variables['away_team_pro_division'] = away_team_stats.get('division_name', '')

        # Home/Away Team Rank, Seed, Streak (positional - based on which team is home/away)
        # Rank (college - show #X if ranked top 25, else empty)
        home_rank = home_team_stats.get('rank', 99)
        away_rank = away_team_stats.get('rank', 99)
        variables['home_team_rank'] = f"#{home_rank}" if home_rank <= 25 else ''
        variables['away_team_rank'] = f"#{away_rank}" if away_rank <= 25 else ''

        # Playoff seed (pro - show ordinal if seeded)
        home_seed = home_team_stats.get('playoff_seed', 0)
        away_seed = away_team_stats.get('playoff_seed', 0)
        variables['home_team_seed'] = self._format_rank(home_seed) if home_seed > 0 else ''
        variables['away_team_seed'] = self._format_rank(away_seed) if away_seed > 0 else ''

        # Streak (formatted as W3 or L2)
        home_streak = home_team_stats.get('streak_count', 0)
        away_streak = away_team_stats.get('streak_count', 0)
        if home_streak > 0:
            variables['home_team_streak'] = f"W{home_streak}"
        elif home_streak < 0:
            variables['home_team_streak'] = f"L{abs(home_streak)}"
        else:
            variables['home_team_streak'] = ''

        if away_streak > 0:
            variables['away_team_streak'] = f"W{away_streak}"
        elif away_streak < 0:
            variables['away_team_streak'] = f"L{abs(away_streak)}"
        else:
            variables['away_team_streak'] = ''

        # =====================================================================
        # DATE & TIME
        # =====================================================================

        game_date_str = game.get('date', '')
        if game_date_str:
            try:
                from zoneinfo import ZoneInfo
                game_datetime = datetime.fromisoformat(game_date_str.replace('Z', '+00:00'))

                # Convert to user's EPG timezone (from settings, not team timezone)
                local_datetime = game_datetime.astimezone(ZoneInfo(epg_timezone))

                variables['game_date'] = local_datetime.strftime('%A, %B %d, %Y')
                variables['game_date_short'] = local_datetime.strftime('%b %d')

                # Use user's time format preferences for game_time
                if time_format_settings:
                    tf, show_tz = get_time_settings(time_format_settings)
                    variables['game_time'] = fmt_time(local_datetime, tf, show_tz)
                else:
                    variables['game_time'] = local_datetime.strftime('%I:%M %p %Z')

                variables['game_day'] = local_datetime.strftime('%A')
                variables['game_day_short'] = local_datetime.strftime('%a')

                # Today vs Tonight based on 5pm cutoff in user's timezone
                variables['today_tonight'] = 'tonight' if local_datetime.hour >= 17 else 'today'
                variables['today_tonight_title'] = 'Tonight' if local_datetime.hour >= 17 else 'Today'

                # Time until game
                now = datetime.now(game_datetime.tzinfo)
                time_diff = game_datetime - now
                days_until = int(time_diff.total_seconds() / 86400)

                variables['days_until'] = str(max(0, days_until))

            except Exception:
                pass

        # =====================================================================
        # VENUE
        # =====================================================================
        # ESPN returns venue data in different structures:
        # - Current/past games: {name: "...", city: "...", state: "..."}
        # - Future games: {fullName: "...", address: {city: "...", state: "..."}}

        venue_name = venue.get('name') or venue.get('fullName', '')

        # Try top-level city/state first (current games), fall back to address (future games)
        venue_city = venue.get('city') or venue.get('address', {}).get('city', '')
        venue_state = venue.get('state') or venue.get('address', {}).get('state', '')

        variables['venue'] = venue_name
        variables['venue_city'] = venue_city
        variables['venue_state'] = venue_state

        # Build venue_full: "Stadium Name, City, ST"
        if venue_name and venue_city and venue_state:
            variables['venue_full'] = f"{venue_name}, {venue_city}, {venue_state}"
        elif venue_name and venue_city:
            variables['venue_full'] = f"{venue_name}, {venue_city}"
        else:
            variables['venue_full'] = venue_name

        # =====================================================================
        # HOME/AWAY CONTEXT
        # =====================================================================

        variables['is_home'] = 'true' if is_home else 'false'
        variables['is_away'] = 'false' if is_home else 'true'
        variables['home_away_text'] = 'at home' if is_home else 'on the road'
        variables['vs_at'] = 'vs' if is_home else 'at'
        variables['vs_@'] = 'vs' if is_home else '@'
        variables['home_team'] = home_team.get('name', '')
        variables['away_team'] = away_team.get('name', '')
        variables['home_team_pascal'] = to_pascal_case(variables['home_team'])
        variables['away_team_pascal'] = to_pascal_case(variables['away_team'])
        variables['home_team_abbrev'] = home_team.get('abbrev', '')
        variables['home_team_abbrev_lower'] = variables['home_team_abbrev'].lower()
        variables['away_team_abbrev'] = away_team.get('abbrev', '')
        variables['away_team_abbrev_lower'] = variables['away_team_abbrev'].lower()

        # =====================================================================
        # BROADCAST
        # =====================================================================

        broadcasts = game.get('broadcasts', [])
        # Filter out None values
        broadcasts = [b for b in broadcasts if b is not None]

        # =====================================================================
        # TEAM RECORDS (if enabled)
        # =====================================================================

        record = team_stats.get('record', {})

        # Always use opponent_stats for accurate records (fetched from opponent team endpoint)
        # Schedule data often has stale or missing opponent records
        opp_record = opponent_stats.get('record', {})


        # Fall back to schedule data only if opponent_stats is empty
        if not opp_record:
            opp_record = opponent.get('record', {})

        # Team record
        # Use ESPN's summary field directly - it has the correct format for each sport:
        # - US sports: W-L or W-L-T
        # - Soccer: W-D-L (wins-draws-losses)
        wins = record.get('wins', 0)
        losses = record.get('losses', 0)
        ties = record.get('ties', 0)

        # Use summary if available, otherwise reconstruct (fallback for edge cases)
        record_summary = record.get('summary', '')
        if record_summary and record_summary != '0-0':
            variables['team_record'] = record_summary
        elif ties > 0:
            variables['team_record'] = f"{wins}-{losses}-{ties}"
        else:
            variables['team_record'] = f"{wins}-{losses}"

        variables['team_wins'] = str(wins)
        variables['team_losses'] = str(losses)
        variables['team_ties'] = str(ties)
        variables['team_win_pct'] = f"{record.get('winPercent', 0):.3f}"

        # Opponent record
        opp_wins = opp_record.get('wins', 0)
        opp_losses = opp_record.get('losses', 0)
        opp_ties = opp_record.get('ties', 0)

        # Use summary if available, otherwise reconstruct
        opp_record_summary = opp_record.get('summary', '')
        if opp_record_summary and opp_record_summary != '0-0':
            variables['opponent_record'] = opp_record_summary
        elif opp_ties > 0:
            variables['opponent_record'] = f"{opp_wins}-{opp_losses}-{opp_ties}"
        else:
            variables['opponent_record'] = f"{opp_wins}-{opp_losses}"

        variables['opponent_wins'] = str(opp_wins)
        variables['opponent_losses'] = str(opp_losses)
        variables['opponent_ties'] = str(opp_ties)
        variables['opponent_win_pct'] = f"{opp_record.get('winPercent', 0):.3f}"

        # =====================================================================
        # STREAKS (if enabled)
        # =====================================================================

        # Get streak data from team_stats (fetched from team API)
        # ESPN returns positive integers for win streaks, negative for loss streaks
        streak_count_raw = team_stats.get('streak_count', 0)
        streak_count = abs(streak_count_raw)  # Always use positive value for display
        streak_type = 'W' if streak_count_raw > 0 else ('L' if streak_count_raw < 0 else '')

        # Base streak variables
        # streak: Absolute value for display (e.g., "7" for 7-game losing streak)
        # streak_raw: Signed value for conditional logic (e.g., "-7" for losses, "7" for wins)
        variables['streak'] = str(streak_count)
        variables['streak_raw'] = str(streak_count_raw)

        # Home/Away Streaks (calculated in orchestrator, passed as parameter)
        variables['home_streak'] = streaks.get('home_streak', '')
        variables['away_streak'] = streaks.get('away_streak', '')

        # =====================================================================
        # HEAD-TO-HEAD (if enabled)
        # =====================================================================

        season_series = h2h.get('season_series', {})
        variables['season_series'] = f"{season_series.get('team_wins', 0)}-{season_series.get('opponent_wins', 0)}"
        variables['season_series_team_wins'] = str(season_series.get('team_wins', 0))
        variables['season_series_opponent_wins'] = str(season_series.get('opponent_wins', 0))

        team_series_wins = season_series.get('team_wins', 0)
        opp_series_wins = season_series.get('opponent_wins', 0)
        if team_series_wins > opp_series_wins:
            variables['season_series_leader'] = variables['team_name']
        elif opp_series_wins > team_series_wins:
            variables['season_series_leader'] = variables['opponent']
        else:
            variables['season_series_leader'] = 'tied'

        # Rematch variables (previous matchup against same opponent)
        previous = h2h.get('previous_game', {})
        variables['rematch_date'] = previous.get('date', '')
        variables['rematch_result'] = previous.get('result', '')
        variables['rematch_score'] = previous.get('score', '')
        variables['rematch_score_abbrev'] = previous.get('score_abbrev', '')
        variables['rematch_venue'] = previous.get('venue', '')
        variables['rematch_city'] = previous.get('venue_city', '')
        variables['rematch_days_since'] = str(previous.get('days_since', 0))
        variables['rematch_season_series'] = f"{season_series.get('team_wins', 0)}-{season_series.get('opponent_wins', 0)}"

        # =====================================================================
        # PLAYOFFS (simple flags only)
        # =====================================================================

        # Check if this is a playoff game (simple boolean flag)
        is_playoff = game.get('season', {}).get('type') == 3 if game else False

        variables['is_playoff'] = 'true' if is_playoff else 'false'
        variables['is_regular_season'] = 'true' if not is_playoff else 'false'

        # =====================================================================
        # STANDINGS (if enabled)
        # =====================================================================

        # Get standings data from team_stats API
        playoff_seed = team_stats.get('playoff_seed', 0)
        games_back = team_stats.get('games_back', 0.0)

        variables['playoff_seed'] = self._format_rank(playoff_seed)
        variables['games_back'] = f"{games_back:.1f}" if games_back > 0 else "0.0"


        # =====================================================================
        # RECENT PERFORMANCE (if enabled)
        # =====================================================================

        # Home/away records from team_stats API
        home_record = team_stats.get('home_record', '0-0')
        away_record = team_stats.get('away_record', '0-0')

        variables['home_record'] = home_record
        variables['away_record'] = away_record

        # Calculate win percentages from records
        def calc_win_pct(record_str):
            """Calculate win percentage from 'W-L' string"""
            if not record_str or record_str == '0-0':
                return '.000'
            try:
                parts = record_str.split('-')
                if len(parts) >= 2:
                    wins = int(parts[0])
                    losses = int(parts[1])
                    total = wins + losses
                    if total > 0:
                        return f"{wins / total:.3f}"
            except:
                pass
            return '.000'

        variables['home_win_pct'] = calc_win_pct(home_record)
        variables['away_win_pct'] = calc_win_pct(away_record)

        # Home team record and away team record (based on matchup position)
        # For completed games: use record from game data (always populated)
        # For future games: fall back to opponent_stats (fetched separately)
        home_team_record_from_game = home_team.get('record', {}).get('displayValue', '')
        away_team_record_from_game = away_team.get('record', {}).get('displayValue', '')

        # Determine which team is opponent to get their stats
        opponent_record = opponent_stats.get('record', {}).get('summary', '0-0') if opponent_stats else '0-0'

        # Assign records based on home/away position
        if is_home:
            # We are home team
            variables['home_team_record'] = variables.get('team_record', '0-0')
            # Opponent is away - use game data if available (completed), else opponent_stats (future)
            variables['away_team_record'] = away_team_record_from_game or opponent_record
        else:
            # We are away team
            variables['away_team_record'] = variables.get('team_record', '0-0')
            # Opponent is home - use game data if available (completed), else opponent_stats (future)
            variables['home_team_record'] = home_team_record_from_game or opponent_record

        # Last 5/10 and recent form from streaks parameter
        variables['last_5_record'] = streaks.get('last_5_record', '')
        variables['last_10_record'] = streaks.get('last_10_record', '')

        # =====================================================================
        # STATISTICS (if enabled)
        # =====================================================================

        # Get PPG/PAPG from team_stats API data
        variables['team_ppg'] = f"{team_stats.get('ppg', 0):.1f}"
        variables['team_papg'] = f"{team_stats.get('papg', 0):.1f}"

        # Get opponent PPG/PAPG from opponent_stats API data
        variables['opponent_ppg'] = f"{opponent_stats.get('ppg', 0):.1f}"
        variables['opponent_papg'] = f"{opponent_stats.get('papg', 0):.1f}"

        # =====================================================================
        # ROSTERS AND PLAYER STATS
        # =====================================================================

        # Head Coach (all sports)
        variables['head_coach'] = head_coach

        # Player Leaders (sport-specific, from parameter)
        # player_leaders is passed as a parameter

        # Set all possible player leader variables to empty by default
        # Basketball variables (game leaders - .last only)
        for var in ['basketball_scoring_leader_name', 'basketball_scoring_leader_points']:
            variables[var] = player_leaders.get(var, '')

        # Football variables (game leaders - .last only)
        for var in ['football_passing_leader_name', 'football_passing_leader_stats',
                    'football_rushing_leader_name', 'football_rushing_leader_stats',
                    'football_receiving_leader_name', 'football_receiving_leader_stats']:
            variables[var] = player_leaders.get(var, '')

        # =====================================================================
        # GAME STATUS (for live games)
        # =====================================================================

        status = game.get('status', {})

        # Live/Final scores
        # Handle score being either a number or dict (from different API responses)
        our_score_raw = our_team.get('score', 0) or 0
        opp_score_raw = opponent.get('score', 0) or 0

        # Extract numeric score if it's a dict
        if isinstance(our_score_raw, dict):
            our_score = int(our_score_raw.get('value', 0) or our_score_raw.get('displayValue', '0'))
        else:
            our_score = int(our_score_raw) if our_score_raw else 0

        if isinstance(opp_score_raw, dict):
            opp_score = int(opp_score_raw.get('value', 0) or opp_score_raw.get('displayValue', '0'))
        else:
            opp_score = int(opp_score_raw) if opp_score_raw else 0

        variables['team_score'] = str(our_score)
        variables['opponent_score'] = str(opp_score)
        variables['score'] = f"{our_score}-{opp_score}"
        score_diff = our_score - opp_score
        variables['score_diff'] = f"+{score_diff}" if score_diff > 0 else str(score_diff)

        # final_score - only show if game is actually final, otherwise empty (gracefully disappears)
        is_final = status.get('name', '') in ['STATUS_FINAL', 'Final']
        variables['final_score'] = f"{our_score}-{opp_score}" if (is_final and our_score > 0 and opp_score > 0) else ''

        # =====================================================================
        # ATTENDANCE
        # =====================================================================

        # Get attendance from competition data
        competition = game.get('competitions', [{}])[0] if game.get('competitions') else {}
        attendance = competition.get('attendance', 0)

        variables['attendance'] = f"{attendance:,}" if attendance else ''

        # =====================================================================
        # SCORE & OUTCOME (for postgame filler content)
        # =====================================================================

        # Determine if game is final
        is_final = status.get('name', '') in ['STATUS_FINAL', 'Final']

        if is_final and our_score > 0 and opp_score > 0:
            # Score differential
            abs_diff = abs(score_diff)
            variables['score_differential'] = str(abs_diff)
            variables['score_differential_text'] = f"by {abs_diff} point{'s' if abs_diff != 1 else ''}"

            # Win/Loss result
            if our_score > opp_score:
                variables['result'] = 'win'
                variables['result_text'] = 'defeated'
                variables['result_verb'] = 'beat'
            else:
                variables['result'] = 'loss'
                variables['result_text'] = 'lost to'
                variables['result_verb'] = 'fell to'

            # Check for overtime
            periods = status.get('period', 0) or 0
            # NBA/NHL = 4 periods (regulation), NFL = 4 quarters, MLB = 9 innings
            overtime_thresholds = {
                'basketball': 4,
                'hockey': 3,
                'football': 4,
                'baseball': 9
            }
            overtime_threshold = overtime_thresholds.get(sport_code, 4)

            if periods > overtime_threshold:
                variables['overtime_text'] = 'in overtime'
            else:
                variables['overtime_text'] = ''

        else:
            # Game not final - set empty defaults
            variables['score_differential'] = '0'
            variables['score_differential_text'] = ''
            variables['result'] = ''
            variables['result_text'] = ''
            variables['result_verb'] = ''
            variables['overtime_text'] = ''

        # =====================================================================
        # SEASON CONTEXT
        # =====================================================================

        season = game.get('season', {})
        season_type_id = season.get('type', 2)  # 1=preseason, 2=regular, 3=postseason

        variables['season_type'] = season.get('type', 'regular')
        variables['is_preseason'] = 'true' if season_type_id == 1 else 'false'

        # =====================================================================
        # ODDS & BETTING
        # =====================================================================

        # Get odds data from competition
        odds_list = competition.get('odds', [])
        if odds_list and len(odds_list) > 0:
            odds = odds_list[0]  # Use first odds provider (usually ESPN BET)
            if not odds:
                odds = {}  # Handle None entries in odds list

            # Provider info
            provider = odds.get('provider', {}) or {}
            variables['odds_provider'] = provider.get('name', '')

            # Over/Under
            over_under = odds.get('overUnder', 0)
            variables['odds_over_under'] = str(over_under) if over_under else ''

            # Spread (absolute value)
            spread = abs(odds.get('spread', 0))
            variables['odds_spread'] = str(spread) if spread else ''

            # Details (e.g., "HOU -1.5")
            variables['odds_details'] = odds.get('details', '')

            # Determine which team is home/away
            home_team_obj = home_team if game else {}
            away_team_obj = away_team if game else {}

            our_team_id_str = str(team_config.get('espn_team_id', ''))
            is_home_game = str(home_team_obj.get('id', '')) == our_team_id_str

            # Get the appropriate team odds
            if is_home_game:
                our_odds = odds.get('homeTeamOdds', {})
                opp_odds = odds.get('awayTeamOdds', {})
            else:
                our_odds = odds.get('awayTeamOdds', {})
                opp_odds = odds.get('homeTeamOdds', {})

            # Money line
            our_moneyline = our_odds.get('moneyLine', 0)
            opp_moneyline = opp_odds.get('moneyLine', 0)
            variables['odds_moneyline'] = str(our_moneyline) if our_moneyline else ''
            variables['odds_opponent_moneyline'] = str(opp_moneyline) if opp_moneyline else ''

            # Spread odds
            opp_spread_odds = opp_odds.get('spreadOdds', 0)
            variables['odds_opponent_spread'] = str(opp_spread_odds) if opp_spread_odds else ''

        else:
            # No odds available - set defaults
            variables['odds_provider'] = ''
            variables['odds_over_under'] = ''
            variables['odds_spread'] = ''
            variables['odds_details'] = ''
            variables['odds_moneyline'] = ''
            variables['odds_opponent_moneyline'] = ''
            variables['odds_opponent_spread'] = ''

        # =====================================================================
        # BROADCAST INFORMATION
        # =====================================================================

        broadcasts = competition.get('broadcasts', [])

        # Determine if team is home or away
        home_team_obj = home_team if game else {}
        our_team_id_str = str(team_config.get('espn_team_id', ''))
        is_home_game = str(home_team_obj.get('id', '')) == our_team_id_str

        # Get broadcast variables
        variables['broadcast_simple'] = self._get_broadcast_simple(broadcasts, is_home_game)
        variables['broadcast_network'] = self._get_broadcast_network(broadcasts, is_home_game)
        variables['broadcast_national_network'] = self._get_broadcast_national_network(broadcasts)
        variables['is_national_broadcast'] = self._is_national_broadcast(broadcasts)

        return variables

    def _build_variable_dict(self, context: Dict[str, Any]) -> Dict[str, str]:
        """
        Build complete dictionary with base, .next, and .last variables

        This method calls _build_variables_from_game_context() three times:
        1. For current game (no suffix) - 227 base variables
        2. For next game (.next suffix) - 227 variables with .next suffix
        3. For last game (.last suffix) - 227 variables with .last suffix

        Total: 681 variables available in templates

        Args:
            context: Full context dictionary from orchestrator containing:
                - game: Current game event (None for filler programs)
                - next_game: Next scheduled game context
                - last_game: Last completed game context
                - team_config, team_stats, opponent_stats, h2h, streaks, etc.

        Returns:
            Dictionary of all variables (base + suffixed)
        """
        # Variables that should ONLY have .last suffix (no base, no .next)
        LAST_ONLY_VARS = {
            'final_score', 'opponent_score', 'overtime_text', 'result', 'result_text', 'result_verb',
            'score', 'score_diff', 'score_differential', 'score_differential_text',
            'team_score'
        }

        # Variables that should have BASE + .next ONLY (no .last)
        BASE_NEXT_ONLY_VARS = {
            'odds_details', 'odds_provider', 'odds_moneyline', 'odds_opponent_moneyline',
            'odds_opponent_spread', 'odds_over_under', 'odds_spread'
        }

        # Variables that should be BASE ONLY (no .next, no .last)
        BASE_ONLY_VARS = {
            'away_record', 'away_streak', 'away_win_pct', 'games_back', 'head_coach',
            'home_record', 'home_streak', 'home_win_pct', 'is_national_broadcast', 'is_playoff',
            'is_preseason', 'is_ranked', 'is_ranked_matchup', 'is_regular_season', 'last_10_record',
            'last_5_record', 'league', 'league_id', 'league_name', 'gracenote_category', 'opponent_is_ranked', 'playoff_seed',
            'pro_conference', 'pro_conference_abbrev', 'pro_division',
            'soccer_primary_league', 'soccer_primary_league_id', 'sport',
            'streak', 'team_abbrev', 'team_losses', 'team_name', 'team_name_pascal', 'team_papg', 'team_ppg',
            'team_rank', 'team_record', 'team_ties', 'team_win_pct', 'team_wins'
        }

        all_variables = {}

        # Extract common context components
        team_config = context.get('team_config', {})
        team_stats = context.get('team_stats', {})
        epg_timezone = context.get('epg_timezone', 'America/Detroit')
        time_format_settings = context.get('time_format_settings', {})

        # =====================================================================
        # BUILD CURRENT GAME VARIABLES (no suffix)
        # =====================================================================

        current_game = context.get('game', {}) or {}  # Handle None for fillers
        current_opponent_stats = context.get('opponent_stats', {})
        current_h2h = context.get('h2h', {})
        current_streaks = context.get('streaks', {})
        current_head_coach = context.get('head_coach', '')
        current_player_leaders = context.get('player_leaders', {})

        current_vars = self._build_variables_from_game_context(
            game=current_game,
            team_config=team_config,
            team_stats=team_stats,
            opponent_stats=current_opponent_stats,
            h2h=current_h2h,
            streaks=current_streaks,
            head_coach=current_head_coach,
            player_leaders=current_player_leaders,
            epg_timezone=epg_timezone,
            time_format_settings=time_format_settings
        )

        # Add base variables (no suffix), excluding LAST_ONLY_VARS
        for key, value in current_vars.items():
            if key not in LAST_ONLY_VARS:
                all_variables[key] = value

        # =====================================================================
        # BUILD NEXT GAME VARIABLES (.next suffix)
        # =====================================================================

        next_game_ctx = context.get('next_game', {})
        if next_game_ctx and next_game_ctx.get('game'):
            next_vars = self._build_variables_from_game_context(
                game=next_game_ctx.get('game', {}),
                team_config=team_config,
                team_stats=team_stats,
                opponent_stats=next_game_ctx.get('opponent_stats', {}),
                h2h=next_game_ctx.get('h2h', {}),
                streaks=next_game_ctx.get('streaks', {}),
                head_coach=next_game_ctx.get('head_coach', ''),
                player_leaders=next_game_ctx.get('player_leaders', {}),
                epg_timezone=epg_timezone,
                time_format_settings=time_format_settings
            )

            # Add .next suffix only to allowed variables
            for key, value in next_vars.items():
                # Skip if BASE_ONLY or LAST_ONLY
                if key not in BASE_ONLY_VARS and key not in LAST_ONLY_VARS:
                    all_variables[f"{key}.next"] = value

        # =====================================================================
        # BUILD LAST GAME VARIABLES (.last suffix)
        # =====================================================================

        last_game_ctx = context.get('last_game', {})
        if last_game_ctx and last_game_ctx.get('game'):
            last_vars = self._build_variables_from_game_context(
                game=last_game_ctx.get('game', {}),
                team_config=team_config,
                team_stats=team_stats,
                opponent_stats=last_game_ctx.get('opponent_stats', {}),
                h2h=last_game_ctx.get('h2h', {}),
                streaks=last_game_ctx.get('streaks', {}),
                head_coach=last_game_ctx.get('head_coach', ''),
                player_leaders=last_game_ctx.get('player_leaders', {}),
                epg_timezone=epg_timezone,
                time_format_settings=time_format_settings
            )

            # Add .last suffix only to allowed variables
            for key, value in last_vars.items():
                # Skip if BASE_ONLY or BASE_NEXT_ONLY
                if key not in BASE_ONLY_VARS and key not in BASE_NEXT_ONLY_VARS:
                    all_variables[f"{key}.last"] = value

        return all_variables

    def _normalize_broadcast(self, broadcast) -> dict:
        """
        Normalize broadcast to standard dict format.
        Handles multiple ESPN API broadcast formats:
        - String: "ESPN", "ABC", etc. (NCAAM, some other sports)
        - Dict with string market: {"market": "national", "names": ["ESPN"]} (scoreboard format)
        - Dict with dict market: {"market": {"type": "National"}, "media": {...}} (schedule format)

        Returns:
            Standardized dict with keys: type, market, media
        """
        # Case 1: String broadcast (e.g., "ESPN")
        if isinstance(broadcast, str):
            return {
                'type': {'id': '1', 'shortName': 'TV'},
                'market': {'type': 'National'},  # Assume national for string broadcasts
                'media': {'shortName': broadcast}
            }

        # Case 2: Already a dict
        if isinstance(broadcast, dict):
            # Case 2a: Scoreboard format with string market
            if 'market' in broadcast and isinstance(broadcast['market'], str):
                market_str = broadcast['market']
                market_type = market_str.capitalize()  # "national" -> "National"
                network_name = broadcast.get('names', [None])[0]

                return {
                    'type': {'id': '1', 'shortName': 'TV'},
                    'market': {'type': market_type},
                    'media': {'shortName': network_name} if network_name else {}
                }

            # Case 2b: Already in schedule format (dict market)
            return broadcast

        # Case 3: Unknown format - return empty dict
        return {}

    def _get_broadcast_simple(self, broadcasts: List[Dict], team_is_home: bool) -> str:
        """
        Get all broadcast networks in priority order.
        Returns comma-separated list of networks.
        Filters out radio and subscription packages (League Pass, etc.)
        """
        if not broadcasts:
            return ""

        # Packages to skip (noise)
        SKIP_PACKAGES = [
            'NBA League Pass',
            'NHL.TV',
            'MLB.TV',
            'MLS Season Pass'
        ]

        # Normalize all broadcasts to standard format
        normalized = [self._normalize_broadcast(b) for b in broadcasts]

        # Filter out radio broadcasts, subscription packages, and empty dicts
        usable = [b for b in normalized
                  if b and  # Skip empty dicts
                     b.get('type', {}).get('shortName', '').upper() != 'RADIO' and
                     b.get('media', {}).get('shortName', '') not in SKIP_PACKAGES]

        if not usable:
            return ""

        # Separate by type and market
        national_tv = []
        national_streaming = []
        team_tv = []
        team_streaming = []
        other_tv = []
        other_streaming = []

        team_market = "Home" if team_is_home else "Away"

        for b in usable:
            network = b.get('media', {}).get('shortName', '')
            if not network:
                continue

            market = b.get('market', {}).get('type')
            btype = b.get('type', {}).get('shortName', '').upper()

            # Categorize by market and type
            if market == 'National':
                if btype == 'TV':
                    national_tv.append(network)
                else:
                    national_streaming.append(network)
            elif market == team_market:
                if btype == 'TV':
                    team_tv.append(network)
                else:
                    team_streaming.append(network)
            else:
                # null market or other (EPL, international)
                if btype == 'TV':
                    other_tv.append(network)
                else:
                    other_streaming.append(network)

        # Collect all networks in priority order
        all_networks = []

        # Priority 1: National TV
        all_networks.extend(national_tv)
        # Priority 2: Team TV
        all_networks.extend(team_tv)
        # Priority 3: National streaming
        all_networks.extend(national_streaming)
        # Priority 4: Team streaming
        all_networks.extend(team_streaming)
        # Priority 5: Other TV (EPL, MLS, etc)
        all_networks.extend(other_tv)
        # Priority 6: Other streaming
        all_networks.extend(other_streaming)

        # Remove duplicates while preserving order
        seen = set()
        unique_networks = []
        for network in all_networks:
            if network not in seen:
                seen.add(network)
                unique_networks.append(network)

        return ", ".join(unique_networks) if unique_networks else ""

    def _get_broadcast_network(self, broadcasts: List[Dict], team_is_home: bool) -> str:
        """
        Get team's primary broadcast network (single network only).
        Returns the most relevant network based on priority.
        """
        if not broadcasts:
            return ""

        SKIP_PACKAGES = [
            'NBA League Pass',
            'NHL.TV',
            'MLB.TV',
            'MLS Season Pass'
        ]

        # Normalize all broadcasts to standard format
        normalized = [self._normalize_broadcast(b) for b in broadcasts]

        # Filter out radio, subscription packages, and empty dicts
        usable = [b for b in normalized
                  if b and  # Skip empty dicts
                     b.get('type', {}).get('shortName', '').upper() != 'RADIO' and
                     b.get('media', {}).get('shortName', '') not in SKIP_PACKAGES]

        if not usable:
            return ""

        team_market = "Home" if team_is_home else "Away"

        # Priority 1: National TV
        for b in usable:
            if b.get('market', {}).get('type') == 'National' and \
               b.get('type', {}).get('shortName', '').upper() == 'TV':
                return b.get('media', {}).get('shortName', '')

        # Priority 2: Team regional TV
        for b in usable:
            if b.get('market', {}).get('type') == team_market and \
               b.get('type', {}).get('shortName', '').upper() == 'TV':
                return b.get('media', {}).get('shortName', '')

        # Priority 3: National streaming
        for b in usable:
            if b.get('market', {}).get('type') == 'National' and \
               b.get('type', {}).get('shortName', '').upper() in ['STREAMING', 'SUBSCRIPTION PACKAGE']:
                return b.get('media', {}).get('shortName', '')

        # Priority 4: Team streaming
        for b in usable:
            if b.get('market', {}).get('type') == team_market and \
               b.get('type', {}).get('shortName', '').upper() in ['STREAMING', 'SUBSCRIPTION PACKAGE']:
                return b.get('media', {}).get('shortName', '')

        # Priority 5: Any TV (null market - EPL, MLS)
        for b in usable:
            if b.get('type', {}).get('shortName', '').upper() == 'TV':
                return b.get('media', {}).get('shortName', '')

        # Priority 6: Any streaming
        for b in usable:
            if b.get('type', {}).get('shortName', '').upper() in ['STREAMING', 'SUBSCRIPTION PACKAGE']:
                return b.get('media', {}).get('shortName', '')

        return ""

    def _get_broadcast_national_network(self, broadcasts: List[Dict]) -> str:
        """
        Get national broadcast network(s) only.
        Returns comma-separated list of networks with market type = "National".
        """
        if not broadcasts:
            return ""

        SKIP_PACKAGES = [
            'NBA League Pass',
            'NHL.TV',
            'MLB.TV',
            'MLS Season Pass'
        ]

        # Normalize all broadcasts to standard format
        normalized = [self._normalize_broadcast(b) for b in broadcasts]

        # Filter to National market + TV/Streaming only (no radio, no packages)
        national = [b for b in normalized
                    if b and  # Skip empty dicts
                       b.get('market', {}).get('type') == 'National' and
                       b.get('type', {}).get('shortName', '').upper() != 'RADIO' and
                       b.get('media', {}).get('shortName', '') not in SKIP_PACKAGES]

        if not national:
            return ""

        networks = [b.get('media', {}).get('shortName', '') for b in national
                    if b.get('media', {}).get('shortName')]

        # Remove duplicates while preserving order
        seen = set()
        unique = []
        for n in networks:
            if n not in seen:
                seen.add(n)
                unique.append(n)

        return ", ".join(unique) if unique else ""

    def _is_national_broadcast(self, broadcasts: List[Dict]) -> str:
        """
        Check if game has a national broadcast.
        Returns "true" or "false" as string.
        """
        if not broadcasts:
            return "false"

        # Normalize and check if any broadcast has market type = "National"
        has_national = any(
            self._normalize_broadcast(b).get('market', {}).get('type') == 'National'
            for b in broadcasts
        )

        return "true" if has_national else "false"

    def select_description(self, description_options: Any, context: Dict[str, Any]) -> str:
        """
        Select the best description template based on conditional logic and fallbacks

        Args:
            description_options: JSON string or list of description options
                                Includes both conditionals (priority 1-99) and fallbacks (priority 100)
            context: Game and team context for evaluation

        Returns:
            Selected description template string
        """
        # Parse description_options if it's a JSON string
        if isinstance(description_options, str):
            try:
                options = json.loads(description_options) if description_options else []
            except:
                return ''  # No fallback, return empty
        elif isinstance(description_options, list):
            options = description_options
        else:
            return ''  # No fallback, return empty

        if not options:
            return ''  # No descriptions configured

        # Group matching options by priority
        priority_groups = {}

        for option in options:
            template = option.get('template', '')
            priority = option.get('priority', 50)

            if not template:
                continue

            # Priority 100 = fallback descriptions (always match)
            if priority == 100:
                if priority not in priority_groups:
                    priority_groups[priority] = []
                priority_groups[priority].append(template)
                continue

            # Priority 1-99 = conditional descriptions (evaluate condition)
            condition_type = option.get('condition', '')
            condition_value = option.get('condition_value')

            if not condition_type:
                continue

            # Evaluate if this condition matches
            if self._evaluate_condition(condition_type, condition_value, context):
                if priority not in priority_groups:
                    priority_groups[priority] = []
                priority_groups[priority].append(template)

        # If no descriptions matched, return empty
        if not priority_groups:
            return ''

        # Get the highest priority (lowest number = highest priority)
        highest_priority = min(priority_groups.keys())
        matching_templates = priority_groups[highest_priority]

        # Randomly select from matching templates at same priority
        return random.choice(matching_templates)

    def _evaluate_condition(self, condition_type: str, condition_value: Any, context: Dict[str, Any]) -> bool:
        """
        Evaluate whether a condition is met

        Args:
            condition_type: Type of condition to check
            condition_value: Value to compare against (for numeric conditions)
            context: Game and team context

        Returns:
            True if condition is met, False otherwise
        """
        game = context.get('game', {})
        team_stats = context.get('team_stats', {})
        opponent_stats = context.get('opponent_stats', {})
        team_config = context.get('team_config', {})

        # Extract teams
        our_team_id = team_config.get('espn_team_id', '')
        is_home, our_team, opponent = self._determine_home_away(game, our_team_id)

        # Performance conditions
        # ESPN returns positive integers for win streaks, negative for loss streaks
        if condition_type == 'win_streak':
            streak_count = team_stats.get('streak_count', 0)
            return streak_count >= int(condition_value) if condition_value else False

        elif condition_type == 'loss_streak':
            streak_count = team_stats.get('streak_count', 0)
            return streak_count <= -int(condition_value) if condition_value else False

        elif condition_type == 'is_top_ten_matchup':
            # Both our team and opponent ranked in top 10
            # Get ranks from stats (which come from team info API)
            our_rank = team_stats.get('rank', 99)
            opp_rank = opponent_stats.get('rank', 99)
            return our_rank <= 10 and opp_rank <= 10

        elif condition_type == 'is_ranked_opponent':
            # Opponent is ranked in top 25 (our rank doesn't matter)
            opp_rank = opponent_stats.get('rank', 99)
            return opp_rank <= 25

        # Matchup conditions
        elif condition_type == 'is_rematch':
            # Check if teams have played this season
            # NOTE: In-season rematches only. Only detects previous games within the current season.
            h2h = context.get('h2h', {})
            season_series = h2h.get('season_series', {})
            games = season_series.get('games', [])
            return len(games) > 0

        elif condition_type == 'is_home':
            return is_home

        elif condition_type == 'is_away':
            return not is_home

        # Conference game condition (college only)
        elif condition_type == 'is_conference_game':
            # Check if both teams are in the same conference
            # Only applicable for college sports
            league = team_config.get('league', '').lower()
            if 'college' not in league:
                return False  # Not a college league, so not a conference game

            our_conference = team_stats.get('conference_abbrev', '') or team_stats.get('conference_name', '')
            opp_conference = opponent_stats.get('conference_abbrev', '') or opponent_stats.get('conference_name', '')

            # Both teams must have conference data and it must match
            if not our_conference or not opp_conference:
                return False

            return our_conference.lower() == opp_conference.lower()

        # Odds availability condition
        elif condition_type == 'has_odds':
            # NOTE: Same-day only.
            # The odds field is only available in scoreboard API (today's games),
            # not in schedule API (future games). Only works when event is enriched with scoreboard data.
            competition = game.get('competitions', [{}])[0]
            odds_list = competition.get('odds', [])
            return bool(odds_list and len(odds_list) > 0)

        # Home/Away Streak conditions
        elif condition_type == 'home_win_streak':
            home_streak = context.get('streaks', {}).get('home_streak', '')
            if not home_streak or not home_streak.startswith('W'):
                return False
            streak_count = int(home_streak[1:])  # Extract number from "W3"
            return streak_count >= int(condition_value) if condition_value else False

        elif condition_type == 'home_loss_streak':
            home_streak = context.get('streaks', {}).get('home_streak', '')
            if not home_streak or not home_streak.startswith('L'):
                return False
            streak_count = int(home_streak[1:])  # Extract number from "L2"
            return streak_count >= int(condition_value) if condition_value else False

        elif condition_type == 'away_win_streak':
            away_streak = context.get('streaks', {}).get('away_streak', '')
            if not away_streak or not away_streak.startswith('W'):
                return False
            streak_count = int(away_streak[1:])  # Extract number from "W3"
            return streak_count >= int(condition_value) if condition_value else False

        elif condition_type == 'away_loss_streak':
            away_streak = context.get('streaks', {}).get('away_streak', '')
            if not away_streak or not away_streak.startswith('L'):
                return False
            streak_count = int(away_streak[1:])  # Extract number from "L2"
            return streak_count >= int(condition_value) if condition_value else False

        # Season type conditions
        elif condition_type == 'is_playoff':
            season = game.get('season', {})
            season_type = season.get('type', 0)
            return season_type == 3

        elif condition_type == 'is_preseason':
            season = game.get('season', {})
            season_type = season.get('type', 0)
            return season_type == 1

        # Broadcast conditions
        elif condition_type == 'is_national_broadcast':
            competition = game.get('competitions', [{}])[0]
            broadcasts = competition.get('broadcasts', [])
            # Normalize and check if any broadcast has national market type
            for broadcast in broadcasts:
                normalized = self._normalize_broadcast(broadcast)
                market = normalized.get('market', {})
                if isinstance(market, dict):
                    market_type = market.get('type', '').lower()
                    if market_type == 'national':
                        return True
            return False

        # Opponent name condition
        elif condition_type == 'opponent_name_contains':
            if not condition_value:
                return False
            opponent_name = opponent.get('displayName', '') or opponent.get('name', '')
            return condition_value.lower() in opponent_name.lower()

        return False

    def _format_rank(self, rank: int) -> str:
        """Format rank with ordinal suffix (1st, 2nd, 3rd, etc.)"""
        if rank == 0:
            return ''

        if 10 <= rank % 100 <= 20:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(rank % 10, 'th')

        return f"{rank}{suffix}"
