"""Variables API endpoint for template variable picker."""

from fastapi import APIRouter

from teamarr.templates.sample_data import AVAILABLE_SPORTS, get_all_sample_data
from teamarr.templates.variables import Category, SuffixRules, get_registry

router = APIRouter()


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
    }
    return names.get(category, category.name.title())


def _suffix_rules_display(rules: SuffixRules) -> list[str]:
    """Get list of supported suffixes for a variable."""
    if rules == SuffixRules.ALL:
        return ["base", ".next", ".last"]
    elif rules == SuffixRules.BASE_ONLY:
        return ["base"]
    elif rules == SuffixRules.BASE_NEXT_ONLY:
        return ["base", ".next"]
    elif rules == SuffixRules.LAST_ONLY:
        return [".last"]
    return ["base"]


@router.get("/variables")
def get_variables():
    """Get all template variables grouped by category.

    Returns variables organized for the template variable picker UI.
    Each variable includes name, description, category, and supported suffixes.
    """
    registry = get_registry()
    all_vars = registry.all_variables()

    # Group by category
    by_category: dict[str, list[dict]] = {}

    for var in sorted(all_vars, key=lambda v: (v.category.value, v.name)):
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
        "total": registry.count(),
        "categories": categories,
        "available_sports": AVAILABLE_SPORTS,
    }


@router.get("/variables/samples")
def get_sample_data(sport: str = "NBA"):
    """Get sample data for template variable preview.

    Returns sample values for all variables for a given sport.
    Used for live preview in the template form.
    """
    if sport not in AVAILABLE_SPORTS:
        sport = "NBA"  # Default fallback

    return {
        "sport": sport,
        "available_sports": AVAILABLE_SPORTS,
        "samples": get_all_sample_data(sport),
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
    # Conditions that apply to both template types
    common_conditions = [
        {
            "name": "is_ranked_matchup",
            "description": "Both teams are ranked (college sports)",
            "requires_value": False,
        },
        {
            "name": "is_top_ten_matchup",
            "description": "Both teams are ranked in top 10",
            "requires_value": False,
        },
        {
            "name": "is_conference_game",
            "description": "Game is a conference matchup",
            "requires_value": False,
        },
        {
            "name": "is_playoff",
            "description": "Game is a playoff/postseason game",
            "requires_value": False,
        },
        {
            "name": "is_preseason",
            "description": "Game is a preseason game",
            "requires_value": False,
        },
        {
            "name": "is_national_broadcast",
            "description": "Game is on national TV",
            "requires_value": False,
        },
        {
            "name": "has_odds",
            "description": "Betting odds are available for the game",
            "requires_value": False,
        },
        {
            "name": "always",
            "description": "Always matches (use as fallback)",
            "requires_value": False,
        },
    ]

    # Team-only conditions (require "our team" perspective)
    team_only_conditions = [
        {
            "name": "is_home",
            "description": "Team is playing at home",
            "requires_value": False,
        },
        {
            "name": "is_away",
            "description": "Team is playing away",
            "requires_value": False,
        },
        {
            "name": "win_streak",
            "description": "Team is on a win streak of N or more games",
            "requires_value": True,
            "value_type": "number",
        },
        {
            "name": "loss_streak",
            "description": "Team is on a loss streak of N or more games",
            "requires_value": True,
            "value_type": "number",
        },
        {
            "name": "is_ranked",
            "description": "Team is ranked (college sports)",
            "requires_value": False,
        },
        {
            "name": "is_ranked_opponent",
            "description": "Opponent is ranked (college sports)",
            "requires_value": False,
        },
        {
            "name": "opponent_name_contains",
            "description": "Opponent name contains specific text",
            "requires_value": True,
            "value_type": "string",
        },
    ]

    if template_type == "event":
        conditions = common_conditions
    else:
        # Team templates get all conditions
        conditions = team_only_conditions + common_conditions

    return {"conditions": conditions, "template_type": template_type}
