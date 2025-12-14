"""Conditional description selection system.

Evaluates conditions to select the appropriate description template.
Priority-based: lower number = higher priority (100 = fallback).

Example usage:
    descriptions = [
        {"condition": "win_streak", "value": 3, "priority": 10, "template": "..."},
        {"condition": "is_ranked_matchup", "priority": 20, "template": "..."},
        {"condition": "always", "priority": 100, "template": "..."},  # fallback
    ]
"""

from dataclasses import dataclass
from enum import Enum, auto

from teamarr.templates.context import GameContext, TemplateContext


class ConditionType(Enum):
    """Supported condition types for description selection."""

    # Streak conditions (check team_stats.streak_count)
    WIN_STREAK = auto()  # streak_count >= value
    LOSS_STREAK = auto()  # streak_count <= -value
    HOME_WIN_STREAK = auto()
    HOME_LOSS_STREAK = auto()
    AWAY_WIN_STREAK = auto()
    AWAY_LOSS_STREAK = auto()

    # Rankings (check team_stats.rank)
    IS_RANKED = auto()  # team is ranked
    IS_RANKED_OPPONENT = auto()  # opponent is ranked
    IS_RANKED_MATCHUP = auto()  # both ranked
    IS_TOP_TEN_MATCHUP = auto()  # both in top 10

    # Game type
    IS_HOME = auto()
    IS_AWAY = auto()
    IS_PLAYOFF = auto()
    IS_PRESEASON = auto()
    IS_CONFERENCE_GAME = auto()  # college sports

    # H2H
    IS_REMATCH = auto()  # teams have played this season

    # Broadcast
    IS_NATIONAL_BROADCAST = auto()

    # Odds (requires external data)
    HAS_ODDS = auto()

    # Text matching
    OPPONENT_NAME_CONTAINS = auto()  # opponent name contains value

    # Always true (fallback)
    ALWAYS = auto()


@dataclass
class Condition:
    """A condition for description selection."""

    type: ConditionType
    value: str | int | None = None  # For conditions that need a threshold/pattern


@dataclass
class ConditionalDescription:
    """A description template with a condition."""

    condition: Condition
    priority: int  # Lower = higher priority, 100 = fallback
    template: str


class ConditionEvaluator:
    """Evaluates conditions against template context."""

    def evaluate(self, condition: Condition, ctx: TemplateContext) -> bool:
        """Check if a condition is satisfied."""
        game_ctx = ctx.game_context
        ctype = condition.type

        # Always true
        if ctype == ConditionType.ALWAYS:
            return True

        # Streak conditions
        if ctype == ConditionType.WIN_STREAK:
            return self._check_streak(ctx, condition.value, positive=True)
        if ctype == ConditionType.LOSS_STREAK:
            return self._check_streak(ctx, condition.value, positive=False)
        if ctype == ConditionType.HOME_WIN_STREAK:
            return self._check_home_streak(game_ctx, condition.value, positive=True)
        if ctype == ConditionType.HOME_LOSS_STREAK:
            return self._check_home_streak(game_ctx, condition.value, positive=False)
        if ctype == ConditionType.AWAY_WIN_STREAK:
            return self._check_away_streak(game_ctx, condition.value, positive=True)
        if ctype == ConditionType.AWAY_LOSS_STREAK:
            return self._check_away_streak(game_ctx, condition.value, positive=False)

        # Ranking conditions
        if ctype == ConditionType.IS_RANKED:
            return ctx.team_stats is not None and ctx.team_stats.rank is not None
        if ctype == ConditionType.IS_RANKED_OPPONENT:
            return self._opponent_is_ranked(game_ctx)
        if ctype == ConditionType.IS_RANKED_MATCHUP:
            team_ranked = ctx.team_stats is not None and ctx.team_stats.rank is not None
            return team_ranked and self._opponent_is_ranked(game_ctx)
        if ctype == ConditionType.IS_TOP_TEN_MATCHUP:
            return self._is_top_ten_matchup(ctx, game_ctx)

        # Game type conditions
        if ctype == ConditionType.IS_HOME:
            return self._is_home(ctx, game_ctx)
        if ctype == ConditionType.IS_AWAY:
            return not self._is_home(ctx, game_ctx)
        if ctype == ConditionType.IS_PLAYOFF:
            return self._is_playoff(game_ctx)
        if ctype == ConditionType.IS_PRESEASON:
            return self._is_preseason(game_ctx)
        if ctype == ConditionType.IS_CONFERENCE_GAME:
            return self._is_conference_game(ctx, game_ctx)

        # H2H conditions
        if ctype == ConditionType.IS_REMATCH:
            return self._is_rematch(game_ctx)

        # Broadcast conditions
        if ctype == ConditionType.IS_NATIONAL_BROADCAST:
            return self._is_national_broadcast(game_ctx)

        # Odds (placeholder - requires external data)
        if ctype == ConditionType.HAS_ODDS:
            return False  # TODO: implement when odds data available

        # Text matching
        if ctype == ConditionType.OPPONENT_NAME_CONTAINS:
            return self._opponent_name_contains(ctx, game_ctx, condition.value)

        return False

    def _check_streak(
        self, ctx: TemplateContext, threshold: int | None, positive: bool
    ) -> bool:
        """Check overall streak against threshold."""
        if not ctx.team_stats or threshold is None:
            return False
        streak = ctx.team_stats.streak_count
        if positive:
            return streak >= threshold
        return streak <= -threshold

    def _check_home_streak(
        self, game_ctx: GameContext | None, threshold: int | None, positive: bool
    ) -> bool:
        """Check home streak from Streaks context."""
        if not game_ctx or not game_ctx.streaks or threshold is None:
            return False
        streak_str = game_ctx.streaks.home_streak
        return self._parse_streak_string(streak_str, threshold, positive)

    def _check_away_streak(
        self, game_ctx: GameContext | None, threshold: int | None, positive: bool
    ) -> bool:
        """Check away streak from Streaks context."""
        if not game_ctx or not game_ctx.streaks or threshold is None:
            return False
        streak_str = game_ctx.streaks.away_streak
        return self._parse_streak_string(streak_str, threshold, positive)

    def _parse_streak_string(
        self, streak_str: str, threshold: int, positive: bool
    ) -> bool:
        """Parse streak string like 'W3' or 'L2' and check threshold."""
        if not streak_str:
            return False
        try:
            if streak_str.startswith("W") and positive:
                return int(streak_str[1:]) >= threshold
            elif streak_str.startswith("L") and not positive:
                return int(streak_str[1:]) >= threshold
        except ValueError:
            pass
        return False

    def _opponent_is_ranked(self, game_ctx: GameContext | None) -> bool:
        """Check if opponent is ranked."""
        if not game_ctx or not game_ctx.opponent_stats:
            return False
        return game_ctx.opponent_stats.rank is not None

    def _is_top_ten_matchup(
        self, ctx: TemplateContext, game_ctx: GameContext | None
    ) -> bool:
        """Check if both teams are in top 10."""
        team_top_10 = (
            ctx.team_stats is not None
            and ctx.team_stats.rank is not None
            and ctx.team_stats.rank <= 10
        )
        opp_top_10 = (
            game_ctx is not None
            and game_ctx.opponent_stats is not None
            and game_ctx.opponent_stats.rank is not None
            and game_ctx.opponent_stats.rank <= 10
        )
        return team_top_10 and opp_top_10

    def _is_home(self, ctx: TemplateContext, game_ctx: GameContext | None) -> bool:
        """Check if team is home."""
        if not game_ctx or not game_ctx.event:
            return False
        return game_ctx.event.home_team.id == ctx.team_config.team_id

    def _is_playoff(self, game_ctx: GameContext | None) -> bool:
        """Check if game is playoff."""
        if not game_ctx or not game_ctx.event:
            return False
        season_type = game_ctx.event.season_type
        return season_type is not None and "post" in season_type.lower()

    def _is_preseason(self, game_ctx: GameContext | None) -> bool:
        """Check if game is preseason."""
        if not game_ctx or not game_ctx.event:
            return False
        season_type = game_ctx.event.season_type
        return season_type is not None and "pre" in season_type.lower()

    def _is_conference_game(
        self, ctx: TemplateContext, game_ctx: GameContext | None
    ) -> bool:
        """Check if both teams in same conference (college sports)."""
        if not ctx.team_stats or not game_ctx or not game_ctx.opponent_stats:
            return False
        team_conf = ctx.team_stats.conference
        opp_conf = game_ctx.opponent_stats.conference
        return team_conf is not None and team_conf == opp_conf

    def _is_rematch(self, game_ctx: GameContext | None) -> bool:
        """Check if teams have played this season."""
        if not game_ctx or not game_ctx.h2h:
            return False
        h2h = game_ctx.h2h
        return h2h.team_wins > 0 or h2h.opponent_wins > 0

    def _is_national_broadcast(self, game_ctx: GameContext | None) -> bool:
        """Check if game is on national TV."""
        if not game_ctx or not game_ctx.event:
            return False
        national_networks = {"ESPN", "ABC", "FOX", "CBS", "NBC", "TNT", "TBS"}
        broadcasts = game_ctx.event.broadcasts
        return any(b.upper() in national_networks for b in broadcasts)

    def _opponent_name_contains(
        self,
        ctx: TemplateContext,
        game_ctx: GameContext | None,
        pattern: str | int | None,
    ) -> bool:
        """Check if opponent name contains pattern."""
        if not game_ctx or not game_ctx.event or pattern is None:
            return False
        event = game_ctx.event
        is_home = event.home_team.id == ctx.team_config.team_id
        opponent = event.away_team if is_home else event.home_team
        return str(pattern).lower() in opponent.name.lower()


def select_description(
    descriptions: list[ConditionalDescription],
    ctx: TemplateContext,
) -> str | None:
    """Select the highest priority description whose condition is met.

    Args:
        descriptions: List of conditional descriptions, sorted by priority
        ctx: Template context to evaluate against

    Returns:
        Template string of first matching description, or None if no match
    """
    evaluator = ConditionEvaluator()

    # Sort by priority (lower = higher priority)
    sorted_descs = sorted(descriptions, key=lambda d: d.priority)

    for desc in sorted_descs:
        if evaluator.evaluate(desc.condition, ctx):
            return desc.template

    return None
