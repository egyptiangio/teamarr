"""
Teamarr - Dynamic EPG Generator for Sports Team Channels
Flask web application for managing templates and teams
"""

from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, flash, Response
import os
import json
import sys
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import traceback

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from database import (
    init_database, get_connection,
    get_all_templates, get_template, create_template, update_template, delete_template, get_template_team_count,
    get_all_teams, get_team, create_team, update_team, delete_team,
    bulk_assign_template, bulk_delete_teams, bulk_set_active,
    get_active_teams_with_templates
)
from api.espn_client import ESPNClient
from epg.orchestrator import EPGOrchestrator
from epg.xmltv_generator import XMLTVGenerator
from utils.logger import setup_logging, get_logger
from config import VERSION

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Setup logging system
log_level = os.environ.get('LOG_LEVEL', 'DEBUG').upper()
setup_logging(app, log_level)

# Initialize database on startup
if not os.path.exists(os.path.join(os.path.dirname(__file__), 'teamarr.db')):
    app.logger.info("🔧 Initializing database...")
    init_database()
    app.logger.info("✅ Database initialized")

# Initialize EPG components
epg_orchestrator = EPGOrchestrator()
xmltv_generator = XMLTVGenerator(
    generator_name="Teamarr - Dynamic EPG Generator for Sports Team Channels",
    generator_url="http://localhost:9195",
    version=VERSION
)

# Scheduler thread
scheduler_thread = None
scheduler_running = False
last_run_time = None

# =============================================================================
# CONTEXT PROCESSORS
# =============================================================================

@app.context_processor
def inject_version():
    """Make version available to all templates"""
    return dict(version=VERSION)

# =============================================================================
# SCHEDULER FUNCTIONS
# =============================================================================

def run_scheduled_generation():
    """Run EPG generation (called by scheduler)"""
    try:
        app.logger.info(f"🕐 Scheduled EPG generation started at {datetime.now()}")

        # Call the generate_epg endpoint internally using test client
        with app.app_context():
            with app.test_client() as client:
                # Make internal POST request to /generate endpoint with JSON header
                response = client.post(
                    '/generate',
                    data={'days_ahead': ''},
                    headers={'Accept': 'application/json'}
                )

                if response.status_code == 200:
                    result = response.get_json()
                    app.logger.info(f"✅ Scheduled EPG generation completed: {result.get('num_programmes', 0)} programs from {result.get('num_channels', 0)} teams in {result.get('generation_time', 0):.2f}s")
                else:
                    error_data = response.get_json() if response.content_type == 'application/json' else {}
                    error_msg = error_data.get('error', 'Unknown error')
                    app.logger.error(f"❌ Scheduled EPG generation failed: {error_msg}")

    except Exception as e:
        app.logger.error(f"❌ Scheduler error: {e}", exc_info=True)

def scheduler_loop():
    """Background thread that runs the scheduler"""
    global scheduler_running, last_run_time

    app.logger.info("🚀 EPG Auto-Generation Scheduler started")

    while scheduler_running:
        try:
            conn = get_connection()
            settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
            conn.close()

            if not settings.get('auto_generate_enabled', False):
                time.sleep(60)  # Check every minute if disabled
                continue

            frequency = settings.get('auto_generate_frequency', 'daily')
            now = datetime.now()

            # Check if it's time to run based on frequency and last run time
            should_run = False

            if frequency == 'hourly':
                # Run once per hour
                if last_run_time is None:
                    app.logger.debug(f"Scheduler: First run, triggering generation")
                    should_run = True
                else:
                    # Check if we're in a different hour than last run
                    last_hour = last_run_time.replace(minute=0, second=0, microsecond=0)
                    current_hour = now.replace(minute=0, second=0, microsecond=0)
                    app.logger.debug(f"Scheduler: Checking hourly - Last: {last_hour}, Current: {current_hour}")
                    if current_hour > last_hour:
                        app.logger.info(f"⏰ New hour detected, triggering scheduled generation")
                        should_run = True

            elif frequency == 'daily':
                # Run once per day at midnight
                if last_run_time is None:
                    # Never run before, run if past midnight
                    if now.hour >= 0:
                        should_run = True
                else:
                    # Check if we're in a different day
                    if now.date() > last_run_time.date():
                        should_run = True

            if should_run:
                run_scheduled_generation()
                last_run_time = now

            time.sleep(30)  # Check every 30 seconds

        except Exception as e:
            app.logger.error(f"❌ Scheduler loop error: {e}", exc_info=True)
            time.sleep(60)

    app.logger.info("🛑 EPG Auto-Generation Scheduler stopped")

def start_scheduler():
    """Start the scheduler background thread"""
    global scheduler_thread, scheduler_running

    if scheduler_thread and scheduler_thread.is_alive():
        app.logger.warning("⚠️  Scheduler already running")
        return

    scheduler_running = True
    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler_thread.start()
    app.logger.info("✅ Scheduler thread started")

def stop_scheduler():
    """Stop the scheduler background thread"""
    global scheduler_running
    scheduler_running = False
    app.logger.info("⏹️  Scheduler stopping...")

# =============================================================================
# DASHBOARD / HOME
# =============================================================================

@app.route('/')
def index():
    """Dashboard - overview of templates and teams"""
    conn = get_connection()
    cursor = conn.cursor()

    # Get stats
    template_count = cursor.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
    team_count = cursor.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    active_team_count = cursor.execute("SELECT COUNT(*) FROM teams WHERE active = 1").fetchone()[0]
    assigned_team_count = cursor.execute("SELECT COUNT(*) FROM teams WHERE template_id IS NOT NULL").fetchone()[0]

    # Get timezone from settings
    settings_row = cursor.execute("SELECT default_timezone FROM settings WHERE id = 1").fetchone()
    user_timezone = settings_row[0] if settings_row else 'America/New_York'

    # Get latest EPG generation stats
    latest_epg = cursor.execute("""
        SELECT generated_at, num_programmes, num_events, num_channels
        FROM epg_history
        ORDER BY generated_at DESC
        LIMIT 1
    """).fetchone()

    # Get last 10 EPG generations for history table
    epg_history = cursor.execute("""
        SELECT generated_at, num_channels, num_events, num_programmes,
               generation_time_seconds, status
        FROM epg_history
        ORDER BY generated_at DESC
        LIMIT 10
    """).fetchall()

    conn.close()

    # Convert timestamps to user's timezone
    def format_timestamp(utc_timestamp):
        if not utc_timestamp:
            return None
        # SQLite timestamps are stored as 'YYYY-MM-DD HH:MM:SS' without timezone info
        # We need to parse them as naive datetime and then treat as UTC
        dt_naive = datetime.fromisoformat(utc_timestamp.replace('Z', '+00:00'))
        # If the datetime is naive (no timezone info), assume it's UTC
        if dt_naive.tzinfo is None:
            dt_utc = dt_naive.replace(tzinfo=ZoneInfo('UTC'))
        else:
            dt_utc = dt_naive
        # Convert to user's timezone
        dt_local = dt_utc.astimezone(ZoneInfo(user_timezone))
        # Format with timezone abbreviation
        return dt_local.strftime('%Y-%m-%d %H:%M:%S %Z')

    # Format timestamps for latest EPG
    if latest_epg:
        latest_epg_dict = dict(latest_epg)
        latest_epg_dict['generated_at_formatted'] = format_timestamp(latest_epg_dict['generated_at'])
        latest_epg = latest_epg_dict

    # Format timestamps for history
    epg_history_formatted = []
    for entry in epg_history:
        entry_dict = dict(entry)
        entry_dict['generated_at_formatted'] = format_timestamp(entry_dict['generated_at'])
        epg_history_formatted.append(entry_dict)

    return render_template('index.html',
        template_count=template_count,
        team_count=team_count,
        active_team_count=active_team_count,
        assigned_team_count=assigned_team_count,
        latest_epg=latest_epg,
        epg_history=epg_history_formatted
    )

# =============================================================================
# TEMPLATES MANAGEMENT
# =============================================================================

@app.route('/templates')
def templates_list():
    """List all templates with team counts"""
    templates = get_all_templates()
    return render_template('template_list.html', templates=templates)

@app.route('/templates/add', methods=['GET'])
def templates_add_form():
    """Show add template form"""
    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()
    return render_template('template_form.html', template=None, mode='add', description_options_json='[]', settings=settings)

@app.route('/templates/add', methods=['POST'])
def templates_add():
    """Create a new template"""
    try:
        data = _extract_template_form_data(request.form)
        app.logger.info(f"Creating new template: {data['name']}")
        app.logger.debug(f"Template data: {data}")

        template_id = create_template(data)

        app.logger.info(f"✅ Template created successfully - ID: {template_id}, Name: {data['name']}")
        flash(f"Template '{data['name']}' created successfully!", 'success')
        return redirect(url_for('templates_list'))
    except Exception as e:
        app.logger.error(f"❌ Error creating template: {str(e)}", exc_info=True)
        flash(f"Error creating template: {str(e)}", 'error')
        return redirect(url_for('templates_add_form'))

@app.route('/templates/<int:template_id>/edit', methods=['GET'])
def templates_edit_form(template_id):
    """Show edit template form"""
    import json
    template = get_template(template_id)
    if not template:
        flash('Template not found', 'error')
        return redirect(url_for('templates_list'))

    # Pass description_options as JSON for JavaScript
    description_options_json = template.get('description_options', '[]') if template else '[]'

    # Get settings for default duration values
    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()

    return render_template('template_form.html', template=template, mode='edit', description_options_json=description_options_json, settings=settings)

@app.route('/templates/<int:template_id>/edit', methods=['POST'])
def templates_edit(template_id):
    """Update an existing template"""
    try:
        data = _extract_template_form_data(request.form)
        if update_template(template_id, data):
            flash(f"Template '{data['name']}' updated successfully!", 'success')
        else:
            flash('Template not found', 'error')
        return redirect(url_for('templates_list'))
    except Exception as e:
        flash(f"Error updating template: {str(e)}", 'error')
        return redirect(url_for('templates_edit_form', template_id=template_id))

@app.route('/templates/<int:template_id>/delete', methods=['POST'])
def templates_delete(template_id):
    """Delete a template (with warning if teams assigned)"""
    try:
        template = get_template(template_id)
        if not template:
            flash('Template not found', 'error')
            return redirect(url_for('templates_list'))

        team_count = get_template_team_count(template_id)
        app.logger.warning(f"🗑️  Deleting template: {template['name']} (ID: {template_id}, {team_count} teams affected)")

        if delete_template(template_id):
            if team_count > 0:
                app.logger.warning(f"⚠️  {team_count} team(s) are now unassigned after template deletion")
                flash(f"Template deleted. {team_count} team(s) are now unassigned.", 'warning')
            else:
                app.logger.info(f"✅ Template '{template['name']}' deleted successfully")
                flash('Template deleted successfully!', 'success')
        else:
            flash('Template not found', 'error')
    except Exception as e:
        app.logger.error(f"❌ Error deleting template: {str(e)}", exc_info=True)
        flash(f"Error deleting template: {str(e)}", 'error')
    return redirect(url_for('templates_list'))

@app.route('/templates/<int:template_id>/duplicate', methods=['POST'])
def templates_duplicate(template_id):
    """Duplicate a template with 'Copy of' prefix"""
    try:
        template = get_template(template_id)
        if not template:
            flash('Template not found', 'error')
            return redirect(url_for('templates_list'))

        # Create copy with new name
        template_data = dict(template)
        template_data['name'] = f"Copy of {template['name']}"

        # Remove fields that shouldn't be copied
        fields_to_remove = ['id', 'created_at', 'updated_at', 'team_count', 'template_name']
        for field in fields_to_remove:
            template_data.pop(field, None)

        new_template_id = create_template(template_data)
        app.logger.info(f"✅ Duplicated template '{template['name']}' to '{template_data['name']}' (ID: {new_template_id})")
        flash(f"Template duplicated successfully as '{template_data['name']}'!", 'success')
    except Exception as e:
        app.logger.error(f"❌ Error duplicating template: {str(e)}", exc_info=True)
        flash(f"Error duplicating template: {str(e)}", 'error')
    return redirect(url_for('templates_list'))

@app.route('/templates/<int:template_id>/export', methods=['GET'])
def templates_export(template_id):
    """Export template as JSON file"""
    template = get_template(template_id)
    if not template:
        flash('Template not found', 'error')
        return redirect(url_for('templates_list'))

    # Remove ID, timestamps, sport, and league for clean export
    export_data = {k: v for k, v in template.items() if k not in ['id', 'created_at', 'updated_at', 'team_count', 'sport', 'league']}

    # Create temporary JSON file
    filename = f"template_{template['name'].replace(' ', '_')}.json"
    filepath = f"/tmp/{filename}"

    with open(filepath, 'w') as f:
        json.dump(export_data, f, indent=2)

    return send_file(filepath, as_attachment=True, download_name=filename)

@app.route('/templates/import', methods=['POST'])
def templates_import():
    """Import template from JSON file"""
    try:
        if 'file' not in request.files:
            flash('No file provided', 'error')
            return redirect(url_for('templates_list'))

        file = request.files['file']
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('templates_list'))

        # Read and parse JSON
        data = json.load(file)

        # Check if template name already exists
        existing = get_connection().execute("SELECT id FROM templates WHERE name = ?", (data.get('name', ''),)).fetchone()
        if existing:
            # Append timestamp to make unique
            data['name'] = f"{data['name']} (imported {datetime.now().strftime('%Y-%m-%d %H:%M')})"

        template_id = create_template(data)
        flash(f"Template '{data['name']}' imported successfully!", 'success')
    except Exception as e:
        flash(f"Error importing template: {str(e)}", 'error')

    return redirect(url_for('templates_list'))

# =============================================================================
# TEAMS MANAGEMENT
# =============================================================================

@app.route('/teams')
def teams_list():
    """List all teams"""
    teams = get_all_teams()
    templates = get_all_templates()  # For bulk assign dropdown

    # Get league logos and settings for display
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT league_code, logo_url FROM league_config")
    league_logos = {row['league_code']: row['logo_url'] for row in cursor.fetchall()}
    settings = dict(cursor.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()

    return render_template('team_list.html', teams=teams, templates=templates, league_logos=league_logos, settings=settings)

@app.route('/teams/import', methods=['GET'])
def teams_import():
    """Show team import UI"""
    return render_template('team_import.html')

@app.route('/teams/add', methods=['GET'])
def teams_add_form():
    """Show add team form"""
    templates = get_all_templates()
    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()
    return render_template('team_form.html', team=None, templates=templates, settings=settings, mode='add')

@app.route('/teams/add', methods=['POST'])
def teams_add():
    """Create a new team"""
    try:
        data = _extract_team_form_data(request.form)
        app.logger.info(f"Adding new team: {data['team_name']} ({data['league'].upper()})")
        app.logger.debug(f"Team data: {data}")

        team_id = create_team(data)

        template_info = f"Template: {get_template(data['template_id'])['name']}" if data.get('template_id') else "No template"
        app.logger.info(f"✅ Team added successfully - ID: {team_id}, Name: {data['team_name']}, {template_info}")
        flash(f"Team '{data['team_name']}' added successfully!", 'success')
        return redirect(url_for('teams_list'))
    except Exception as e:
        app.logger.error(f"❌ Error adding team: {str(e)}", exc_info=True)
        flash(f"Error adding team: {str(e)}", 'error')
        return redirect(url_for('teams_add_form'))

@app.route('/teams/<int:team_id>/edit', methods=['GET'])
def teams_edit_form(team_id):
    """Show edit team form"""
    team = get_team(team_id)
    if not team:
        flash('Team not found', 'error')
        return redirect(url_for('teams_list'))
    templates = get_all_templates()
    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()
    return render_template('team_form.html', team=team, templates=templates, settings=settings, mode='edit')

@app.route('/teams/<int:team_id>/edit', methods=['POST'])
def teams_edit(team_id):
    """Update an existing team"""
    try:
        data = _extract_team_form_data(request.form)
        if update_team(team_id, data):
            flash(f"Team '{data['team_name']}' updated successfully!", 'success')
        else:
            flash('Team not found', 'error')
        return redirect(url_for('teams_list'))
    except Exception as e:
        flash(f"Error updating team: {str(e)}", 'error')
        return redirect(url_for('teams_edit_form', team_id=team_id))

@app.route('/teams/<int:team_id>/delete', methods=['POST'])
def teams_delete_single(team_id):
    """Delete a single team"""
    try:
        if delete_team(team_id):
            flash('Team deleted successfully!', 'success')
        else:
            flash('Team not found', 'error')
    except Exception as e:
        flash(f"Error deleting team: {str(e)}", 'error')
    return redirect(url_for('teams_list'))

# =============================================================================
# BULK OPERATIONS
# =============================================================================

@app.route('/teams/bulk/assign-template', methods=['POST'])
def teams_bulk_assign_template():
    """Bulk assign template to teams"""
    try:
        team_ids = request.form.getlist('team_ids[]')
        template_id = request.form.get('template_id')

        if not team_ids:
            return jsonify({'success': False, 'message': 'No teams selected'})

        # Convert to integers
        team_ids = [int(tid) for tid in team_ids]
        template_id = int(template_id) if template_id and template_id != '' else None

        template_name = get_template(template_id)['name'] if template_id else 'Unassigned'
        app.logger.info(f"🔗 Bulk assigning {len(team_ids)} team(s) to template: {template_name}")

        count = bulk_assign_template(team_ids, template_id)

        app.logger.info(f"✅ Bulk assignment complete - {count} team(s) assigned to '{template_name}'")

        return jsonify({
            'success': True,
            'message': f'Assigned {count} team(s) to template: {template_name}'
        })
    except Exception as e:
        app.logger.error(f"❌ Error in bulk template assignment: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)})

@app.route('/teams/bulk/delete', methods=['POST'])
def teams_bulk_delete():
    """Bulk delete teams"""
    try:
        team_ids = request.form.getlist('team_ids[]')
        if not team_ids:
            return jsonify({'success': False, 'message': 'No teams selected'})

        team_ids = [int(tid) for tid in team_ids]
        count = bulk_delete_teams(team_ids)

        return jsonify({
            'success': True,
            'message': f'Deleted {count} team(s)'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/teams/bulk/activate', methods=['POST'])
def teams_bulk_activate():
    """Bulk activate teams"""
    try:
        team_ids = request.form.getlist('team_ids[]')
        if not team_ids:
            return jsonify({'success': False, 'message': 'No teams selected'})

        team_ids = [int(tid) for tid in team_ids]
        count = bulk_set_active(team_ids, True)

        return jsonify({
            'success': True,
            'message': f'Activated {count} team(s)'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/teams/bulk/deactivate', methods=['POST'])
def teams_bulk_deactivate():
    """Bulk deactivate teams"""
    try:
        team_ids = request.form.getlist('team_ids[]')
        if not team_ids:
            return jsonify({'success': False, 'message': 'No teams selected'})

        team_ids = [int(tid) for tid in team_ids]
        count = bulk_set_active(team_ids, False)

        return jsonify({
            'success': True,
            'message': f'Deactivated {count} team(s)'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/teams/bulk/change-channel-id', methods=['POST'])
def teams_bulk_change_channel_id():
    """Bulk change channel IDs based on a format template"""
    try:
        team_ids = request.form.getlist('team_ids[]')
        format_template = request.form.get('format', '')

        if not team_ids:
            return jsonify({'success': False, 'message': 'No teams selected'})

        if not format_template:
            return jsonify({'success': False, 'message': 'No format template provided'})

        team_ids = [int(tid) for tid in team_ids]
        updated_count = 0
        errors = []

        conn = get_connection()
        cursor = conn.cursor()

        for team_id in team_ids:
            try:
                # Get team data
                team = cursor.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
                if not team:
                    errors.append(f"Team ID {team_id} not found")
                    continue

                team_dict = dict(team)

                # Generate team_name_pascal (PascalCase): "Detroit Pistons" -> "DetroitPistons"
                team_name = team_dict.get('team_name', '')
                team_name_pascal = ''.join(word.capitalize() for word in team_name.split())

                # Get league_name for uppercase league variable
                league_name_row = cursor.execute(
                    "SELECT league_name FROM league_config WHERE league_code = ?",
                    (team_dict.get('league'),)
                ).fetchone()
                league_name = league_name_row[0] if league_name_row else team_dict.get('league', '').upper()

                # Generate channel ID from format
                channel_id = format_template \
                    .replace('{team_name_pascal}', team_name_pascal) \
                    .replace('{team_abbrev}', (team_dict.get('team_abbrev') or '').lower()) \
                    .replace('{team_name}', team_name.lower().replace(' ', '-')) \
                    .replace('{team_slug}', team_dict.get('team_slug') or team_name.lower().replace(' ', '-')) \
                    .replace('{espn_team_id}', str(team_dict.get('espn_team_id') or '')) \
                    .replace('{league_id}', (team_dict.get('league') or '').lower()) \
                    .replace('{league}', league_name) \
                    .replace('{sport}', (team_dict.get('sport') or '').lower())

                # Clean up channel ID (remove special characters, multiple dashes, etc.)
                # Preserve uppercase if format uses {team_name_pascal} or {league}
                import re
                if '{team_name_pascal}' in format_template or ('{league}' in format_template and '{league_id}' not in format_template):
                    # Allow uppercase letters (for PascalCase channel IDs)
                    channel_id = re.sub(r'[^a-zA-Z0-9.-]+', '', channel_id)
                else:
                    # Traditional: lowercase only, replace spaces with dashes
                    channel_id = re.sub(r'[^a-z0-9.-]+', '-', channel_id)
                    channel_id = re.sub(r'-+', '-', channel_id)
                    channel_id = channel_id.strip('-')

                if not channel_id:
                    errors.append(f"Generated empty channel ID for team {team_dict.get('team_name')}")
                    continue

                # Update the team's channel_id
                cursor.execute("UPDATE teams SET channel_id = ? WHERE id = ?", (channel_id, team_id))
                updated_count += 1

            except Exception as e:
                errors.append(f"Error updating team ID {team_id}: {str(e)}")

        conn.commit()
        conn.close()

        message = f'Updated {updated_count} team(s)'
        if errors:
            message += f'. Errors: {"; ".join(errors[:3])}'  # Show first 3 errors

        return jsonify({
            'success': True,
            'updated': updated_count,
            'message': message
        })
    except Exception as e:
        app.logger.error(f"❌ Error in bulk channel ID change: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)})

@app.route('/teams/<int:team_id>/toggle-status', methods=['POST'])
def teams_toggle_status(team_id):
    """Toggle team active status"""
    try:
        active = request.form.get('active') == '1'

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE teams SET active = ? WHERE id = ?", (active, team_id))
        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'Team {"activated" if active else "deactivated"}'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

# =============================================================================
# EPG MANAGEMENT
# =============================================================================

@app.route('/epg', methods=['GET'])
def epg_management():
    """EPG management page"""
    import os

    # Get latest EPG generation info
    conn = get_connection()
    latest_epg = conn.execute("""
        SELECT * FROM epg_history
        ORDER BY generated_at DESC
        LIMIT 1
    """).fetchone()

    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()

    # Check if EPG file exists
    epg_path = settings.get('epg_output_path', '/app/data/teamarr.xml')
    epg_file_exists = os.path.exists(epg_path)
    epg_filename = os.path.basename(epg_path) if epg_file_exists else None
    epg_file_size = None

    if epg_file_exists:
        size_bytes = os.path.getsize(epg_path)
        if size_bytes < 1024:
            epg_file_size = f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            epg_file_size = f"{size_bytes / 1024:.1f} KB"
        else:
            epg_file_size = f"{size_bytes / (1024 * 1024):.1f} MB"

    # Read EPG content for preview (full file)
    epg_content = None
    epg_total_lines = 0
    epg_analysis = None

    if epg_file_exists:
        try:
            with open(epg_path, 'r', encoding='utf-8') as f:
                epg_content = f.read()
                epg_total_lines = epg_content.count('\n')

            # Analyze EPG content
            epg_analysis = _analyze_epg_content(epg_content)
        except Exception as e:
            app.logger.error(f"Error reading EPG file: {e}")
            epg_content = None

    # Generate EPG URL
    epg_url = f"{request.url_root}teamarr.xml"

    return render_template('epg_management.html',
                         latest_epg=dict(latest_epg) if latest_epg else None,
                         epg_file_exists=epg_file_exists,
                         epg_filename=epg_filename,
                         epg_file_size=epg_file_size,
                         epg_content=epg_content,
                         epg_total_lines=epg_total_lines,
                         epg_analysis=epg_analysis,
                         epg_url=epg_url)

# =============================================================================
# SETTINGS
# =============================================================================

@app.route('/settings', methods=['GET'])
def settings_form():
    """Show settings form"""
    conn = get_connection()
    settings = dict(conn.execute("SELECT * FROM settings WHERE id = 1").fetchone())
    conn.close()
    return render_template('settings.html', settings=settings)

@app.route('/settings', methods=['POST'])
def settings_update():
    """Update global settings"""
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Update settings (extract from form)
        fields = [
            'epg_days_ahead', 'epg_update_time', 'epg_output_path',
            'default_timezone', 'default_channel_id_format', 'midnight_crossover_mode',
            'game_duration_default',
            'game_duration_basketball', 'game_duration_football', 'game_duration_hockey',
            'game_duration_baseball', 'game_duration_soccer',
            'cache_enabled', 'cache_duration_hours',
            'xmltv_generator_name', 'xmltv_generator_url',
            'auto_generate_enabled', 'auto_generate_frequency'
        ]

        for field in fields:
            value = request.form.get(field)
            if value is not None:
                # Handle boolean fields
                if field in ['cache_enabled', 'auto_generate_enabled']:
                    value = 1 if value == 'on' else 0
                # Handle numeric fields
                elif field in ['epg_days_ahead', 'cache_duration_hours']:
                    value = int(value)
                    # Validate epg_days_ahead range
                    if field == 'epg_days_ahead' and (value < 1 or value > 14):
                        flash('Days to Generate must be between 1 and 14', 'error')
                        return redirect(url_for('settings_form'))
                elif field in ['game_duration_default', 'max_program_hours_default',
                               'game_duration_basketball', 'game_duration_football',
                               'game_duration_hockey', 'game_duration_baseball', 'game_duration_soccer']:
                    value = float(value)

                cursor.execute(f"UPDATE settings SET {field} = ? WHERE id = 1", (value,))

        conn.commit()
        conn.close()

        flash('Settings updated successfully!', 'success')
    except Exception as e:
        flash(f"Error updating settings: {str(e)}", 'error')

    return redirect(url_for('settings_form'))

# =============================================================================
# EPG GENERATION
# =============================================================================

@app.route('/generate', methods=['POST'])
def generate_epg():
    """Generate EPG file"""
    start_time = datetime.now()
    conn = get_connection()

    try:
        app.logger.info('🚀 EPG generation requested')

        # Get settings
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM settings WHERE id = 1")
        settings_row = cursor.fetchone()

        if not settings_row:
            flash('Settings not configured', 'error')
            return redirect(url_for('index'))

        # Convert to dict
        settings = dict(settings_row)

        # Get days_ahead from settings
        days_ahead = settings.get('epg_days_ahead', 14)
        epg_timezone = settings.get('default_timezone', 'America/New_York')

        app.logger.info(f"Generating EPG: {days_ahead} days ahead, timezone: {epg_timezone}")

        # Generate EPG data using orchestrator
        result = epg_orchestrator.generate_epg(
            days_ahead=days_ahead,
            epg_timezone=epg_timezone,
            settings=settings
        )

        if not result['teams_list']:
            app.logger.warning('No active teams with templates found')
            flash('No active teams with templates configured', 'warning')
            conn.close()
            return redirect(url_for('index'))

        # Generate XMLTV
        app.logger.info(f"Generating XMLTV for {len(result['teams_list'])} teams")
        xml_content = xmltv_generator.generate(
            result['teams_list'],
            result['all_events'],
            settings
        )

        # Save to file
        output_path = settings.get('epg_output_path', '/app/data/teamarr.xml')
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(xml_content)

        # Calculate stats
        file_size = os.path.getsize(output_path)
        file_hash = xmltv_generator.calculate_file_hash(xml_content)
        generation_time = (datetime.now() - start_time).total_seconds()

        total_programmes = sum(len(events) for events in result['all_events'].values())
        total_events = sum(
            len([e for e in events if e.get('status') not in ['filler']])
            for events in result['all_events'].values()
        )

        # Log to history
        cursor.execute("""
            INSERT INTO epg_history (
                file_path, file_size, num_channels, num_programmes, num_events,
                generation_time_seconds, api_calls_made, file_hash, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            output_path, file_size, len(result['teams_list']), total_programmes, total_events,
            generation_time, result.get('api_calls', 0), file_hash, 'success'
        ))

        conn.commit()
        conn.close()

        app.logger.info(f"✅ EPG generated successfully: {total_programmes} programmes, {generation_time:.2f}s")

        # Return JSON for scheduler, redirect for web UI
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({
                'success': True,
                'num_programmes': total_programmes,
                'num_events': total_events,
                'num_channels': len(result['teams_list']),
                'generation_time': generation_time
            })

        flash(f"EPG generated successfully! {total_programmes} programmes in {generation_time:.2f}s", 'success')

        # Redirect back to EPG management if that's where request came from
        return_to = request.args.get('return_to', 'index')
        if return_to == 'epg':
            return redirect(url_for('epg_management'))
        return redirect(url_for('index'))

    except Exception as e:
        app.logger.error(f"❌ Error generating EPG: {str(e)}", exc_info=True)

        # Log error to history
        try:
            generation_time = (datetime.now() - start_time).total_seconds()
            cursor.execute("""
                INSERT INTO epg_history (
                    file_path, file_size, num_channels, num_programmes, num_events,
                    generation_time_seconds, api_calls_made, file_hash, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                'failed', 0, 0, 0, 0,
                generation_time, 0, '', 'error', str(e)
            ))
            conn.commit()
        except:
            pass

        conn.close()

        # Return JSON for scheduler, redirect for web UI
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

        flash(f"Error generating EPG: {str(e)}", 'error')

        # Redirect back to EPG management if that's where request came from
        return_to = request.args.get('return_to', 'index')
        if return_to == 'epg':
            return redirect(url_for('epg_management'))
        return redirect(url_for('index'))

@app.route('/generate/stream')
def generate_epg_stream():
    """Stream EPG generation progress using Server-Sent Events"""
    import threading
    import queue
    import time

    def generate():
        """Generator function for SSE stream"""
        progress_queue = queue.Queue()

        try:
            # Get settings
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM settings WHERE id = 1")
            settings_row = cursor.fetchone()
            conn.close()

            if not settings_row:
                yield f"data: {json.dumps({'status': 'error', 'message': 'Settings not configured'})}\n\n"
                return

            settings = dict(settings_row)
            days_ahead = settings.get('epg_days_ahead', 14)
            epg_timezone = settings.get('default_timezone', 'America/New_York')

            # Send initial progress
            yield f"data: {json.dumps({'status': 'starting', 'message': 'Initializing EPG generation...'})}\n\n"

            # Progress callback that puts updates in queue
            def progress_callback(current, total, team_name, message):
                progress_data = {
                    'status': 'progress',
                    'current': current,
                    'total': total,
                    'team_name': team_name,
                    'message': message,
                    'percent': int((current / total) * 100) if total > 0 else 0
                }
                progress_queue.put(progress_data)

            # Run EPG generation in background thread
            result_container = {'result': None, 'error': None}

            def run_generation():
                try:
                    result_container['result'] = epg_orchestrator.generate_epg(
                        days_ahead=days_ahead,
                        epg_timezone=epg_timezone,
                        settings=settings,
                        progress_callback=progress_callback
                    )
                except Exception as e:
                    result_container['error'] = e
                finally:
                    progress_queue.put({'status': 'generation_done'})

            # Start generation thread
            generation_thread = threading.Thread(target=run_generation)
            generation_thread.start()

            # Stream progress updates
            while True:
                try:
                    progress_data = progress_queue.get(timeout=0.1)

                    if progress_data.get('status') == 'generation_done':
                        break

                    yield f"data: {json.dumps(progress_data)}\n\n"
                except queue.Empty:
                    # Send heartbeat to keep connection alive
                    yield f": heartbeat\n\n"

            # Wait for thread to complete
            generation_thread.join()

            # Check for errors
            if result_container['error']:
                raise result_container['error']

            result = result_container['result']

            if not result or not result['teams_list']:
                yield f"data: {json.dumps({'status': 'error', 'message': 'No active teams with templates configured'})}\n\n"
                return

            # Generate XMLTV
            yield f"data: {json.dumps({'status': 'finalizing', 'message': 'Generating XMLTV file...'})}\n\n"

            xml_content = xmltv_generator.generate(
                result['teams_list'],
                result['all_events'],
                settings
            )

            # Save to file
            output_path = settings.get('epg_output_path', '/app/data/teamarr.xml')
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(xml_content)

            # Log to history
            conn = get_connection()
            file_size = os.path.getsize(output_path)
            file_hash = xmltv_generator.calculate_file_hash(xml_content)
            generation_time = result['stats'].get('generation_time', 0)
            total_programmes = result['stats'].get('num_programmes', 0)
            total_events = result['stats'].get('num_events', 0)

            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO epg_history (
                    file_path, file_size, num_channels, num_programmes, num_events,
                    generation_time_seconds, api_calls_made, file_hash, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                output_path, file_size, len(result['teams_list']), total_programmes, total_events,
                generation_time, result.get('api_calls', 0), file_hash, 'success'
            ))
            conn.commit()
            conn.close()

            yield f"data: {json.dumps({'status': 'complete', 'message': f'EPG generated successfully! {total_programmes} programmes in {generation_time:.2f}s', 'programmes': total_programmes, 'time': f'{generation_time:.2f}s'})}\n\n"

        except Exception as e:
            app.logger.error(f"Error in EPG stream: {e}", exc_info=True)
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    })

@app.route('/download')
def download_epg():
    """Download generated EPG file"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT epg_output_path FROM settings WHERE id = 1")
        result = cursor.fetchone()
        conn.close()

        output_path = result[0] if result else '/app/data/teamarr.xml'

        if not os.path.exists(output_path):
            app.logger.warning(f'EPG file not found at {output_path}')
            flash('EPG file not found. Generate it first.', 'error')
            return redirect(url_for('index'))

        app.logger.info(f'📥 Downloading EPG file: {output_path}')
        return send_file(output_path, as_attachment=True, download_name='teamarr.xml')
    except Exception as e:
        app.logger.error(f"❌ Error downloading EPG: {str(e)}", exc_info=True)
        flash(f"Error downloading EPG: {str(e)}", 'error')
        return redirect(url_for('index'))

@app.route('/teamarr.xml')
def serve_epg():
    """Serve EPG file for IPTV clients"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT epg_output_path FROM settings WHERE id = 1")
        result = cursor.fetchone()
        conn.close()

        output_path = result[0] if result else '/app/data/teamarr.xml'

        if not os.path.exists(output_path):
            app.logger.warning(f'EPG file not found at {output_path}')
            return "EPG file not found. Generate it first.", 404

        app.logger.info(f'📡 Serving EPG file: {output_path}')
        return send_file(output_path, mimetype='application/xml')
    except Exception as e:
        app.logger.error(f"❌ Error serving EPG: {str(e)}", exc_info=True)
        return f"Error serving EPG: {str(e)}", 500

# =============================================================================
# API ENDPOINTS
# =============================================================================

@app.route('/api/parse-espn-url', methods=['POST'])
def api_parse_espn_url():
    """Parse ESPN URL and fetch team data"""
    try:
        url = request.json.get('url', '')

        # Extract team info from URL
        # ESPN URL format: https://www.espn.com/nba/team/_/name/det/detroit-pistons
        import re
        match = re.search(r'espn\.com/([^/]+)/team/_/name/([^/]+)/([^/]+)', url)

        if not match:
            return jsonify({'success': False, 'message': 'Invalid ESPN URL format'})

        league = match.group(1)  # nba, nfl, etc.
        team_slug = match.group(2)  # det, dal, etc.

        # Fetch team data from ESPN API
        espn_client = ESPNClient()
        team_data = espn_client.get_team_info_from_url(url)

        if not team_data:
            return jsonify({'success': False, 'message': 'Could not fetch team data from ESPN'})

        # Fetch default channel ID format from settings and generate suggested channel_id
        conn = get_connection()
        settings = conn.execute("SELECT default_channel_id_format FROM settings WHERE id = 1").fetchone()
        conn.close()

        channel_id_format = settings['default_channel_id_format'] if settings else '{team_abbrev}.{league}'

        # Generate suggested channel ID
        team_data['channel_id'] = _generate_channel_id(
            channel_id_format,
            team_name=team_data.get('team_name', ''),
            team_abbrev=team_data.get('team_abbrev', ''),
            team_slug=team_data.get('team_slug', ''),
            league=team_data.get('league', ''),
            sport=team_data.get('sport', '')
        )

        return jsonify({
            'success': True,
            'team': team_data
        })

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/templates', methods=['GET'])
def api_templates_list():
    """Get all templates (for dropdowns)"""
    templates = get_all_templates()
    return jsonify([{
        'id': t['id'],
        'name': t['name'],
        'sport': t['sport'],
        'league': t['league']
    } for t in templates])

@app.route('/api/variables', methods=['GET'])
def api_variables():
    """Get all template variables from variables.json with suffix availability"""
    import json
    variables_path = os.path.join(os.path.dirname(__file__), 'config', 'variables.json')
    try:
        with open(variables_path, 'r', encoding='utf-8') as f:
            variables_data = json.load(f)

        # Build suffix mapping from audit_summary
        audit_summary = variables_data.get('suffix_system', {}).get('audit_summary', {})

        # Get example lists (full lists would be better, but we'll use what's available)
        last_only_vars = set(audit_summary.get('last_only', {}).get('examples', []))
        base_next_only_vars = set(audit_summary.get('base_next_only', {}).get('examples', []))
        base_only_vars = set(audit_summary.get('base_only', {}).get('examples', []))
        all_three_vars = set(audit_summary.get('all_three', {}).get('examples', []))

        # Build complete sets by checking variable notes
        for var in variables_data.get('variables', []):
            var_name = var['name']
            notes = var.get('notes', '')

            # Determine available suffixes
            available_suffixes = []

            if 'BASE_ONLY' in notes or var_name in base_only_vars:
                available_suffixes = ['base']
            elif 'LAST_ONLY' in notes or var_name in last_only_vars:
                available_suffixes = ['last']
            elif 'BASE_NEXT_ONLY' in notes or var_name in base_next_only_vars:
                available_suffixes = ['base', 'next']
            elif var_name in all_three_vars:
                available_suffixes = ['base', 'next', 'last']
            else:
                # Default: check if it's a game-specific variable (most are all_three)
                if var.get('category') in ['🏈 Teams', '📊 Stats']:
                    available_suffixes = ['base']  # Team stats are usually base only
                else:
                    available_suffixes = ['base', 'next', 'last']  # Game-specific vars

            var['available_suffixes'] = available_suffixes

        return jsonify(variables_data)
    except Exception as e:
        app.logger.error(f"Failed to load variables.json: {e}")
        return jsonify({'error': 'Failed to load variables', 'total_variables': 0, 'variables': []}), 500

@app.route('/api/condition-presets', methods=['GET'])
def api_condition_presets_list():
    """Get all condition presets"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, description, condition_type, condition_value, priority, template, usage_count
        FROM condition_presets
        WHERE active = 1
        ORDER BY name
    """)
    presets = []
    for row in cursor.fetchall():
        presets.append({
            'id': row[0],
            'name': row[1],
            'description': row[2],
            'condition_type': row[3],
            'condition_value': row[4],
            'priority': row[5],
            'template': row[6],
            'usage_count': row[7]
        })
    conn.close()
    return jsonify(presets)

@app.route('/api/condition-presets', methods=['POST'])
def api_condition_preset_create():
    """Create a new condition preset"""
    try:
        data = request.get_json()

        # Validate required fields
        required_fields = ['name', 'condition_type', 'template']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'error': f'Missing required field: {field}'}), 400

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO condition_presets (name, description, condition_type, condition_value, priority, template)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            data['name'],
            data.get('description', ''),
            data['condition_type'],
            data.get('condition_value', ''),
            int(data.get('priority', 50)),
            data['template']
        ))

        conn.commit()
        preset_id = cursor.lastrowid
        conn.close()

        app.logger.info(f"Created condition preset: {data['name']} (ID: {preset_id})")
        return jsonify({'success': True, 'id': preset_id}), 201

    except Exception as e:
        app.logger.error(f"Failed to create condition preset: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/condition-presets/<int:preset_id>', methods=['DELETE'])
def api_condition_preset_delete(preset_id):
    """Delete a condition preset"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE condition_presets SET active = 0 WHERE id = ?", (preset_id,))
    conn.commit()
    conn.close()
    app.logger.info(f"Deactivated condition preset {preset_id}")
    return jsonify({'success': True})

@app.route('/api/leagues', methods=['GET'])
def api_leagues_list():
    """Get all available leagues from league_config"""
    conn = get_connection()
    cursor = conn.cursor()
    results = cursor.execute("""
        SELECT league_code, league_name, sport, api_path, logo_url
        FROM league_config
        WHERE active = 1
        ORDER BY sport, league_name
    """).fetchall()
    conn.close()

    leagues = [{'code': row[0], 'name': row[1], 'sport': row[2], 'api_path': row[3], 'logo': row[4]} for row in results]
    return jsonify({'leagues': leagues})

@app.route('/api/leagues/<league_code>/conferences', methods=['GET'])
def api_league_conferences(league_code):
    """Fetch all conferences for a college league"""
    try:
        # Get league config
        conn = get_connection()
        cursor = conn.cursor()
        league_info = cursor.execute("""
            SELECT sport, api_path, league_name
            FROM league_config
            WHERE league_code = ?
        """, (league_code,)).fetchone()
        conn.close()

        if not league_info:
            return jsonify({'error': 'League not found'}), 404

        sport = league_info[0]
        api_path = league_info[1]
        league_name = league_info[2]

        # Extract the league identifier from api_path
        league_identifier = api_path.split('/')[-1]

        # Fetch conferences from ESPN API
        espn = ESPNClient()
        conferences_data = espn.get_league_conferences(sport, league_identifier)

        if not conferences_data:
            return jsonify({'conferences': []})

        return jsonify({
            'league': {'code': league_code, 'name': league_name, 'sport': sport},
            'conferences': conferences_data
        })

    except Exception as e:
        app.logger.error(f"Error fetching conferences for league {league_code}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/leagues/<league_code>/teams', methods=['GET'])
def api_league_teams(league_code):
    """Fetch all teams for a given league from ESPN API"""
    try:
        # Check if requesting teams for a specific conference
        conference_id = request.args.get('conference')

        # Get league config
        conn = get_connection()
        cursor = conn.cursor()
        league_info = cursor.execute("""
            SELECT sport, api_path, league_name
            FROM league_config
            WHERE league_code = ?
        """, (league_code,)).fetchone()
        conn.close()

        if not league_info:
            return jsonify({'error': 'League not found'}), 404

        sport = league_info[0]
        api_path = league_info[1]
        league_name = league_info[2]

        # Fetch teams from ESPN API using the api_path (not league_code)
        # For example: 'eng.1' for EPL, not 'epl'
        espn = ESPNClient()

        # Extract the league identifier from api_path (e.g., 'soccer/eng.1' -> 'eng.1')
        league_identifier = api_path.split('/')[-1]

        # If conference ID provided, fetch teams for that conference
        if conference_id:
            teams_data = espn.get_conference_teams(sport, league_identifier, int(conference_id))
        else:
            teams_data = espn.get_league_teams(sport, league_identifier)

        if not teams_data:
            return jsonify({'error': 'Failed to fetch teams from ESPN'}), 500

        return jsonify({
            'league': {'code': league_code, 'name': league_name, 'sport': sport},
            'teams': teams_data
        })

    except Exception as e:
        app.logger.error(f"Error fetching teams for league {league_code}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/teams/imported', methods=['GET'])
def api_teams_imported():
    """Get all imported team IDs grouped by league"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        results = cursor.execute("""
            SELECT league, espn_team_id
            FROM teams
            WHERE espn_team_id IS NOT NULL
        """).fetchall()
        conn.close()

        # Group by league
        imported = {}
        for row in results:
            league = row[0]
            team_id = row[1]
            if league not in imported:
                imported[league] = []
            imported[league].append(team_id)

        return jsonify({'imported': imported})

    except Exception as e:
        app.logger.error(f"Error fetching imported teams: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/teams/bulk-import', methods=['POST'])
def api_teams_bulk_import():
    """Bulk import teams from ESPN API data"""
    try:
        data = request.get_json()
        league_code = data.get('league_code')
        sport = data.get('sport')
        teams = data.get('teams', [])

        if not league_code or not sport or not teams:
            return jsonify({'error': 'Missing required fields'}), 400

        # Fetch default channel ID format from settings
        conn = get_connection()
        settings = conn.execute("SELECT default_channel_id_format FROM settings WHERE id = 1").fetchone()
        conn.close()

        channel_id_format = settings['default_channel_id_format'] if settings else '{team_abbrev}.{league}'

        imported_count = 0
        skipped_count = 0
        errors = []

        for team_data in teams:
            try:
                # Generate channel ID using the format from settings
                team_name = team_data.get('name', '')
                team_abbrev = team_data.get('abbreviation', '')
                team_slug = team_data.get('slug', '')

                # Build channel ID from format
                channel_id = _generate_channel_id(
                    channel_id_format,
                    team_name=team_name,
                    team_abbrev=team_abbrev,
                    team_slug=team_slug,
                    league=league_code,
                    sport=sport
                )

                # Check if team already exists
                conn = get_connection()
                existing = conn.execute(
                    "SELECT id FROM teams WHERE espn_team_id = ? AND league = ?",
                    (str(team_data['id']), league_code)
                ).fetchone()
                conn.close()

                if existing:
                    skipped_count += 1
                    app.logger.info(f"Skipped {team_name} - already exists")
                    continue

                # Create team
                new_team = {
                    'espn_team_id': str(team_data['id']),
                    'league': league_code,
                    'sport': sport,
                    'team_name': team_name,
                    'team_abbrev': team_data.get('abbreviation'),
                    'team_slug': team_data.get('slug'),
                    'team_logo_url': team_data.get('logo'),
                    'team_color': team_data.get('color'),
                    'channel_id': channel_id,
                    'template_id': None,  # User assigns template later
                    'active': 1
                }

                create_team(new_team)
                imported_count += 1
                app.logger.info(f"✅ Imported {team_name}")

            except Exception as e:
                errors.append(f"{team_data.get('name', 'Unknown')}: {str(e)}")
                app.logger.error(f"Failed to import {team_data.get('name')}: {e}")

        return jsonify({
            'success': True,
            'imported_count': imported_count,
            'skipped_count': skipped_count,
            'errors': errors
        })

    except Exception as e:
        app.logger.error(f"Bulk import failed: {e}")
        return jsonify({'error': str(e)}), 500

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _analyze_epg_content(xml_content):
    """
    Analyze EPG XML content and return statistics

    Returns:
        dict: Analysis results with counts and findings
    """
    import re
    from datetime import datetime, timedelta
    import xml.etree.ElementTree as ET

    analysis = {
        'total_programs': 0,
        'total_events': 0,
        'filler_programs': {
            'pregame': 0,
            'postgame': 0,
            'idle': 0,
            'other': 0
        },
        'unreplaced_variables': [],
        'channels': 0,
        'coverage_gaps': [],
        'date_range': None
    }

    try:
        # Parse XML
        root = ET.fromstring(xml_content)

        # Count channels
        analysis['channels'] = len(root.findall('channel'))

        # Analyze programs
        programs = root.findall('.//programme')
        analysis['total_programs'] = len(programs)

        # Track program times per channel for gap detection
        channel_programs = {}

        for programme in programs:
            channel = programme.get('channel', '')
            start = programme.get('start', '')
            stop = programme.get('stop', '')

            # Get title and description
            title_elem = programme.find('title')
            desc_elem = programme.find('desc')
            title = title_elem.text if title_elem is not None else ''
            desc = desc_elem.text if desc_elem is not None else ''

            # Check for unreplaced variables in title and description
            for text in [title, desc]:
                if text:
                    vars_found = re.findall(r'\{[^}]+\}', text)
                    for var in vars_found:
                        if var not in analysis['unreplaced_variables']:
                            analysis['unreplaced_variables'].append(var)

            # Classify program type
            is_filler = False
            if title:
                title_lower = title.lower()
                if 'pregame' in title_lower or 'pre-game' in title_lower or 'preview' in title_lower or 'starting soon' in title_lower:
                    analysis['filler_programs']['pregame'] += 1
                    is_filler = True
                elif 'postgame' in title_lower or 'post-game' in title_lower or 'recap' in title_lower or 'highlights' in title_lower or 'replay' in title_lower:
                    analysis['filler_programs']['postgame'] += 1
                    is_filler = True
                elif 'programming' in title_lower or 'next game' in title_lower or 'no game' in title_lower:
                    analysis['filler_programs']['idle'] += 1
                    is_filler = True
                elif not any(sport in title_lower for sport in ['football', 'basketball', 'baseball', 'hockey', 'soccer']):
                    # Might be other filler
                    analysis['filler_programs']['other'] += 1
                    is_filler = True

            if not is_filler:
                analysis['total_events'] += 1

            # Track for gap detection
            if channel not in channel_programs:
                channel_programs[channel] = []
            channel_programs[channel].append({
                'start': start,
                'stop': stop,
                'title': title
            })

        # Detect coverage gaps (programs that don't connect properly)
        for channel, progs in channel_programs.items():
            # Sort by start time
            progs_sorted = sorted(progs, key=lambda x: x['start'])
            for i in range(len(progs_sorted) - 1):
                current_stop = progs_sorted[i]['stop']
                next_start = progs_sorted[i + 1]['start']

                # If there's a gap (stop time doesn't match next start time)
                if current_stop != next_start:
                    try:
                        # Parse times (format: YYYYMMDDHHMMSS +0000)
                        stop_time = datetime.strptime(current_stop[:14], '%Y%m%d%H%M%S')
                        start_time = datetime.strptime(next_start[:14], '%Y%m%d%H%M%S')
                        gap_minutes = (start_time - stop_time).total_seconds() / 60

                        # Only report gaps > 1 minute (to avoid floating point rounding)
                        if gap_minutes > 1:
                            analysis['coverage_gaps'].append({
                                'channel': channel,
                                'gap_minutes': int(gap_minutes),
                                'after_program': progs_sorted[i]['title'],
                                'before_program': progs_sorted[i + 1]['title']
                            })
                    except:
                        pass

        # Calculate date range
        if programs:
            try:
                start_times = [p.get('start', '') for p in programs]
                stop_times = [p.get('stop', '') for p in programs]
                if start_times and stop_times:
                    earliest = min(start_times)
                    latest = max(stop_times)
                    analysis['date_range'] = {
                        'start': earliest[:8],  # YYYYMMDD
                        'end': latest[:8]
                    }
            except:
                pass

    except Exception as e:
        app.logger.error(f"Error analyzing EPG: {e}")

    return analysis

def _generate_channel_id(format_template, **kwargs):
    """
    Generate a channel ID based on the format template from settings

    Args:
        format_template: String template with variables like '{team_abbrev}.{league}'
        **kwargs: Variables to substitute (team_name, team_abbrev, team_slug, league, sport)

    Returns:
        str: Generated channel ID (lowercased, sanitized)
    """
    # Start with the format template
    channel_id = format_template

    # Replace all available variables
    replacements = {
        '{team_name}': kwargs.get('team_name', ''),
        '{team_abbrev}': kwargs.get('team_abbrev', ''),
        '{team_slug}': kwargs.get('team_slug', ''),
        '{league}': kwargs.get('league', ''),
        '{sport}': kwargs.get('sport', '')
    }

    for placeholder, value in replacements.items():
        channel_id = channel_id.replace(placeholder, str(value))

    # Clean up the channel ID (lowercase, remove special chars, replace spaces)
    channel_id = channel_id.lower()
    channel_id = channel_id.replace(' ', '-')
    channel_id = channel_id.replace("'", "")
    # Remove any other problematic characters
    import re
    channel_id = re.sub(r'[^a-z0-9.-]', '', channel_id)

    return channel_id

def _extract_template_form_data(form):
    """Extract template data from form submission"""
    data = {
        'name': form.get('name'),

        # Programme templates
        'title_format': form.get('title_format'),
        'subtitle_template': form.get('subtitle_template'),
        'program_art_url': form.get('program_art_url'),

        # Game settings
        'game_duration_mode': form.get('game_duration_mode', 'default'),
        'game_duration_override': float(form.get('game_duration_override')) if form.get('game_duration_override') else None,

        # XMLTV settings
        'flags': form.get('flags'),  # Already JSON string
        'categories': form.get('categories'),  # Already JSON string
        'categories_apply_to': form.get('categories_apply_to', 'events'),

        # Filler content
        'pregame_enabled': 1 if form.get('pregame_enabled') == 'on' else 0,
        'pregame_title': form.get('pregame_title'),
        'pregame_subtitle': form.get('pregame_subtitle'),
        'pregame_description': form.get('pregame_description'),
        'pregame_art_url': form.get('pregame_art_url'),
        'pregame_periods': form.get('pregame_periods'),  # JSON string

        'postgame_enabled': 1 if form.get('postgame_enabled') == 'on' else 0,
        'postgame_title': form.get('postgame_title'),
        'postgame_subtitle': form.get('postgame_subtitle'),
        'postgame_description': form.get('postgame_description'),
        'postgame_art_url': form.get('postgame_art_url'),
        'postgame_periods': form.get('postgame_periods'),  # JSON string

        'idle_enabled': 1 if form.get('idle_enabled') == 'on' else 0,
        'idle_title': form.get('idle_title'),
        'idle_subtitle': form.get('idle_subtitle'),
        'idle_description': form.get('idle_description'),
        'idle_art_url': form.get('idle_art_url'),

        # Conditional descriptions
        'description_options': form.get('description_options')  # JSON string
    }

    return data

def _extract_team_form_data(form):
    """Extract team data from form submission"""
    data = {
        'espn_team_id': form.get('espn_team_id'),
        'league': form.get('league'),
        'sport': form.get('sport'),
        'team_name': form.get('team_name'),
        'team_abbrev': form.get('team_abbrev'),
        'team_logo_url': form.get('team_logo_url'),
        'team_color': form.get('team_color'),
        'channel_id': form.get('channel_id'),
        'channel_logo_url': form.get('channel_logo_url') if form.get('channel_logo_url') else None,
        'template_id': int(form.get('template_id')) if form.get('template_id') and form.get('template_id') != '' else None,
        'active': 1 if form.get('active') == 'on' else 0,
    }

    return data

# =============================================================================
# RUN APPLICATION
# =============================================================================

if __name__ == '__main__':
    # Start the auto-generation scheduler
    # Only start in main process, not in werkzeug reloader process
    # In Docker/production, WERKZEUG_RUN_MAIN won't be set, so check differently
    import sys
    is_reloader = sys.argv[0].endswith('flask') and '--reloader' in ' '.join(sys.argv)
    is_werkzeug_child = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'

    # Start scheduler only if: not running from reloader, OR we're the werkzeug child process
    if not is_reloader or is_werkzeug_child:
        start_scheduler()

    try:
        port = int(os.environ.get('PORT', 9195))
        app.run(host='0.0.0.0', port=port, debug=True, use_reloader=False)  # Disable reloader to prevent duplicates
    except KeyboardInterrupt:
        stop_scheduler()
        print("👋 Goodbye!")
