"""Template variable resolver.

Resolves {variable} placeholders in template strings using registered extractors.
Supports three suffix types: base, .next, .last

Also supports conditional descriptions - selecting the best template based on
game conditions (is_home, win_streak, etc.) and priority.
"""

import re
from typing import Any

from templates_v2.conditions import get_condition_selector
from templates_v2.context import GameContext, TemplateContext
from templates_v2.variables import SuffixRules, get_registry

# Pattern matches: {variable} or {variable.next} or {variable.last}
# Note: @ is allowed to support {vs_@} variable
VARIABLE_PATTERN = re.compile(r"\{([a-z_][a-z0-9_@]*(?:\.[a-z]+)?)\}", re.IGNORECASE)


class TemplateResolver:
    """Resolves template variables in strings.

    Usage:
        resolver = TemplateResolver()
        result = resolver.resolve("{team_name} vs {opponent}", context)
        # -> "Detroit Lions vs Chicago Bears"

        # Conditional descriptions
        options = '[{"condition": "is_home", "priority": 50, "template": "Home: {team_name}"}]'
        result = resolver.resolve_conditional(options, context)
    """

    def __init__(self) -> None:
        self._registry = get_registry()
        self._condition_selector = get_condition_selector()

    def resolve(self, template: str, context: TemplateContext) -> str:
        """Replace all {variable} placeholders with values.

        Args:
            template: String with {variable} placeholders
            context: Complete template context

        Returns:
            String with all variables resolved
        """
        if not template:
            return ""

        # Build all variables (base + suffixed)
        variables = self._build_all_variables(context)

        def replace(match: re.Match) -> str:
            var_name = match.group(1).lower()
            return variables.get(var_name, "")

        return VARIABLE_PATTERN.sub(replace, template)

    def _build_all_variables(self, ctx: TemplateContext) -> dict[str, str]:
        """Build complete variable dict with all suffixes.

        Generates up to 3 values per variable:
        - base (no suffix): from ctx.game_context
        - .next suffix: from ctx.next_game
        - .last suffix: from ctx.last_game

        Suffix generation follows each variable's SuffixRules.
        """
        variables: dict[str, str] = {}

        for var_def in self._registry.all_variables():
            rules = var_def.suffix_rules

            # Base variable (current game)
            if rules != SuffixRules.LAST_ONLY:
                value = var_def.extractor(ctx, ctx.game_context)
                variables[var_def.name] = value

            # .next suffix
            if rules in (SuffixRules.ALL, SuffixRules.BASE_NEXT_ONLY):
                if ctx.next_game:
                    value = var_def.extractor(ctx, ctx.next_game)
                    variables[f"{var_def.name}.next"] = value

            # .last suffix
            if rules in (SuffixRules.ALL, SuffixRules.LAST_ONLY):
                if ctx.last_game:
                    value = var_def.extractor(ctx, ctx.last_game)
                    variables[f"{var_def.name}.last"] = value

        return variables

    def resolve_conditional(
        self,
        description_options: str | list[dict[str, Any]] | None,
        context: TemplateContext,
        game_ctx: GameContext | None = None,
    ) -> str:
        """Select and resolve a conditional description.

        Evaluates conditions against the game context to select the best
        template, then resolves variables in that template.

        Args:
            description_options: JSON string or list of description options.
                Each option has: condition, condition_value, priority, template
            context: Template context
            game_ctx: Game context for condition evaluation.
                If None, uses context.game_context.

        Returns:
            Resolved description string, or empty string if no match.

        Example:
            options = [
                {"condition": "win_streak", "condition_value": "5", "priority": 10,
                 "template": "{team_name} on a {win_streak}-game win streak!"},
                {"condition": "is_home", "priority": 50,
                 "template": "{team_name} hosts {opponent}"},
                {"priority": 100, "template": "{team_name} vs {opponent}"}  # Fallback
            ]
            result = resolver.resolve_conditional(options, ctx)
        """
        if game_ctx is None:
            game_ctx = context.game_context

        # Select the best template based on conditions
        template = self._condition_selector.select(description_options, context, game_ctx)

        if not template:
            return ""

        # Resolve variables in the selected template
        return self.resolve(template, context)

    def get_available_variables(self) -> list[str]:
        """Get list of all registered variable names."""
        return [v.name for v in self._registry.all_variables()]

    def get_variable_count(self) -> int:
        """Get count of registered variables."""
        return self._registry.count()

    def get_available_conditions(self) -> list[str]:
        """Get list of all available condition types."""
        return [
            "is_home",
            "is_away",
            "win_streak",
            "loss_streak",
            "home_win_streak",
            "home_loss_streak",
            "away_win_streak",
            "away_loss_streak",
            "is_ranked_opponent",
            "is_top_ten_matchup",
            "is_conference_game",
            "is_playoff",
            "is_preseason",
            "is_national_broadcast",
            "has_odds",
            "opponent_name_contains",
        ]


def resolve(template: str, context: TemplateContext) -> str:
    """Convenience function for one-off resolution.

    For repeated resolution, create a TemplateResolver instance instead.
    """
    return TemplateResolver().resolve(template, context)
