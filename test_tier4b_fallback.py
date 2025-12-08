#!/usr/bin/env python3
"""
Synthetic test for Tier 4b+ fallback fix.

Tests the case where:
1. Team matcher finds both team IDs (but wrong ones - e.g., "Miami" matches Miami Hurricanes)
2. find_and_enrich() returns not found (no game between Maine and Miami Hurricanes)
3. Disambiguation fails (no alternates work)
4. NEW: Tier 4b+ fallback searches raw opponent name across team schedules
5. Finds the correct game: Maine Black Bears @ Miami (OH) RedHawks

Stream: ESPN+ 62 : Maine vs. Miami (OH) @ Dec 06 01:00 PM ET
Expected: Match to ESPN event 401706814 (Maine at Miami (OH) in NCAAM)
"""

import sys
import os
import logging

# Setup path
sys.path.insert(0, '/srv/dev-disk-by-uuid-c332869f-d034-472c-a641-ccf1f28e52d6/scratch/teamarr')

# Setup logging to see debug output
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Reduce noise from other loggers
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('database').setLevel(logging.WARNING)

from epg.multi_sport_matcher import MultiSportMatcher, MatcherConfig
from epg.league_detector import LeagueDetector
from epg.team_matcher import TeamMatcher
from epg.event_matcher import EventMatcher
from api.espn_client import ESPNClient


def main():
    print("=" * 80)
    print("TIER 4b+ FALLBACK TEST")
    print("Testing: Maine vs. Miami (OH) @ Dec 06 01:00 PM ET")
    print("=" * 80)
    print()

    # Initialize components
    print("Initializing components...")
    espn_client = ESPNClient()

    # Create team matcher
    team_matcher = TeamMatcher(espn_client)

    # Create event matcher
    event_matcher = EventMatcher(espn_client)

    # Create league detector
    enabled_leagues = ['mens-college-basketball', 'womens-college-basketball']
    league_detector = LeagueDetector(
        espn_client=espn_client,
        enabled_leagues=enabled_leagues,
        lookahead_days=7
    )

    # Configure for multi-sport matching with all common leagues enabled
    config = MatcherConfig(
        enabled_leagues=enabled_leagues,
        include_final_events=False,  # Don't include finished games
        soccer_enabled=False
    )

    matcher = MultiSportMatcher(
        team_matcher=team_matcher,
        event_matcher=event_matcher,
        league_detector=league_detector,
        config=config
    )

    # The problematic stream - use a dict with 'name' key like real streams
    stream_name = "ESPN+ 62 : Maine vs. Miami (OH) @ Dec 06 01:00 PM ET"
    stream = {'name': stream_name, 'id': 'test-62'}

    print(f"\nStream name: {stream_name}")
    print("-" * 80)

    # Run the matcher
    print("\nRunning MultiSportMatcher.match_stream()...")
    print()

    result = matcher.match_stream(stream)

    print()
    print("=" * 80)
    print("RESULT:")
    print("=" * 80)
    print(f"  matched: {result.matched}")
    print(f"  reason: {result.reason}")
    print(f"  detected_league: {result.detected_league}")
    print(f"  detection_tier: {result.detection_tier}")

    if result.team_result:
        print(f"\nTeam Result:")
        print(f"  away_team_id: {result.team_result.get('away_team_id')}")
        print(f"  away_team_name: {result.team_result.get('away_team_name')}")
        print(f"  home_team_id: {result.team_result.get('home_team_id')}")
        print(f"  home_team_name: {result.team_result.get('home_team_name')}")

    if result.event:
        event = result.event
        print(f"\nEvent Found:")
        print(f"  id: {event.get('id')}")
        print(f"  name: {event.get('name')}")
        print(f"  date: {event.get('date')}")

        competitions = event.get('competitions', [{}])
        if competitions:
            comp = competitions[0]
            competitors = comp.get('competitors', [])
            for c in competitors:
                team = c.get('team', {})
                home_away = c.get('homeAway', 'unknown')
                print(f"  {home_away}: {team.get('displayName')} (ID: {team.get('id')})")

    print()
    if result.matched:
        print("✅ TEST PASSED - Stream matched successfully!")
        return 0
    else:
        print("❌ TEST FAILED - Stream did not match")
        return 1


if __name__ == '__main__':
    sys.exit(main())
