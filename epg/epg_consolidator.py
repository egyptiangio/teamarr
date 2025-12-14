"""
EPG Consolidator - Manages the EPG file pipeline

File structure:
  /data/teams.xml         - Team-based EPG (one channel per team)
  /data/event_epg_*.xml   - Per-group event EPG files
  /data/teamarr.xml       - Final combined EPG (teams + all events)

Flow:
  Team generation    → teams.xml       ─┐
                                        ├─→ teamarr.xml
  Event generation   → event_epg_*.xml ─┘

Single-stage consolidation: teams.xml + all event_epg_*.xml → teamarr.xml
"""

import os
import glob
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Default data directory
DEFAULT_DATA_DIR = '/app/data'


def get_data_dir(from_output_path: str = None) -> str:
    """
    Get data directory for intermediate EPG files.

    Priority:
    1. Derive from output path if provided (keeps all files together)
    2. Use /app/data if running in Docker container
    3. Fall back to project's data dir for local development

    Args:
        from_output_path: If provided, use the parent directory of this path
    """
    # If output path provided, use its directory (keeps all files together)
    # This works for both Docker and local dev because os.path.abspath() resolves
    # relative paths based on the current working directory:
    #   - Docker (cwd=/app): ./data/teamarr.xml → /app/data/teamarr.xml
    #   - Local dev (cwd=/project): ./data/teamarr.xml → /project/data/teamarr.xml
    if from_output_path:
        output_abs = os.path.abspath(from_output_path)
        return os.path.dirname(output_abs)

    # Check if actually running in Docker container (not just if path exists)
    # Docker containers have /.dockerenv or /run/.containerenv
    in_docker = os.path.exists('/.dockerenv') or os.path.exists('/run/.containerenv')

    if in_docker and os.path.exists('/app/data'):
        return '/app/data'

    # Fallback to project's data dir
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base_dir, 'data')


def get_epg_paths(final_output_path: str = None) -> Dict[str, str]:
    """
    Get all EPG file paths.

    Args:
        final_output_path: Override for final merged output (from settings)
    """
    # Use output path's directory to keep all files together
    data_dir = get_data_dir(final_output_path)

    # Final output path from settings or default
    if not final_output_path:
        final_output_path = os.path.join(data_dir, 'teamarr.xml')

    return {
        'teams': os.path.join(data_dir, 'teams.xml'),
        'combined': final_output_path,
        'data_dir': data_dir
    }


def cleanup_old_archives(data_dir: str) -> int:
    """
    Remove old .bak archive files from previous consolidation cycles.

    NOTE: teams.xml.bak is NOT cleaned up because teams.xml is never archived.
    teams.xml persists across cycles to prevent race conditions.

    Returns:
        Number of files deleted
    """
    deleted = 0
    bak_patterns = [
        os.path.join(data_dir, 'event_epg_*.xml.bak'),
        os.path.join(data_dir, 'events.xml.bak'),  # Legacy intermediate file
    ]

    for pattern in bak_patterns:
        for bak_file in glob.glob(pattern):
            try:
                os.remove(bak_file)
                logger.debug(f"Removed old archive: {os.path.basename(bak_file)}")
                deleted += 1
            except Exception as e:
                logger.warning(f"Could not remove {bak_file}: {e}")

    # Also remove legacy events.xml if it exists (old two-stage intermediate)
    legacy_events = os.path.join(data_dir, 'events.xml')
    if os.path.exists(legacy_events):
        try:
            os.remove(legacy_events)
            logger.info(f"Removed legacy events.xml")
            deleted += 1
        except Exception as e:
            logger.warning(f"Could not remove legacy events.xml: {e}")

    return deleted


def archive_intermediate_files(files: List[str]) -> int:
    """
    Archive intermediate files by renaming them to .bak.

    Args:
        files: List of file paths to archive

    Returns:
        Number of files archived
    """
    archived = 0
    for filepath in files:
        if os.path.exists(filepath):
            bak_path = filepath + '.bak'
            try:
                # Remove existing .bak if present
                if os.path.exists(bak_path):
                    os.remove(bak_path)
                os.rename(filepath, bak_path)
                logger.debug(f"Archived: {os.path.basename(filepath)} -> {os.path.basename(bak_path)}")
                archived += 1
            except Exception as e:
                logger.warning(f"Could not archive {filepath}: {e}")
    return archived


def merge_all_epgs(final_output_path: str = None, cleanup: bool = True) -> Dict[str, Any]:
    """
    Merge teams.xml and all event_epg_*.xml files into final output.

    Single-stage consolidation that combines:
    - teams.xml (team-based EPG)
    - All event_epg_*.xml files (per-group event EPGs)

    After successful merge, intermediate files are archived (.bak) and
    old archives from previous cycles are deleted.

    Args:
        final_output_path: Final destination (from settings' epg_output_path)
        cleanup: If True, archive intermediate files after merge (default: True)

    Returns:
        Dict with success status and stats
    """
    from epg.event_epg_generator import merge_xmltv_files

    paths = get_epg_paths(final_output_path)
    data_dir = paths['data_dir']

    # First, clean up old .bak archives from previous cycle
    if cleanup:
        old_deleted = cleanup_old_archives(data_dir)
        if old_deleted:
            logger.debug(f"Cleaned up {old_deleted} old archive files")

    # Collect all files to merge
    files_to_merge = []

    # Include teams.xml if it exists
    if os.path.exists(paths['teams']):
        files_to_merge.append(paths['teams'])
        logger.debug(f"Including teams.xml in merge")

    # Include all event_epg_*.xml files
    event_pattern = os.path.join(data_dir, 'event_epg_*.xml')
    event_files = glob.glob(event_pattern)
    if event_files:
        files_to_merge.extend(event_files)
        logger.debug(f"Including {len(event_files)} event EPG files in merge")

    if not files_to_merge:
        logger.warning("No EPG files to merge - creating empty teamarr.xml")
        empty_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE tv SYSTEM "xmltv.dtd">
<tv generator-info-name="Teamarr">
</tv>'''
        os.makedirs(data_dir, exist_ok=True)
        with open(paths['combined'], 'w') as f:
            f.write(empty_xml)
        return {
            'success': True,
            'files_merged': 0,
            'output_path': paths['combined']
        }

    logger.info(f"Merging {len(files_to_merge)} EPG files into {paths['combined']}")
    for f in files_to_merge:
        logger.debug(f"  - {os.path.basename(f)}")

    result = merge_xmltv_files(
        file_paths=files_to_merge,
        output_path=paths['combined'],
        generator_name="Teamarr"
    )

    if result.get('success'):
        logger.info(f"Combined EPG: {result.get('channel_count')} channels, {result.get('programme_count')} programmes")

        # Archive intermediate files after successful merge
        if cleanup and files_to_merge:
            archived = archive_intermediate_files(files_to_merge)
            if archived:
                logger.debug(f"Archived {archived} intermediate files")
            result['files_archived'] = archived

    return result


def after_team_epg_generation(xml_content: str, final_output_path: str = None) -> Dict[str, Any]:
    """
    Called after team EPG is generated.

    Archives existing teams.xml to .bak (if present), saves new teams.xml,
    then triggers merge to final output.

    Args:
        xml_content: Generated team XMLTV content
        final_output_path: Final merged destination (from settings' epg_output_path)

    Returns:
        Dict with file paths and merge result
    """
    paths = get_epg_paths(final_output_path)
    os.makedirs(paths['data_dir'], exist_ok=True)

    # Archive existing teams.xml to .bak before writing new one
    # This preserves the previous generation as a backup
    if os.path.exists(paths['teams']):
        bak_path = paths['teams'] + '.bak'
        try:
            if os.path.exists(bak_path):
                os.remove(bak_path)
            os.rename(paths['teams'], bak_path)
            logger.debug(f"Archived previous teams.xml to {os.path.basename(bak_path)}")
        except Exception as e:
            logger.warning(f"Could not archive teams.xml: {e}")

    # Save new teams.xml
    with open(paths['teams'], 'w', encoding='utf-8') as f:
        f.write(xml_content)
    logger.info(f"Saved team EPG to {paths['teams']}")

    # Trigger merge WITHOUT cleanup - cleanup happens after full generation cycle
    merge_result = merge_all_epgs(final_output_path, cleanup=False)

    return {
        'teams_path': paths['teams'],
        'combined_path': paths['combined'],
        'merge_result': merge_result
    }


def after_event_epg_generation(group_id: int = None, final_output_path: str = None) -> Dict[str, Any]:
    """
    Called after event EPG is generated for a group.

    Triggers merge of teams.xml + all event_epg_*.xml → teamarr.xml
    Does NOT archive intermediate files - that happens after the full generation cycle.

    Args:
        group_id: Optional group ID that was just generated (for logging)
        final_output_path: Final merged destination (from settings' epg_output_path)

    Returns:
        Dict with merge results
    """
    if group_id:
        logger.info(f"Event EPG updated for group {group_id}, merging all EPGs...")

    # Trigger merge WITHOUT cleanup - cleanup happens after full generation cycle
    merge_result = merge_all_epgs(final_output_path, cleanup=False)

    paths = get_epg_paths(final_output_path)
    return {
        'success': merge_result.get('success', False),
        'combined_path': paths['combined'],
        'merge_result': merge_result
    }


def finalize_epg_generation(final_output_path: str = None) -> Dict[str, Any]:
    """
    Finalize EPG generation by archiving intermediate event files.

    Call this ONCE at the end of the full generation cycle (after all teams and events).

    NOTE: teams.xml is NOT archived - it persists across cycles so that subsequent
    event-only refreshes can still include team-based EPG in the merge.

    Args:
        final_output_path: Final merged destination (from settings' epg_output_path)

    Returns:
        Dict with cleanup stats
    """
    paths = get_epg_paths(final_output_path)
    data_dir = paths['data_dir']

    # Clean up old archives first (only event_epg_*.xml.bak, NOT teams.xml.bak)
    old_deleted = cleanup_old_archives(data_dir)

    # Only archive event EPG files - teams.xml persists across cycles
    # This prevents race conditions where a subsequent refresh loses team EPG
    files_to_archive = []
    event_pattern = os.path.join(data_dir, 'event_epg_*.xml')
    event_files = glob.glob(event_pattern)
    files_to_archive.extend(event_files)

    # Archive them
    archived = archive_intermediate_files(files_to_archive)

    logger.info(f"Finalized EPG: archived {archived} event files, cleaned {old_deleted} old archives")

    return {
        'files_archived': archived,
        'old_archives_deleted': old_deleted
    }


def get_epg_stats() -> Dict[str, Any]:
    """
    Get statistics about current EPG files.

    Returns:
        Dict with file info and stats
    """
    import xml.etree.ElementTree as ET

    paths = get_epg_paths()
    stats = {}

    # Check teams.xml
    if os.path.exists(paths['teams']):
        try:
            tree = ET.parse(paths['teams'])
            root = tree.getroot()
            stats['teams'] = {
                'exists': True,
                'path': paths['teams'],
                'size': os.path.getsize(paths['teams']),
                'channels': len(root.findall('channel')),
                'programmes': len(root.findall('programme')),
                'modified': os.path.getmtime(paths['teams'])
            }
        except Exception as e:
            stats['teams'] = {
                'exists': True,
                'path': paths['teams'],
                'error': str(e)
            }
    else:
        stats['teams'] = {
            'exists': False,
            'path': paths['teams']
        }

    # Check combined output
    if os.path.exists(paths['combined']):
        try:
            tree = ET.parse(paths['combined'])
            root = tree.getroot()
            stats['combined'] = {
                'exists': True,
                'path': paths['combined'],
                'size': os.path.getsize(paths['combined']),
                'channels': len(root.findall('channel')),
                'programmes': len(root.findall('programme')),
                'modified': os.path.getmtime(paths['combined'])
            }
        except Exception as e:
            stats['combined'] = {
                'exists': True,
                'path': paths['combined'],
                'error': str(e)
            }
    else:
        stats['combined'] = {
            'exists': False,
            'path': paths['combined']
        }

    # Count and list event EPG files
    pattern = os.path.join(paths['data_dir'], 'event_epg_*.xml')
    event_files = glob.glob(pattern)
    stats['event_group_files'] = {
        'count': len(event_files),
        'files': [os.path.basename(f) for f in event_files]
    }

    # Get stats for each event file
    event_stats = []
    for event_file in event_files:
        try:
            tree = ET.parse(event_file)
            root = tree.getroot()
            event_stats.append({
                'file': os.path.basename(event_file),
                'channels': len(root.findall('channel')),
                'programmes': len(root.findall('programme'))
            })
        except Exception as e:
            event_stats.append({
                'file': os.path.basename(event_file),
                'error': str(e)
            })
    stats['event_files_detail'] = event_stats

    return stats
