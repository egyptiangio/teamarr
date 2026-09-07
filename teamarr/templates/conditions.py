"""Conditional description selection.

Allows templates to have multiple description options with conditions.
The best matching description is selected based on priority and conditions.

Rows may also carry optional ``title`` and ``subtitle`` overrides (#370
part 2): selection then runs PER FIELD — for each of title/subtitle/
description, the highest-priority matching row that DEFINES that field wins;
fields a row omits fall through to lower rows, then to the template's plain
title/subtitle/description format strings. Ties at the winning priority are
resolved randomly for descriptions (a deliberate variety feature) but
deterministically (first row in input order) for titles and subtitles, so
guide titles don't change between generation runs.

Condition Types:
- is_home, is_away: Home/away game
- win_streak, loss_streak: Team streak (value = minimum streak length)
- is_ranked_opponent: Opponent in top 25
- is_top_ten_matchup: Both teams in top 10
- is_conference_game: Same conference (college)
- is_playoff, is_preseason: Season type
- is_neutral_site: Game is at a neutral site (bowls, CFP/NCAA tournament)
- is_national_broadcast: National TV broadcast
- has_odds: Betting odds available
- is_final, is_not_final: Reference game's final status (#420 filler rows;
  the disjoint pair keeps migrated final/not-final per-field semantics exact)
- has_recap: Provider recap headline available (postgame)
- has_preview: Provider preview blurb available (same-day pregame)
- has_generated_preview: Deterministic generated preview is available
- has_structured_preview: Recent-form data available (days-ahead)
- has_event_note: Marquee/playoff note available ('NBA Finals - Game 5')
- has_match_note: Soccer competition note available ('FIFA World Cup, Group C')
- opponent_name_contains: Opponent name contains string
- league_is: Event's league is one of the given codes (value = "cfb" or "cfb,nfl")
- sport_is: Event's sport is one of the given codes (value = "football,basketball")

Priority:
- 1-99: Conditional descriptions (lower = higher priority)
- 100: Default descriptions (always match, randomly selected if multiple)

Example JSON format for description_options:
[
    {"condition": "win_streak", "condition_value": "5", "priority": 10,
     "template": "On fire! {win_streak}-game win streak!"},
    {"condition": "is_home", "priority": 50,
     "template": "{team_name} hosts {opponent}"},
    {"condition": "has_event_note", "priority": 40,
     "title": "{game_event_note}: {matchup}", "subtitle": "{venue_city}",
     "template": "{game_event_note} clash..."},
    {"priority": 100, "label": "Generic", "template": "{team_name} vs {opponent}"}
]
"""

import json
import logging
import random
from dataclasses import dataclass
from typing import Any

from teamarr.core import SEASON_POSTSEASON, SEASON_PRESEASON
from teamarr.templates.context import GameContext, TemplateContext
from teamarr.templates.variables.combat import COMBAT_SPORTS
from teamarr.utilities.event_status import is_event_final

logger = logging.getLogger(__name__)


@dataclass
class ConditionOption:
    """A single conditional row (description, plus optional title/subtitle).

    ``template`` keeps its historical meaning of "the description string" —
    renaming the JSON key would break every stored template.
    """

    template: str
    priority: int = 50
    condition: str | None = None
    condition_value: str | None = None
    title: str | None = None
    subtitle: str | None = None

    @property
    def is_default(self) -> bool:
        """Priority 100 = default description (always matches)."""
        return self.priority == 100

    def field_text(self, field: str) -> str:
        """The row's text for a selectable field ('' when not defined)."""
        if field == "description":
            return self.template or ""
        return getattr(self, field, None) or ""


# Fields the selector can pick per row. Order matters only for trace-reason
# readability; each field's selection is independent.
SELECTABLE_FIELDS = ("description", "title", "subtitle")


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

    def _eval_always(self, value: str | None, ctx: TemplateContext, game_ctx: GameContext) -> bool:
        """Always returns True. Legacy compatibility - use priority 100 defaults instead."""
        return True

    def _eval_is_home(self, value: str | None, ctx: TemplateContext, game_ctx: GameContext) -> bool:
        """Check if team is playing at home."""
        return game_ctx.is_home

    def _eval_is_away(self, value: str | None, ctx: TemplateContext, game_ctx: GameContext) -> bool:
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

    # Note: home/away streak conditions removed - can't reliably get venue-specific streak data from providers  # noqa: E501

    # =========================================================================
    # Ranking conditions
    # =========================================================================

    def _eval_is_ranked(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if team is ranked (top 25)."""
        if not ctx.team_stats:
            return False
        rank = ctx.team_stats.rank
        return rank is not None and rank <= 25

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
        return bool(event and event.season_type == SEASON_POSTSEASON)

    def _eval_is_preseason(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if this is a preseason game."""
        event = game_ctx.event
        return bool(event and event.season_type == SEASON_PRESEASON)

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
            if broadcast.lower() in national_networks:
                return True
        return False

    # =========================================================================
    # Odds conditions
    # =========================================================================

    def _eval_has_odds(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if betting odds are available."""
        return game_ctx.odds is not None

    # =========================================================================
    # Game state (#420 — filler condition rows)
    # =========================================================================

    def _eval_is_final(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Reference game exists and is final.

        On the filler path the reference game is the register's game (pregame
        -> next, postgame/idle -> last), status-refreshed by the generator
        before evaluation.
        """
        event = game_ctx.event
        return bool(event) and is_event_final(event)

    def _eval_is_not_final(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Reference game exists and is NOT final.

        Deliberately a separate evaluator rather than negation-of-is_final:
        both return False when there is no reference game, and the disjoint
        pair lets migrated final/not-final variants keep exact per-field
        fall-to-base semantics (#420) — an `always` row would wrongly donate
        its fields to final games.
        """
        event = game_ctx.event
        return bool(event) and not is_event_final(event)

    # =========================================================================
    # Provider copy availability (ESPN recaps/previews, epic tvnk #329)
    # =========================================================================

    def _eval_has_recap(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if the provider's postgame recap headline is available."""
        event = game_ctx.event
        return bool(event and event.game_recap)

    def _eval_has_preview(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if the provider's pregame preview blurb is available."""
        event = game_ctx.event
        return bool(event and event.game_preview)

    def _eval_has_generated_preview(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check that deterministic generated-preview prose can be rendered."""
        from teamarr.templates.generated_preview import build_generated_preview

        return bool(build_generated_preview(game_ctx.event))

    def _eval_has_structured_preview(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if structured preview data (recent form) is available."""
        event = game_ctx.event
        return bool(event and (event.home_last_five or event.away_last_five))

    def _eval_is_neutral_site(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if the game is at a neutral site (ESPN neutralSite: bowls,
        CFP/NCAA tournament rounds, showcase games). Host framing ('X travel
        to…', 'Y host X…') misrepresents these games (#355 item 3)."""
        event = game_ctx.event
        return bool(event and event.neutral_site)

    def _eval_has_event_note(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if the provider's marquee/playoff note is available
        ('NBA Finals - Game 5', 'CFP Quarterfinal at the Cotton Bowl Classic').
        Empty for ordinary regular-season games."""
        event = game_ctx.event
        return bool(event and event.game_event_note)

    def _eval_has_match_note(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if the provider's soccer competition note is available
        ('FIFA World Cup, Group C'). Soccer-only; empty otherwise."""
        event = game_ctx.event
        return bool(event and event.soccer_match_note)

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

    # Note: is_rematch removed - requires schedule history we can't reliably get from providers

    def _eval_is_ranked_matchup(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if both teams are ranked (top 25)."""
        if not ctx.team_stats or not game_ctx.opponent_stats:
            return False
        our_rank = ctx.team_stats.rank
        opp_rank = game_ctx.opponent_stats.rank
        if our_rank is None or opp_rank is None:
            return False
        return our_rank <= 25 and opp_rank <= 25

    # =========================================================================
    # Combat sports conditions (UFC/MMA)
    # =========================================================================

    def _eval_is_knockout(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if fight ended by KO or TKO."""
        event = game_ctx.event
        if not event or event.sport not in COMBAT_SPORTS:
            return False
        method = event.fight_result_method
        return method in ("ko", "tko")

    def _eval_is_submission(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if fight ended by submission."""
        event = game_ctx.event
        if not event or event.sport not in COMBAT_SPORTS:
            return False
        return event.fight_result_method == "submission"

    def _eval_is_decision(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if fight went to decision."""
        event = game_ctx.event
        if not event or event.sport not in COMBAT_SPORTS:
            return False
        method = event.fight_result_method
        return method is not None and "decision" in method

    def _eval_is_finish(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if fight ended by finish (KO/TKO/Submission, not decision)."""
        event = game_ctx.event
        if not event or event.sport not in COMBAT_SPORTS:
            return False
        method = event.fight_result_method
        return method in ("ko", "tko", "submission")

    def _eval_went_distance(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if fight went all scheduled rounds."""
        event = game_ctx.event
        if not event or event.sport not in COMBAT_SPORTS:
            return False
        method = event.fight_result_method
        # If it went to decision, it went the distance
        return method is not None and "decision" in method

    # =========================================================================
    # Motorsports conditions (F1, NASCAR, IndyCar, MotoGP, ...)
    # =========================================================================

    def _eval_is_race_session(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if this channel's session is the race itself."""
        event = game_ctx.event
        if not event or event.sport != "racing":
            return False
        return game_ctx.card_segment == "race"

    def _eval_is_qualifying_session(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if this channel's session is qualifying or sprint qualifying."""
        event = game_ctx.event
        if not event or event.sport != "racing":
            return False
        return game_ctx.card_segment in ("qualifying", "sprint_qualifying")

    def _eval_has_results(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if this channel's session has finished with results."""
        event = game_ctx.event
        if not event or event.sport != "racing" or not game_ctx.card_segment:
            return False
        for session in event.sessions:
            if session.code == game_ctx.card_segment:
                return any(r.position is not None for r in session.results)
        return False

    # =========================================================================
    # Event identity conditions (#370)
    # =========================================================================

    @staticmethod
    def _matches_any(actual: str | None, value: str | None) -> bool:
        """Case-insensitive membership of actual in a comma-separated value list."""
        if not value or not actual:
            return False
        wanted = {v.strip().lower() for v in value.split(",") if v.strip()}
        return actual.lower() in wanted

    def _eval_league_is(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if the event's league matches one of the given codes.

        Value is a comma-separated list of canonical league codes
        (e.g. "nfl" or "cfb,nfl"), case-insensitive. Lets one template
        branch a register by league instead of needing per-league variants.
        """
        return self._matches_any(getattr(game_ctx.event, "league", None), value)

    def _eval_sport_is(
        self, value: str | None, ctx: TemplateContext, game_ctx: GameContext
    ) -> bool:
        """Check if the event's sport matches one of the given codes.

        Value is a comma-separated list of sport codes
        (e.g. "football" or "basketball,hockey"), case-insensitive.
        """
        return self._matches_any(getattr(game_ctx.event, "sport", None), value)


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
        template, _ = self.select_with_trace(description_options, ctx, game_ctx)
        return template

    def select_with_trace(
        self,
        description_options: str | list[dict[str, Any]] | None,
        ctx: TemplateContext | None,
        game_ctx: GameContext | None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Select the best description template, with a per-row evaluation trace.

        Description-only view of select_fields_with_trace — kept because most
        callers (and the filler paths) only care about descriptions.
        """
        fields, trace = self.select_fields_with_trace(description_options, ctx, game_ctx)
        return fields.get("description", ""), trace

    def select_fields(
        self,
        description_options: str | list[dict[str, Any]] | None,
        ctx: TemplateContext | None,
        game_ctx: GameContext | None,
    ) -> dict[str, str]:
        """Per-field selection without the trace (generation-time convenience)."""
        fields, _ = self.select_fields_with_trace(description_options, ctx, game_ctx)
        return fields

    def select_fields_with_trace(
        self,
        description_options: str | list[dict[str, Any]] | None,
        ctx: TemplateContext | None,
        game_ctx: GameContext | None,
    ) -> tuple[dict[str, str], list[dict[str, Any]]]:
        """Select the winning template string PER FIELD, with an evaluation trace.

        Each row is condition-evaluated once; then title, subtitle and
        description are selected independently among the matching rows that
        define that field (#370 part 2). The trace answers "why did my
        template render THIS row?" (#357): one entry per option, in input
        order, carrying whether it matched, which fields it was selected for,
        and a human-readable reason.

        ``ctx`` may be None (preview without a live event): conditional rows
        then can't be evaluated and only defaults match — which mirrors what
        the engine does at generation time when an event lacks data.

        Returns:
            ({field: selected template string} — absent when no row defines
            the field, and trace rows with keys: index, condition,
            condition_value, priority, matched, selected (description),
            selected_for (list of fields), reason).
        """
        options = self._parse_options(description_options)
        trace: list[dict[str, Any]] = []
        if not options:
            return {}, trace

        has_event = bool(game_ctx and game_ctx.event)

        # Per-field priority groups: field -> priority -> [(option index, text)]
        field_groups: dict[str, dict[int, list[tuple[int, str]]]] = {
            f: {} for f in SELECTABLE_FIELDS
        }

        for i, opt in enumerate(options):
            row: dict[str, Any] = {
                "index": i,
                "condition": opt.condition,
                "condition_value": opt.condition_value,
                "priority": opt.priority,
                "matched": False,
                "selected": False,
                "selected_for": [],
                "reason": "",
            }
            trace.append(row)

            defined = {f: opt.field_text(f) for f in SELECTABLE_FIELDS}
            if not any(defined.values()):
                # Historical wording — asserted by tests and shown in the UI.
                row["reason"] = "skipped — empty template"
                continue

            # Default rows always match
            if opt.is_default:
                row["matched"] = True
                row["reason"] = "default (priority 100) — always matches"
            elif not opt.condition:
                row["reason"] = "skipped — no condition set"
                continue
            elif not has_event or ctx is None:
                row["reason"] = f"'{opt.condition}' not evaluated — no event data"
                continue
            else:
                matched = self._evaluator.evaluate(
                    opt.condition, opt.condition_value, ctx, game_ctx
                )
                row["matched"] = matched
                detail = f" (value: {opt.condition_value})" if opt.condition_value else ""
                row["reason"] = (
                    f"'{opt.condition}'{detail} evaluated {'true' if matched else 'false'}"
                )

            if row["matched"]:
                for field, text in defined.items():
                    if text:
                        field_groups[field].setdefault(opt.priority, []).append((i, text))

        selected_fields: dict[str, str] = {}
        for field in SELECTABLE_FIELDS:
            groups = field_groups[field]
            if not groups:
                continue
            winning_priority = min(groups.keys())
            matching = groups[winning_priority]

            if field == "description":
                # Random among ties — a deliberate variety feature for
                # descriptions only.
                selected_index, selected = random.choice(matching)
                if len(matching) > 1:
                    trace[selected_index]["reason"] += (
                        f" — chosen randomly among {len(matching)} matches"
                        f" at priority {winning_priority}"
                    )
                # Description keeps the historical outranked annotation.
                for priority, group in groups.items():
                    if priority > winning_priority:
                        for other_index, _ in group:
                            trace[other_index]["reason"] += (
                                f" — outranked by priority {winning_priority}"
                            )
                trace[selected_index]["selected"] = True
            else:
                # Deterministic for title/subtitle: first row in input order
                # at the winning priority, so guide titles are stable
                # run-to-run.
                selected_index, selected = matching[0]

            selected_fields[field] = selected
            trace[selected_index]["selected_for"].append(field)

        if not selected_fields:
            logger.debug("[CONDITION] No matching conditions found")
        else:
            logger.debug("[CONDITION] Selected fields: %s", sorted(selected_fields))
        return selected_fields, trace

    def select_filler_fields(
        self,
        rows: str | list[dict[str, Any]] | None,
        ctx: TemplateContext | None,
        game_ctx: GameContext | None,
    ) -> tuple[dict[str, str], list[str], list[dict[str, Any]]]:
        """Per-field selection for filler condition rows (#420).

        Same semantics as select_fields, plus the ordered runner-up
        description texts among the OTHER matching rows (priority order,
        then input order) and the per-row trace. The filler generator chains
        the runner-ups for the cascade-on-empty (winning description
        resolves empty at render time → next matching candidate → base
        register); the preview endpoint surfaces the trace (#428).
        """
        fields, trace = self.select_fields_with_trace(rows, ctx, game_ctx)
        winner_index = next(
            (r["index"] for r in trace if "description" in r["selected_for"]), None
        )
        runners_up = sorted(
            (opt.priority, i, opt.field_text("description"))
            for i, opt in enumerate(self._parse_options(rows))
            if i != winner_index and trace[i]["matched"] and opt.field_text("description")
        )
        return fields, [text for _, _, text in runners_up], trace

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
                    title=item.get("title"),
                    subtitle=item.get("subtitle"),
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
