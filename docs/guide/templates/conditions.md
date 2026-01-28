---
title: Conditions
parent: Templates
grand_parent: User Guide
nav_order: 3
---

# Template Conditions

Conditions let you show different descriptions based on game context. Instead of a single static description, you can have multiple options that trigger based on specific situations.

## How Conditions Work

Each condition option has:
- **Condition**: What to check (e.g., `is_home`, `win_streak`)
- **Value**: For numeric conditions, the threshold (e.g., `5` for a 5-game streak)
- **Priority**: Lower numbers = higher priority (1-99 for conditionals, 100 for defaults)
- **Template**: The description to use if the condition matches

When generating EPG, Teamarr evaluates all conditions and selects the highest-priority (lowest number) match. If multiple conditions match at the same priority, one is chosen randomly.

## Priority System

| Priority | Purpose |
|----------|---------|
| 1-49 | High priority conditionals (rare situations) |
| 50-99 | Normal priority conditionals |
| 100 | Default fallback (always matches) |

**Lower numbers win.** A priority 10 condition beats priority 50, which beats priority 100.

---

## Available Conditions

### Home/Away

| Condition | Value | Description |
|-----------|-------|-------------|
| `is_home` | - | Team is playing at home |
| `is_away` | - | Team is playing away/on the road |

**Example:**
```json
{"condition": "is_home", "priority": 50, "template": "{team_name} hosts {opponent} at {venue}"}
{"condition": "is_away", "priority": 50, "template": "{team_name} travels to face {opponent}"}
```

---

### Streaks

| Condition | Value | Description |
|-----------|-------|-------------|
| `win_streak` | Minimum streak length | Team is on a winning streak >= value |
| `loss_streak` | Minimum streak length | Team is on a losing streak >= value |

**Example:**
```json
{"condition": "win_streak", "condition_value": "5", "priority": 10, "template": "🔥 {team_name} riding a {win_streak}-game win streak!"}
{"condition": "loss_streak", "condition_value": "3", "priority": 20, "template": "{team_name} looking to snap a {loss_streak}-game skid"}
```

---

### Rankings (College)

| Condition | Value | Description |
|-----------|-------|-------------|
| `is_ranked` | - | Team is ranked (top 25) |
| `is_ranked_opponent` | - | Opponent is ranked (top 25) |
| `is_ranked_matchup` | - | Both teams are ranked (top 25) |
| `is_top_ten_matchup` | - | Both teams are in the top 10 |

**Example:**
```json
{"condition": "is_top_ten_matchup", "priority": 5, "template": "🏆 Top 10 showdown! {team_rank_display} {team_name} vs {opponent_rank_display} {opponent}"}
{"condition": "is_ranked_matchup", "priority": 15, "template": "Ranked matchup: {team_rank_display} {team_name} vs {opponent_rank_display} {opponent}"}
{"condition": "is_ranked_opponent", "priority": 30, "template": "{team_name} faces {opponent_rank_display} {opponent}"}
```

---

### Season Type

| Condition | Value | Description |
|-----------|-------|-------------|
| `is_playoff` | - | Playoff/postseason game |
| `is_preseason` | - | Preseason/exhibition game |

**Example:**
```json
{"condition": "is_playoff", "priority": 5, "template": "🏆 PLAYOFF: {team_name} vs {opponent}"}
{"condition": "is_preseason", "priority": 50, "template": "Preseason: {team_name} vs {opponent}"}
```

---

### Conference (College)

| Condition | Value | Description |
|-----------|-------|-------------|
| `is_conference_game` | - | Both teams are in the same conference |

**Example:**
```json
{"condition": "is_conference_game", "priority": 40, "template": "{college_conference} matchup: {team_name} vs {opponent}"}
```

---

### Broadcast

| Condition | Value | Description |
|-----------|-------|-------------|
| `is_national_broadcast` | - | Game is on national TV (ABC, CBS, NBC, FOX, ESPN, TNT, TBS) |

**Example:**
```json
{"condition": "is_national_broadcast", "priority": 60, "template": "{team_name} vs {opponent} on {broadcast_network}"}
```

---

### Odds

| Condition | Value | Description |
|-----------|-------|-------------|
| `has_odds` | - | Betting odds are available for this game |

**Example:**
```json
{"condition": "has_odds", "priority": 70, "template": "{team_name} ({odds_spread}) vs {opponent}. O/U: {odds_over_under}"}
```

---

### Opponent

| Condition | Value | Description |
|-----------|-------|-------------|
| `opponent_name_contains` | Search string | Opponent name contains the specified text (case-insensitive) |

**Example:**
```json
{"condition": "opponent_name_contains", "condition_value": "Rival", "priority": 20, "template": "🔥 Rivalry game! {team_name} vs {opponent}"}
```

---

## Default Descriptions

Priority 100 is reserved for default descriptions that always match. You should always have at least one default as a fallback.

```json
{"priority": 100, "template": "{team_name} vs {opponent}"}
```

If you have multiple defaults (all at priority 100), one is chosen randomly - useful for variety.

---

## Complete Example

Here's a complete set of conditions for a college football team template:

```json
[
  {"condition": "is_playoff", "priority": 5, "template": "🏆 PLAYOFF: {team_name} vs {opponent}"},
  {"condition": "is_top_ten_matchup", "priority": 10, "template": "🔥 Top 10 clash! {team_rank_display} {team_name} vs {opponent_rank_display} {opponent}"},
  {"condition": "win_streak", "condition_value": "5", "priority": 15, "template": "{team_name} riding a {win_streak}-game win streak vs {opponent}"},
  {"condition": "is_ranked_matchup", "priority": 20, "template": "Ranked matchup: {team_rank_display} {team_name} vs {opponent_rank_display} {opponent}"},
  {"condition": "is_ranked_opponent", "priority": 30, "template": "{team_name} faces {opponent_rank_display} {opponent}"},
  {"condition": "is_conference_game", "priority": 40, "template": "{college_conference} game: {team_name} vs {opponent}"},
  {"condition": "is_home", "priority": 50, "template": "{team_name} hosts {opponent} at {venue}"},
  {"condition": "is_away", "priority": 50, "template": "{team_name} travels to face {opponent}"},
  {"priority": 100, "template": "{team_name} vs {opponent}"}
]
```

**Evaluation order:**
1. If it's a playoff game → use playoff template (priority 5)
2. Else if both teams are top 10 → use top 10 template (priority 10)
3. Else if team has 5+ game win streak → use win streak template (priority 15)
4. Else if both teams ranked → use ranked matchup template (priority 20)
5. Else if opponent is ranked → use ranked opponent template (priority 30)
6. Else if same conference → use conference template (priority 40)
7. Else if home game → use home template (priority 50)
8. Else if away game → use away template (priority 50)
9. Otherwise → use default template (priority 100)

---

## Condition Summary

| Condition | Requires Value | Best For |
|-----------|----------------|----------|
| `is_home` | No | Home game messaging |
| `is_away` | No | Road game messaging |
| `win_streak` | Yes (min length) | Hot streak highlights |
| `loss_streak` | Yes (min length) | Struggling team context |
| `is_ranked` | No | Ranked team (college) |
| `is_ranked_opponent` | No | Facing ranked opponent |
| `is_ranked_matchup` | No | Both teams ranked |
| `is_top_ten_matchup` | No | Elite matchups |
| `is_playoff` | No | Postseason games |
| `is_preseason` | No | Exhibition games |
| `is_conference_game` | No | Conference play (college) |
| `is_national_broadcast` | No | National TV games |
| `has_odds` | No | Including betting lines |
| `opponent_name_contains` | Yes (search text) | Rivalry or specific opponent |
