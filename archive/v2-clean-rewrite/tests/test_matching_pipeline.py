"""Test the Events → Streams matching pipeline with real v1 stream data."""

from datetime import date

from teamarr.consumers import MultiLeagueMatcher, SingleLeagueMatcher
from teamarr.providers.espn import ESPNProvider
from teamarr.services import SportsDataService

# Real stream names from v1 ESPN+ multi-event group (Dec 12, 2025)
ESPN_PLUS_STREAMS = [
    "ESPN+ 01 : Perth Glory vs. Wellington Phoenix @ Dec 12 05:55 AM ET",
    "ESPN+ 05 : Florida Gulf Coast vs. Florida Atlantic @ Dec 12 11:00 AM ET",
    "ESPN+ 10 : UT Arlington vs. Rice @ Dec 12 12:15 PM ET",
    "ESPN+ 11 : SpVgg Greuther Fürth vs. Hertha BSC @ Dec 12 12:25 PM ET",
    "ESPN+ 12 : SG Dynamo Dresden vs. Eintracht Braunschweig @ Dec 12 12:25 PM ET",
    "ESPN+ 15 : 1. FC Union Berlin vs. RB Leipzig @ Dec 12 02:20 PM ET",
    "ESPN+ 16 : En Español-1. FC Union Berlin vs. RB Leipzig @ Dec 12 02:20 PM ET",
    "ESPN+ 18 : Real Sociedad vs. Girona FC @ Dec 12 02:20 PM ET",
    "ESPN+ 19 : En Español-Real Sociedad vs. Girona FC @ Dec 12 02:55 PM ET",
    "ESPN+ 20 : Saint Michael's vs. Dartmouth @ Dec 12 03:00 PM ET",
    "ESPN+ 26 : Army vs. UMBC @ Dec 12 06:00 PM ET",
    "ESPN+ 28 : East Texas A&M vs. McNeese @ Dec 12 06:00 PM ET",
    "ESPN+ 30 : Wright State vs. Canisius @ Dec 12 06:30 PM ET",
    "ESPN+ 31 : Yale vs. Merrimack @ Dec 12 07:00 PM ET",
    "ESPN+ 32 : Maine vs. Fairfield @ Dec 12 07:00 PM ET",
    "ESPN+ 35 : Army vs. #8 Dartmouth @ Dec 12 07:00 PM ET",
    "ESPN+ 36 : SE Louisiana vs. Houston Christian @ Dec 12 07:00 PM ET",
    "ESPN+ 37 : Alaska-Fairbanks vs. Union @ Dec 12 07:00 PM ET",
    "ESPN+ 38 : South Carolina State vs. Queens University @ Dec 12 07:00 PM ET",
    "ESPN+ 41 : SIUE vs. Valparaiso @ Dec 12 07:00 PM ET",
    "ESPN+ 42 : William Peace vs. UNC Greensboro @ Dec 12 07:00 PM ET",
    "ESPN+ 44 : Chicago Blackhawks vs. St. Louis Blues @ Dec 12 08:00 PM ET",
    "ESPN+ 46 : East Tennessee State vs. Austin Peay @ Dec 12 08:00 PM ET",
    "ESPN+ 49 : East Texas A&M vs. McNeese @ Dec 12 08:30 PM ET",
    "ESPN+ 48 : Seattle Kraken vs. Utah Mammoth @ Dec 12 08:30 PM ET",
    "ESPN+ 50 : Texas Tech vs. Washington State @ Dec 12 09:00 PM ET",
    "ESPN+ 51 : California Baptist vs. Eastern Washington @ Dec 12 09:00 PM ET",
    "ESPN+ 52 : Newcastle Jets vs. Brisbane Roar FC @ Dec 12 09:55 PM ET",
]

NBA_STREAMS = [
    "NBA 01: Chicago Bulls  vs  Charlotte Hornets @ 07:00 PM ET",
    "NBA 02: Atlanta Hawks  vs  Detroit Pistons @ 07:00 PM ET",
    "NBA 03: Indiana Pacers  vs  Philadelphia 76ers @ 07:00 PM ET",
    "NBA 04: Cleveland Cavaliers  vs  Washington Wizards @ 07:00 PM ET",
    "NBA 05: Utah Jazz  vs  Memphis Grizzlies @ 08:00 PM ET",
    "NBA 06: Brooklyn Nets  vs  Dallas Mavericks @ 08:30 PM ET",
    "NBA 07: Minnesota Timberwolves  vs  Golden State Warriors @ 10:00 PM ET",
]

NFL_STREAMS = [
    "NFL Game Pass 01: Atlanta Falcons  vs  Tampa Bay Buccaneers @ 08:15 PM ET",
    "NFL Game Pass 02: Falcons @ Buccaneers (Prime Vision with Next Gen Stats) @ 08:15 PM ET",
]

NHL_STREAMS = [
    "NHL 01: Chicago Blackhawks  vs  St. Louis Blues @ 08:00 PM ET",
    "NHL 02: Seattle Kraken  vs  Utah Mammoth @ 09:00 PM ET",
]

NCAAB_STREAMS = [
    "NCAAB 01: Army vs UMBC @ Dec 12 06:00 PM ET",
    "NCAAB 03: Missouri State vs Xavier @ Dec 12 07:00 PM ET",
    "NCAAB 04: William Peace vs UNC Greensboro @ Dec 12 07:00 PM ET",
    "NCAAB 05: Maryland-Eastern Shore vs North Carolina A&T @ Dec 12 07:00 PM ET",
    "NCAAB 07: South Carolina State vs Queens-NC @ Dec 12 07:00 PM ET",
    "NCAAB 08: Texas vs #5 UConn @ Dec 12 08:00 PM ET",
    "NCAAB 09: Dakota Wesleyan vs South Dakota State @ Dec 12 08:00 PM ET",
    "NCAAB 11: East Tennessee State vs Austin Peay @ Dec 12 08:00 PM ET",
    "NCAAB 12: East Texas A&M vs McNeese @ Dec 12 08:30 PM ET",
    "NCAAB 13: California Baptist vs Eastern Washington @ Dec 12 09:00 PM ET",
]


def test_single_league_nba():
    """Test SingleLeagueMatcher with NBA streams."""
    print("=" * 60)
    print("TEST: SingleLeagueMatcher - NBA")
    print("=" * 60)

    provider = ESPNProvider()
    service = SportsDataService([provider])
    matcher = SingleLeagueMatcher(service, "nba")

    # NBA games for tonight are on Dec 12 in ESPN
    target_date = date(2025, 12, 12)

    results = matcher.match_batch(NBA_STREAMS, target_date)

    matched = sum(1 for r in results if r.matched)
    print(f"\nResults: {matched}/{len(results)} matched")
    print()

    for result in results:
        status = "✓" if result.matched else "✗"
        if result.matched:
            print(f"  {status} {result.stream_name}")
            print(f"      → {result.event.name}")
        else:
            print(f"  {status} {result.stream_name}")

    return matched, len(results)


def test_single_league_nfl():
    """Test SingleLeagueMatcher with NFL streams."""
    print("\n" + "=" * 60)
    print("TEST: SingleLeagueMatcher - NFL")
    print("=" * 60)

    provider = ESPNProvider()
    service = SportsDataService([provider])
    matcher = SingleLeagueMatcher(service, "nfl")

    # Thursday Night Football (8:15 PM ET) shows as Dec 11 in ESPN
    target_date = date(2025, 12, 11)

    results = matcher.match_batch(NFL_STREAMS, target_date)

    matched = sum(1 for r in results if r.matched)
    print(f"\nResults: {matched}/{len(results)} matched")
    print()

    for result in results:
        status = "✓" if result.matched else "✗"
        if result.matched:
            print(f"  {status} {result.stream_name}")
            print(f"      → {result.event.name}")
        else:
            print(f"  {status} {result.stream_name}")

    return matched, len(results)


def test_multi_league():
    """Test MultiLeagueMatcher with mixed ESPN+ streams."""
    print("\n" + "=" * 60)
    print("TEST: MultiLeagueMatcher - ESPN+ (multi-sport)")
    print("=" * 60)

    provider = ESPNProvider()
    service = SportsDataService([provider])

    # Search multiple leagues that appear in ESPN+ streams
    search_leagues = ["nba", "nfl", "nhl", "ger.1", "ger.2", "esp.1"]

    # Only include these leagues in EPG output
    include_leagues = ["nba", "nfl", "nhl"]

    matcher = MultiLeagueMatcher(
        service,
        search_leagues=search_leagues,
        include_leagues=include_leagues,
    )

    # Use Dec 12-13 range (some events are on each day)
    target_date = date(2025, 12, 12)

    # Test with a subset of ESPN+ streams
    test_streams = ESPN_PLUS_STREAMS[:10] + NHL_STREAMS + NBA_STREAMS[:3]

    result = matcher.match_all(test_streams, target_date)

    print(f"\nSearch leagues: {result.leagues_searched}")
    print(f"Include leagues: {result.include_leagues}")
    print(f"Events found: {result.events_found}")
    print("\nResults:")
    print(f"  Total streams: {result.total}")
    print(f"  Matched: {result.matched_count}")
    print(f"  Included: {result.included_count}")
    print(f"  Excluded (matched but not in whitelist): {result.excluded_count}")
    print(f"  Unmatched: {result.unmatched_count}")
    print(f"  Match rate: {result.match_rate:.1%}")
    print()

    print("Included streams:")
    for r in result.results:
        if r.included:
            print(f"  ✓ [{r.league}] {r.stream_name}")
            print(f"      → {r.event.name}")

    print("\nExcluded streams (matched to non-whitelisted league):")
    for r in result.results:
        if r.matched and not r.included:
            print(f"  ○ [{r.league}] {r.stream_name}")
            print(f"      → {r.event.name}")

    print("\nUnmatched streams:")
    for r in result.results:
        if not r.matched and not r.is_exception:
            print(f"  ✗ {r.stream_name}")

    return result


def test_expected_failures():
    """Test streams that are expected to fail (non-ESPN sports, missing teams)."""
    print("\n" + "=" * 60)
    print("TEST: Expected Failures (non-ESPN sports)")
    print("=" * 60)

    # These streams should NOT match - they're for sports/leagues not in ESPN
    # or have teams ESPN doesn't have
    expected_failures = [
        # Non-team sports (UFC, cricket)
        "ESPN+ 25 : UFC Fight Night Pre-Show: Royval vs. Kape @ Dec 12 05:35 PM ET",
        "ESPN+ 24 : New Zealand vs. West Indies (2nd Test - Day 4) @ Dec 12 05:00 PM ET",
        # Teams not in ESPN (lacrosse, lower divisions)
        "ESPN+ 43 : Saskatchewan Rush vs. Ottawa Black Bears @ Dec 12 07:30 PM ET",
        "ESPN+ 14 : VfL Osnabrück vs. SSV Ulm @ Dec 12 01:00 PM ET",
        # D2/D3 schools not in ESPN
        "ESPN+ 23 : Point Loma vs. Concordia - St. Paul (Semifinal #1) @ Dec 12 05:00 PM ET",
    ]

    provider = ESPNProvider()
    service = SportsDataService([provider])

    # Search common leagues
    matcher = MultiLeagueMatcher(
        service,
        search_leagues=["nba", "nfl", "nhl", "nba", "mens-college-basketball"],
    )

    result = matcher.match_all(expected_failures, date(2025, 12, 12))

    print("\nExpected: 0 matches (these are non-ESPN sports/teams)")
    print(f"Actual: {result.matched_count} matches")
    print()

    if result.matched_count == 0:
        print("✓ All expected failures correctly unmatched")
    else:
        print("⚠ Some streams unexpectedly matched:")
        for r in result.results:
            if r.matched:
                print(f"  {r.stream_name} → {r.event.name}")

    return result.matched_count == 0


def main():
    """Run all matching tests."""
    print("\n" + "=" * 70)
    print("  TEAMARR V2 - Events → Streams Matching Pipeline Test")
    print("=" * 70)

    # Test single-league matchers
    nba_matched, nba_total = test_single_league_nba()
    nfl_matched, nfl_total = test_single_league_nfl()

    # Test multi-league matcher
    multi_result = test_multi_league()

    # Test expected failures
    failures_correct = test_expected_failures()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  NBA SingleLeagueMatcher: {nba_matched}/{nba_total}")
    print(f"  NFL SingleLeagueMatcher: {nfl_matched}/{nfl_total}")
    print(
        f"  MultiLeagueMatcher: {multi_result.matched_count}/{multi_result.total} matched, "
        f"{multi_result.included_count} included"
    )
    print(f"  Expected failures handled: {'✓' if failures_correct else '✗'}")


if __name__ == "__main__":
    main()
