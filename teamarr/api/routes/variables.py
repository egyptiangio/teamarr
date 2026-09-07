"""Variables API endpoint for template variable picker."""

import logging

from fastapi import APIRouter

from teamarr.database import get_db
from teamarr.database.subscription import get_subscribed_league_codes
from teamarr.services.cache_service import create_cache_service
from teamarr.templates.preview import build_live_context, lookup_league_fields
from teamarr.templates.resolver import TemplateResolver
from teamarr.templates.sample_data import (
    AVAILABLE_SPORTS,
    get_all_sample_data,
    get_all_sample_data_for_league,
    resolve_profile_for_league,
    resolve_shape,
)
from teamarr.templates.validation import supported_suffixes
from teamarr.templates.variables import Category, SuffixRules, get_registry

router = APIRouter()
logger = logging.getLogger(__name__)

# Variable categories that are cross-sport noise for a given sample shape, so a
# basketball preview doesn't flag empty combat/racing variables as live "gaps"
# (option B — gaps surface only within categories relevant to the event).
_SHAPE_IRRELEVANT_CATEGORIES: dict[str, frozenset[str]] = {
    "team": frozenset({"COMBAT", "MOTORSPORTS"}),
    "combat": frozenset({"MOTORSPORTS", "SOCCER", "RANKINGS", "RECORDS",
                         "CONFERENCE", "STANDINGS", "STREAKS", "STATISTICS",
                         "SCORES", "HOME_AWAY"}),
    "racing": frozenset({"COMBAT", "SOCCER", "RANKINGS", "RECORDS",
                         "CONFERENCE", "STANDINGS", "STREAKS", "STATISTICS",
                         "SCORES", "HOME_AWAY"}),
}


def _relevant_keys(keys, shape: str) -> list[str]:
    """Base sample keys whose variable category applies to the event's shape.

    Coverage counts BASE variables only — the `.next`/`.last` variants depend on
    the sample event having adjacent games, which it usually doesn't, so counting
    them would dwarf the tally with non-gaps. Combat/racing variables are N/A on a
    team game, so they're excluded too.
    """
    registry = get_registry()
    irrelevant = _SHAPE_IRRELEVANT_CATEGORIES.get(shape, frozenset())
    out = []
    for k in keys:
        if k.endswith(".next") or k.endswith(".last"):
            continue  # base variables only
        var_def = registry.get(k)
        if var_def is not None and var_def.category.name not in irrelevant:
            out.append(k)
    return out

def _fetch_live_samples(league: str) -> dict[str, str] | None:
    """Resolve every variable against a real upcoming/recent event for a league.

    Returns a name -> value map for non-empty live values, or None if no usable
    event could be found or the provider failed. Context building (and its
    per-league cache) lives in templates/preview.py, shared with the
    server-side preview endpoint (#357).
    """
    ctx = build_live_context(league)
    if ctx is None:
        return None
    variables = TemplateResolver().build_variable_map(ctx)
    # Keep only non-empty live values; static samples fill the rest.
    return {k: v for k, v in variables.items() if v}


def _category_display_name(category: Category) -> str:
    """Get human-readable category name."""
    names = {
        Category.IDENTITY: "Identity",
        Category.DATETIME: "Date & Time",
        Category.VENUE: "Venue",
        Category.HOME_AWAY: "Home/Away",
        Category.RECORDS: "Records",
        Category.STREAKS: "Streaks",
        Category.SCORES: "Scores",
        Category.OUTCOME: "Outcome",
        Category.STANDINGS: "Standings",
        Category.STATISTICS: "Statistics",
        Category.PLAYOFFS: "Playoffs",
        Category.ODDS: "Odds",
        Category.BROADCAST: "Broadcast",
        Category.RANKINGS: "Rankings",
        Category.CONFERENCE: "Conference",
        Category.SOCCER: "Soccer",
        Category.COMBAT: "Combat Sports",
    }
    return names.get(category, category.name.title())


def _suffix_rules_display(rules: SuffixRules) -> list[str]:
    """Get list of supported suffixes for a variable (shared with validation)."""

    return supported_suffixes(rules)


@router.get("/variables")
def get_variables(template_type: str | None = None):
    """Get template variables grouped by category, optionally scoped to a template type.

    Args:
        template_type: Optional filter — 'team' or 'event'. When set, only
            variables valid for that template type are returned (per each
            variable's registered TemplateScope). Unknown values and None
            return all variables (matches the conditions endpoint's behavior).

    Team templates render from an "our team" perspective (variables like
    {team}, {opponent}, {is_home}). Event templates are positional
    (matchup-level) and additionally expose the feed_team family on
    feed-separated channels.
    """
    registry = get_registry()
    scoped_vars = registry.filter_by_template_type(template_type)

    # Group by category
    by_category: dict[str, list[dict]] = {}

    for var in sorted(scoped_vars, key=lambda v: (v.category.value, v.name)):
        cat_name = _category_display_name(var.category)

        if cat_name not in by_category:
            by_category[cat_name] = []

        by_category[cat_name].append(
            {
                "name": var.name,
                "description": var.description or "",
                "suffixes": _suffix_rules_display(var.suffix_rules),
            }
        )

    # Convert to list format for frontend
    categories = []
    for cat_name, variables in by_category.items():
        categories.append(
            {
                "name": cat_name,
                "variables": variables,
            }
        )

    return {
        "total": len(scoped_vars),
        "template_type": template_type,
        "categories": categories,
        "available_sports": AVAILABLE_SPORTS,
    }


def _league_info_dict(lg) -> dict:
    """Serialize a LeagueInfo for the sample-league picker (CachedLeague shape)."""
    return {
        "slug": lg.slug,
        "provider": lg.provider,
        "name": lg.name,
        "sport": lg.sport,
        "team_count": lg.team_count,
        "logo_url": lg.logo_url,
        "logo_url_dark": lg.logo_url_dark,
        "import_enabled": lg.import_enabled,
        "league_alias": lg.league_alias,
    }


@router.get("/variables/sample-leagues")
def get_sample_leagues():
    """Leagues to offer in the template preview selector.

    Returns all enabled configured leagues plus the subset the user has
    subscribed to (event-based sports subscription + the leagues of followed
    teams). The picker shows the subscribed subset by default but can search the
    full list.
    """

    with get_db() as conn:
        codes = get_subscribed_league_codes(conn)

    service = create_cache_service(get_db)
    enabled = service.get_leagues(configured_only=True)
    subscribed_slugs = [lg.slug for lg in enabled if lg.slug.lower() in codes]

    return {
        "count": len(enabled),
        "leagues": [_league_info_dict(lg) for lg in enabled],
        "subscribed_slugs": subscribed_slugs,
    }


@router.get("/variables/samples")
def get_sample_data(
    sport: str = "NBA", league: str | None = None, live: bool = False
):
    """Get sample data for template variable preview.

    Returns sample values for all variables, used for the live preview in the
    template form. Prefer ``league`` (any supported league code) for
    league-accurate placeholders; ``sport`` is kept for back-compat and selects
    a profile directly.

    When ``live`` is set, real provider data for an upcoming/recent event in the
    league replaces the static samples. Variables the real event can't fill are
    surfaced as gaps (empty value, and listed in ``gaps``) rather than masked
    with the fictitious sample — so the preview honestly reflects what live data
    actually provides. Any failure (no event found, provider down) falls back
    silently to the static sample.
    """
    if league:
        # Resolve the profile from the league's own record (sport + provider)
        # so the mapping is data-driven and custom leagues work too.
        league_sport, league_provider = lookup_league_fields(league)
        samples = get_all_sample_data_for_league(league, league_sport, league_provider)
        profile = resolve_profile_for_league(league, league_sport, league_provider)
    else:
        if sport not in AVAILABLE_SPORTS:
            sport = "NBA"  # Default fallback
        samples = get_all_sample_data(sport)
        profile = sport

    is_live = False
    gaps: list[str] = []
    live_populated = live_total = None
    if live and league:
        live_samples = _fetch_live_samples(league)
        if live_samples:
            is_live = True
            # Gaps surface only within categories relevant to this event's shape
            # (option B): combat/racing variables are N/A on a team game, not
            # gaps. A gap is a *relevant* variable the live event didn't fill.
            shape = resolve_shape(league_sport)
            relevant = _relevant_keys(samples.keys(), shape)
            samples = {k: live_samples.get(k, "") for k in samples}
            gaps = sorted(k for k in relevant if not samples[k])
            live_total = len(relevant)
            live_populated = live_total - len(gaps)

    return {
        "sport": profile,
        "league": league,
        "live": is_live,
        "available_sports": AVAILABLE_SPORTS,
        "samples": samples,
        "gaps": gaps,
        "live_populated": live_populated,
        "live_total": live_total,
    }


@router.get("/variables/conditions")
def get_conditions(template_type: str = "team"):
    """Get available conditions for conditional descriptions.

    Args:
        template_type: "team" or "event" - filters to relevant conditions

    Team templates have "our team" perspective, so conditions like
    is_home/is_away and win_streak make sense.

    Event templates are positional (home/away teams), so only
    game-level conditions like is_playoff, has_odds apply.
    """
    # Provider support:
    # - "all": Works with all providers (ESPN, TSDB)
    # - "espn": ESPN leagues only (NFL, NBA, NHL, MLB, MLS, college, soccer)
    # For TSDB-only leagues (OHL, WHL, NLL, etc.), ESPN-only conditions return false

    # Conditions that apply to both template types
    common_conditions = [
        # ESPN-only: requires ranking data
        {
            "name": "is_ranked_matchup",
            "description": "Both teams are ranked (college sports)",
            "requires_value": False,
            "providers": "espn",
        },
        {
            "name": "is_top_ten_matchup",
            "description": "Both teams are ranked in top 10",
            "requires_value": False,
            "providers": "espn",
        },
        # ESPN-only: requires conference data
        {
            "name": "is_conference_game",
            "description": "Game is a conference matchup",
            "requires_value": False,
            "providers": "espn",
        },
        # ESPN-only: requires season type flags
        {
            "name": "is_playoff",
            "description": "Game is a playoff/postseason game",
            "requires_value": False,
            "providers": "espn",
        },
        {
            "name": "is_preseason",
            "description": "Game is a preseason game",
            "requires_value": False,
            "providers": "espn",
        },
        # ESPN-only: requires broadcast data
        {
            "name": "is_national_broadcast",
            "description": "Game is on national TV",
            "requires_value": False,
            "providers": "espn",
        },
        # ESPN-only: only ESPN provides odds data
        {
            "name": "has_odds",
            "description": "Betting odds are available for the game",
            "requires_value": False,
            "providers": "espn",
        },
        # Game state (#420): finality of the reference game — works with any
        # provider that reports event status.
        {
            "name": "is_final",
            "description": "Reference game is final",
            "requires_value": False,
            "providers": "all",
        },
        {
            "name": "is_not_final",
            "description": "Reference game exists but is not final yet",
            "requires_value": False,
            "providers": "all",
        },
    ]

    # Provider editorial copy — available to BOTH template types (epic tvnk,
    # #329): event templates use has_preview/has_recap for ESPN-copy-first
    # description chains.
    summary_conditions = [
        {
            "name": "has_generated_preview",
            "description": "Source-grounded generated pregame description is available",
            "requires_value": False,
            "providers": "all",
        },
        {
            "name": "has_preview",
            "description": "Provider preview blurb is available (same-day pregame)",
            "requires_value": False,
            "providers": "espn",
        },
        {
            "name": "has_recap",
            "description": "Provider recap headline is available (postgame)",
            "requires_value": False,
            "providers": "espn",
        },
        {
            "name": "has_structured_preview",
            "description": "Recent-form data is available (populates days ahead)",
            "requires_value": False,
            "providers": "espn",
        },
        {
            "name": "has_event_note",
            "description": "Marquee/playoff note is available ('NBA Finals - Game 5')",
            "requires_value": False,
            "providers": "espn",
        },
        {
            "name": "has_match_note",
            "description": "Soccer competition note is available ('FIFA World Cup, Group C')",
            "requires_value": False,
            "providers": "espn",
        },
        {
            "name": "is_neutral_site",
            "description": "Game is at a neutral site (bowls, CFP/NCAA tournament rounds)",
            "requires_value": False,
            "providers": "espn",
        },
    ]

    # Event identity conditions (#370) — available to BOTH template types so
    # one template can branch a register by league/sport instead of needing
    # per-league template variants.
    identity_conditions = [
        {
            "name": "league_is",
            "description": "Event's league is one of the given codes (e.g. cfb,nfl)",
            "requires_value": True,
            "value_type": "string",
            "providers": "all",
        },
        {
            "name": "sport_is",
            "description": "Event's sport is one of the given codes (e.g. football,basketball)",
            "requires_value": True,
            "value_type": "string",
            "providers": "all",
        },
    ]

    # Team-only conditions (require "our team" perspective)
    team_only_conditions = [
        # Universal: works with all providers
        {
            "name": "is_home",
            "description": "Team is playing at home",
            "requires_value": False,
            "providers": "all",
        },
        {
            "name": "is_away",
            "description": "Team is playing away",
            "requires_value": False,
            "providers": "all",
        },
        {
            "name": "opponent_name_contains",
            "description": "Opponent name contains specific text",
            "requires_value": True,
            "value_type": "string",
            "providers": "all",
        },
        # ESPN-only: requires team stats
        {
            "name": "win_streak",
            "description": "Team is on a win streak of N or more games",
            "requires_value": True,
            "value_type": "number",
            "providers": "espn",
        },
        {
            "name": "loss_streak",
            "description": "Team is on a loss streak of N or more games",
            "requires_value": True,
            "value_type": "number",
            "providers": "espn",
        },
        # ESPN-only: requires ranking data
        {
            "name": "is_ranked",
            "description": "Team is ranked (college sports)",
            "requires_value": False,
            "providers": "espn",
        },
        {
            "name": "is_ranked_opponent",
            "description": "Opponent is ranked (college sports)",
            "requires_value": False,
            "providers": "espn",
        },
    ]

    # Combat sports conditions (MMA/UFC events)
    combat_conditions = [
        {
            "name": "is_knockout",
            "description": "Fight ended by KO or TKO",
            "requires_value": False,
            "providers": "espn",
        },
        {
            "name": "is_submission",
            "description": "Fight ended by submission",
            "requires_value": False,
            "providers": "espn",
        },
        {
            "name": "is_decision",
            "description": "Fight went to decision",
            "requires_value": False,
            "providers": "espn",
        },
        {
            "name": "is_finish",
            "description": "Fight ended by finish (KO/TKO/Submission)",
            "requires_value": False,
            "providers": "espn",
        },
        {
            "name": "went_distance",
            "description": "Fight went all scheduled rounds",
            "requires_value": False,
            "providers": "espn",
        },
    ]

    # Motorsports conditions (F1, NASCAR, IndyCar, MotoGP, ... events)
    motorsports_conditions = [
        {
            "name": "is_race_session",
            "description": "This channel's session is the race itself",
            "requires_value": False,
            "providers": "all",
        },
        {
            "name": "is_qualifying_session",
            "description": "This channel's session is qualifying or sprint qualifying",
            "requires_value": False,
            "providers": "all",
        },
        {
            "name": "has_results",
            "description": "This channel's session has finished with results",
            "requires_value": False,
            "providers": "all",
        },
    ]

    if template_type == "event":
        # Event templates get everything except the "our team" perspective
        # conditions. The common set belongs here (#521): event contexts are
        # built home-team-first (event_epg.py), so team_stats/opponent_stats
        # are populated and is_ranked_matchup/is_conference_game evaluate as
        # written — and the shipped event starters already use is_final.
        conditions = (
            identity_conditions
            + common_conditions
            + summary_conditions
            + combat_conditions
            + motorsports_conditions
        )
    else:
        # Team templates get all conditions
        conditions = (
            team_only_conditions
            + identity_conditions
            + common_conditions
            + summary_conditions
            + combat_conditions
            + motorsports_conditions
        )

    return {"conditions": conditions, "template_type": template_type}
