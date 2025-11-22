# Teamarr Variable Centralization - Handoff Document

**Date**: 2025-11-21
**Status**: Complete - Phases 1-3 ✅ (Centralization implemented and working)

---

## What We Accomplished

### Variable Cleanup (Complete ✅)
Reduced variables from **246 → 220** by removing:

1. **Section 1: Redundant Booleans** (5 removed)
   - `is_home_game`, `is_away_game` (duplicates of `is_home`, `is_away`)
   - `is_playoffs` (duplicate of `is_playoff`)
   - `opponent_is_favorite`, `opponent_is_underdog` (inverses of `is_favorite`, `is_underdog`)

2. **Section 2: Formatted Variables** (5 removed)
   - `team_rank_formatted`, `opponent_rank_formatted` (duplicates)
   - `win_streak_text`, `loss_streak_text` (simple concatenations)
   - `time_until_text` (not useful for static EPG)

3. **Section 4: Confusing Variables** (2 removed)
   - `final_score` (duplicate of `score`)
   - `outcome` (can construct from `{team_name} {result}`)

4. **Section 5: Playoff Variables** (4 removed)
   - `can_advance`, `can_be_eliminated` (duplicates of `is_clinch_game`, `is_must_win`)
   - `series_status` (duplicate of ESPN's `series_summary`)
   - `playoff_position` (hardcoded threshold wrong for NFL/MLB)

5. **Section 6 & 7: Never-Populate Variables** (6 removed)
   - `scoring_run` (ESPN API doesn't have this)
   - `team_ppg_rank`, `opponent_ppg_rank`, `team_papg_rank`, `opponent_papg_rank` (not in API)
   - `conference_record` (never implemented, only `division_record` works)

**All 220 remaining variables verified from ESPN API - zero hallucinations!**

---

## Current Task: Variable Centralization

### Goal
Create single source of truth (`data/variables.json`) for all template variables that auto-populates:
- UI categories in `team_form.html`
- Sample data in preview
- Documentation in `variable_reference.md`
- Variable validation

### Why This Matters
**Current Problem**: Adding a new variable requires updating 5 places:
1. `epg/template_engine.py` (implementation)
2. `templates/team_form.html` (UI category)
3. `templates/team_form.html` (sample data)
4. `docs/variable_reference.md` (documentation)
5. `/tmp/variable_audit.md` (master list)

**Solution**: Single JSON file, auto-generates everything.

---

## What's Been Done

### 1. Created Generator Script ✅
**File**: `scripts/generate_variables_json.py`
- Parses `docs/variable_reference.md` markdown tables
- Extracts: name, description, source, example, category, icon
- Auto-detects: type (string/boolean/number), sports, availability
- Generates: `data/variables.json`

**Run it**: `python3 scripts/generate_variables_json.py`

### 2. Generated Initial JSON ✅
**File**: `data/variables.json`
- Currently has 179 variables (missing 41 player stat variables)
- Schema includes:
  ```json
  {
    "name": "team_name",
    "type": "string",
    "category": "Team Information (Basic)",
    "icon": "👥",
    "description": "Full team display name",
    "example": "Detroit Pistons",
    "source": "ESPN API: /teams/{id} → team.displayName",
    "sports": ["basketball", "football", "hockey", "baseball"],
    "availability": "always"
  }
  ```

---

## Implementation Summary

### Phase 1: Complete variables.json (COMPLETE ✅)

**Completed**:
- ✅ Added all 41 missing player stat variables
- ✅ Removed 10 cleaned-up variables (conference_record, team_ppg_rank, etc.)
- ✅ Final count: **220 variables** (matches audit exactly)
- ✅ Verified: All 220 variables match /tmp/variable_audit.md
- ✅ Categories: 28 categories with proper icons and grouping

**File**: `data/variables.json` (220 variables, version 1.0.0)

### Phase 2: Add Flask API Endpoint (COMPLETE ✅)

**Completed**:
- ✅ Added `/api/variables` endpoint in `app.py` (line 741)
- ✅ Returns JSON from `data/variables.json`
- ✅ Handles FileNotFoundError and JSONDecodeError gracefully
- ✅ Uses UTF-8 encoding for emoji support

**Endpoint**: `GET /api/variables` returns all 220 variables with full metadata

### Phase 3: Refactor UI to Load Dynamically (COMPLETE ✅)

**Completed**:
- ✅ Replaced hardcoded `TEMPLATE_VARIABLES` object with dynamic `loadVariablesFromAPI()` function
- ✅ Replaced hardcoded `sampleData` object with data from API `example` fields
- ✅ Updated `DOMContentLoaded` to load variables before initializing UI
- ✅ Categories auto-generated from `category` field in JSON
- ✅ Icons auto-loaded from `icon` field in JSON
- ✅ Sample data auto-populated from `example` field in JSON

**Changes in `templates/team_form.html`**:
- Line ~1520: Changed `const TEMPLATE_VARIABLES = {...}` to `let TEMPLATE_VARIABLES = {};`
- Line ~1524: Added `async function loadVariablesFromAPI()` to fetch from `/api/variables`
- Line ~844: Changed `const sampleData = {...}` to `let sampleData = {...}` (minimal fallback)
- Line ~1003: Updated `DOMContentLoaded` to call `await loadVariablesFromAPI()` first

**Result**: UI now automatically reflects any changes to `data/variables.json`

### Phase 4: Auto-Generate Documentation (OPTIONAL 🟡)

**Create**: `scripts/generate_docs.py`

```python
def generate_markdown(variables_json):
    """Generate variable_reference.md from variables.json"""
    # Group by category
    # Generate markdown tables
    # Write to docs/variable_reference.md
```

### Phase 5: Validation & Advanced Features (TODO 🔴)

- Validate templates against schema (detect undefined vars)
- Template auto-complete
- Variable usage analytics

---

## File Locations

### Current State
```
teamarr/
├── data/
│   └── variables.json          # ✅ Created (179/220 vars)
├── scripts/
│   └── generate_variables_json.py  # ✅ Created
├── docs/
│   └── variable_reference.md   # ✅ Exists (source of truth for now)
├── templates/
│   └── team_form.html          # ❌ Still hardcoded
├── epg/
│   └── template_engine.py      # ✅ Cleaned up
└── /tmp/
    └── variable_audit.md       # ✅ Updated (220 vars)
```

### Target State
```
teamarr/
├── data/
│   └── variables.json          # SINGLE SOURCE OF TRUTH (220 vars)
├── scripts/
│   ├── generate_variables_json.py  # Parse MD → JSON
│   └── generate_docs.py        # Generate MD from JSON
├── docs/
│   └── variable_reference.md   # Auto-generated from JSON
├── templates/
│   └── team_form.html          # Loads from /api/variables
├── app.py                      # Added /api/variables endpoint
└── epg/
    └── template_engine.py      # Optionally load schema
```

---

## Key Decisions Made

1. **JSON over YAML**: Simpler, no dependencies, works in Python & JS
2. **Single file**: 220 variables isn't large enough to split
3. **Parse from markdown first**: Leverage existing documentation
4. **Keep variable_reference.md**: But auto-generate it from JSON eventually

---

## Quick Commands

```bash
# Generate/update variables.json
python3 scripts/generate_variables_json.py

# Count variables
python3 << 'EOF'
import json
with open('data/variables.json') as f:
    print(f"Total: {json.load(f)['total_variables']} variables")
EOF

# Verify all 220 are present
python3 << 'EOF'
import json
with open('data/variables.json') as f:
    var_count = json.load(f)['total_variables']
with open('/tmp/variable_audit.md') as f:
    audit_count = sum(1 for line in f if line.strip() and not line.startswith('#') and line[0].islower())
print(f"JSON: {var_count}, Audit: {audit_count}, Missing: {audit_count - var_count}")
EOF
```

---

## Next Session Goals

1. **Add missing 41 variables** to `data/variables.json`
2. **Create `/api/variables` endpoint** in `app.py`
3. **Refactor `team_form.html`** to load dynamically
4. **Test end-to-end**: Add new variable, verify it appears in UI

---

## Context for Next Session

- All variable cleanup is done, 220 variables remain
- Generator script works but needs to capture missing player stats
- Schema structure is solid, ready to use
- This is high-impact: future variables are 1-place changes instead of 5
