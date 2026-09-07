"""Public typed facts used by the optional generated-preview formatter."""

from teamarr.templates.context import GameContext, TemplateContext
from teamarr.templates.generated_preview import build_generated_preview
from teamarr.templates.variables.registry import Category, SuffixRules, register_variable


def _register_event_field(name: str, description: str, sample: str) -> None:
    """Register a direct Event string/int field as a public template variable."""

    def extract(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
        if not game_ctx or not game_ctx.event:
            return ""
        value = getattr(game_ctx.event, name, "")
        return "" if value in (None, "") else str(value)

    extract.__name__ = f"extract_{name}"
    register_variable(
        name=name,
        category=Category.SUMMARY,
        suffix_rules=SuffixRules.ALL,
        description=description,
        sample=sample,
    )(extract)


_FIELD_DEFINITIONS = {
    "week": ("Provider-reported week number; empty when unavailable", "3"),
    "home_probable_starter": (
        "Home probable starter and ESPN-reported record/ERA",
        "M. Boyd (8-2, 4.02 ERA)",
    ),
    "away_probable_starter": (
        "Away probable starter and ESPN-reported record/ERA",
        "L. Gilbert (10-5, 1.01 ERA)",
    ),
    "home_home_runs_leader": ("Home team home-run leader", "S. Ohtani — 30 home runs"),
    "away_home_runs_leader": ("Away team home-run leader", "P. Crow-Armstrong — 31 home runs"),
    "home_batting_average_leader": (
        "Home team batting-average leader",
        "R. Arozarena — .272 batting average",
    ),
    "away_batting_average_leader": (
        "Away team batting-average leader",
        "S. Kwan — .301 batting average",
    ),
    "home_rbi_leader": ("Home team RBI leader", "J. Ramírez — 82 RBI"),
    "away_rbi_leader": ("Away team RBI leader", "K. Tucker — 75 RBI"),
    "home_passing_leader": ("Home team passing leader", "B. Nix — 18/25, 246 YDS"),
    "away_passing_leader": ("Away team passing leader", "J. Love — 17/24, 221 YDS"),
    "home_rushing_leader": ("Home team rushing leader", "J. Dobbins — 12 CAR, 68 YDS"),
    "away_rushing_leader": ("Away team rushing leader", "J. Jacobs — 14 CAR, 73 YDS"),
    "home_receiving_leader": ("Home team receiving leader", "C. Sutton — 6 REC, 84 YDS"),
    "away_receiving_leader": ("Away team receiving leader", "J. Reed — 5 REC, 79 YDS"),
    "home_total_yards_per_game": ("Home team total yards per game", "360"),
    "away_total_yards_per_game": ("Away team total yards per game", "204"),
    "home_rushing_yards_per_game": ("Home team rushing yards per game", "162"),
    "away_rushing_yards_per_game": ("Away team rushing yards per game", "63"),
    "home_points_leader": ("Home team points-per-game leader", "L. Lacan — 11.6 points per game"),
    "away_points_leader": ("Away team points-per-game leader", "K. Cardoso — 14.7 points per game"),
    "home_rebounds_leader": (
        "Home team rebounds-per-game leader",
        "O. Nelson-Ododa — 5.9 rebounds per game",
    ),
    "away_rebounds_leader": (
        "Away team rebounds-per-game leader",
        "K. Cardoso — 8.8 rebounds per game",
    ),
    "home_assists_leader": ("Home team assists-per-game leader", "L. Lacan — 4.6 assists per game"),
    "away_assists_leader": ("Away team assists-per-game leader", "N. Cloud — 5.0 assists per game"),
    "home_points_allowed_per_game": ("Home team points allowed per game", "87.0"),
    "away_points_allowed_per_game": ("Away team points allowed per game", "89.9"),
}

for _name, (_description, _sample) in _FIELD_DEFINITIONS.items():
    _register_event_field(_name, _description, _sample)


@register_variable(
    name="generated_preview",
    category=Category.SUMMARY,
    suffix_rules=SuffixRules.ALL,
    description=(
        "Optional deterministic preview with sport-specific baseball, football and "
        "basketball prose and a generic matchup sentence for other sports; assembled "
        "from public fields without betting information"
    ),
)
def extract_generated_preview(ctx: TemplateContext, game_ctx: GameContext | None) -> str:
    return build_generated_preview(game_ctx.event if game_ctx else None)
