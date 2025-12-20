# V1 → V2 UI Rewrite Plan

Systematic rewrite of every V1 page, modal, and component as V2 React/TypeScript.
**NO code copy-pasting** - fresh rewrites informed by V1's function and form.

## V1 Template Inventory

| Template | Lines | Complexity | V2 Status |
|----------|-------|------------|-----------|
| `base.html` | 364 | Layout, nav, notifications | Partial (App.tsx) |
| `index.html` (Dashboard) | 655 | 4 quadrants, tooltips, history | Basic |
| `team_list.html` | 1517 | List, filters, bulk actions, modals | Basic |
| `team_form.html` | 506 | Create/edit team form | Not started |
| `team_import.html` | 876 | League sidebar, team grid, bulk import | Started |
| `template_list.html` | 316 | Template cards, preview | Basic |
| `template_form.html` | 2830 | MASSIVE form, variable picker, live preview | Not started |
| `event_epg.html` | 2636 | Event groups list, channels, processing | Basic |
| `event_group_form.html` | 2038 | Complex form, league picker, stream config | Not started |
| `event_groups_import.html` | 720 | M3U account sidebar, group grid, preview | Started |
| `epg_management.html` | 1985 | Team EPG, event EPG tabs, XMLTV viewer | Not started |
| `channels.html` | 1829 | Managed channels, sync status, reconciliation | Basic |
| `settings.html` | 1437 | Multi-section settings, Dispatcharr config | Basic |

---

## Phase 1: Foundation & Layout

### 1.1 Base Layout (`base.html` → `App.tsx`)
- [x] Sidebar navigation with icons
- [x] Mobile responsive layout
- [ ] **Theme toggle** (dark/light)
- [ ] **Notification system** (toast is there, need persistent)
- [ ] **EPG generation progress** (polling, progress bar)

### 1.2 Navigation Structure
V1 Navigation:
- Dashboard
- Teams → List, Import, Add/Edit
- Event Groups → List, Import, Add/Edit
- EPG → Management (tabs: Team EPG, Event EPG, XMLTV Viewer)
- Channels → Managed Channels
- Templates → List, Add/Edit
- Settings

---

## Phase 2: Dashboard (`index.html`)

### 2.1 Page Header
- [ ] Title + subtitle
- [ ] Quick Actions bar:
  - Create Template button
  - Import Teams button
  - Import Event Group button
  - Generate EPG button (with progress)

### 2.2 Four Quadrants Grid
Each quadrant has: header with "Manage →" link, 4 stat tiles

**Teams Quadrant:**
- [ ] Total teams count
- [ ] Leagues count + **hover tooltip** showing league breakdown with logos
- [ ] Active teams count
- [ ] Assigned (to template) count

**Event Groups Quadrant:**
- [ ] Groups count + **hover tooltip** showing match rates
- [ ] Leagues count + **hover tooltip** with logos
- [ ] Total streams count
- [ ] Matched streams + percentage + **hover tooltip**

**EPG Quadrant:**
- [ ] Channels count + **hover tooltip** (team-based vs event-based)
- [ ] Events count + **hover tooltip**
- [ ] Filler count + **hover tooltip** (pregame/postgame/idle)
- [ ] Total programmes

**Channels Quadrant:**
- [ ] Active managed channels
- [ ] Channels with logos
- [ ] Dispatcharr groups + **hover tooltip**
- [ ] Deleted in 24h

### 2.3 EPG Generation History Table
- [ ] Table with: Generated At, Channels, Events, Programs, Duration, Status
- [ ] Status badges (success/error/partial)

### 2.4 Getting Started Guide
- [ ] Show when no templates/teams exist
- [ ] Step-by-step instructions

---

## Phase 3: Teams Section

### 3.1 Team List (`team_list.html`)
- [ ] **Search bar** with filters (league, active status)
- [ ] **View toggle** (cards / compact list)
- [ ] **Bulk actions** (delete, assign template, toggle active)
- [ ] Team cards showing:
  - Team logo
  - Team name + abbreviation
  - League badge
  - Channel ID
  - Template assignment (or "Unassigned")
  - Active/inactive status
  - Edit/Delete actions
- [ ] **Quick assign modal** (assign template to multiple teams)
- [ ] **Pagination** or virtual scrolling

### 3.2 Team Form (`team_form.html`)
- [ ] Create/Edit mode
- [ ] Fields:
  - Team search (autocomplete from cache)
  - Provider + Provider Team ID (auto-filled)
  - League + Sport (auto-filled)
  - Team Name, Abbreviation (editable)
  - Team Logo URL (with preview)
  - Channel ID (auto-generated, editable)
  - Channel Logo URL (optional override)
  - Template dropdown
  - Active toggle
- [ ] Live preview of channel ID generation
- [ ] Save/Cancel buttons

### 3.3 Team Import (`team_import.html`)
- [ ] Left sidebar: Leagues grouped by sport (collapsible)
- [ ] Main area: Team grid with checkboxes
- [ ] College sports: Conference grouping (collapsible)
- [ ] Already-imported teams: grayed out with "IMPORTED" badge
- [ ] Select All / Deselect All buttons
- [ ] Sticky footer: selection count + Import button
- [ ] Bulk import with progress feedback

---

## Phase 4: Templates Section

### 4.1 Template List (`template_list.html`)
- [ ] Template cards showing:
  - Name
  - Type (team/event)
  - Sport/League scope
  - Title format preview
  - Filler status (pregame/postgame/idle enabled)
  - Created/Updated timestamps
  - Edit/Delete/Duplicate actions
- [ ] **Import/Export** buttons
- [ ] **Create Template** button

### 4.2 Template Form (`template_form.html`) - COMPLEX
**This is the biggest form, 2830 lines in V1**

Full-page editor (NOT modal), sections:

**Identity Section:**
- [ ] Template name
- [ ] Template type (team/event)
- [ ] Sport filter (optional)
- [ ] League filter (optional)

**Title & Description Section:**
- [ ] Title format with variable picker
- [ ] Subtitle template with variable picker
- [ ] Program art URL

**Game Duration Section:**
- [ ] Duration mode: sport-default / global-default / custom
- [ ] Custom duration override (hours)

**XMLTV Metadata Section:**
- [ ] Categories (multi-select or comma-separated)
- [ ] Categories apply to: all / events only
- [ ] Flags (new, live, date)

**Pregame Filler Section:**
- [ ] Enable toggle
- [ ] Time periods (list):
  - Start hours before
  - End hours before
  - Title template
  - Description template
- [ ] Add/remove periods
- [ ] Fallback content (title, subtitle, description, art URL)

**Postgame Filler Section:**
- [ ] Enable toggle
- [ ] Time periods (list)
- [ ] Fallback content
- [ ] Conditional descriptions (final vs not-final)

**Idle Filler Section:**
- [ ] Enable toggle
- [ ] Content (title, subtitle, description, art URL)
- [ ] Conditional descriptions
- [ ] Offseason content

**Conditional Descriptions Section:**
- [ ] Enable toggle
- [ ] Conditions list:
  - Condition type dropdown
  - Condition value (if needed)
  - Priority
  - Template
- [ ] Add/remove conditions
- [ ] Preview which condition would match

**Event Template Fields (if type=event):**
- [ ] Channel name template
- [ ] Channel logo URL

**Variable Picker Component:**
- [ ] Collapsible sidebar/panel
- [ ] Categories: Identity, Game, Teams, Venue, Time, Stats, Odds, etc.
- [ ] Click to insert variable
- [ ] Search/filter variables
- [ ] Variable tooltips with descriptions

**Live Preview:**
- [ ] Sample event data
- [ ] Real-time template rendering
- [ ] Show pregame/postgame/idle preview

---

## Phase 5: Event Groups Section

### 5.1 Event Groups List (`event_epg.html`)
- [ ] Group cards showing:
  - Name
  - Leagues (badges)
  - M3U group binding
  - Stream count (total / matched)
  - Match rate percentage
  - Template assignment
  - Enabled/disabled status
  - Last processed timestamp
  - Actions: Edit, Process, Enable/Disable, Delete
- [ ] **Process All** button
- [ ] Managed channels summary per group

### 5.2 Event Group Form (`event_group_form.html`)
Full-page editor:

**Basic Info:**
- [ ] Group name
- [ ] Enabled toggle

**League Selection:**
- [ ] Multi-select league picker
- [ ] Search/filter leagues
- [ ] Show league logos
- [ ] Quick presets (e.g., "All Soccer", "US Major Sports")

**M3U Binding:**
- [ ] M3U Account dropdown (from Dispatcharr)
- [ ] M3U Group dropdown (from selected account)
- [ ] Stream preview button

**Template:**
- [ ] Template dropdown

**Channel Settings:**
- [ ] Channel assignment mode: auto / manual
- [ ] Start channel number (for manual)
- [ ] Expected stream count (for range reservation)
- [ ] Dispatcharr channel group
- [ ] Stream profile
- [ ] Channel profiles (multi-select)

**Lifecycle Settings:**
- [ ] Create timing dropdown
- [ ] Delete timing dropdown

**Duplicate Handling:**
- [ ] Mode: consolidate / separate / ignore

**Advanced:**
- [ ] Sort order
- [ ] Parent group (for hierarchical grouping)

### 5.3 Event Groups Import (`event_groups_import.html`)
- [ ] Left sidebar: M3U accounts from Dispatcharr
- [ ] Main area: M3U groups grid
- [ ] Search/filter groups
- [ ] Preview streams modal
- [ ] Import → redirects to form with pre-filled M3U binding

---

## Phase 6: EPG Management

### 6.1 EPG Management (`epg_management.html`)
Tabbed interface:

**Team EPG Tab:**
- [ ] Team list with last generated status
- [ ] Generate for specific teams
- [ ] View generated programmes per team

**Event EPG Tab:**
- [ ] Event groups with processing status
- [ ] Process specific groups
- [ ] View matched events per group

**XMLTV Viewer Tab:**
- [ ] Combined XMLTV preview
- [ ] Copy URL button
- [ ] Download XML button
- [ ] Channel filter
- [ ] Date range filter

---

## Phase 7: Channels (`channels.html`)

### 7.1 Managed Channels
- [ ] Filter by: Event group, Sync status, Date range
- [ ] Channels table:
  - Channel name
  - Channel number
  - Event name
  - Teams (home vs away with logos)
  - Event date/time
  - League
  - Streams count
  - Sync status badge
  - Dispatcharr channel ID
  - Actions: View, Sync, Delete
- [ ] **Bulk actions:** delete selected, force sync
- [ ] **Sync status legend**

### 7.2 Reconciliation Panel
- [ ] Run reconciliation button
- [ ] Issues found:
  - Orphan Teamarr (in DB but not in Dispatcharr)
  - Orphan Dispatcharr (in Dispatcharr but not tracked)
  - Duplicates
  - Drift (mismatched data)
- [ ] Auto-fix toggles
- [ ] Fix selected / Fix all buttons

---

## Phase 8: Settings (`settings.html`)

### 8.1 Dispatcharr Integration
- [ ] Enable toggle
- [ ] URL input
- [ ] Username input
- [ ] Password input (masked)
- [ ] **EPG ID dropdown** (fetched from Dispatcharr EPG sources)
- [ ] Test connection button
- [ ] Connection status indicator

### 8.2 EPG Settings
- [ ] Team schedule days ahead
- [ ] Event match days ahead
- [ ] EPG output days ahead
- [ ] EPG lookback hours
- [ ] Timezone dropdown
- [ ] Output path
- [ ] Include final events toggle
- [ ] Midnight crossover mode

### 8.3 Game Durations
- [ ] Default duration
- [ ] Sport-specific durations (grid/list)

### 8.4 Channel Lifecycle
- [ ] Create timing dropdown
- [ ] Delete timing dropdown
- [ ] Channel range start
- [ ] Channel range end

### 8.5 Scheduler
- [ ] Enable toggle
- [ ] Interval (minutes)
- [ ] **Cron expression** with natural language preview

### 8.6 Reconciliation
- [ ] Auto-reconcile on EPG generation
- [ ] Auto-reconcile on startup
- [ ] Auto-fix options
- [ ] History retention days

### 8.7 Display Preferences
- [ ] Time format (12h/24h)
- [ ] Show timezone toggle
- [ ] Channel ID format template

### 8.8 Team/League Cache
- [ ] Cache status (leagues count, teams count)
- [ ] Last refresh timestamp
- [ ] Stale indicator
- [ ] Refresh button with progress

---

## Phase 9: Shared Components

### 9.1 Rich Tooltips (from V1)
- [ ] HoverCard or Tooltip component
- [ ] Support for tables inside tooltips
- [ ] Support for logos/images
- [ ] Positioned correctly (avoids edges)

### 9.2 Variable Picker
- [ ] Reusable component
- [ ] Categories with expand/collapse
- [ ] Search/filter
- [ ] Click to insert
- [ ] Variable descriptions on hover

### 9.3 League Picker
- [ ] Multi-select with checkboxes
- [ ] Grouped by sport
- [ ] Search/filter
- [ ] Show logos

### 9.4 Template Picker
- [ ] Dropdown with template previews
- [ ] Quick create option

### 9.5 Progress Notifications
- [ ] EPG generation progress
- [ ] Cache refresh progress
- [ ] Batch import progress

---

## Implementation Order

1. **Phase 2: Dashboard** - Core landing page with rich tooltips
2. **Phase 8: Settings** - Add EPG dropdown from Dispatcharr
3. **Phase 3: Teams** - Complete list, form, import
4. **Phase 4: Templates** - List first, then the big form
5. **Phase 5: Event Groups** - List, form, import
6. **Phase 6: EPG Management** - Tabbed interface
7. **Phase 7: Channels** - Managed channels with reconciliation
8. **Phase 9: Shared Components** - As needed during above

---

## Backend API Gaps

APIs that may need to be added/updated:

- [ ] `GET /dispatcharr/epg-sources` - For EPG ID dropdown
- [ ] `GET /cache/leagues` - For team import sidebar
- [ ] `GET /cache/leagues/{league}/teams` - For team import
- [ ] `POST /teams/bulk-import` - For team import
- [ ] `GET /dispatcharr/m3u-accounts` - For event group import
- [ ] `GET /dispatcharr/m3u-accounts/{id}/groups` - For event group import
- [ ] `GET /dispatcharr/m3u-accounts/{id}/groups/{id}/streams` - For preview
- [ ] `GET /stats/dashboard` - Aggregated dashboard stats
- [ ] `GET /stats/history` - EPG generation history
