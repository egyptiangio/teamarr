"""Teamarr - Dynamic Sports Team EPG Generator Flask Application"""
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
import os
from database import get_connection, init_database
from api.espn_client import ESPNClient
from epg.xmltv_generator import XMLTVGenerator
from epg.template_engine import TemplateEngine
from datetime import datetime, timedelta, date
from typing import List
from zoneinfo import ZoneInfo
import json
import threading
import time
from config import VERSION

app = Flask(__name__)
app.config['SECRET_KEY'] = 'teamarr-sports-epg-generator'

# Initialize components
espn = ESPNClient(db_path='teamarr.db')
xmltv_gen = XMLTVGenerator()
template_engine = TemplateEngine()

# Scheduler thread
scheduler_thread = None
scheduler_running = False
last_run_time = None

# Ensure database exists
if not os.path.exists('teamarr.db'):
    init_database()

# ============================================================================
# SCHEDULER FUNCTIONS
# ============================================================================

def run_scheduled_generation():
    """Run EPG generation (called by scheduler)"""
    try:
        print(f"🕐 Scheduled EPG generation started at {datetime.now()}")

        # Call the generate_epg endpoint internally using test client
        with app.app_context():
            with app.test_client() as client:
                # Make internal POST request to /generate endpoint
                response = client.post('/generate', data={'days_ahead': ''})

                if response.status_code == 200:
                    result = response.get_json()
                    print(f"✅ Scheduled EPG generation completed: {result.get('num_programmes', 0)} programs from {result.get('num_channels', 0)} teams in {result.get('generation_time', 0):.2f}s")
                else:
                    error_data = response.get_json() if response.content_type == 'application/json' else {}
                    error_msg = error_data.get('error', 'Unknown error')
                    print(f"❌ Scheduled EPG generation failed: {error_msg}")

    except Exception as e:
        print(f"❌ Scheduler error: {e}")

def scheduler_loop():
    """Background thread that runs the scheduler"""
    global scheduler_running, last_run_time

    print("🚀 EPG Auto-Generation Scheduler started")

    while scheduler_running:
        try:
            conn = get_connection()
            settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
            conn.close()

            if not settings.get('auto_generate_enabled', False):
                time.sleep(60)  # Check every minute if disabled
                continue

            frequency = settings.get('auto_generate_frequency', 'daily')
            update_time_str = settings.get('epg_update_time', '00:00')

            now = datetime.now()

            # Parse update time (HH:MM format)
            try:
                hour, minute = map(int, update_time_str.split(':'))
            except:
                hour, minute = 0, 0

            # Check if it's time to run based on frequency and last run time
            should_run = False

            if frequency == 'hourly':
                # Run once per hour (at any point in the hour we haven't run yet)
                if last_run_time is None:
                    # Never run before, run now
                    should_run = True
                else:
                    # Check if we're in a different hour than last run
                    last_hour = last_run_time.replace(minute=0, second=0, microsecond=0)
                    current_hour = now.replace(minute=0, second=0, microsecond=0)
                    if current_hour > last_hour:
                        should_run = True

            elif frequency == 'daily':
                # Run once per day at specified time
                if last_run_time is None:
                    # Never run before, check if we're past the scheduled time today
                    if now.hour > hour or (now.hour == hour and now.minute >= minute):
                        should_run = True
                else:
                    # Check if we're in a different day and past the scheduled time
                    if now.date() > last_run_time.date():
                        if now.hour > hour or (now.hour == hour and now.minute >= minute):
                            should_run = True

            elif frequency == 'weekly':
                # Run once per week on Monday at specified time
                if last_run_time is None:
                    # Never run before, check if today is Monday and past scheduled time
                    if now.weekday() == 0 and (now.hour > hour or (now.hour == hour and now.minute >= minute)):
                        should_run = True
                else:
                    # Check if it's Monday, we're past scheduled time, and we haven't run this week
                    if now.weekday() == 0 and (now.hour > hour or (now.hour == hour and now.minute >= minute)):
                        # Check if last run was in a previous week
                        days_since_last_run = (now.date() - last_run_time.date()).days
                        if days_since_last_run >= 7:
                            should_run = True

            if should_run:
                run_scheduled_generation()
                last_run_time = now

            time.sleep(30)  # Check every 30 seconds

        except Exception as e:
            print(f"❌ Scheduler loop error: {e}")
            time.sleep(60)

    print("🛑 EPG Auto-Generation Scheduler stopped")

def start_scheduler():
    """Start the scheduler background thread"""
    global scheduler_thread, scheduler_running

    if scheduler_thread and scheduler_thread.is_alive():
        print("⚠️  Scheduler already running")
        return

    scheduler_running = True
    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler_thread.start()
    print("✅ Scheduler thread started")

def stop_scheduler():
    """Stop the scheduler background thread"""
    global scheduler_running
    scheduler_running = False
    print("⏹️  Scheduler stopping...")

# ============================================================================
# ROUTES
# ============================================================================

@app.route('/')
def index():
    """Dashboard - show teams and recent EPG generations"""
    conn = get_connection()

    # Get active teams
    teams = conn.execute("""
        SELECT t.*, lc.league_name, lc.sport
        FROM teams t
        LEFT JOIN league_config lc ON t.league = lc.league_code
        WHERE t.active = 1
        ORDER BY t.team_name
    """).fetchall()

    # Get recent EPG history
    history = conn.execute("""
        SELECT * FROM epg_history
        ORDER BY generated_at DESC
        LIMIT 10
    """).fetchall()

    # Get default timezone from settings
    settings = conn.execute("SELECT default_timezone FROM settings WHERE id = 1").fetchone()
    default_tz = settings['default_timezone'] if settings else 'America/New_York'

    conn.close()

    # Convert history timestamps to timezone-aware format
    history_list = []
    for h in history:
        h_dict = dict(h)
        if h_dict.get('generated_at'):
            try:
                # Parse UTC timestamp string from SQLite (format: "YYYY-MM-DD HH:MM:SS")
                timestamp_str = str(h_dict['generated_at'])
                utc_time = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                # Make it timezone-aware (UTC) and convert to local timezone
                utc_time = utc_time.replace(tzinfo=ZoneInfo('UTC'))
                local_time = utc_time.astimezone(ZoneInfo(default_tz))
                # Format: "MM/DD/YYYY HH:MM AM/PM"
                h_dict['generated_at_local'] = local_time.strftime('%m/%d/%Y %I:%M %p')
            except Exception as e:
                # Fallback to original timestamp if conversion fails
                print(f"Error converting timestamp: {e}")
                h_dict['generated_at_local'] = h_dict['generated_at']
        history_list.append(h_dict)

    return render_template('index.html',
                          teams=[dict(t) for t in teams],
                          version=VERSION,
                          history=history_list)

@app.route('/teams')
def teams_list():
    """List all teams"""
    conn = get_connection()
    teams = conn.execute("""
        SELECT t.*, lc.league_name, lc.sport
        FROM teams t
        LEFT JOIN league_config lc ON t.league = lc.league_code
        ORDER BY t.team_name
    """).fetchall()
    conn.close()

    return render_template('teams.html', teams=[dict(t) for t in teams], version=VERSION)

def parse_description_options(form_data):
    """Parse description_options from form data"""
    options = []

    # Find all description_options indices
    indices = set()
    for key in form_data.keys():
        if key.startswith('description_options['):
            # Extract index from "description_options[0][field_name]"
            try:
                index = int(key.split('[')[1].split(']')[0])
                indices.add(index)
            except:
                continue

    # Build options array
    for idx in sorted(indices):
        condition_type = form_data.get(f'description_options[{idx}][condition_type]', '')
        condition_value = form_data.get(f'description_options[{idx}][condition_value]', '')
        template = form_data.get(f'description_options[{idx}][template]', '')
        priority = form_data.get(f'description_options[{idx}][priority]', '50')

        if template:  # Only add if template has content
            option = {
                'condition_type': condition_type,
                'template': template,
                'priority': int(priority) if priority else 50
            }

            # Add condition_value if needed (for non-boolean conditions)
            boolean_conditions = [
                '', 'is_home', 'is_away', 'is_rematch',
                'is_conference_game', 'has_odds'
            ]

            if condition_value and condition_type not in boolean_conditions:
                # Try to convert to number for numeric conditions
                if condition_type in ['win_streak', 'loss_streak', 'team_rank', 'opponent_rank']:
                    try:
                        option['condition_value'] = int(condition_value)
                    except:
                        option['condition_value'] = condition_value
                else:
                    # String value for other conditions
                    option['condition_value'] = condition_value

            options.append(option)

    return options

# ============================================================================
# HEALTH CHECK ENDPOINT
# ============================================================================

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for Docker healthcheck"""
    try:
        # Verify database connection
        conn = get_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()

        return jsonify({
            'status': 'healthy',
            'version': VERSION,
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 503

# ============================================================================
# ROUTES
# ============================================================================

@app.route('/teams/add', methods=['GET', 'POST'])
def add_team():
    """Add new team"""
    conn = get_connection()

    if request.method == 'POST':
        # Get form data
        data = request.form.to_dict()

        # Build categories list from checkboxes + custom field
        categories_list = []
        if request.form.get('category_sports'):
            categories_list.append('Sports')
        if request.form.get('category_sport_variable'):
            categories_list.append('{sport}')

        # Add custom categories
        categories_custom = data.get('categories_custom', '')
        if categories_custom:
            custom_cats = [cat.strip() for cat in categories_custom.split(',') if cat.strip()]
            categories_list.extend(custom_cats)

        # Parse description options
        description_options = parse_description_options(request.form)

        # Insert team
        conn.execute("""
            INSERT INTO teams (
                espn_team_id, league, sport, team_name, team_abbrev,
                team_logo_url, team_color, channel_id, title_format,
                description_template, subtitle_template, game_duration_mode, game_duration_override,
                timezone, flags, categories,
                description_options
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['espn_team_id'],
            data['league'],
            data['sport'],
            data['team_name'],
            data.get('team_abbrev', ''),
            data.get('team_logo_url', ''),
            data.get('team_color', ''),
            data['channel_id'],
            data.get('title_format', '{team_name} Basketball'),
            data.get('description_template', ''),
            data.get('subtitle_template', '{venue_full}'),
            data.get('game_duration_mode', 'default'),
            float(data['game_duration_override']) if data.get('game_duration_override') else None,
            data.get('timezone', 'America/New_York'),
            json.dumps({'new': True, 'live': True}),
            json.dumps(categories_list),
            json.dumps(description_options)
        ))

        conn.commit()
        conn.close()

        return redirect(url_for('teams_list'))

    # GET request - show form
    leagues = conn.execute("SELECT * FROM league_config WHERE active = 1 ORDER BY league_name").fetchall()
    conn.close()

    return render_template('team_form.html', leagues=[dict(l) for l in leagues], team=None, version=VERSION)

@app.route('/teams/<int:team_id>/edit', methods=['GET', 'POST'])
def edit_team(team_id):
    """Edit existing team"""
    conn = get_connection()

    if request.method == 'POST':
        # Update team
        data = request.form.to_dict()

        # DEBUG: Log raw form data
        print(f"\n=== FORM SAVE DEBUG ===")
        print(f"All form keys: {list(request.form.keys())}")

        # Build categories list from checkboxes + custom field
        categories_list = []
        if request.form.get('category_sports'):
            categories_list.append('Sports')
        if request.form.get('category_sport_variable'):
            categories_list.append('{sport}')

        # Add custom categories
        categories_custom = data.get('categories_custom', '')
        if categories_custom:
            custom_cats = [cat.strip() for cat in categories_custom.split(',') if cat.strip()]
            categories_list.extend(custom_cats)
        print(f"Parsed categories list: {categories_list}")

        # Parse description options from form arrays (new format!)
        description_options = []
        cond_names = request.form.getlist('cond_name[]')
        cond_types = request.form.getlist('cond_type[]')
        cond_values = request.form.getlist('cond_value[]')
        cond_priorities = request.form.getlist('cond_priority[]')
        cond_templates = request.form.getlist('cond_template[]')
        cond_sources = request.form.getlist('cond_source[]')

        print(f"Received {len(cond_names)} conditions from form arrays")

        for i in range(len(cond_names)):
            condition = {
                'name': cond_names[i],
                'condition': cond_types[i] if i < len(cond_types) else '',
                'condition_value': cond_values[i] if i < len(cond_values) else '',
                'priority': int(cond_priorities[i]) if i < len(cond_priorities) and cond_priorities[i] else 50,
                'template': cond_templates[i] if i < len(cond_templates) else '',
                'source': cond_sources[i] if i < len(cond_sources) else 'custom'
            }
            description_options.append(condition)
            print(f"  Condition {i+1}: {condition['name']} ({condition['condition']})")

        print(f"Total conditions parsed: {len(description_options)}")
        print(f"JSON to save: {json.dumps(description_options)}")
        print(f"======================\n")

        # Build flags JSON from checkboxes
        flags = {
            'new': data.get('include_new_tag') == 'on',
            'live': data.get('include_live_tag') == 'on',
            'date': data.get('include_date_tag') == 'on',
            'premiere': False  # Not currently exposed in UI
        }

        conn.execute("""
            UPDATE teams SET
                espn_team_id = ?, league = ?, sport = ?, team_name = ?,
                team_abbrev = ?, team_logo_url = ?, team_color = ?,
                channel_id = ?, title_format = ?, description_template = ?,
                subtitle_template = ?, game_duration_mode = ?, game_duration_override = ?, timezone = ?,
                categories = ?, categories_apply_to = ?, active = ?,
                description_options = ?,
                flags = ?,
                no_game_enabled = ?, no_game_title = ?, no_game_description = ?, no_game_duration = ?,
                pregame_enabled = ?, pregame_periods = ?, pregame_title = ?, pregame_description = ?,
                postgame_enabled = ?, postgame_periods = ?, postgame_title = ?, postgame_description = ?,
                idle_enabled = ?, idle_title = ?, idle_description = ?,
                enable_records = ?, enable_streaks = ?, enable_head_to_head = ?,
                enable_standings = ?, enable_statistics = ?, enable_players = ?,
                midnight_crossover_mode = ?, max_program_hours_mode = ?, max_program_hours = ?
            WHERE id = ?
        """, (
            data['espn_team_id'], data['league'], data['sport'], data['team_name'],
            data.get('team_abbrev', ''), data.get('team_logo_url', ''), data.get('team_color', ''),
            data['channel_id'], data.get('title_format'), data.get('description_template'),
            data.get('subtitle_template'),
            data.get('game_duration_mode', 'default'),
            float(data['game_duration_override']) if data.get('game_duration_override') else None,
            data.get('timezone'), json.dumps(categories_list),
            data.get('categories_apply_to', 'events'),
            1 if data.get('active') == 'on' else 0,
            json.dumps(description_options),
            json.dumps(flags),
            1 if data.get('no_game_enabled') == 'on' else 0,
            data.get('no_game_title', 'No Game Today'),
            data.get('no_game_description', 'No {team_name} game scheduled today. Next game: {next_game_date} vs {next_opponent}'),
            float(data.get('no_game_duration', 24.0)),
            1 if data.get('pregame_enabled') == 'on' else 0,
            data.get('pregame_periods', '[{"start_hours_before": 24, "end_hours_before": 6, "title": "Game Preview", "description": "{team_name} plays {opponent} in {hours_until} hours at {venue}"}, {"start_hours_before": 6, "end_hours_before": 2, "title": "Pre-Game Coverage", "description": "{team_name} vs {opponent} starts at {game_time}. {team_name} ({team_record}) looks to improve."}, {"start_hours_before": 2, "end_hours_before": 0, "title": "Game Starting Soon", "description": "{team_name} vs {opponent} starts in {hours_until} hours at {venue_full}"}]'),
            data.get('pregame_title', 'Pregame Coverage'),
            data.get('pregame_description', '{team_name} plays {opponent} today at {game_time}'),
            1 if data.get('postgame_enabled') == 'on' else 0,
            data.get('postgame_periods', '[{"start_hours_after": 0, "end_hours_after": 3, "title": "Game Recap", "description": "{team_name} {result_text} {final_score}. Final record: {final_record}"}, {"start_hours_after": 3, "end_hours_after": 12, "title": "Extended Highlights", "description": "Highlights: {team_name} {result_text} {final_score} vs {opponent}"}, {"start_hours_after": 12, "end_hours_after": 24, "title": "Full Game Replay", "description": "Replay: {team_name} vs {opponent} - Final Score: {final_score}"}]'),
            data.get('postgame_title', 'Postgame Recap'),
            data.get('postgame_description', '{team_name} {result_text} {opponent} - Final: {final_score}'),
            1 if data.get('idle_enabled') == 'on' else 0,
            data.get('idle_title', '{team_name} Programming'),
            data.get('idle_description', 'Next game: {next_date} at {next_time} vs {next_opponent}'),
            1 if data.get('enable_records') == 'on' else 0,
            1 if data.get('enable_streaks') == 'on' else 0,
            1 if data.get('enable_head_to_head') == 'on' else 0,
            1 if data.get('enable_standings') == 'on' else 0,
            1 if data.get('enable_statistics') == 'on' else 0,
            1 if data.get('enable_players') == 'on' else 0,
            data.get('midnight_crossover_mode', 'postgame'),
            data.get('max_program_hours_mode', 'default'),
            float(data['max_program_hours']) if data.get('max_program_hours') else None,
            team_id
        ))

        conn.commit()
        conn.close()

        return redirect(url_for('teams_list'))

    # GET - show form
    team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    leagues = conn.execute("SELECT * FROM league_config WHERE active = 1 ORDER BY league_name").fetchall()
    conn.close()

    if not team:
        return "Team not found", 404

    # Convert team to dict and parse JSON fields
    team_dict = dict(team)

    print(f"\n=== FORM LOAD DEBUG ===")
    print(f"Raw categories from DB: {repr(team_dict.get('categories'))}")
    print(f"Raw description_options from DB: {repr(team_dict.get('description_options'))}")

    # Parse categories - check for None, not empty list
    if team_dict.get('categories') is not None:
        try:
            team_dict['categories'] = json.loads(team_dict['categories'])
        except (json.JSONDecodeError, TypeError):
            team_dict['categories'] = []
    else:
        team_dict['categories'] = []

    # Parse description_options - check for None, not empty list
    if team_dict.get('description_options') is not None:
        try:
            team_dict['description_options'] = json.loads(team_dict['description_options'])
        except (json.JSONDecodeError, TypeError) as e:
            print(f"ERROR parsing description_options: {e}")
            print(f"String that failed to parse: {repr(team_dict.get('description_options'))}")
            team_dict['description_options'] = []
    else:
        team_dict['description_options'] = []

    # Parse flags - check for None
    if team_dict.get('flags') is not None:
        try:
            team_dict['flags'] = json.loads(team_dict['flags'])
        except (json.JSONDecodeError, TypeError) as e:
            print(f"ERROR parsing flags: {e}")
            print(f"String that failed to parse: {repr(team_dict.get('flags'))}")
            team_dict['flags'] = {'new': True, 'live': False, 'date': False, 'premiere': False}
    else:
        team_dict['flags'] = {'new': True, 'live': False, 'date': False, 'premiere': False}

    print(f"Parsed categories (Python list): {team_dict['categories']}")
    print(f"Parsed description_options (Python list): {team_dict['description_options']}")
    print(f"Parsed flags (Python dict): {team_dict['flags']}")
    print(f"======================\n")

    # Pass description_options as both parsed list and JSON string for JavaScript
    description_options_json = json.dumps(team_dict['description_options']) if team_dict.get('description_options') else '[]'

    return render_template('team_form.html', team=team_dict, leagues=[dict(l) for l in leagues], version=VERSION, description_options_json=description_options_json)

@app.route('/teams/<int:team_id>/delete', methods=['POST'])
def delete_team(team_id):
    """Delete team"""
    conn = get_connection()
    conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
    conn.commit()
    conn.close()

    return redirect(url_for('teams_list'))

@app.route('/api/teams/<int:team_id>/templates', methods=['GET'])
def get_team_templates(team_id):
    """Get template settings from a team for copying"""
    conn = get_connection()

    team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    conn.close()

    if not team:
        return jsonify({'error': 'Team not found'}), 404

    team_dict = dict(team)

    # Parse JSON fields
    if team_dict.get('description_options') and isinstance(team_dict['description_options'], str):
        team_dict['description_options'] = json.loads(team_dict['description_options'])
    if team_dict.get('flags') and isinstance(team_dict['flags'], str):
        team_dict['flags'] = json.loads(team_dict['flags'])
    if team_dict.get('categories') and isinstance(team_dict['categories'], str):
        team_dict['categories'] = json.loads(team_dict['categories'])

    # Return only template-related fields
    template_data = {
        'title_format': team_dict.get('title_format', ''),
        'subtitle_template': team_dict.get('subtitle_template', ''),
        'description_template': team_dict.get('description_template', ''),
        'description_options': team_dict.get('description_options', []),
        'flags': team_dict.get('flags', {'new': True, 'live': False, 'date': False, 'premiere': False}),
        'categories': team_dict.get('categories', []),
        'game_duration_mode': team_dict.get('game_duration_mode', 'default'),
        'game_duration_override': team_dict.get('game_duration_override'),
        'pregame_enabled': team_dict.get('pregame_enabled', True),
        'pregame_title': team_dict.get('pregame_title', ''),
        'pregame_description': team_dict.get('pregame_description', ''),
        'postgame_enabled': team_dict.get('postgame_enabled', True),
        'postgame_title': team_dict.get('postgame_title', ''),
        'postgame_description': team_dict.get('postgame_description', ''),
        'idle_title': team_dict.get('idle_title', ''),
        'idle_description': team_dict.get('idle_description', ''),
    }

    return jsonify(template_data)

@app.route('/api/teams/list', methods=['GET'])
def get_teams_list():
    """Get list of all teams for template copy dropdown"""
    conn = get_connection()

    teams = conn.execute("""
        SELECT t.id, t.team_name, t.league, t.sport, lc.league_name
        FROM teams t
        LEFT JOIN league_config lc ON t.league = lc.league_code
        WHERE t.active = 1
        ORDER BY t.team_name
    """).fetchall()

    conn.close()

    teams_list = [dict(t) for t in teams]
    return jsonify(teams_list)

@app.route('/api/teams/batch-copy', methods=['POST'])
def batch_copy_templates():
    """Copy templates from one team to multiple teams"""
    data = request.json
    source_team_id = data.get('source_team_id')
    target_team_ids = data.get('target_team_ids', [])
    fields = data.get('fields', [])

    if not source_team_id or not target_team_ids or not fields:
        return jsonify({'error': 'Missing required parameters'}), 400

    conn = get_connection()

    try:
        # Get source team templates
        source_team = conn.execute("SELECT * FROM teams WHERE id = ?", (source_team_id,)).fetchone()
        if not source_team:
            return jsonify({'error': 'Source team not found'}), 404

        source_dict = dict(source_team)

        # Parse JSON fields from source
        if source_dict.get('description_options') and isinstance(source_dict['description_options'], str):
            source_dict['description_options'] = json.loads(source_dict['description_options'])
        if source_dict.get('flags') and isinstance(source_dict['flags'], str):
            source_dict['flags'] = json.loads(source_dict['flags'])
        if source_dict.get('categories') and isinstance(source_dict['categories'], str):
            source_dict['categories'] = json.loads(source_dict['categories'])

        # Build UPDATE query dynamically based on selected fields
        update_parts = []
        params = []

        for field in fields:
            if field in source_dict:
                value = source_dict[field]
                # Re-serialize JSON fields
                if field in ['description_options', 'flags', 'categories'] and isinstance(value, (list, dict)):
                    value = json.dumps(value)
                update_parts.append(f"{field} = ?")
                params.append(value)

        if not update_parts:
            return jsonify({'error': 'No valid fields to copy'}), 400

        # Update each target team
        updated_count = 0
        for target_id in target_team_ids:
            # Check if target team exists
            target_team = conn.execute("SELECT id FROM teams WHERE id = ?", (target_id,)).fetchone()
            if target_team:
                query = f"UPDATE teams SET {', '.join(update_parts)} WHERE id = ?"
                conn.execute(query, params + [target_id])
                updated_count += 1

        conn.commit()
        conn.close()

        return jsonify({'success': True, 'updated_count': updated_count})

    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500

@app.route('/api/condition-presets', methods=['GET'])
def get_condition_presets():
    """Get all condition presets from library"""
    conn = get_connection()

    presets = conn.execute("""
        SELECT id, name, description, condition_type, condition_value, priority, template, usage_count
        FROM condition_presets
        ORDER BY usage_count DESC, name
    """).fetchall()

    conn.close()

    presets_list = [dict(p) for p in presets]
    return jsonify(presets_list)

@app.route('/api/condition-presets', methods=['POST'])
def create_condition_preset():
    """Create a new condition preset"""
    data = request.json
    conn = get_connection()

    try:
        conn.execute("""
            INSERT INTO condition_presets (name, description, condition_type, condition_value, priority, template)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            data.get('name'),
            data.get('description', ''),
            data.get('condition_type'),
            data.get('condition_value', ''),
            data.get('priority', 50),
            data.get('template')
        ))
        conn.commit()
        preset_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return jsonify({'success': True, 'id': preset_id})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/condition-presets/<int:preset_id>/increment', methods=['POST'])
def increment_preset_usage(preset_id):
    """Increment usage count when preset is used"""
    conn = get_connection()
    conn.execute("UPDATE condition_presets SET usage_count = usage_count + 1 WHERE id = ?", (preset_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/condition-presets/<int:preset_id>', methods=['DELETE'])
def delete_condition_preset(preset_id):
    """Delete a condition preset"""
    conn = get_connection()
    conn.execute("DELETE FROM condition_presets WHERE id = ?", (preset_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/variables')
def get_variables():
    """Serve variable schema for UI and validation"""
    from pathlib import Path
    variables_file = Path(__file__).parent / 'config' / 'variables.json'

    try:
        with open(variables_file, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except FileNotFoundError:
        return jsonify({'error': 'Variables file not found'}), 404
    except json.JSONDecodeError as e:
        return jsonify({'error': f'Invalid JSON: {str(e)}'}), 500

def get_game_duration(team, settings):
    """
    Get game duration for a team based on mode

    Args:
        team: Team dict with sport, game_duration_mode, and game_duration_override fields
        settings: Settings dict with game_duration_default field

    Returns:
        float: Game duration in hours
    """
    mode = team.get('game_duration_mode', 'default')

    if mode == 'custom':
        # Use custom override
        return float(team.get('game_duration_override', 4.0))
    elif mode == 'sport':
        # Use sport-specific recommendation
        sport = team.get('sport', 'basketball').lower()
        sport_defaults = {
            'basketball': 3.0,
            'football': 3.5,
            'soccer': 2.0,
            'baseball': 3.0,
            'hockey': 3.0
        }
        return float(sport_defaults.get(sport, 3.0))
    else:  # mode == 'default'
        # Use global default from settings
        return float(settings.get('game_duration_default', 4.0))

@app.route('/generate', methods=['POST'])
def generate_epg():
    """Generate EPG file"""
    import sys
    import traceback

    start_time = datetime.now()

    conn = get_connection()

    try:
        # Get active teams
        teams = conn.execute("""
            SELECT t.*, lc.league_name, lc.api_path, lc.default_category as league_category
            FROM teams t
            LEFT JOIN league_config lc ON t.league = lc.league_code
            WHERE t.active = 1
        """).fetchall()

        teams_list = []
        for t in teams:
            team_dict = dict(t)
            # Parse JSON fields
            if team_dict.get('flags') and isinstance(team_dict['flags'], str):
                team_dict['flags'] = json.loads(team_dict['flags'])
            if team_dict.get('categories') and isinstance(team_dict['categories'], str):
                team_dict['categories'] = json.loads(team_dict['categories'])
            if team_dict.get('description_options') and isinstance(team_dict['description_options'], str):
                team_dict['description_options'] = json.loads(team_dict['description_options'])
            teams_list.append(team_dict)

        if not teams_list:
            return jsonify({'error': 'No active teams configured'}), 400

        # Get settings
        settings_row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        settings = dict(settings_row) if settings_row else {}

        # Allow days_ahead override from POST parameter
        try:
            days_ahead = int(request.form.get('days_ahead', settings.get('epg_days_ahead', 14)))
        except (ValueError, TypeError):
            days_ahead = settings.get('epg_days_ahead', 14)

        epg_timezone = settings.get('default_timezone', 'America/New_York')

        # Calculate EPG start date (today in EPG timezone)
        from zoneinfo import ZoneInfo
        epg_tz = ZoneInfo(epg_timezone)
        epg_start_date = datetime.now(epg_tz).date()

        # Fetch schedules for each team
        all_events = {}
        api_calls = 0

        for team in teams_list:
            # Fetch team stats (record, standings, etc.)
            team_data = espn.get_team_info(team['sport'], team['league'], team['espn_team_id'])
            team_stats_basic = espn.get_team_stats(team['sport'], team['league'], team['espn_team_id'])
            api_calls += 1

            # Extract team logo from ESPN data if not already set
            if team_data and 'team' in team_data and not team.get('team_logo_url'):
                logos = team_data['team'].get('logos', [])
                if logos and len(logos) > 0:
                    team['team_logo_url'] = logos[0].get('href', '')

            # Fetch enhanced team stats (streaks, PPG, standings, home/away records)
            enhanced_stats = espn.get_team_stats(team['sport'], team['league'], team['espn_team_id'])
            api_calls += 1

            # Merge basic and enhanced stats first (without streak_type)
            team_stats = {**team_stats_basic, **enhanced_stats}

            # Fetch schedule from ESPN
            schedule_data = espn.get_team_schedule(
                team['sport'],
                team['league'],
                team['espn_team_id'],
                days_ahead
            )
            api_calls += 1

            # Fetch extended schedule for context (next/last game info beyond EPG window)
            extended_schedule_data = espn.get_team_schedule(
                team['sport'],
                team['league'],
                team['espn_team_id'],
                30  # Look 30 days ahead for context
            )
            api_calls += 1

            if schedule_data:
                # Parse events (only within EPG window)
                events = espn.parse_schedule_events(schedule_data, days_ahead)

                # Enrich today's games with scoreboard data (odds, conferenceCompetition, etc.)
                api_counter = {'count': api_calls}
                events = _enrich_with_scoreboard(events, team, espn, api_counter, epg_timezone)
                api_calls = api_counter['count']

                # Parse extended events (for context only)
                # Include past 30 days for last game context with scores
                extended_events = espn.parse_schedule_events(extended_schedule_data, days_ahead=30, days_behind=30) if extended_schedule_data else []

                # Enrich past events with scoreboard data to get actual scores
                if extended_events:
                    from zoneinfo import ZoneInfo
                    now_utc = datetime.now(ZoneInfo('UTC'))

                    # Filter to past events using the 'date' string field
                    past_events = []
                    for e in extended_events:
                        if e.get('date'):
                            event_dt = datetime.fromisoformat(e['date'].replace('Z', '+00:00'))
                            if event_dt < now_utc:
                                past_events.append(e)

                    if past_events:
                        # Group by date
                        past_by_date = {}
                        for event in past_events:
                            event_dt = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
                            date_str = event_dt.strftime('%Y%m%d')
                            if date_str not in past_by_date:
                                past_by_date[date_str] = []
                            past_by_date[date_str].append(event)

                        # Fetch scoreboards for last 7 days (to control API calls)
                        for date_str in sorted(past_by_date.keys(), reverse=True)[:7]:
                            scoreboard = espn.get_scoreboard(team['sport'], team['league'], date_str)
                            api_calls += 1

                            if scoreboard and 'events' in scoreboard:
                                for sb_event in scoreboard['events']:
                                    event_id = str(sb_event.get('id', ''))
                                    for our_event in past_by_date[date_str]:
                                        if str(our_event.get('id', '')) == event_id:
                                            # Update scores from scoreboard data
                                            # Extract scores from scoreboard competitors
                                            if 'competitions' in sb_event and len(sb_event['competitions']) > 0:
                                                sb_competitors = sb_event['competitions'][0].get('competitors', [])
                                                for sb_comp in sb_competitors:
                                                    team_id = str(sb_comp.get('team', {}).get('id', ''))
                                                    score = sb_comp.get('score')

                                                    # Update score in our event's home/away teams
                                                    if our_event['home_team']['id'] == team_id:
                                                        our_event['home_team']['score'] = score
                                                    elif our_event['away_team']['id'] == team_id:
                                                        our_event['away_team']['score'] = score

                                                # Update status to mark as completed (preserve parsed structure)
                                                sb_status = sb_event['competitions'][0].get('status', {})
                                                if sb_status and 'type' in sb_status:
                                                    our_event['status']['name'] = sb_status['type'].get('name', our_event['status'].get('name', ''))
                                                    our_event['status']['state'] = sb_status['type'].get('state', our_event['status'].get('state', ''))
                                                    our_event['status']['completed'] = sb_status['type'].get('completed', our_event['status'].get('completed', False))
                                                    our_event['status']['detail'] = sb_status['type'].get('detail', our_event['status'].get('detail', ''))
                                            break

                # Cache for opponent stats to avoid duplicate API calls
                opponent_stats_cache = {}

                # Process each event (add templates, times)
                processed_events = []
                for event in events:
                    # Identify opponent
                    our_team_id = str(team_data.get('team', {}).get('id', '')) if team_data else ''
                    home_team = event.get('home_team', {})
                    away_team = event.get('away_team', {})

                    # Determine which team is the opponent
                    is_home = str(home_team.get('id', '')) == our_team_id
                    opponent = away_team if is_home else home_team
                    opp_id = opponent.get('id', '')

                    # Fetch opponent stats if not already in cache
                    if opp_id and opp_id not in opponent_stats_cache:
                        # Fetch enhanced opponent stats
                        opp_enhanced = espn.get_team_stats(team['sport'], team['league'], opp_id)
                        api_calls += 1

                        opponent_stats_cache[opp_id] = opp_enhanced

                    opponent_stats = opponent_stats_cache.get(opp_id, {})

                    processed = _process_event(event, team, team_stats, opponent_stats, epg_timezone, schedule_data, settings)
                    if processed:
                        processed_events.append(processed)

                # Process extended events for context (next/last game lookups)
                extended_processed_events = []
                for event in extended_events:
                    # For extended events, we don't need full opponent stats - just basic game data
                    processed = _process_event(event, team, team_stats, {}, epg_timezone, schedule_data, settings)
                    if processed:
                        extended_processed_events.append(processed)

                # Generate filler entries (pregame/postgame/idle)
                # Pass extended events for next/last game context
                filler_entries = _generate_filler_entries(team, processed_events, days_ahead, team_stats, epg_timezone, extended_processed_events, epg_start_date, espn, team.get('api_path', ''), settings)

                # Combine game events and filler entries, then sort by start time
                combined_events = processed_events + filler_entries
                combined_events.sort(key=lambda x: x['start_datetime'])

                all_events[str(team['id'])] = combined_events

        # Generate XMLTV
        xml_content = xmltv_gen.generate(teams_list, all_events, settings)

        # Save to file
        output_path = settings.get('epg_output_path', '/app/data/teamarr.xml')
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(xml_content)

        # Calculate stats
        file_size = os.path.getsize(output_path)
        file_hash = xmltv_gen.calculate_file_hash(xml_content)
        generation_time = (datetime.now() - start_time).total_seconds()

        total_programmes = sum(len(events) for events in all_events.values())
        # Count actual events (non-filler)
        total_events = sum(len([e for e in events if e.get('status') not in ['filler']]) for events in all_events.values())

        # Log to history
        conn.execute("""
            INSERT INTO epg_history (
                file_path, file_size, num_channels, num_programmes, num_events,
                generation_time_seconds, api_calls_made, file_hash, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            output_path, file_size, len(teams_list), total_programmes, total_events,
            generation_time, api_calls, file_hash, 'success'
        ))

        conn.commit()

        return jsonify({
            'success': True,
            'file_path': output_path,
            'file_size': file_size,
            'num_channels': len(teams_list),
            'num_programmes': total_programmes,
            'num_events': total_events,
            'generation_time': generation_time,
            'api_calls': api_calls
        })

    except Exception as e:
        # Write full traceback to file for debugging
        with open('/tmp/teamarr_error.txt', 'w') as f:
            f.write("="*80 + "\n")
            f.write("EXCEPTION IN GENERATE_EPG (main handler):\n")
            traceback.print_exc(file=f)
            f.write("="*80 + "\n")

        # Log error
        conn.execute("""
            INSERT INTO error_log (level, category, message, details)
            VALUES (?, ?, ?, ?)
        """, ('ERROR', 'GENERATION', str(e), json.dumps({'error': str(e)})))

        conn.commit()

        return jsonify({'error': str(e)}), 500

    finally:
        conn.close()

@app.route('/teamarr.xml')
def serve_epg():
    """Serve the EPG file at /teamarr.xml for direct IPTV integration"""
    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()

    output_path = settings.get('epg_output_path', '/app/data/teamarr.xml')

    if not os.path.exists(output_path):
        return "EPG file not found. Generate it first.", 404

    return send_file(output_path, mimetype='application/xml')

@app.route('/download')
def download_epg():
    """Download generated EPG file"""
    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()

    output_path = settings.get('epg_output_path', '/app/data/teamarr.xml')

    if not os.path.exists(output_path):
        return "EPG file not found. Generate it first.", 404

    return send_file(output_path, as_attachment=True, download_name='teamarr.xml')

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    """Application settings"""
    conn = get_connection()

    if request.method == 'POST':
        data = request.form.to_dict()

        conn.execute("""
            UPDATE settings SET
                epg_days_ahead = ?,
                epg_update_time = ?,
                epg_output_path = ?,
                cache_enabled = ?,
                cache_duration_hours = ?,
                default_timezone = ?,
                auto_generate_enabled = ?,
                auto_generate_frequency = ?,
                xmltv_generator_name = ?,
                xmltv_generator_url = ?,
                game_duration_default = ?,
                max_program_hours_default = ?
            WHERE id = 1
        """, (
            int(data.get('epg_days_ahead', 14)),
            data.get('epg_update_time', '00:00'),
            '/app/data/teamarr.xml',  # Fixed filename
            1 if data.get('cache_enabled') == 'on' else 0,
            int(data.get('cache_duration_hours', 24)),
            data.get('default_timezone', 'America/New_York'),
            1 if data.get('auto_generate_enabled') == 'on' else 0,
            data.get('auto_generate_frequency', 'daily'),
            data.get('xmltv_generator_name', ''),
            data.get('xmltv_generator_url', ''),
            float(data.get('game_duration_default', 3.0)),
            float(data.get('max_program_hours_default', 6.0))
        ))

        conn.commit()
        conn.close()

        return redirect(url_for('index'))

    # GET
    settings_row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    conn.close()

    return render_template('settings.html', settings=dict(settings_row), version=VERSION)

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/api/parse-espn-url', methods=['POST'])
def parse_espn_url():
    """Parse ESPN team URL to extract team info"""
    url = request.json.get('url', '')

    team_info = espn.extract_team_from_url(url)

    if not team_info:
        return jsonify({'error': 'Could not parse ESPN URL'}), 400

    # For soccer, we need to detect the actual league from team data
    # since the URL doesn't specify (could be EPL, EFL, NWSL, MLS, etc.)
    if team_info['sport'] == 'soccer':
        # Map API paths to league codes (from league_config table)
        api_path_to_league_code = {
            'usa.1': 'mls',
            'usa.w.1': 'nwsl',
            'eng.1': 'epl',
            'eng.2': 'efl',
            'esp.1': 'laliga',
            'ger.1': 'bundesliga',
            'ita.1': 'seriea',
            'fra.1': 'ligue1'
        }

        # Try multiple possible leagues to find the team
        possible_api_paths = ['usa.1', 'usa.w.1', 'eng.1', 'eng.2', 'esp.1', 'ger.1', 'ita.1', 'fra.1']
        team_data = None

        for api_path in possible_api_paths:
            test_data = espn.get_team_info(
                team_info['sport'],
                api_path,
                team_info['team_slug']
            )
            if test_data and 'team' in test_data:
                team_data = test_data
                # Map API path back to league code
                team_info['league'] = api_path_to_league_code.get(api_path, api_path)
                break

        if not team_data:
            return jsonify({'error': 'Could not find team in any soccer league'}), 404
    else:
        # Try to fetch team data
        team_data = espn.get_team_info(
            team_info['sport'],
            team_info['league'],
            team_info['team_slug']
        )

    if team_data and 'team' in team_data:
        team = team_data['team']

        # Extract mascot from team data
        # Try these in order: nickname, name (last word), slug (last part)
        mascot = None

        # Option 1: Use 'nickname' field if available (e.g., "Pistons")
        if team.get('nickname'):
            mascot = team.get('nickname')
        # Option 2: Extract from 'name' or 'displayName' (e.g., "Detroit Pistons" -> "Pistons")
        elif team.get('name'):
            mascot = team.get('name').split()[-1]
        elif team.get('displayName'):
            mascot = team.get('displayName').split()[-1]
        # Option 3: Fallback to slug parsing (e.g., "detroit-pistons" -> "pistons")
        elif team_info['team_slug']:
            parts = team_info['team_slug'].split('-')
            mascot = parts[-1] if len(parts) > 1 else team_info['team_slug']

        # Generate channel_id as {team_slug}.{league}
        # Use the full team slug (e.g., "detroit-pistons") instead of just mascot
        channel_id = f"{team_info['team_slug']}.{team_info['league']}"

        # Use numeric team ID from ESPN API for espn_team_id (needed for schedule matching)
        # The numeric ID is what ESPN uses in schedule/game data
        team_id = str(team.get('id', team_info['team_slug']))

        return jsonify({
            'espn_team_id': team_id,
            'league': team_info['league'],
            'sport': team_info['sport'],
            'team_name': team.get('displayName', ''),
            'team_abbrev': team.get('abbreviation', ''),
            'team_logo_url': team.get('logos', [{}])[0].get('href', ''),
            'team_color': team.get('color', ''),
            'channel_id': channel_id
        })

    return jsonify(team_info)

@app.route('/api/preview-template', methods=['POST'])
def preview_template():
    """Preview template with real team data"""
    data = request.json

    template_text = data.get('template', '')
    sport = data.get('sport', '')
    league = data.get('league', '')
    team_slug = data.get('espn_team_id', '')

    if not all([template_text, sport, league, team_slug]):
        return jsonify({'error': 'Missing required fields'}), 400

    try:
        # Fetch real team data
        team_data = espn.get_team_info(sport, league, team_slug)
        team_stats = espn.get_team_stats(team['sport'], team['league'], team['espn_team_id'])

        # Fetch schedule to get next/last game info
        schedule_data = espn.get_team_schedule(sport, league, team_slug, 14)

        if schedule_data:
            events = espn.parse_schedule_events(schedule_data, 14)

            # Find next and last game
            from datetime import timezone as dt_timezone
            now = datetime.now(dt_timezone.utc)
            next_game = None
            last_game = None

            for event in events:
                game_dt = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))

                if game_dt > now and not next_game:
                    next_game = event
                elif game_dt < now:
                    last_game = event

            # Build context with real data
            context = {
                'team_config': {
                    'team_name': team_data.get('team', {}).get('displayName', '') if team_data else '',
                    'team_abbrev': team_data.get('team', {}).get('abbreviation', '') if team_data else '',
                },
                'team_stats': team_stats,
                'opponent_stats': {},
                'h2h': {}
            }

            # Add next game context if available
            if next_game:
                context['game'] = next_game

                # Identify opponent for next game
                our_team_id = str(team_data.get('team', {}).get('id', '')) if team_data else ''
                home_team = next_game.get('home_team', {})
                away_team = next_game.get('away_team', {})
                is_home = str(home_team.get('id', '')) == our_team_id
                opponent = away_team if is_home else home_team

                # Fetch opponent stats
                opp_id = opponent.get('id', '')
                if opp_id:
                    context['opponent_stats'] = espn.get_team_stats(sport, league, str(opp_id))

            # Add last game context if available
            elif last_game:
                context['game'] = last_game

                # Identify opponent for last game
                our_team_id = str(team_data.get('team', {}).get('id', '')) if team_data else ''
                home_team = last_game.get('home_team', {})
                away_team = last_game.get('away_team', {})
                is_home = str(home_team.get('id', '')) == our_team_id
                opponent = away_team if is_home else home_team

                # Fetch opponent stats
                opp_id = opponent.get('id', '')
                if opp_id:
                    context['opponent_stats'] = espn.get_team_stats(sport, league, str(opp_id))

            # Resolve template
            resolved = template_engine.resolve(template_text, context)

            return jsonify({
                'preview': resolved,
                'has_data': True
            })
        else:
            # No schedule data - return template with basic team info only
            context = {
                'team_config': {
                    'team_name': team_data.get('team', {}).get('displayName', '') if team_data else '',
                    'team_abbrev': team_data.get('team', {}).get('abbreviation', '') if team_data else '',
                },
                'team_stats': team_stats,
                'opponent_stats': {},
                'h2h': {},
                'game': {}
            }

            resolved = template_engine.resolve(template_text, context)

            return jsonify({
                'preview': resolved,
                'has_data': False
            })

    except Exception as e:
        # Write full traceback to file
        with open('/tmp/teamarr_error.txt', 'w') as f:
            f.write("="*80 + "\n")
            f.write("EXCEPTION IN GENERATE_EPG:\n")
            traceback.print_exc(file=f)
            f.write("="*80 + "\n")
        return jsonify({'error': str(e)}), 500

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _enrich_with_scoreboard(events: List[dict], team: dict, espn_client, api_calls_counter: dict, epg_timezone: str = 'America/New_York') -> List[dict]:
    """
    Enrich today's games with scoreboard data (odds, conferenceCompetition, etc.)

    Args:
        events: List of parsed schedule events
        team: Team configuration dict
        espn_client: ESPN API client instance
        api_calls_counter: Dict with 'count' key to track API calls
        epg_timezone: User's EPG timezone (e.g., 'America/New_York')

    Returns:
        List of enriched events (today's games have scoreboard data merged)
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    # Get today's date string in USER'S timezone (not UTC!)
    user_tz = ZoneInfo(epg_timezone)
    today_str = datetime.now(user_tz).strftime('%Y%m%d')

    # Debug: Log all events being checked
    with open('/tmp/enrichment_debug.txt', 'a') as f:
        f.write(f"\n{'='*80}\n")
        f.write(f"Team: {team.get('team_name', 'team')}\n")
        f.write(f"Today: {today_str} in {epg_timezone}\n")
        f.write(f"Events to check: {len(events)}\n")

    # Check if any events are today (in user's timezone)
    has_today_games = False
    for event in events:
        try:
            # Parse UTC event date and convert to user's timezone
            event_date_utc = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
            event_date_local = event_date_utc.astimezone(user_tz)
            event_date_str = event_date_local.strftime('%Y%m%d')

            # Debug: Log each event check
            with open('/tmp/enrichment_debug.txt', 'a') as f:
                f.write(f"  Event {event.get('id')}: {event['date']} -> {event_date_str} (today={event_date_str == today_str})\n")

            if event_date_str == today_str:
                has_today_games = True
                with open('/tmp/enrichment_debug.txt', 'a') as f:
                    f.write(f"  ✅ MATCH! This event is today!\n")
                # Don't break - continue checking to log all events
        except Exception as e:
            with open('/tmp/enrichment_debug.txt', 'a') as f:
                f.write(f"  ⚠️  Error: {e}\n")
            continue

    # If no today's games, return unchanged
    if not has_today_games:
        print(f"  ⏭️  No today's games for {team.get('team_name', 'team')}, skipping scoreboard enrichment")
        return events

    # Fetch scoreboard for today
    print(f"  📊 Fetching scoreboard for {team.get('team_name', 'team')} (today: {today_str} in {epg_timezone})")
    scoreboard_data = espn_client.get_scoreboard(team['sport'], team['league'], today_str)
    api_calls_counter['count'] += 1

    if not scoreboard_data or 'events' not in scoreboard_data:
        return events

    # Parse scoreboard events
    scoreboard_events = espn_client.parse_schedule_events(scoreboard_data, 1)
    print(f"  📋 Found {len(scoreboard_events)} scoreboard events")

    # Create lookup by event ID
    scoreboard_lookup = {e['id']: e for e in scoreboard_events}

    # Merge scoreboard data into schedule events
    enriched_events = []
    enriched_count = 0
    for event in events:
        event_id = event.get('id')

        # If this event has scoreboard data, merge it
        if event_id in scoreboard_lookup:
            scoreboard_event = scoreboard_lookup[event_id]

            # Deep merge: scoreboard competitions have priority
            if 'competitions' in scoreboard_event:
                event['competitions'] = scoreboard_event['competitions']

                # Normalize scoreboard broadcast format to match schedule format
                # Scoreboard API: {'market': 'national', 'names': ['ESPN+']}
                # Schedule API: {'type': {...}, 'market': {'type': 'National'}, 'media': {'shortName': 'ESPN+'}}
                comp = scoreboard_event['competitions'][0] if scoreboard_event['competitions'] else {}
                if 'broadcasts' in comp:
                    normalized_broadcasts = []
                    for b in comp['broadcasts']:
                        if isinstance(b, dict) and 'market' in b and isinstance(b['market'], str):
                            # This is scoreboard format - normalize it
                            market_str = b['market']
                            # Capitalize first letter: 'national' -> 'National', 'home' -> 'Home'
                            market_type = market_str.capitalize()

                            # Get network name from names array
                            network_name = b.get('names', [None])[0]

                            # Convert to schedule format
                            normalized = {
                                'type': {'id': '1', 'shortName': 'TV'},  # Default to TV
                                'market': {'type': market_type},
                                'media': {'shortName': network_name} if network_name else {}
                            }
                            normalized_broadcasts.append(normalized)
                        else:
                            # Already in schedule format, keep as-is
                            normalized_broadcasts.append(b)

                    comp['broadcasts'] = normalized_broadcasts

                # Check if we got odds and SET THE FLAG ON THE EVENT
                has_odds = bool(comp.get('odds'))
                event['has_odds'] = has_odds  # ← THIS WAS MISSING!
                print(f"  ✅ Enriched event {event_id}: has_odds={has_odds}")
                enriched_count += 1

            # Also merge any other scoreboard-specific fields at event level
            for key in ['uid', 'season', 'status']:
                if key in scoreboard_event:
                    event[key] = scoreboard_event[key]

        enriched_events.append(event)

    print(f"  🎯 Enriched {enriched_count}/{len(events)} events with scoreboard data")
    return enriched_events

def _find_next_game(current_date: date, game_schedule: dict, game_dates: set) -> dict:
    """
    Find the next game after the given date.

    Args:
        current_date: The EPG date to search from
        game_schedule: Dict mapping dates to lists of game events
        game_dates: Set of dates with games

    Returns:
        Next game event dict (raw game data), or None if no future games
    """
    for future_date in sorted(game_dates):
        if future_date > current_date:
            future_games = game_schedule[future_date]
            if future_games:
                # Return the earliest game on that date (by start time)
                return sorted(future_games, key=lambda x: x['start'])[0]['event']
    return None


def _find_last_game(current_date: date, game_schedule: dict, game_dates: set) -> dict:
    """
    Find the most recent game before the given EPG date (for date-relative filler).
    Returns the game regardless of completion status - scores will show if available.

    Args:
        current_date: The EPG date to search from
        game_schedule: Dict mapping dates to lists of game events
        game_dates: Set of dates with games

    Returns:
        Last game event dict (raw game data), or None if no past games
    """
    for past_date in sorted(game_dates, reverse=True):
        if past_date < current_date:
            past_games = game_schedule[past_date]
            if past_games:
                # Return the latest game on that date (by end time)
                # Note: Game may or may not have scores depending on if it's been played
                return sorted(past_games, key=lambda x: x['end'])[-1]['event']
    return None


def _generate_filler_entries(team: dict, game_events: List[dict], days_ahead: int, team_stats: dict = None, epg_timezone: str = 'America/New_York', extended_events: List[dict] = None, epg_start_date: date = None, espn_client = None, api_path: str = '', settings: dict = None) -> List[dict]:
    """
    Generate pregame, postgame, and idle EPG entries to fill gaps

    Args:
        team: Team configuration with filler settings
        game_events: List of actual game events in EPG window (sorted by date)
        days_ahead: Number of days in EPG window
        team_stats: Team stats for template resolution
        epg_timezone: Timezone for EPG generation
        extended_events: Extended list of game events (beyond EPG window) for next/last game context
        epg_start_date: Start date for EPG generation (defaults to today if not specified)
        espn_client: ESPN API client for fetching opponent stats
        settings: Global settings (for defaults like max_program_hours_default)

    Returns:
        List of filler event dictionaries
    """
    filler_entries = []

    # Use EPG timezone for filler generation (not team timezone)
    team_tz = ZoneInfo(epg_timezone)

    # Get max program hours (for splitting long periods)
    # Check mode - use default or custom
    max_hours_mode = team.get('max_program_hours_mode', 'default')
    if max_hours_mode == 'default' and settings:
        max_hours = settings.get('max_program_hours_default', 6.0)
    else:
        max_hours = team.get('max_program_hours', 6.0)

    # Get midnight crossover mode
    midnight_mode = team.get('midnight_crossover_mode', 'postgame')

    # Build date range for EPG window
    if epg_start_date is None:
        # Default to current date if not specified
        now = datetime.now(team_tz)
        start_date = now.date()
    else:
        # Use specified EPG start date
        start_date = epg_start_date

    end_date = start_date + timedelta(days=days_ahead)

    # Create a set of game dates for quick lookup (only EPG window games)
    game_dates = set()
    game_schedule = {}  # date -> game info (only EPG window)

    for event in game_events:
        game_dt = event['start_datetime'].astimezone(team_tz)
        game_date = game_dt.date()
        game_dates.add(game_date)

        # Store game info for this date
        if game_date not in game_schedule:
            game_schedule[game_date] = []
        game_schedule[game_date].append({
            'start': event['start_datetime'],
            'end': event['end_datetime'],
            'event': event.get('game', event)  # Get raw game data if available
        })

    # Build extended schedule for next/last game context (beyond EPG window)
    extended_game_dates = set()
    extended_game_schedule = {}

    if extended_events:
        for event in extended_events:
            game_dt = event['start_datetime'].astimezone(team_tz)
            game_date = game_dt.date()
            extended_game_dates.add(game_date)

            if game_date not in extended_game_schedule:
                extended_game_schedule[game_date] = []
            extended_game_schedule[game_date].append({
                'start': event['start_datetime'],
                'end': event['end_datetime'],
                'event': event.get('game', event)
            })

    # Process each day in the EPG window
    current_date = start_date
    while current_date <= end_date:
        # Get midnight times for this day
        day_start = datetime.combine(current_date, datetime.min.time()).replace(tzinfo=team_tz)
        day_end = day_start + timedelta(days=1)

        if current_date in game_dates:
            # This day has game(s)
            games_today = sorted(game_schedule[current_date], key=lambda x: x['start'])

            # PREGAME: Fill from midnight to first game start
            if team.get('pregame_enabled', True):
                first_game_start = games_today[0]['start']

                # Only add pregame if there's a gap
                if day_start < first_game_start:
                    # Find last game before this pregame period (use extended schedule for context)
                    last_game = _find_last_game(current_date, extended_game_schedule or game_schedule, extended_game_dates or game_dates)

                    pregame_entries = _create_filler_chunks(
                        day_start, first_game_start, max_hours,
                        team, 'pregame', games_today[0]['event'], team_stats, last_game, epg_timezone, espn_client, api_path
                    )
                    filler_entries.extend(pregame_entries)

            # POSTGAME: Fill from last game end to midnight
            if team.get('postgame_enabled', True):
                last_game_end = games_today[-1]['end']

                # Check if game crosses midnight
                if last_game_end > day_end:
                    # Game crosses midnight - check next day
                    next_date = current_date + timedelta(days=1)

                    if midnight_mode == 'postgame':
                        # Continue postgame coverage into next day
                        # Fill from game end to next midnight
                        next_day_end = day_end + timedelta(days=1)
                        # For postgame, the game we just finished IS the last game
                        postgame_entries = _create_filler_chunks(
                            last_game_end, next_day_end, max_hours,
                            team, 'postgame', games_today[-1]['event'], team_stats, games_today[-1]['event'], epg_timezone, espn_client, api_path
                        )
                        filler_entries.extend(postgame_entries)
                    elif midnight_mode == 'idle':
                        # Use idle content after game ends (if idle is enabled)
                        if team.get('idle_enabled', True):
                            # Fill from game end to next midnight with idle content
                            next_day_end = day_end + timedelta(days=1)
                            # Find next game for idle context
                            next_game = _find_next_game(next_date, extended_game_schedule or game_schedule, extended_game_dates or game_dates)
                            idle_entries = _create_filler_chunks(
                                last_game_end, next_day_end, max_hours,
                                team, 'idle', next_game, team_stats, games_today[-1]['event'], epg_timezone, espn_client, api_path
                            )
                            filler_entries.extend(idle_entries)
                else:
                    # Game ends before midnight - fill to midnight
                    if last_game_end < day_end:
                        # For postgame, the game we just finished IS the last game
                        postgame_entries = _create_filler_chunks(
                            last_game_end, day_end, max_hours,
                            team, 'postgame', games_today[-1]['event'], team_stats, games_today[-1]['event'], epg_timezone, espn_client, api_path
                        )
                        filler_entries.extend(postgame_entries)

        else:
            # IDLE: No game today - check if previous day's game crossed midnight
            prev_date = current_date - timedelta(days=1)

            # Check if we should skip this day due to midnight crossover
            skip_idle = False
            if prev_date in game_dates:
                prev_games = game_schedule[prev_date]
                if prev_games:
                    last_prev_game = sorted(prev_games, key=lambda x: x['end'])[-1]
                    if last_prev_game['end'] > day_start:
                        # Previous game crosses into today
                        # Skip idle content regardless of mode - we already filled it
                        # (either with postgame or idle content from yesterday)
                        skip_idle = True

            if not skip_idle and team.get('idle_enabled', True):
                # Find next game after current_date (use extended schedule for 30 days ahead)
                next_game = _find_next_game(current_date, extended_game_schedule or game_schedule, extended_game_dates or game_dates)

                # Find last COMPLETED game before current_date (use extended schedule with enriched scores from past 30 days)
                last_game = _find_last_game(current_date, extended_game_schedule or game_schedule, extended_game_dates or game_dates)

                # Fill entire day with idle content
                idle_entries = _create_filler_chunks(
                    day_start, day_end, max_hours,
                    team, 'idle', next_game, team_stats, last_game, epg_timezone, espn_client, api_path
                )
                filler_entries.extend(idle_entries)

        current_date += timedelta(days=1)

    return filler_entries


def _create_filler_chunks(start_dt: datetime, end_dt: datetime, max_hours: int,
                          team: dict, filler_type: str, game_event: dict = None,
                          team_stats: dict = None, last_game_event: dict = None,
                          epg_timezone: str = 'America/New_York', espn_client = None,
                          api_path: str = '') -> List[dict]:
    """
    Create filler EPG entries, splitting into chunks based on max_hours

    Args:
        start_dt: Start datetime
        end_dt: End datetime
        max_hours: Maximum hours per entry
        team: Team configuration
        filler_type: 'pregame', 'postgame', or 'idle'
        game_event: Associated game event (for pregame/postgame)
        team_stats: Team stats for template resolution

    Returns:
        List of filler event dictionaries
    """
    chunks = []

    # Calculate total duration in hours
    total_duration = (end_dt - start_dt).total_seconds() / 3600

    # Calculate number of chunks needed
    num_chunks = max(1, int(total_duration / max_hours) + (1 if total_duration % max_hours > 0 else 0))
    chunk_duration = timedelta(hours=total_duration / num_chunks)

    # Get templates for this filler type
    # Database column names match filler type: 'idle', 'pregame', 'postgame'
    title_template = team.get(f'{filler_type}_title', f'{filler_type.capitalize()} Coverage')
    desc_template = team.get(f'{filler_type}_description', '')

    # Build context for template resolution
    context = {
        'team_config': team,
        'team_stats': team_stats or {},
        'opponent_stats': {},
        'h2h': {},
        'epg_timezone': epg_timezone  # Pass EPG timezone for time conversions
    }

    # Add game context if available
    if game_event:
        # For idle fillers, DON'T set game context (would use wrong team name)
        # Only set next_game for future reference
        if filler_type == 'idle':
            # Don't set context['game'] for idle - no current game!
            pass
        else:
            # For pregame/postgame, set the game context
            context['game'] = game_event

        # Extract next_game info for idle AND pregame fillers
        # For pregame: game_event IS the next game
        # For idle: game_event is the next scheduled game
        if filler_type in ['idle', 'pregame'] and game_event:
            # Extract opponent and date from game event
            # game_event might be a processed event with nested 'game' data
            raw_game = game_event.get('game', game_event)

            home_team = raw_game.get('home_team', {})
            away_team = raw_game.get('away_team', {})
            our_team_id = str(team.get('espn_team_id', ''))
            our_team_name = team.get('team_name', '').lower()

            # Match by ID or name
            is_home = (str(home_team.get('id', '')) == our_team_id or
                      home_team.get('name', '').lower() == our_team_name)

            # Opponent is the other team
            if is_home:
                opponent = away_team
            else:
                opponent = home_team

            # Fetch opponent stats if ESPN client is available
            next_opponent_record = ''
            if espn_client and opponent.get('id'):
                try:
                    opponent_id = str(opponent.get('id', ''))
                    opponent_stats_data = espn_client.get_team_stats(team.get('sport'), team.get('league'), opponent_id)
                    if opponent_stats_data and 'record' in opponent_stats_data:
                        opp_rec = opponent_stats_data['record']
                        opp_wins = opp_rec.get('wins', 0)
                        opp_losses = opp_rec.get('losses', 0)
                        opp_ties = opp_rec.get('ties', 0)
                        if opp_ties > 0:
                            next_opponent_record = f"{opp_wins}-{opp_losses}-{opp_ties}"
                        else:
                            next_opponent_record = f"{opp_wins}-{opp_losses}"
                except Exception as e:
                    print(f"  ⚠️ Could not fetch next opponent stats: {e}")

            # Parse game date and convert to team timezone
            game_date_str = raw_game.get('date', '')
            if game_date_str:
                from datetime import datetime
                from zoneinfo import ZoneInfo
                try:
                    game_dt = datetime.fromisoformat(game_date_str.replace('Z', '+00:00'))
                    # Convert to user's EPG timezone (not team timezone)
                    local_dt = game_dt.astimezone(ZoneInfo(epg_timezone))
                    date_formatted = local_dt.strftime('%B %d, %Y')  # e.g., "November 22, 2025"
                    time_formatted = local_dt.strftime('%I:%M %p %Z')
                except:
                    date_formatted = game_date_str
                    time_formatted = ''
            else:
                date_formatted = ''
                time_formatted = ''

            # Build matchup string (e.g., "BOS @ LAL")
            home_abbrev = home_team.get('abbrev', home_team.get('abbreviation', home_team.get('name', '')[:3].upper()))
            away_abbrev = away_team.get('abbrev', away_team.get('abbreviation', away_team.get('name', '')[:3].upper()))
            matchup = f"{away_abbrev} @ {home_abbrev}"

            # Build combined datetime string
            if date_formatted and time_formatted:
                datetime_str = f"{date_formatted} at {time_formatted}"
            elif date_formatted:
                datetime_str = date_formatted
            else:
                datetime_str = ''

            context['next_game'] = {
                'opponent': opponent.get('name', ''),
                'opponent_record': next_opponent_record,
                'date': date_formatted,
                'time': time_formatted,
                'datetime': datetime_str,
                'matchup': matchup,
                'venue': raw_game.get('venue', {}).get('name', ''),
                'is_home': is_home
            }

    # Add last_game context if available
    if last_game_event:
        # Extract opponent and result from last game event
        home_team = last_game_event.get('home_team', {})
        away_team = last_game_event.get('away_team', {})
        our_team_id = str(team.get('espn_team_id', ''))

        is_home = str(home_team.get('id', '')) == our_team_id
        opponent = away_team if is_home else home_team
        our_team_obj = home_team if is_home else away_team

        # Fetch opponent stats if ESPN client is available
        last_opponent_record = ''
        if espn_client and opponent.get('id'):
            try:
                opponent_id = str(opponent.get('id', ''))
                opponent_stats_data = espn_client.get_team_stats(team.get('sport'), team.get('league'), opponent_id)
                if opponent_stats_data and 'record' in opponent_stats_data:
                    opp_rec = opponent_stats_data['record']
                    opp_wins = opp_rec.get('wins', 0)
                    opp_losses = opp_rec.get('losses', 0)
                    opp_ties = opp_rec.get('ties', 0)
                    if opp_ties > 0:
                        last_opponent_record = f"{opp_wins}-{opp_losses}-{opp_ties}"
                    else:
                        last_opponent_record = f"{opp_wins}-{opp_losses}"
            except Exception as e:
                print(f"  ⚠️ Could not fetch last opponent stats: {e}")

        # Get scores (handle None values and dict format from scoreboards)
        team_score = our_team_obj.get('score')
        opp_score = opponent.get('score')

        # Only set result if scores are available
        if team_score is not None and opp_score is not None:
            # Handle score being either a number or dict (from different API responses)
            if isinstance(team_score, dict):
                team_score = int(team_score.get('value', 0) or team_score.get('displayValue', '0'))
            else:
                team_score = int(team_score)

            if isinstance(opp_score, dict):
                opp_score = int(opp_score.get('value', 0) or opp_score.get('displayValue', '0'))
            else:
                opp_score = int(opp_score)

            if team_score > opp_score:
                result = 'Win'
            elif opp_score > team_score:
                result = 'Loss'
            else:
                result = 'Tie'
        else:
            # Game hasn't been played yet or no score available
            team_score = 0
            opp_score = 0
            result = ''

        # Parse game date
        game_date_str = last_game_event.get('date', '')
        if game_date_str:
            try:
                game_dt = datetime.fromisoformat(game_date_str.replace('Z', '+00:00'))
                date_formatted = game_dt.strftime('%B %d, %Y')
            except:
                date_formatted = game_date_str
        else:
            date_formatted = ''

        # Build matchup string (e.g., "BOS @ LAL")
        home_abbrev = home_team.get('abbrev', home_team.get('abbreviation', home_team.get('name', '')[:3].upper()))
        away_abbrev = away_team.get('abbrev', away_team.get('abbreviation', away_team.get('name', '')[:3].upper()))
        matchup = f"{away_abbrev} @ {home_abbrev}"

        # Build score_abbrev format: "CHI 130 @ DEN 127" or just "CHI @ DEN" if no scores
        our_abbrev = our_team_obj.get('abbrev', our_team_obj.get('abbreviation', our_team_obj.get('name', '')[:3].upper()))
        opp_abbrev = opponent.get('abbrev', opponent.get('abbreviation', opponent.get('name', '')[:3].upper()))

        # Check if we have valid scores (not 0 or empty)
        has_scores = result != ''  # result is only set when we have valid scores

        if has_scores:
            # Include scores in format
            if is_home:
                score_abbrev = f"{opp_abbrev} {opp_score} @ {our_abbrev} {team_score}"
            else:
                score_abbrev = f"{our_abbrev} {team_score} @ {opp_abbrev} {opp_score}"
        else:
            # Fall back to just team abbreviations
            if is_home:
                score_abbrev = f"{opp_abbrev} @ {our_abbrev}"
            else:
                score_abbrev = f"{our_abbrev} @ {opp_abbrev}"

        # Extract player leaders from last game (game-specific stats)
        last_game_leaders = {}
        if 'competitions' in last_game_event and last_game_event['competitions']:
            last_game_leaders = _extract_player_leaders(
                last_game_event['competitions'][0],
                our_team_id,
                api_path
            )

        context['last_game'] = {
            'opponent': opponent.get('name', ''),
            'opponent_record': last_opponent_record,
            'date': date_formatted,
            'matchup': matchup,
            'result': result,
            'score': f"{team_score}-{opp_score}",
            'score_abbrev': score_abbrev,
            'is_home': is_home,
            **last_game_leaders  # Merge in player leader stats
        }
        # Store raw event for today_game logic
        context['_last_game_event'] = last_game_event

    # Add today_game context if game was today (available in all filler types)
    # Check the last game to see if it was today
    last_game_event = context.get('_last_game_event')  # Raw event data
    if last_game_event:
        game_date_str = last_game_event.get('date', '')
        if game_date_str:
            try:
                from zoneinfo import ZoneInfo
                game_dt = datetime.fromisoformat(game_date_str.replace('Z', '+00:00'))
                game_date = game_dt.astimezone(ZoneInfo(epg_timezone)).date()
                filler_date = start_dt.date()

                # If game was today (same date as filler), populate today_game
                # Only show if game is completed
                if game_date == filler_date:
                    status = last_game_event.get('status', {})
                    is_completed = status.get('completed', False) or status.get('state') == 'post'

                    if is_completed:
                        # Reuse the same data from last_game since it was today and completed
                        context['today_game'] = context.get('last_game', {})
            except:
                pass

    # Build template variables for category resolution in XMLTV
    template_vars = template_engine._build_variable_dict(context)

    # Create chunks
    current_start = start_dt
    for i in range(num_chunks):
        current_end = current_start + chunk_duration

        # Ensure last chunk ends exactly at end_dt
        if i == num_chunks - 1:
            current_end = end_dt

        # Resolve templates
        title = template_engine.resolve(title_template, context)
        description = template_engine.resolve(desc_template, context)

        chunks.append({
            'start_datetime': current_start,
            'end_datetime': current_end,
            'title': title,
            'subtitle': '',  # No subtitle for filler content
            'description': description,
            'status': 'filler',  # Special status to identify filler content
            'filler_type': filler_type,  # Track the type
            'context': template_vars  # Include template variables for category resolution
        })

        current_start = current_end

    return chunks


def _calculate_home_away_streaks(our_team_id: str, schedule_data: dict) -> dict:
    """
    Calculate current home and away win/loss streaks, plus last 5/10 records from schedule data.

    Args:
        our_team_id: Our team's ESPN ID
        schedule_data: Schedule data from ESPN API

    Returns:
        Dict with home_streak, away_streak, last_5_record, last_10_record, and recent_form strings
    """
    if not schedule_data or 'events' not in schedule_data:
        return {
            'home_streak': '',
            'away_streak': '',
            'last_5_record': '',
            'last_10_record': '',
            'recent_form': ''
        }

    # Collect completed games by location and overall
    home_games = []
    away_games = []
    all_games = []

    for event in schedule_data['events']:
        try:
            comp = event.get('competitions', [{}])[0]
            status = comp.get('status', {}).get('type', {})

            # Only completed games
            if not status.get('completed', False):
                continue

            # Find our team in competitors
            competitors = comp.get('competitors', [])
            our_team = None
            for c in competitors:
                if str(c.get('team', {}).get('id')) == str(our_team_id):
                    our_team = c
                    break

            if not our_team:
                continue

            # Categorize by home/away
            home_away = our_team.get('homeAway', '').lower()
            won = our_team.get('winner', False)
            game_date = event.get('date', '')

            game_data = {'date': game_date, 'won': won}

            # Add to overall games
            all_games.append(game_data)

            # Add to home/away specific lists
            if home_away == 'home':
                home_games.append(game_data)
            elif home_away == 'away':
                away_games.append(game_data)

        except (KeyError, IndexError, TypeError):
            continue

    # Helper to calculate streak from game list
    def calc_streak(games, location_text):
        if not games:
            return ""

        # Sort by date (most recent first)
        games.sort(key=lambda x: x['date'], reverse=True)

        # Calculate current streak
        is_winning = games[0]['won']
        count = 0

        for game in games:
            if game['won'] == is_winning:
                count += 1
            else:
                break

        # Only show significant streaks (3+ games)
        if count < 3:
            return ""

        if is_winning:
            return f"{count}-0 {location_text}"
        else:
            return f"Lost last {count} {location_text}"

    # Calculate last 5 and last 10 records
    # Sort all games by date (most recent first)
    all_games.sort(key=lambda x: x['date'], reverse=True)

    # Last 5 record
    last_5 = all_games[:5]
    if len(last_5) >= 5:
        wins_5 = sum(1 for g in last_5 if g['won'])
        losses_5 = 5 - wins_5
        last_5_record = f"{wins_5}-{losses_5}"
    else:
        last_5_record = ''

    # Last 10 record
    last_10 = all_games[:10]
    if len(last_10) >= 10:
        wins_10 = sum(1 for g in last_10 if g['won'])
        losses_10 = 10 - wins_10
        last_10_record = f"{wins_10}-{losses_10}"
    else:
        last_10_record = ''

    # Recent form (last 5 as W/L string)
    if len(last_5) >= 5:
        recent_form = ''.join('W' if g['won'] else 'L' for g in reversed(last_5))
    else:
        recent_form = ''

    return {
        'home_streak': calc_streak(home_games, "at home"),
        'away_streak': calc_streak(away_games, "on road"),
        'last_5_record': last_5_record,
        'last_10_record': last_10_record,
        'recent_form': recent_form
    }


def _get_head_coach(team_id: str, league: str) -> str:
    """
    Fetch head coach name from roster API.

    Args:
        team_id: Team's ESPN ID
        league: League API path (e.g., 'basketball/nba')

    Returns:
        Coach's full name or empty string if not found
    """
    try:
        url = f"{espn.base_url}/{league}/teams/{team_id}/roster"
        roster_data = espn._make_request(url)

        if roster_data and 'coach' in roster_data and roster_data['coach']:
            coach = roster_data['coach'][0]
            first = coach.get('firstName', '')
            last = coach.get('lastName', '')
            return f"{first} {last}".strip()
    except Exception as e:
        print(f"Error fetching coach for team {team_id}: {e}")

    return ''


def _get_games_played(competitor: dict) -> int:
    """
    Get number of games played from competitor's record.

    Args:
        competitor: Competitor object from ESPN API

    Returns:
        Number of games played
    """
    if 'records' not in competitor:
        return 0

    for record in competitor['records']:
        if record.get('name') == 'overall':
            summary = record.get('summary', '0-0')
            try:
                parts = summary.replace('-', ' ').split()
                return sum(int(p) for p in parts if p.isdigit())
            except (ValueError, AttributeError):
                return 0

    return 0


def _is_season_stats(leader_category: dict, game_status: str) -> bool:
    """
    Determine if leader data represents season stats or single-game stats.

    Args:
        leader_category: Leader category dict from ESPN API
        game_status: Game status (STATUS_SCHEDULED, STATUS_FINAL, etc.)

    Returns:
        True = Season statistics (use for upcoming games)
        False = Game statistics (use for completed games only)
    """
    category_name = leader_category.get('name', '')

    # NBA: Category name changes
    if 'PerGame' in category_name:
        return True  # pointsPerGame = season average

    # NFL: Leaders only present for scheduled games
    if 'Leader' in category_name:
        return True  # passingLeader = season totals

    # If game is completed, assume game stats
    if game_status in ['STATUS_FINAL', 'STATUS_FULL_TIME']:
        # These are game-specific stats (who led in THIS game)
        return False

    # Default: scheduled/in-progress games have season stats
    return True


def _map_basketball_season_leaders(leaders_data: list, games_played: int) -> dict:
    """
    Map NBA/NCAAB season stats (API provides per-game, calculate totals).

    Args:
        leaders_data: List of leader categories from API
        games_played: Number of games played this season

    Returns:
        Dict with basketball_* prefixed variables
    """
    result = {}

    for category in leaders_data:
        if not category.get('leaders'):
            continue

        player = category['leaders'][0]
        athlete = player['athlete']
        per_game = player['value']
        total = per_game * games_played if games_played > 0 else 0
        position = athlete.get('position', {}).get('abbreviation', '')

        if category['name'] == 'pointsPerGame':
            result['basketball_top_scorer_name'] = athlete['displayName']
            result['basketball_top_scorer_position'] = position
            result['basketball_top_scorer_ppg'] = f"{per_game:.1f}"
            result['basketball_top_scorer_total'] = f"{total:.0f}"

        elif category['name'] == 'reboundsPerGame':
            result['basketball_top_rebounder_name'] = athlete['displayName']
            result['basketball_top_rebounder_rpg'] = f"{per_game:.1f}"
            result['basketball_top_rebounder_total'] = f"{total:.0f}"

        elif category['name'] == 'assistsPerGame':
            result['basketball_top_assist_name'] = athlete['displayName']
            result['basketball_top_assist_apg'] = f"{per_game:.1f}"
            result['basketball_top_assist_total'] = f"{total:.0f}"

    return result


def _map_basketball_game_leaders(leaders_data: list) -> dict:
    """
    Map NBA/NCAAB game stats (actual performance in that game).

    Args:
        leaders_data: List of leader categories from API

    Returns:
        Dict with last_game_* prefixed variables
    """
    result = {}

    for category in leaders_data:
        if not category.get('leaders'):
            continue

        player = category['leaders'][0]
        athlete = player['athlete']
        game_stat = player['value']

        if category['name'] == 'points':
            result['last_game_top_scorer_name'] = athlete['displayName']
            result['last_game_top_scorer_points'] = f"{game_stat:.0f}"

        elif category['name'] == 'rebounds':
            result['last_game_top_rebounder_name'] = athlete['displayName']
            result['last_game_top_rebounder_rebounds'] = f"{game_stat:.0f}"

        elif category['name'] == 'assists':
            result['last_game_top_assist_name'] = athlete['displayName']
            result['last_game_top_assist_assists'] = f"{game_stat:.0f}"

    return result


def _map_football_season_leaders(leaders_data: list, games_played: int) -> dict:
    """
    Map NFL/NCAAF season stats (API provides totals, calculate per-game).

    Args:
        leaders_data: List of leader categories from API
        games_played: Number of games played this season

    Returns:
        Dict with football_* prefixed variables
    """
    result = {}

    for category in leaders_data:
        if not category.get('leaders'):
            continue

        player = category['leaders'][0]
        athlete = player['athlete']
        total_value = player['value']
        per_game_value = total_value / games_played if games_played > 0 else 0
        position = athlete.get('position', {}).get('abbreviation', '')

        if category['name'] == 'passingLeader':
            result['football_quarterback_name'] = athlete['displayName']
            result['football_quarterback_position'] = position
            result['football_quarterback_passing_yards'] = f"{total_value:.0f}"
            result['football_quarterback_passing_ypg'] = f"{per_game_value:.1f}"

        elif category['name'] == 'rushingLeader':
            result['football_top_rusher_name'] = athlete['displayName']
            result['football_top_rusher_position'] = position
            result['football_top_rusher_yards'] = f"{total_value:.0f}"
            result['football_top_rusher_ypg'] = f"{per_game_value:.1f}"

        elif category['name'] == 'receivingLeader':
            result['football_top_receiver_name'] = athlete['displayName']
            result['football_top_receiver_position'] = position
            result['football_top_receiver_yards'] = f"{total_value:.0f}"
            result['football_top_receiver_ypg'] = f"{per_game_value:.1f}"

    return result


def _map_football_game_leaders(leaders_data: list) -> dict:
    """
    Map NFL/NCAAF game stats (actual performance in that game).

    Args:
        leaders_data: List of leader categories from API

    Returns:
        Dict with last_game_* prefixed variables
    """
    result = {}

    for category in leaders_data:
        if not category.get('leaders'):
            continue

        player = category['leaders'][0]
        athlete = player['athlete']
        stat_value = player['value']

        if category['name'] in ['passingLeader', 'passingYards']:
            result['last_game_passing_leader_name'] = athlete['displayName']
            result['last_game_passing_leader_yards'] = f"{stat_value:.0f}"

        elif category['name'] in ['rushingLeader', 'rushingYards']:
            result['last_game_rushing_leader_name'] = athlete['displayName']
            result['last_game_rushing_leader_yards'] = f"{stat_value:.0f}"

        elif category['name'] in ['receivingLeader', 'receivingYards']:
            result['last_game_receiving_leader_name'] = athlete['displayName']
            result['last_game_receiving_leader_yards'] = f"{stat_value:.0f}"

    return result


def _map_hockey_season_leaders(leaders_data: list, games_played: int) -> dict:
    """
    Map NHL season stats (API provides totals, calculate per-game).

    Args:
        leaders_data: List of leader categories from API
        games_played: Number of games played this season

    Returns:
        Dict with hockey_* prefixed variables
    """
    result = {}

    for category in leaders_data:
        if not category.get('leaders'):
            continue

        player = category['leaders'][0]
        athlete = player['athlete']
        total_value = player['value']
        per_game_value = total_value / games_played if games_played > 0 else 0
        position = athlete.get('position', {}).get('abbreviation', '')

        if category['name'] in ['goals', 'goalsStat']:
            result['hockey_top_scorer_name'] = athlete['displayName']
            result['hockey_top_scorer_position'] = position
            result['hockey_top_scorer_goals'] = f"{total_value:.0f}"
            result['hockey_top_scorer_gpg'] = f"{per_game_value:.1f}"

        elif category['name'] in ['assists', 'assistsStat']:
            result['hockey_top_playmaker_name'] = athlete['displayName']
            result['hockey_top_playmaker_position'] = position
            result['hockey_top_playmaker_assists'] = f"{total_value:.0f}"
            result['hockey_top_playmaker_apg'] = f"{per_game_value:.1f}"

    return result


def _map_baseball_leaders(leaders_data: list, games_played: int) -> dict:
    """
    Map MLB season stats (various stat types).

    Args:
        leaders_data: List of leader categories from API
        games_played: Number of games played this season

    Returns:
        Dict with baseball_* prefixed variables
    """
    result = {}

    for category in leaders_data:
        if not category.get('leaders'):
            continue

        player = category['leaders'][0]
        athlete = player['athlete']
        position = athlete.get('position', {}).get('abbreviation', '')

        if category['name'] == 'battingAverage':
            result['baseball_top_hitter_name'] = athlete['displayName']
            result['baseball_top_hitter_position'] = position
            result['baseball_top_hitter_avg'] = player['displayValue']
            result['baseball_top_hitter_hits'] = ''  # May not be in leader data

        elif category['name'] == 'homeRuns':
            total_hrs = player['value']
            hr_rate = total_hrs / games_played if games_played > 0 else 0
            result['baseball_power_hitter_name'] = athlete['displayName']
            result['baseball_power_hitter_position'] = position
            result['baseball_power_hitter_hrs'] = f"{total_hrs:.0f}"
            result['baseball_power_hitter_hr_rate'] = f"{hr_rate:.2f}"

    return result


def _extract_player_leaders(competition: dict, team_id: str, league: str) -> dict:
    """
    Extract player leaders from competition data and calculate totals/averages.
    Automatically detects if data is season stats or game stats.

    Args:
        competition: Competition object from ESPN scoreboard API
        team_id: Our team's ESPN ID
        league: League API path (e.g., 'basketball/nba')

    Returns:
        Dict with sport-specific leader variables (prefixed by sport)
    """
    # Find our team's competitor
    competitor = None
    for comp in competition.get('competitors', []):
        if str(comp['team']['id']) == str(team_id):
            competitor = comp
            break

    if not competitor or 'leaders' not in competitor:
        return {}

    leaders_data = competitor['leaders']
    if not leaders_data or not isinstance(leaders_data, list) or len(leaders_data) == 0:
        return {}

    # Determine game status
    game_status = competition.get('status', {}).get('type', {}).get('name', '')

    # Determine if this is season or game stats
    is_season = _is_season_stats(leaders_data[0], game_status)

    # Get games played for calculations
    games_played = _get_games_played(competitor)

    # Map based on sport and data type
    if 'basketball' in league:
        if is_season:
            return _map_basketball_season_leaders(leaders_data, games_played)
        else:
            return _map_basketball_game_leaders(leaders_data)

    elif 'football' in league:
        if is_season:
            return _map_football_season_leaders(leaders_data, games_played)
        else:
            return _map_football_game_leaders(leaders_data)

    elif 'hockey' in league:
        if is_season:
            return _map_hockey_season_leaders(leaders_data, games_played)
        else:
            # Could add game leaders for hockey if needed
            return {}

    elif 'baseball' in league:
        return _map_baseball_leaders(leaders_data, games_played)

    return {}


def _calculate_h2h(our_team_id: str, opponent_id: str, schedule_data: dict) -> dict:
    """
    Calculate head-to-head data from team schedule

    Args:
        our_team_id: Our team's ID
        opponent_id: Opponent team's ID
        schedule_data: Schedule data from ESPN API

    Returns:
        Dict with season_series and previous_game data
    """
    if not schedule_data or 'events' not in schedule_data:
        return {'season_series': {}, 'previous_game': {}}

    # Find all completed games against this opponent
    h2h_games = []
    for event in schedule_data['events']:
        try:
            comp = event.get('competitions', [{}])[0]
            status = comp.get('status', {}).get('type', {})

            # Only look at completed games
            if not status.get('completed', False):
                continue

            # Check if this game involves the opponent
            competitors = comp.get('competitors', [])
            opponent_in_game = any(
                str(c.get('team', {}).get('id')) == str(opponent_id)
                for c in competitors
            )

            if opponent_in_game:
                h2h_games.append(event)
        except:
            continue

    # Calculate series record
    team_wins = 0
    opp_wins = 0

    for event in h2h_games:
        try:
            comp = event.get('competitions', [{}])[0]
            for competitor in comp.get('competitors', []):
                team_id = str(competitor.get('team', {}).get('id'))
                if team_id == str(our_team_id):
                    if competitor.get('winner', False):
                        team_wins += 1
                elif team_id == str(opponent_id):
                    if competitor.get('winner', False):
                        opp_wins += 1
        except:
            continue

    # Build season series data
    season_series = {
        'team_wins': team_wins,
        'opponent_wins': opp_wins,
        'games': h2h_games
    }

    # Get most recent game
    previous_game = {}
    if h2h_games:
        recent = h2h_games[0]  # Schedule is already sorted, most recent first
        try:
            comp = recent.get('competitions', [{}])[0]

            # Find our team and opponent in competitors
            our_team = None
            opp_team = None
            for competitor in comp.get('competitors', []):
                team_id = str(competitor.get('team', {}).get('id'))
                if team_id == str(our_team_id):
                    our_team = competitor
                elif team_id == str(opponent_id):
                    opp_team = competitor

            if our_team and opp_team:
                # Handle score being either a number or dict (from different API responses)
                our_score_raw = our_team.get('score', 0)
                opp_score_raw = opp_team.get('score', 0)

                if isinstance(our_score_raw, dict):
                    our_score = int(our_score_raw.get('value', 0) or our_score_raw.get('displayValue', '0'))
                else:
                    our_score = int(our_score_raw) if our_score_raw else 0

                if isinstance(opp_score_raw, dict):
                    opp_score = int(opp_score_raw.get('value', 0) or opp_score_raw.get('displayValue', '0'))
                else:
                    opp_score = int(opp_score_raw) if opp_score_raw else 0

                if our_score > opp_score:
                    result = 'Win'
                    winner = our_team.get('team', {}).get('displayName', '')
                    loser = opp_team.get('team', {}).get('displayName', '')
                elif opp_score > our_score:
                    result = 'Loss'
                    winner = opp_team.get('team', {}).get('displayName', '')
                    loser = our_team.get('team', {}).get('displayName', '')
                else:
                    result = 'Tie'
                    winner = ''
                    loser = ''

                # Parse date
                game_date_str = recent.get('date', '')
                if game_date_str:
                    try:
                        game_dt = datetime.fromisoformat(game_date_str.replace('Z', '+00:00'))
                        date_formatted = game_dt.strftime('%B %d, %Y')
                        days_since = (datetime.now(ZoneInfo('UTC')) - game_dt).days
                    except:
                        date_formatted = game_date_str
                        days_since = 0
                else:
                    date_formatted = ''
                    days_since = 0

                # Determine home/away
                our_home_away = our_team.get('homeAway', '')
                opp_home_away = opp_team.get('homeAway', '')

                # Get team abbreviations
                our_abbrev = our_team.get('team', {}).get('abbreviation', 'TBD')
                opp_abbrev = opp_team.get('team', {}).get('abbreviation', 'TBD')

                # Build abbreviated score (e.g., "DET 127 @ IND 112" or "DET 127 vs IND 112")
                if our_home_away == 'away':
                    score_abbrev = f"{our_abbrev} {our_score} @ {opp_abbrev} {opp_score}"
                elif our_home_away == 'home':
                    score_abbrev = f"{our_abbrev} {our_score} vs {opp_abbrev} {opp_score}"
                else:
                    score_abbrev = f"{our_abbrev} {our_score} - {opp_abbrev} {opp_score}"

                previous_game = {
                    'result': result,
                    'score': f"{our_score}-{opp_score}",
                    'score_abbrev': score_abbrev,
                    'winner': winner,
                    'loser': loser,
                    'date': date_formatted,
                    'location': comp.get('venue', {}).get('fullName', ''),
                    'days_since': days_since
                }
        except:
            pass

    return {
        'season_series': season_series,
        'previous_game': previous_game
    }


def _process_event(event: dict, team: dict, team_stats: dict = None, opponent_stats: dict = None, epg_timezone: str = 'America/New_York', schedule_data: dict = None, settings: dict = None) -> dict:
    """Process a single event - add templates, calculate times"""
    # Parse game datetime
    game_datetime = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))

    # Calculate end time using game duration helper
    game_duration_hours = get_game_duration(team, settings or {})
    end_datetime = game_datetime + timedelta(hours=game_duration_hours)

    # Get our team ID (needed for various lookups)
    our_team_id = str(team.get('espn_team_id', ''))

    # Calculate h2h data
    h2h = {}
    if schedule_data:
        # Identify opponent from event
        home_team = event.get('home_team', {})
        away_team = event.get('away_team', {})

        is_home = str(home_team.get('id', '')) == our_team_id
        opponent = away_team if is_home else home_team
        opponent_id = str(opponent.get('id', ''))

        if opponent_id:
            h2h = _calculate_h2h(our_team_id, opponent_id, schedule_data)

        # Calculate home/away streaks
        streaks = _calculate_home_away_streaks(our_team_id, schedule_data)
    else:
        streaks = {'home_streak': '', 'away_streak': ''}

    # Get head coach
    api_path = team.get('api_path', '')
    head_coach = _get_head_coach(our_team_id, api_path) if our_team_id and api_path else ''

    # Extract player leaders (season stats for upcoming games)
    player_leaders = {}
    if 'competitions' in event and event['competitions']:
        player_leaders = _extract_player_leaders(
            event['competitions'][0],
            our_team_id,
            api_path
        )

    # Build context for template resolution
    context = {
        'game': event,
        'team_config': team,
        'team_stats': team_stats or {},
        'opponent_stats': opponent_stats or {},
        'h2h': h2h,
        'streaks': streaks,
        'head_coach': head_coach,
        'player_leaders': player_leaders,
        'epg_timezone': epg_timezone
    }

    # Resolve templates
    title = template_engine.resolve(team.get('title_format', '{team_name} Basketball'), context)
    subtitle = template_engine.resolve(team.get('subtitle_template', '{venue_full}'), context)

    # Select description template based on conditional logic
    default_description = team.get('description_template', '')
    description_options = team.get('description_options', '[]')
    selected_description_template = template_engine.select_description(
        default_description,
        description_options,
        context
    )

    # Resolve the selected description template
    description = template_engine.resolve(selected_description_template, context)

    # Determine status
    status_name = event['status']['name']
    if 'SCHEDULED' in status_name or status_name == 'STATUS_SCHEDULED':
        status = 'scheduled'
    elif 'PROGRESS' in status_name or status_name == 'STATUS_IN_PROGRESS':
        status = 'in_progress'
    elif 'FINAL' in status_name or status_name == 'STATUS_FINAL':
        status = 'final'
    else:
        status = 'scheduled'

    # Build template variables for category resolution in XMLTV
    template_vars = template_engine._build_variable_dict(context)

    return {
        'start_datetime': game_datetime,
        'end_datetime': end_datetime,
        'title': title,
        'subtitle': subtitle,
        'description': description,
        'status': status,
        'game': event,  # Preserve raw game data for filler programs
        'context': template_vars  # Include template variables for category resolution
    }

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    # Ensure data directory exists
    os.makedirs('/app/data', exist_ok=True)

    # Get port from settings
    conn = get_connection()
    settings_row = conn.execute("SELECT web_port, web_host FROM settings WHERE id = 1").fetchone()
    conn.close()

    if settings_row:
        port = settings_row['web_port']
        host = settings_row['web_host']
    else:
        port = 9195
        host = '0.0.0.0'

    print(f"""
╔════════════════════════════════════════════════════════════╗
║                                                            ║
║   Teamarr - Dynamic Sports Team EPG Generator              ║
║                                                            ║
║   Web Interface: http://localhost:{port}                    ║
║   EPG File: http://localhost:{port}/teamarr.xml            ║
║                                                            ║
╚════════════════════════════════════════════════════════════╝
    """)

    # Start the auto-generation scheduler
    start_scheduler()

    # Suppress Flask development server warning
    import sys
    import os

    class FilteredStderr:
        def __init__(self, original):
            self.original = original
            self.skip_next_line = False

        def write(self, text):
            # Filter out the Flask development warning
            if 'WARNING: This is a development server' in text:
                self.skip_next_line = True
                return
            if self.skip_next_line:
                if 'Use a production WSGI server instead' in text:
                    self.skip_next_line = False
                    return
            self.original.write(text)

        def flush(self):
            self.original.flush()

    sys.stderr = FilteredStderr(sys.stderr)

    try:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        stop_scheduler()
        print("👋 Goodbye!")
