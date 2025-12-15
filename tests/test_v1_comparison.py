"""Compare v2 matcher performance against v1 results.

Uses exact stream names from v1's matched and failed lists.
"""

from datetime import date

from teamarr.consumers import MultiLeagueMatcher
from teamarr.providers.espn import ESPNProvider
from teamarr.services import SportsDataService

# v1 MATCHED streams (49 total)
V1_MATCHED = {
    "ESPN+ 01 : Perth Glory vs. Wellington Phoenix @ Dec 12 05:55 AM ET": "aus.w.1",
    "ESPN+ 05 : Florida Gulf Coast vs. Florida Atlantic @ Dec 12 11:00 AM ET": "womens-college-basketball",
    "ESPN+ 10 : UT Arlington vs. Rice @ Dec 12 12:15 PM ET": "womens-college-basketball",
    "ESPN+ 11 : SpVgg Greuther Fürth vs. Hertha BSC @ Dec 12 12:25 PM ET": "ger.2",
    "ESPN+ 12 : SG Dynamo Dresden vs. Eintracht Braunschweig @ Dec 12 12:25 PM ET": "ger.2",
    "ESPN+ 15 : 1. FC Union Berlin vs. RB Leipzig @ Dec 12 02:20 PM ET": "ger.1",
    "ESPN+ 16 : En Español-1. FC Union Berlin vs. RB Leipzig @ Dec 12 02:20 PM ET": "ger.1",
    "ESPN+ 18 : Real Sociedad vs. Girona FC @ Dec 12 02:50 PM ET": "esp.1",
    "ESPN+ 19 : En Español-Real Sociedad vs. Girona FC @ Dec 12 02:55 PM ET": "esp.1",
    "ESPN+ 20 : Saint Michael's vs. Dartmouth @ Dec 12 03:00 PM ET": "womens-college-hockey",
    "ESPN+ 26 : Army vs. UMBC @ Dec 12 06:00 PM ET": "mens-college-basketball",
    "ESPN+ 28 : East Texas A&M vs. McNeese @ Dec 12 06:00 PM ET": "womens-college-basketball",
    "ESPN+ 30 : Wright State vs. Canisius @ Dec 12 06:30 PM ET": "womens-college-basketball",
    "ESPN+ 31 : Yale vs. Merrimack @ Dec 12 07:00 PM ET": "mens-college-hockey",
    "ESPN+ 32 : Maine vs. Fairfield @ Dec 12 07:00 PM ET": "womens-college-basketball",
    "ESPN+ 35 : Army vs. #8 Dartmouth @ Dec 12 07:00 PM ET": "mens-college-hockey",
    "ESPN+ 36 : SE Louisiana vs. Houston Christian @ Dec 12 07:00 PM ET": "womens-college-basketball",
    "ESPN+ 37 : Alaska-Fairbanks vs. Union @ Dec 12 07:00 PM ET": "mens-college-hockey",
    "ESPN+ 38 : South Carolina State vs. Queens University @ Dec 12 07:00 PM ET": "mens-college-basketball",
    "ESPN+ 41 : SIUE vs. Valparaiso @ Dec 12 07:00 PM ET": "womens-college-basketball",
    "ESPN+ 42 : William Peace vs. UNC Greensboro @ Dec 12 07:00 PM ET": "mens-college-basketball",
    "ESPN+ 44 : Chicago Blackhawks vs. St. Louis Blues @ Dec 12 08:00 PM ET": "nhl",
    "ESPN+ 46 : East Tennessee State vs. Austin Peay @ Dec 12 08:00 PM ET": "mens-college-basketball",
    "ESPN+ 49 : East Texas A&M vs. McNeese @ Dec 12 08:30 PM ET": "mens-college-basketball",
    "ESPN+ 48 : Seattle Kraken vs. Utah Mammoth @ Dec 12 08:30 PM ET": "nhl",
    "ESPN+ 50 : Texas Tech vs. Washington State @ Dec 12 09:00 PM ET": "womens-college-basketball",
    "ESPN+ 51 : California Baptist vs. Eastern Washington @ Dec 12 09:00 PM ET": "mens-college-basketball",
    "ESPN+ 52 : Newcastle Jets vs. Brisbane Roar FC @ Dec 12 09:55 PM ET": "aus.w.1",
    # NCAA Men's Basketball
    "NCAAB 01: Army vs UMBC @ Dec 12 06:00 PM ET": "mens-college-basketball",
    "NCAAB 03: Missouri State vs Xavier @ Dec 12 07:00 PM ET": "mens-college-basketball",
    "NCAAB 04: William Peace vs UNC Greensboro @ Dec 12 07:00 PM ET": "mens-college-basketball",
    "NCAAB 05: Maryland-Eastern Shore vs North Carolina A&T @ Dec 12 07:00 PM ET": "mens-college-basketball",
    "NCAAB 07: South Carolina State vs Queens-NC @ Dec 12 07:00 PM ET": "mens-college-basketball",
    "NCAAB 08: Texas vs #5 UConn @ Dec 12 08:00 PM ET": "mens-college-basketball",
    "NCAAB 09: Dakota Wesleyan vs South Dakota State @ Dec 12 08:00 PM ET": "mens-college-basketball",
    "NCAAB 11: East Tennessee State vs Austin Peay @ Dec 12 08:00 PM ET": "mens-college-basketball",
    "NCAAB 12: East Texas A&M vs McNeese @ Dec 12 08:30 PM ET": "mens-college-basketball",
    "NCAAB 13: California Baptist vs Eastern Washington @ Dec 12 09:00 PM ET": "mens-college-basketball",
    # NBA
    "NBA 01: Chicago Bulls  vs  Charlotte Hornets @ 07:00 PM ET": "nba",
    "NBA 02: Atlanta Hawks  vs  Detroit Pistons @ 07:00 PM ET": "nba",
    "NBA 03: Indiana Pacers  vs  Philadelphia 76ers @ 07:00 PM ET": "nba",
    "NBA 04: Cleveland Cavaliers  vs  Washington Wizards @ 07:00 PM ET": "nba",
    "NBA 05: Utah Jazz  vs  Memphis Grizzlies @ 08:00 PM ET": "nba",
    "NBA 06: Brooklyn Nets  vs  Dallas Mavericks @ 08:30 PM ET": "nba",
    "NBA 07: Minnesota Timberwolves  vs  Golden State Warriors @ 10:00 PM ET": "nba",
    # NFL
    "NFL Game Pass 01: Atlanta Falcons  vs  Tampa Bay Buccaneers @ 08:15 PM ET": "nfl",
    "NFL Game Pass 02: Falcons @ Buccaneers (Prime Vision with Next Gen Stats) @ 08:15 PM ET": "nfl",
    # NHL
    "NHL 01: Chicago Blackhawks  vs  St. Louis Blues @ 08:00 PM ET": "nhl",
    "NHL 02: Seattle Kraken  vs  Utah Mammoth @ 09:00 PM ET": "nhl",
}

# v1 FAILED streams (15 total)
V1_FAILED = [
    "ESPN+ 03 : Coastal Georgia vs. Georgia Southern @ Dec 12 11:00 AM ET",  # no_event_found
    "ESPN+ 14 : VfL Osnabrück vs. SSV Ulm @ Dec 12 01:00 PM ET",  # team1_not_found
    "ESPN+ 23 : Point Loma vs. Concordia - St. Paul (Semifinal #1) @ Dec 12 05:00 PM ET",  # team1_not_found
    "ESPN+ 24 : New Zealand vs. West Indies (2nd Test - Day 4) @ Dec 12 05:00 PM ET",  # cricket
    "ESPN+ 25 : UFC Fight Night Pre-Show: Royval vs. Kape @ Dec 12 05:35 PM ET",  # ufc
    "ESPN+ 27 : USNTDP U-18 vs. St. Lawrence @ Dec 12 06:00 PM ET",  # team1_not_found
    "ESPN+ 34 : En Español-Erik Miloc vs. Nahuel Gonzalo Garcia @ Dec 12 07:00 PM ET",  # tennis
    "ESPN+ 39 : Brevard vs. Gardner-Webb @ Dec 12 07:00 PM ET",  # team2_not_found
    "ESPN+ 40 : Devils vs. Cyclones (Championship) @ Dec 12 07:00 PM ET",  # no_event_found
    "ESPN+ 43 : Saskatchewan Rush vs. Ottawa Black Bears @ Dec 12 07:30 PM ET",  # lacrosse
    "ESPN+ 45 : Dallas Christian vs. Texas A&M-Corpus Christi @ Dec 12 08:00 PM ET",  # team1_not_found
    "ESPN+ 47 : MSU - Denver vs. Tampa (Semifinal #2) @ Dec 12 08:00 PM ET",  # no_common_league
    "NCAAB 02: Vermont State-Johnson vs Stonehill @ Dec 12 07:00 PM ET",  # no_event_found
    "NCAAB 06: Brevard vs Gardner-Webb @ Dec 12 07:00 PM ET",  # team1_not_found
    "NCAAB 10: Dallas Christian vs Texas A&M—Corpus Christi @ Dec 12 08:00 PM ET",  # team1_not_found
]


def main():
    """Compare v2 matcher against v1 results."""
    print("=" * 70)
    print("  v1 vs v2 MATCHER COMPARISON")
    print("=" * 70)

    # Get unique leagues from v1 matched
    v1_leagues = set(V1_MATCHED.values())
    print(f"\nv1 matched leagues: {sorted(v1_leagues)}")

    # Setup v2 matcher with same leagues
    provider = ESPNProvider()
    service = SportsDataService([provider])

    matcher = MultiLeagueMatcher(
        service,
        search_leagues=list(v1_leagues),
        include_leagues=None,  # Include all matched leagues
    )

    # Combine all streams
    all_streams = list(V1_MATCHED.keys()) + V1_FAILED
    target_date = date(2025, 12, 12)

    print(f"\nTotal streams to test: {len(all_streams)}")
    print(f"  v1 matched: {len(V1_MATCHED)}")
    print(f"  v1 failed: {len(V1_FAILED)}")

    # Run v2 matcher
    print("\nRunning v2 matcher...")
    result = matcher.match_all(all_streams, target_date)

    print(f"\nv2 found {result.events_found} events across {len(v1_leagues)} leagues")

    # Analyze results
    v2_matched_v1_matched = 0  # v2 matched, v1 also matched (good)
    v2_matched_v1_failed = 0   # v2 matched, v1 failed (improvement!)
    v2_failed_v1_matched = 0   # v2 failed, v1 matched (regression!)
    v2_failed_v1_failed = 0    # v2 failed, v1 also failed (expected)

    improvements = []
    regressions = []

    for r in result.results:
        v1_expected_match = r.stream_name in V1_MATCHED

        if r.matched and v1_expected_match:
            v2_matched_v1_matched += 1
        elif r.matched and not v1_expected_match:
            v2_matched_v1_failed += 1
            improvements.append(r)
        elif not r.matched and v1_expected_match:
            v2_failed_v1_matched += 1
            regressions.append(r)
        else:
            v2_failed_v1_failed += 1

    # Print comparison
    print("\n" + "=" * 60)
    print("COMPARISON RESULTS")
    print("=" * 60)

    print(f"\n{'Category':<40} {'Count':>6}")
    print("-" * 50)
    print(f"{'v2 matched, v1 matched (agreement)':<40} {v2_matched_v1_matched:>6}")
    print(f"{'v2 matched, v1 failed (IMPROVEMENT!)':<40} {v2_matched_v1_failed:>6}")
    print(f"{'v2 failed, v1 matched (REGRESSION!)':<40} {v2_failed_v1_matched:>6}")
    print(f"{'v2 failed, v1 failed (agreement)':<40} {v2_failed_v1_failed:>6}")

    # Match rates
    v1_match_rate = len(V1_MATCHED) / len(all_streams) * 100
    v2_match_rate = result.matched_count / len(all_streams) * 100

    print(f"\n{'Metric':<40} {'v1':>10} {'v2':>10}")
    print("-" * 60)
    print(f"{'Total matched':<40} {len(V1_MATCHED):>10} {result.matched_count:>10}")
    print(f"{'Total failed':<40} {len(V1_FAILED):>10} {result.unmatched_count:>10}")
    print(f"{'Match rate':<40} {v1_match_rate:>9.1f}% {v2_match_rate:>9.1f}%")

    # Show regressions (v2 failed where v1 matched)
    if regressions:
        print("\n" + "=" * 60)
        print(f"REGRESSIONS ({len(regressions)} streams v2 missed)")
        print("=" * 60)
        for r in regressions:
            expected_league = V1_MATCHED.get(r.stream_name, "?")
            print(f"\n  Stream: {r.stream_name}")
            print(f"  v1 league: {expected_league}")

    # Show improvements (v2 matched where v1 failed)
    if improvements:
        print("\n" + "=" * 60)
        print(f"IMPROVEMENTS ({len(improvements)} streams v2 found)")
        print("=" * 60)
        for r in improvements[:5]:  # Show first 5
            print(f"\n  Stream: {r.stream_name}")
            print(f"  v2 matched: {r.event.name} [{r.league}]")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    if v2_failed_v1_matched == 0:
        print("✓ No regressions - v2 matches everything v1 matched")
    else:
        print(f"⚠ {v2_failed_v1_matched} regressions - v2 missed some v1 matches")

    if v2_matched_v1_failed > 0:
        print(f"✓ {v2_matched_v1_failed} improvements - v2 found matches v1 missed")

    if v2_match_rate >= v1_match_rate:
        print(f"✓ v2 match rate ({v2_match_rate:.1f}%) >= v1 ({v1_match_rate:.1f}%)")
    else:
        print(f"⚠ v2 match rate ({v2_match_rate:.1f}%) < v1 ({v1_match_rate:.1f}%)")


if __name__ == "__main__":
    main()
