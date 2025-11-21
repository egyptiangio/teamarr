"""Template Variable Resolution Engine for Teamarr"""
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import random
import json

class TemplateEngine:
    """Resolves template variables in user-defined strings"""

    def __init__(self):
        pass

    def resolve(self, template: str, context: Dict[str, Any]) -> str:
        """
        Resolve all template variables in a string

        Args:
            template: String with {variable} placeholders
            context: Dictionary containing all data needed for resolution

        Returns:
            String with all variables replaced with actual values
        """
        if not template:
            return ""

        variables = self._build_variable_dict(context)

        result = template
        for var_name, var_value in variables.items():
            placeholder = f"{{{var_name}}}"
            result = result.replace(placeholder, str(var_value))

        return result

    def _build_variable_dict(self, context: Dict[str, Any]) -> Dict[str, str]:
        """Build complete dictionary of all available variables"""

        variables = {}

        # Extract context components
        game = context.get('game', {})
        team_stats = context.get('team_stats', {})
        opponent_stats = context.get('opponent_stats', {})
        h2h = context.get('h2h', {})
        team_config = context.get('team_config', {})

        # =====================================================================
        # BASIC GAME INFORMATION
        # =====================================================================

        home_team = game.get('home_team', {})
        away_team = game.get('away_team', {})
        venue = game.get('venue', {})

        # Determine which team is "ours"
        our_team_id = team_config.get('espn_team_id', '')
        is_home = str(home_team.get('id')) == str(our_team_id) or home_team.get('name', '').lower().replace(' ', '-') == our_team_id

        our_team = home_team if is_home else away_team
        opponent = away_team if is_home else home_team

        # Use team_config as fallback when game data is not available
        variables['team_name'] = our_team.get('name', '') or team_config.get('team_name', '')
        variables['team_abbrev'] = our_team.get('abbrev', '') or team_config.get('team_abbrev', '')
        variables['opponent'] = opponent.get('name', '')
        variables['opponent_abbrev'] = opponent.get('abbrev', '')
        variables['matchup'] = f"{away_team.get('abbrev', '')} @ {home_team.get('abbrev', '')}"

        # Rankings (primarily for college sports - NFL/NBA don't have rankings)
        # Rank comes from team_stats/opponent_stats (fetched from team info API)
        # Game/schedule data doesn't include rank
        our_team_rank = team_stats.get('rank', 99)
        opponent_rank = opponent_stats.get('rank', 99)

        # Team rank variables (clean fallback - empty if unranked)
        is_team_ranked = our_team_rank <= 25
        variables['team_rank'] = f"#{our_team_rank}" if is_team_ranked else ''
        variables['team_rank_formatted'] = f"#{our_team_rank}" if is_team_ranked else 'Unranked'
        variables['is_ranked'] = 'true' if is_team_ranked else 'false'

        # Opponent rank variables (clean fallback - empty if unranked)
        is_opponent_ranked = opponent_rank <= 25
        variables['opponent_rank'] = f"#{opponent_rank}" if is_opponent_ranked else ''
        variables['opponent_rank_formatted'] = f"#{opponent_rank}" if is_opponent_ranked else 'Unranked'
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
        variables['league'] = team_config.get('league', '')
        variables['league_name'] = team_config.get('league_name', '')

        # Conference/Division variables
        # - college_conference: Conference name for college sports (e.g., "Sun Belt", "ACC")
        # - pro_conference: Conference name for pro sports (e.g., "National Football Conference", "Eastern Conference")
        # - pro_conference_abbrev: Conference abbreviation for pro sports (e.g., "NFC", "AFC")
        # - pro_division: Division name for pro sports (e.g., "NFC North", "Southeast Division")
        variables['college_conference'] = team_stats.get('conference_full_name', '') if 'college' in team_config.get('league', '').lower() else ''
        variables['pro_conference'] = team_stats.get('conference_full_name', '') if 'college' not in team_config.get('league', '').lower() else ''
        variables['pro_conference_abbrev'] = team_stats.get('conference_abbrev', '') if 'college' not in team_config.get('league', '').lower() else ''
        variables['pro_division'] = team_stats.get('division_name', '')

        # Legacy variable (for backward compatibility)
        variables['conference_or_division_name'] = team_stats.get('conference_name', '')

        # =====================================================================
        # DATE & TIME
        # =====================================================================

        game_date_str = game.get('date', '')
        if game_date_str:
            try:
                from zoneinfo import ZoneInfo
                game_datetime = datetime.fromisoformat(game_date_str.replace('Z', '+00:00'))

                # Convert to user's EPG timezone (from settings, not team timezone)
                epg_tz = context.get('epg_timezone', 'America/New_York')
                local_datetime = game_datetime.astimezone(ZoneInfo(epg_tz))

                variables['game_date'] = local_datetime.strftime('%A, %B %d, %Y')
                variables['game_date_short'] = local_datetime.strftime('%b %d')
                variables['game_time'] = local_datetime.strftime('%I:%M %p %Z')
                variables['game_time_12h'] = local_datetime.strftime('%I:%M %p')
                variables['game_time_24h'] = local_datetime.strftime('%H:%M')
                variables['game_day'] = game_datetime.strftime('%A')
                variables['game_day_short'] = game_datetime.strftime('%a')

                # Time until game
                now = datetime.now(game_datetime.tzinfo)
                time_diff = game_datetime - now
                hours_until = int(time_diff.total_seconds() / 3600)
                minutes_until = int(time_diff.total_seconds() / 60)
                days_until = int(time_diff.total_seconds() / 86400)

                variables['hours_until'] = str(max(0, hours_until))
                variables['minutes_until'] = str(max(0, minutes_until))
                variables['days_until'] = str(max(0, days_until))

                if hours_until > 0:
                    variables['time_until_text'] = f"in {hours_until} hours"
                elif minutes_until > 0:
                    variables['time_until_text'] = f"in {minutes_until} minutes"
                else:
                    variables['time_until_text'] = "starting soon"

            except Exception:
                pass

        # =====================================================================
        # VENUE
        # =====================================================================

        variables['venue'] = venue.get('name', '')
        variables['venue_city'] = venue.get('city', '')
        variables['venue_state'] = venue.get('state', '')
        variables['venue_full'] = f"{venue.get('name', '')}, {venue.get('city', '')}" if venue.get('name') else ''

        # =====================================================================
        # HOME/AWAY CONTEXT
        # =====================================================================

        variables['is_home'] = 'true' if is_home else 'false'
        variables['is_away'] = 'false' if is_home else 'true'
        variables['is_home_game'] = 'true' if is_home else 'false'
        variables['is_away_game'] = 'false' if is_home else 'true'
        variables['home_away_text'] = 'at home' if is_home else 'on the road'
        variables['vs_at'] = 'vs' if is_home else '@'
        variables['home_team'] = home_team.get('name', '')
        variables['away_team'] = away_team.get('name', '')

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
        wins = record.get('wins', 0)
        losses = record.get('losses', 0)
        ties = record.get('ties', 0)

        if ties > 0:
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

        if opp_ties > 0:
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
        variables['streak'] = str(streak_count)
        variables['streak_count'] = str(streak_count)
        variables['streak_type'] = streak_type

        # Win streaks (only show if 2+ games)
        if streak_count_raw >= 2:
            variables['has_win_streak'] = 'true'
            variables['win_streak'] = str(streak_count)
            variables['win_streak_text'] = f"{streak_count} game winning streak"
            variables['has_loss_streak'] = 'false'
            variables['loss_streak'] = '0'
            variables['loss_streak_text'] = ''
        # Loss streaks (only show if 2+ games)
        elif streak_count_raw <= -2:
            variables['has_loss_streak'] = 'true'
            variables['loss_streak'] = str(streak_count)
            variables['loss_streak_text'] = f"{streak_count} game losing streak"
            variables['has_win_streak'] = 'false'
            variables['win_streak'] = '0'
            variables['win_streak_text'] = ''
        # No significant streak
        else:
            variables['has_win_streak'] = 'false'
            variables['has_loss_streak'] = 'false'
            variables['win_streak'] = '0'
            variables['loss_streak'] = '0'
            variables['win_streak_text'] = ''
            variables['loss_streak_text'] = ''

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
        variables['rematch_winner'] = previous.get('winner', '')
        variables['rematch_loser'] = previous.get('loser', '')
        variables['rematch_location'] = previous.get('location', '')
        variables['rematch_days_since'] = str(previous.get('days_since', 0))
        variables['rematch_season_series'] = f"{season_series.get('team_wins', 0)}-{season_series.get('opponent_wins', 0)}"

        # =====================================================================
        # SERIES & PLAYOFFS (comprehensive)
        # =====================================================================

        # Check if this is a playoff/series game
        series_data = game.get('series', {}) if game else {}
        is_playoff = series_data.get('type') == 'playoff' or game.get('season', {}).get('type') == 3

        variables['is_playoff'] = 'true' if is_playoff else 'false'
        variables['is_regular_season'] = 'false' if is_playoff else 'true'

        if is_playoff and series_data:
            # Playoff series information
            variables['series_type'] = series_data.get('title', 'Playoff Series')  # e.g., "First Round", "Conference Finals"
            variables['series_round'] = series_data.get('round', '')
            variables['series_summary'] = series_data.get('summary', '')  # e.g., "Series tied 2-2"

            # Series record
            team_series_wins = series_data.get('competitors', [{}])[0].get('wins', 0)
            opp_series_wins = series_data.get('competitors', [{}])[1].get('wins', 0) if len(series_data.get('competitors', [])) > 1 else 0

            variables['series_team_wins'] = str(team_series_wins)
            variables['series_opponent_wins'] = str(opp_series_wins)
            variables['series_record'] = f"{team_series_wins}-{opp_series_wins}"

            # Series status
            series_length = series_data.get('conference', {}).get('seriesLength', 7)
            games_to_win = (series_length // 2) + 1  # 4 for best-of-7, 3 for best-of-5

            variables['series_length'] = str(series_length)
            variables['games_to_win_series'] = str(games_to_win)
            variables['series_games_played'] = str(team_series_wins + opp_series_wins)
            variables['series_games_remaining'] = str(series_length - (team_series_wins + opp_series_wins))

            # Who's leading the series
            if team_series_wins > opp_series_wins:
                variables['series_leader'] = variables['team_name']
                variables['series_leader_abbrev'] = variables['team_abbrev']
                variables['series_lead'] = str(team_series_wins - opp_series_wins)
                variables['series_status'] = f"{variables['team_name']} leads {team_series_wins}-{opp_series_wins}"
            elif opp_series_wins > team_series_wins:
                variables['series_leader'] = variables['opponent']
                variables['series_leader_abbrev'] = variables['opponent_abbrev']
                variables['series_lead'] = str(opp_series_wins - team_series_wins)
                variables['series_status'] = f"{variables['opponent']} leads {opp_series_wins}-{team_series_wins}"
            else:
                variables['series_leader'] = 'tied'
                variables['series_leader_abbrev'] = ''
                variables['series_lead'] = '0'
                variables['series_status'] = f"Series tied {team_series_wins}-{team_series_wins}"

            # Elimination scenarios
            variables['is_elimination_game'] = 'true' if (team_series_wins == games_to_win - 1 or opp_series_wins == games_to_win - 1) else 'false'
            variables['is_clinch_game'] = 'true' if (team_series_wins == games_to_win - 1) else 'false'
            variables['is_must_win'] = 'true' if (opp_series_wins == games_to_win - 1) else 'false'
            variables['is_series_clinched'] = 'true' if (team_series_wins >= games_to_win or opp_series_wins >= games_to_win) else 'false'

            # Can team advance with a win?
            if team_series_wins == games_to_win - 1:
                variables['can_advance'] = 'true'
                variables['series_clinch_text'] = f"Win advances to next round"
            else:
                variables['can_advance'] = 'false'
                variables['series_clinch_text'] = ''

            # Will team be eliminated with a loss?
            if opp_series_wins == games_to_win - 1:
                variables['can_be_eliminated'] = 'true'
                variables['elimination_text'] = f"Loss eliminates {variables['team_name']}"
            else:
                variables['can_be_eliminated'] = 'false'
                variables['elimination_text'] = ''

            # Series game number
            variables['series_game_number'] = str(team_series_wins + opp_series_wins + 1)

        else:
            # Not a playoff series - set defaults
            variables['series_type'] = ''
            variables['series_round'] = ''
            variables['series_summary'] = ''
            variables['series_team_wins'] = '0'
            variables['series_opponent_wins'] = '0'
            variables['series_record'] = '0-0'
            variables['series_length'] = '0'
            variables['games_to_win_series'] = '0'
            variables['series_games_played'] = '0'
            variables['series_games_remaining'] = '0'
            variables['series_leader'] = ''
            variables['series_leader_abbrev'] = ''
            variables['series_lead'] = '0'
            variables['series_status'] = ''
            variables['is_elimination_game'] = 'false'
            variables['is_clinch_game'] = 'false'
            variables['is_must_win'] = 'false'
            variables['is_series_clinched'] = 'false'
            variables['can_advance'] = 'false'
            variables['can_be_eliminated'] = 'false'
            variables['series_clinch_text'] = ''
            variables['elimination_text'] = ''
            variables['series_game_number'] = '0'

        # =====================================================================
        # STANDINGS (if enabled)
        # =====================================================================

        # Get standings data from team_stats API
        playoff_seed = team_stats.get('playoff_seed', 0)
        games_back = team_stats.get('games_back', 0.0)

        variables['playoff_seed'] = self._format_rank(playoff_seed)
        variables['games_back'] = f"{games_back:.1f}" if games_back > 0 else "0.0"

        # Determine playoff position
        # NBA/NHL: top 8 seeds make playoffs
        # NFL: top 7 seeds make playoffs
        # Simplified: if seed exists and <= 8, in playoffs
        if playoff_seed > 0 and playoff_seed <= 8:
            variables['playoff_position'] = 'in playoff position'
        else:
            variables['playoff_position'] = 'outside playoffs'


        # =====================================================================
        # RECENT PERFORMANCE (if enabled)
        # =====================================================================

        # Home/away records from team_stats API
        home_record = team_stats.get('home_record', '0-0')
        away_record = team_stats.get('away_record', '0-0')
        division_record = team_stats.get('division_record', '')

        variables['home_record'] = home_record
        variables['away_record'] = away_record
        variables['division_record'] = division_record

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

        # Home team record and away team record (based on matchup, not our team)
        # If we're home, use our team's overall record; if we're away, use opponent's overall record
        if is_home:
            # We are home team - use our overall record for home_team_record
            variables['home_team_record'] = variables.get('team_record', '0-0')
            # Opponent is away - use their overall record for away_team_record
            variables['away_team_record'] = variables.get('opponent_record', '0-0')
        else:
            # We are away team - use our overall record for away_team_record
            variables['away_team_record'] = variables.get('team_record', '0-0')
            # Opponent is home - use their overall record for home_team_record
            variables['home_team_record'] = variables.get('opponent_record', '0-0')

        # Conference record already available from schedule API competitor.records
        # Last 5/10 would need historical calculation - future implementation
        variables['last_5_record'] = ''
        variables['last_10_record'] = ''
        variables['recent_form'] = ''
        variables['conference_record'] = ''

        # =====================================================================
        # STATISTICS (if enabled)
        # =====================================================================

        # Get PPG/PAPG from team_stats API data
        variables['team_ppg'] = f"{team_stats.get('ppg', 0):.1f}"
        variables['team_papg'] = f"{team_stats.get('papg', 0):.1f}"

        # Get opponent PPG/PAPG from opponent_stats API data
        variables['opponent_ppg'] = f"{opponent_stats.get('ppg', 0):.1f}"
        variables['opponent_papg'] = f"{opponent_stats.get('papg', 0):.1f}"

        # Rankings not available from API - set empty
        variables['team_ppg_rank'] = ''
        variables['opponent_ppg_rank'] = ''
        variables['team_papg_rank'] = ''
        variables['opponent_papg_rank'] = ''

        # =====================================================================
        # PLAYERS (if enabled)
        # =====================================================================

        players = team_stats.get('players', {})

        top_scorer = players.get('top_scorer', {})
        variables['top_scorer_name'] = top_scorer.get('name', '')
        variables['top_scorer_ppg'] = f"{top_scorer.get('ppg', 0):.1f}"
        variables['top_scorer_position'] = top_scorer.get('position', '')

        top_rebounder = players.get('top_rebounder', {})
        variables['top_rebounder_name'] = top_rebounder.get('name', '')
        variables['top_rebounder_rpg'] = f"{top_rebounder.get('rpg', 0):.1f}"

        top_assist = players.get('top_assist', {})
        variables['top_assist_name'] = top_assist.get('name', '')
        variables['top_assist_apg'] = f"{top_assist.get('apg', 0):.1f}"

        injuries = players.get('injuries', [])
        variables['has_injuries'] = 'true' if injuries else 'false'
        variables['injury_count'] = str(len(injuries))
        variables['injury_list'] = ', '.join(injuries)
        variables['injury_status'] = 'dealing with injuries' if injuries else 'healthy'

        # =====================================================================
        # GAME STATUS (for live games)
        # =====================================================================

        status = game.get('status', {})
        variables['game_status'] = status.get('name', 'Scheduled')
        variables['game_clock'] = status.get('detail', '')
        variables['period'] = status.get('period', '')
        variables['period_short'] = status.get('period_short', '')

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

        # =====================================================================
        # ATTENDANCE
        # =====================================================================

        # Get attendance from competition data
        competition = game.get('competitions', [{}])[0] if game.get('competitions') else {}
        attendance = competition.get('attendance', 0)

        variables['attendance'] = f"{attendance:,}" if attendance else ''
        variables['has_attendance'] = 'true' if attendance else 'false'

        # =====================================================================
        # SCORE & OUTCOME (for postgame filler content)
        # =====================================================================

        # Determine if game is final
        is_final = status.get('name', '') in ['STATUS_FINAL', 'Final']
        variables['is_final'] = 'true' if is_final else 'false'

        if is_final and our_score > 0 and opp_score > 0:
            # Final scores
            variables['final_score'] = f"{our_score}-{opp_score}"

            # Score differential
            abs_diff = abs(score_diff)
            variables['score_differential'] = str(abs_diff)
            variables['score_differential_text'] = f"by {abs_diff} point{'s' if abs_diff != 1 else ''}"

            # Win/Loss result
            if our_score > opp_score:
                variables['result'] = 'win'
                variables['result_text'] = 'defeated'
                variables['result_verb'] = 'beat'
                variables['outcome'] = f"{variables['team_name']} won"
            else:
                variables['result'] = 'loss'
                variables['result_text'] = 'lost to'
                variables['result_verb'] = 'fell to'
                variables['outcome'] = f"{variables['team_name']} lost"

            # Game summary (blowout, close, overtime)
            if abs_diff >= 20:
                variables['game_summary'] = 'blowout'
                variables['game_summary_text'] = 'in a blowout'
            elif abs_diff <= 5:
                variables['game_summary'] = 'close game'
                variables['game_summary_text'] = 'in a close game'
            elif abs_diff <= 10:
                variables['game_summary'] = 'competitive game'
                variables['game_summary_text'] = 'in a competitive matchup'
            else:
                variables['game_summary'] = 'game'
                variables['game_summary_text'] = ''

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
                variables['is_overtime'] = 'true'
                variables['overtime_text'] = 'in overtime'
                variables['game_summary'] = 'overtime game'
                variables['game_summary_text'] = 'in overtime'
            else:
                variables['is_overtime'] = 'false'
                variables['overtime_text'] = ''

            # Scoring run (if available in game data)
            # This would need to be extracted from play-by-play data if available
            variables['scoring_run'] = game.get('scoring_run', '')

        else:
            # Game not final - set empty defaults
            variables['final_score'] = ''
            variables['score_differential'] = '0'
            variables['score_differential_text'] = ''
            variables['result'] = ''
            variables['result_text'] = ''
            variables['result_verb'] = ''
            variables['outcome'] = ''
            variables['game_summary'] = ''
            variables['game_summary_text'] = ''
            variables['is_overtime'] = 'false'
            variables['overtime_text'] = ''
            variables['scoring_run'] = ''

        # =====================================================================
        # SEASON CONTEXT
        # =====================================================================

        season = game.get('season', {})
        season_type_id = season.get('type', 2)  # 1=preseason, 2=regular, 3=postseason

        variables['season_type'] = season.get('type', 'regular')
        variables['season_year'] = str(season.get('year', ''))
        variables['is_preseason'] = 'true' if season_type_id == 1 else 'false'
        variables['is_playoffs'] = 'true' if season_type_id == 3 else 'false'

        # =====================================================================
        # SPECIAL GAME FLAGS
        # =====================================================================

        variables['is_rivalry'] = 'true' if context.get('is_rivalry', False) else 'false'
        variables['is_division_game'] = 'true' if context.get('is_division', False) else 'false'
        variables['is_conference_game'] = 'true' if context.get('is_conference', False) else 'false'

        # =====================================================================
        # LAST GAME (most recent completed game)
        # =====================================================================

        last_game = context.get('last_game', {})
        variables['last_opponent'] = last_game.get('opponent', '')
        variables['last_date'] = last_game.get('date', '')
        variables['last_matchup'] = last_game.get('matchup', '')
        variables['last_result'] = last_game.get('result', '')  # "Win", "Loss", or "Tie"
        variables['last_score'] = last_game.get('score', '')
        variables['last_score_abbrev'] = last_game.get('score_abbrev', '')

        # =====================================================================
        # TODAY'S GAME (completed game from today only - for postgame)
        # =====================================================================

        today_game = context.get('today_game', {})
        variables['today_score_abbrev'] = today_game.get('score_abbrev', '')

        # =====================================================================
        # NEXT GAME (upcoming scheduled game)
        # =====================================================================

        next_game = context.get('next_game', {})
        variables['next_opponent'] = next_game.get('opponent', '')
        variables['next_date'] = next_game.get('date', '')
        variables['next_time'] = next_game.get('time', '')
        variables['next_datetime'] = next_game.get('datetime', '')
        variables['next_matchup'] = next_game.get('matchup', '')
        variables['next_venue'] = next_game.get('venue', '')


        # =====================================================================
        # ODDS & BETTING
        # =====================================================================

        # Get odds data from competition
        odds_list = competition.get('odds', [])
        if odds_list and len(odds_list) > 0:
            odds = odds_list[0]  # Use first odds provider (usually ESPN BET)

            # Provider info
            provider = odds.get('provider', {})
            variables['odds_provider'] = provider.get('name', '')

            # Over/Under
            over_under = odds.get('overUnder', 0)
            variables['over_under'] = str(over_under) if over_under else ''
            variables['has_over_under'] = 'true' if over_under else 'false'

            # Spread (absolute value)
            spread = abs(odds.get('spread', 0))
            variables['spread'] = str(spread) if spread else ''
            variables['has_spread'] = 'true' if spread else 'false'

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

            # Favorite/Underdog status
            variables['is_favorite'] = 'true' if our_odds.get('favorite', False) else 'false'
            variables['is_underdog'] = 'true' if our_odds.get('underdog', False) else 'false'
            variables['opponent_is_favorite'] = 'true' if opp_odds.get('favorite', False) else 'false'
            variables['opponent_is_underdog'] = 'true' if opp_odds.get('underdog', False) else 'false'

            # Money line
            our_moneyline = our_odds.get('moneyLine', 0)
            opp_moneyline = opp_odds.get('moneyLine', 0)
            variables['moneyline'] = str(our_moneyline) if our_moneyline else ''
            variables['opponent_moneyline'] = str(opp_moneyline) if opp_moneyline else ''

            # Spread odds
            our_spread_odds = our_odds.get('spreadOdds', 0)
            opp_spread_odds = opp_odds.get('spreadOdds', 0)
            variables['spread_odds'] = str(our_spread_odds) if our_spread_odds else ''
            variables['opponent_spread_odds'] = str(opp_spread_odds) if opp_spread_odds else ''

            # Favorite at open (line movement)
            variables['was_favorite_at_open'] = 'true' if our_odds.get('favoriteAtOpen', False) else 'false'
            variables['opponent_was_favorite_at_open'] = 'true' if opp_odds.get('favoriteAtOpen', False) else 'false'

            # Spread category (close game, moderate, blowout prediction)
            if spread > 0:
                if spread <= 3:
                    variables['spread_category'] = 'close'
                    variables['spread_category_text'] = 'close game'
                elif spread <= 7:
                    variables['spread_category'] = 'moderate'
                    variables['spread_category_text'] = 'moderate spread'
                else:
                    variables['spread_category'] = 'wide'
                    variables['spread_category_text'] = 'large spread'
            else:
                variables['spread_category'] = ''
                variables['spread_category_text'] = ''
        else:
            # No odds available - set defaults
            variables['odds_provider'] = ''
            variables['over_under'] = ''
            variables['has_over_under'] = 'false'
            variables['spread'] = ''
            variables['has_spread'] = 'false'
            variables['odds_details'] = ''
            variables['is_favorite'] = 'false'
            variables['is_underdog'] = 'false'
            variables['opponent_is_favorite'] = 'false'
            variables['opponent_is_underdog'] = 'false'
            variables['moneyline'] = ''
            variables['opponent_moneyline'] = ''
            variables['spread_odds'] = ''
            variables['opponent_spread_odds'] = ''
            variables['was_favorite_at_open'] = 'false'
            variables['opponent_was_favorite_at_open'] = 'false'
            variables['spread_category'] = ''
            variables['spread_category_text'] = ''

        return variables

    def select_description(self, default_description: str, description_options: Any, context: Dict[str, Any]) -> str:
        """
        Select the best description template based on conditional logic

        Args:
            default_description: Fallback description if no conditions match
            description_options: JSON string or list of conditional description options
            context: Game and team context for evaluation

        Returns:
            Selected description template string
        """
        # Parse description_options if it's a JSON string
        if isinstance(description_options, str):
            try:
                options = json.loads(description_options) if description_options else []
            except:
                return default_description
        elif isinstance(description_options, list):
            options = description_options
        else:
            return default_description

        if not options:
            return default_description

        # Group matching options by priority
        priority_groups = {}

        for option in options:
            condition_type = option.get('condition', '')  # Fixed: was 'condition_type'
            condition_value = option.get('condition_value')
            template = option.get('template', '')
            priority = option.get('priority', 50)

            if not template or not condition_type:
                continue

            # Evaluate if this condition matches
            if self._evaluate_condition(condition_type, condition_value, context):
                if priority not in priority_groups:
                    priority_groups[priority] = []
                priority_groups[priority].append(template)

        # If no conditions matched, use default
        if not priority_groups:
            return default_description

        # Get the highest priority (lowest number)
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
        home_team = game.get('home_team', {})
        away_team = game.get('away_team', {})
        our_team_id = team_config.get('espn_team_id', '')
        is_home = str(home_team.get('id')) == str(our_team_id) or home_team.get('name', '').lower().replace(' ', '-') == our_team_id
        our_team = home_team if is_home else away_team
        opponent = away_team if is_home else home_team

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

        elif condition_type == 'is_conference_game':
            # NOTE: College sports only. Same-day only (like has_odds).
            # The conferenceCompetition field is only available in scoreboard API (today's games),
            # not in schedule API (future games). Only works when event is enriched with scoreboard data.
            competition = game.get('competitions', [{}])[0]
            return competition.get('conferenceCompetition', False)

        # Odds availability condition
        elif condition_type == 'has_odds':
            # NOTE: Same-day only.
            # The odds field is only available in scoreboard API (today's games),
            # not in schedule API (future games). Only works when event is enriched with scoreboard data.
            competition = game.get('competitions', [{}])[0]
            odds_list = competition.get('odds', [])
            return bool(odds_list and len(odds_list) > 0)

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
