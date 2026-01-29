"""Service for Regular TV (Virtual M3U) playlist generation."""

import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

from dateutil import parser
from pytz import UTC

from teamarr.database import get_db
from teamarr.database.regular_tv import RegularTVGroup, get_all_groups
from teamarr.dispatcharr import get_factory

logger = logging.getLogger(__name__)

OUTPUT_FILE = Path("data/regular_tv.m3u")
EXCLUDED_OUTPUT_FILE = Path("data/regular_tv_excluded.m3u")


class RegularTVService:
    """Service to generate a virtual M3U based on EPG metadata matching."""

    def generate_playlist(self) -> dict:
        """
        Processes EPG data for specific M3U groups and creates unique 
        M3U entries for individual programs/matches.
        """
        logger.info("[REGULAR_TV] DEBUG: Starting Playlist Generation")
        
        with get_db() as conn:
            # 1. Get Settings
            settings_row = conn.execute(
                "SELECT regular_tv_enabled, regular_tv_lookback_hours, regular_tv_lookahead_hours, regular_tv_epg_source_id FROM settings WHERE id = 1"
            ).fetchone()

            enabled = bool(settings_row["regular_tv_enabled"]) if settings_row and settings_row["regular_tv_enabled"] is not None else True
            if not enabled:
                logger.info("[REGULAR_TV] Generation disabled in settings")
                return {"status": "skipped", "message": "Regular TV generation is disabled"}

            lookback = settings_row["regular_tv_lookback_hours"] if settings_row else 0.0
            lookahead = settings_row["regular_tv_lookahead_hours"] if settings_row else 24.0
            global_epg_id = settings_row["regular_tv_epg_source_id"] if settings_row else None
            
            # 2. Get only enabled groups
            groups = [g for g in get_all_groups(conn) if g.enabled]

        if not groups:
            return {"status": "error", "warnings": ["No groups enabled"]}

        factory = get_factory()
        client = factory.get_connection()

        # 3. Map Groups to their Accounts and EPG Sources
        # We need to know which tvg-ids to pull from the XML
        needed_tvg_ids_by_source = {} 
        streams_by_account = {}
        
        for group in groups:
            # Fetch streams for this account if not already cached
            if group.m3u_account_id not in streams_by_account:
                streams_by_account[group.m3u_account_id] = client.m3u.list_streams(account_id=group.m3u_account_id)
            
            source_id = group.epg_source_id or global_epg_id
            if not source_id: continue
            
            if source_id not in needed_tvg_ids_by_source:
                needed_tvg_ids_by_source[source_id] = set()

            # Identify which tvg_ids belong to this specific M3U group
            for stream in streams_by_account[group.m3u_account_id]:
                # Check ID match first if available
                if group.m3u_group_id and getattr(stream, "channel_group", None) == group.m3u_group_id:
                    if stream.tvg_id:
                        needed_tvg_ids_by_source[source_id].add(stream.tvg_id)
                    continue
                
                # Fallback to name match
                if getattr(stream, "channel_group_name", None) == group.m3u_group_name and stream.tvg_id:
                    needed_tvg_ids_by_source[source_id].add(stream.tvg_id)

        # 4. Fetch and Parse EPGs (The "Shopping List" approach)
        now = datetime.now(UTC)
        fetch_start = now - timedelta(hours=lookback)
        fetch_end = now + timedelta(hours=lookahead)
        programs_by_source = {}

        for source_id, allowed_ids in needed_tvg_ids_by_source.items():
            try:
                epg_source = client.epg.get_source(source_id)
                if not epg_source or not epg_source.url: continue

                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
                req = urllib.request.Request(epg_source.url, headers=headers)
                
                with urllib.request.urlopen(req, timeout=240) as response:
                    # Parse only the channels we found in our groups
                    programs_by_source[source_id] = self._parse_programs_in_window(
                        response, fetch_start, fetch_end, allowed_ids
                    )
            except Exception as e:
                logger.error(f"EPG Source {source_id} failed: {e}")

        # 5. Build the Virtual M3U
        playlist_entries = []
        excluded_entries = []
        # Keywords to identify "Matches" or "Sporting Events"
        # \b ensures "vs" is its own word and not the start of "VSiN"
        # \b at BOTH ends ensures "vs" is not the start of "VSiN" 
# and "v" is not the start of "Vegas"
        sport_keywords = re.compile(r"\bvs\.?\b|\bv\.?\b|(?<=\s)at\b", re.IGNORECASE)
        # Exclusion of specific feeds
        exclude_keywords = re.compile(r"news|highlights", re.IGNORECASE)

        for group in groups:
            source_id = group.epg_source_id or global_epg_id
            epg_data = programs_by_source.get(source_id, {})
            account_streams = streams_by_account.get(group.m3u_account_id, [])

            # Filter streams to just this group
            if group.m3u_group_id:
                group_streams = [s for s in account_streams if getattr(s, "channel_group", None) == group.m3u_group_id]
            else:
                # Fallback to name match
                group_streams = [
                    s for s in account_streams if getattr(s, "channel_group_name", None) == group.m3u_group_name
                ]

            for stream in group_streams:
                channel_programs = epg_data.get(stream.tvg_id, [])
                
                for prog in channel_programs:
                    title = prog['title']
                    
                    # Check if this program looks like a match/event
                    if sport_keywords.search(title) and not exclude_keywords.search(title):
                        # Teamarr M3U Format
                        # We use the program title as the channel name so Teamarr can parse the teams
                        
                        clean_title = (title or '').strip().replace('\n', ' ')

                        entry = (
                            f'#EXTINF:-1 tvg-id="{stream.tvg_id}" tvg-name="{stream.tvg_name}" '
                            f'group-title="Regular_TV",{clean_title}\n'
                            f'{stream.url}'
                        )
                        playlist_entries.append(entry)
                    else:
                        clean_title = (title or '').strip().replace('\n', ' ')
                        entry = (
                            f'#EXTINF:-1 tvg-id="{stream.tvg_id}" tvg-name="{stream.tvg_name}" '
                            f'group-title="Regular_TV_Excluded",{clean_title}\n'
                            f'{stream.url}'
                        )
                        excluded_entries.append(entry)

        # 6. Save
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n" + "\n".join(playlist_entries))

        with open(EXCLUDED_OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n" + "\n".join(excluded_entries))

        return {
            "matched_streams": len(playlist_entries),
            "excluded_streams": len(excluded_entries),
            "groups_processed": len(groups)
        }

    def _parse_programs_in_window(self, xml_stream, window_start, window_end, allowed_ids):
        """Standard XMLTV Parser with allowed_id filtering."""
        programs = {}
        context = ET.iterparse(xml_stream, events=("end",))
        for _, elem in context:
            if elem.tag == "programme":
                chan_id = elem.get("channel")
                if chan_id in allowed_ids:
                    try:
                        start = parser.parse(elem.get("start"))
                        stop = parser.parse(elem.get("stop"))
                        if stop >= window_start and start <= window_end:
                            if chan_id not in programs: programs[chan_id] = []
                            programs[chan_id].append({
                                "title": elem.findtext("title"),
                                "category": elem.findtext("category"),
                                "desc": elem.findtext("desc"),
                                "start": start,
                                "stop": stop
                            })
                    except: pass
                elem.clear()
        return programs