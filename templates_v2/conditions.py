"""Conditional description selection.

Allows templates to have multiple description options with conditions.
The best matching description is selected based on priority and conditions.

Condition Types:
- is_home, is_away: Home/away game
- win_streak, loss_streak: Team streak (value = minimum streak length)
- home_win_streak, home_loss_streak: Home-specific streaks
- away_win_streak, away_loss_streak: Away-specific streaks
- is_ranked_opponent: Opponent in top 25
- is_top_ten_matchup: Both teams in top 10
- is_conference_game: Same conference (college)
- is_playoff, is_preseason: Season type
- is_national_broadcast: National TV broadcast
- has_odds: Betting odds available
- opponent_name_contains: Opponent name contains string

Priority:
- 1-99: Conditional descriptions (lower = higher priority)
- 100: Fallback descriptions (always match)

Example JSON format for description_options:
[
    {"condition": "win_streak", "condition_value": "5", "priority": 10,
     "template": "On fire! {win_streak}-game win streak!"},
    {"condition": "is_home", "priority": 50,
     "template": "{team_name} hosts {opponent}"},
    {"priority": 100, "template": "{team_name} vs {opponent}"}
]
"""

import json
import random
from dataclasses import dataclass
from typing import Any

from templates_v2.context import GameContext, TemplateContext


@dataclass
class ConditionOption:
    """A single conditional description option."""

    template: str
    priority: int = 50
    condition: str | None = None
    condition_value: str | None = None

    @property
    def is_fallback(self) -> bool:
        """Priority 100 = fallback (always matches)."""
        return self.priority == 100


class ConditionEvaluator:
    """Evaluates conditions against game context."""

    def evaluate(
        self,
        condition: str,
        value: str | None,
        ctx: TemplateContext,
        game_ctx: GameContext | None,
    ) -> bool:
        """Evaluate a condition.

        Args:
            condition: Condition type to check
            value: Optional value for numeric conditions
            ctx: Template context
            game_ctx: Game context (current, next, or last game)

        Returns:
            True if condition is met
        """
        if not game_ctx or not game_ctx.event:
            return False

        # Dispatch to specific evaluator
        method = getattr(self, f"_eval_{condition}", None)
        if method:
            return method(value, ctx, game_ctx)

        return False

    # =========================================================================
    # Home/Away conditions
    # =========================================================================

    def _eval_is_home(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if team is playing at home."""
        return game_ctx.is_home

    def _eval_is_away(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if team is playing away."""
        return not game_ctx.is_home

    # =========================================================================
    # Streak conditions
    # =========================================================================

    def _eval_win_streak(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if team is on a win streak >= value."""
        if not value or not ctx.team_stats:
            return False
        try:
            streak = ctx.team_stats.streak
            if not streak or not streak.startswith("W"):
                return False
            streak_count = int(streak[1:])
            return streak_count >= int(value)
        except (ValueError, IndexError):
            return False

    def _eval_loss_streak(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if team is on a loss streak >= value."""
        if not value or not ctx.team_stats:
            return False
        try:
            streak = ctx.team_stats.streak
            if not streak or not streak.startswith("L"):
                return False
            streak_count = int(streak[1:])
            return streak_count >= int(value)
        except (ValueError, IndexError):
            return False

    def _eval_home_win_streak(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if team is on a home win streak >= value."""
        if not value or not ctx.team_stats:
            return False
        try:
            streak = ctx.team_stats.home_streak
            if not streak or not streak.startswith("W"):
                return False
            streak_count = int(streak[1:])
            return streak_count >= int(value)
        except (ValueError, IndexError):
            return False

    def _eval_home_loss_streak(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if team is on a home loss streak >= value."""
        if not value or not ctx.team_stats:
            return False
        try:
            streak = ctx.team_stats.home_streak
            if not streak or not streak.startswith("L"):
                return False
            streak_count = int(streak[1:])
            return streak_count >= int(value)
        except (ValueError, IndexError):
            return False

    def _eval_away_win_streak(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if team is on an away win streak >= value."""
        if not value or not ctx.team_stats:
            return False
        try:
            streak = ctx.team_stats.away_streak
            if not streak or not streak.startswith("W"):
                return False
            streak_count = int(streak[1:])
            return streak_count >= int(value)
        except (ValueError, IndexError):
            return False

    def _eval_away_loss_streak(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if team is on an away loss streak >= value."""
        if not value or not ctx.team_stats:
            return False
        try:
            streak = ctx.team_stats.away_streak
            if not streak or not streak.startswith("L"):
                return False
            streak_count = int(streak[1:])
            return streak_count >= int(value)
        except (ValueError, IndexError):
            return False

    # =========================================================================
    # Ranking conditions
    # =========================================================================

    def _eval_is_ranked_opponent(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if opponent is ranked (top 25)."""
        opponent_stats = game_ctx.opponent_stats
        if not opponent_stats:
            return False
        rank = opponent_stats.rank
        return rank is not None and rank <= 25

    def _eval_is_top_ten_matchup(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if both teams are top 10."""
        if not ctx.team_stats or not game_ctx.opponent_stats:
            return False
        our_rank = ctx.team_stats.rank
        opp_rank = game_ctx.opponent_stats.rank
        if our_rank is None or opp_rank is None:
            return False
        return our_rank <= 10 and opp_rank <= 10

    # =========================================================================
    # Season type conditions
    # =========================================================================

    def _eval_is_playoff(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if this is a playoff game."""
        event = game_ctx.event
        return event.is_playoff if event else False

    def _eval_is_preseason(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if this is a preseason game."""
        event = game_ctx.event
        return event.is_preseason if event else False

    # =========================================================================
    # Conference conditions (college)
    # =========================================================================

    def _eval_is_conference_game(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if both teams are in the same conference."""
        if not ctx.team_stats or not game_ctx.opponent_stats:
            return False

        our_conf = ctx.team_stats.conference or ""
        opp_conf = game_ctx.opponent_stats.conference or ""

        if not our_conf or not opp_conf:
            return False

        return our_conf.lower() == opp_conf.lower()

    # =========================================================================
    # Broadcast conditions
    # =========================================================================

    def _eval_is_national_broadcast(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if game is on national TV."""
        event = game_ctx.event
        if not event or not event.broadcasts:
            return False

        national_networks = {"abc", "cbs", "nbc", "fox", "espn", "espn2", "tnt", "tbs"}
        for broadcast in event.broadcasts:
            network = broadcast.get("network", "").lower()
            market = broadcast.get("market", "").lower()
            if market == "national" or network in national_networks:
                return True
        return False

    # =========================================================================
    # Odds conditions
    # =========================================================================

    def _eval_has_odds(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if betting odds are available."""
        event = game_ctx.event
        return bool(event and event.odds)

    # =========================================================================
    # Opponent conditions
    # =========================================================================

    def _eval_opponent_name_contains(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if opponent name contains a string."""
        if not value:
            return False
        opponent = game_ctx.opponent
        if not opponent:
            return False
        return value.lower() in opponent.name.lower()


class ConditionalDescriptionSelector:
    """Selects the best description based on conditions and priority."""

    def __init__(self):
        self._evaluator = ConditionEvaluator()

    def select(
        self,
        description_options: str | list[dict[str, Any]] | None,
        ctx: TemplateContext,
        game_ctx: GameContext | None,
    ) -> str:
        """Select the best description template.

        Args:
            description_options: JSON string or list of description options
            ctx: Template context
            game_ctx: Game context

        Returns:
            Selected template string, or empty string if none match
        """
        options = self._parse_options(description_options)
        if not options:
            return ""

        # Group matching options by priority
        priority_groups: dict[int, list[str]] = {}

        for opt in options:
            if not opt.template:
                continue

            # Fallbacks always match
            if opt.is_fallback:
                if opt.priority not in priority_groups:
                    priority_groups[opt.priority] = []
                priority_groups[opt.priority].append(opt.template)
                continue

            # Conditionals need to be evaluated
            if not opt.condition:
                continue

            if self._evaluator.evaluate(opt.condition, opt.condition_value, ctx, game_ctx):
                if opt.priority not in priority_groups:
                    priority_groups[opt.priority] = []
                priority_groups[opt.priority].append(opt.template)

        if not priority_groups:
            return ""

        # Get highest priority (lowest number)
        highest_priority = min(priority_groups.keys())
        matching_templates = priority_groups[highest_priority]

        # Random selection from same-priority templates
        return random.choice(matching_templates)

    def _parse_options(
        self, description_options: str | list[dict[str, Any]] | None
    ) -> list[ConditionOption]:
        """Parse description options into ConditionOption objects."""
        if not description_options:
            return []

        # Parse JSON string if needed
        if isinstance(description_options, str):
            try:
                raw_options = json.loads(description_options)
            except json.JSONDecodeError:
                return []
        else:
            raw_options = description_options

        if not isinstance(raw_options, list):
            return []

        options = []
        for item in raw_options:
            if not isinstance(item, dict):
                continue
            options.append(
                ConditionOption(
                    template=item.get("template", ""),
                    priority=item.get("priority", 50),
                    condition=item.get("condition"),
                    condition_value=item.get("condition_value"),
                )
            )

        return options


# Default singleton
_selector: ConditionalDescriptionSelector | None = None


def get_condition_selector() -> ConditionalDescriptionSelector:
    """Get the default condition selector."""
    global _selector
    if _selector is None:
        _selector = ConditionalDescriptionSelector()
    return _selector
