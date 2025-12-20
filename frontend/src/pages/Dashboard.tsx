import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { api } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Quadrant, StatTile } from "@/components/ui/rich-tooltip"
import {
  RefreshCw,
  Rocket,
  FileText,
  Plus,
  Download,
} from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"

// Types for dashboard stats
interface LeagueBreakdown {
  league: string
  logo_url: string | null
  count: number
}

interface GroupBreakdown {
  name: string
  matched: number
  total: number
}

interface DashboardStats {
  teams: {
    total: number
    active: number
    assigned: number
    leagues: LeagueBreakdown[]
  }
  event_groups: {
    total: number
    streams_total: number
    streams_matched: number
    match_percent: number
    leagues: LeagueBreakdown[]
    groups: GroupBreakdown[]
  }
  epg: {
    channels_total: number
    channels_team: number
    channels_event: number
    events_total: number
    events_team: number
    events_event: number
    filler_total: number
    filler_pregame: number
    filler_postgame: number
    filler_idle: number
    programmes_total: number
  }
  channels: {
    active: number
    with_logos: number
    groups: number
    deleted_24h: number
    group_breakdown: { name: string; count: number }[]
  }
}

interface EPGHistoryEntry {
  id: number
  run_type: string
  started_at: string
  completed_at: string | null
  duration_ms: number | null
  status: string
  programmes: {
    total: number
    events: number
    pregame: number
    postgame: number
    idle: number
  }
}

// Fetch dashboard stats
async function fetchDashboardStats(): Promise<DashboardStats> {
  return api.get("/stats/dashboard")
}

// Fetch EPG history (recent runs)
async function fetchEPGHistory(): Promise<EPGHistoryEntry[]> {
  const response = await api.get<{ runs: EPGHistoryEntry[]; count: number }>("/stats/runs?limit=10")
  return response.runs || []
}

// Fetch counts for entities
async function fetchCounts() {
  const [teams, groups, templates] = await Promise.all([
    api.get<unknown[]>("/teams"),
    api.get<{ groups: unknown[]; total: number }>("/groups"),
    api.get<unknown[]>("/templates"),
  ])
  return {
    teams: Array.isArray(teams) ? teams.length : 0,
    groups: groups?.total ?? 0,
    templates: Array.isArray(templates) ? templates.length : 0,
  }
}

export function Dashboard() {
  const navigate = useNavigate()

  // Fetch dashboard stats
  const statsQuery = useQuery({
    queryKey: ["dashboard-stats"],
    queryFn: fetchDashboardStats,
    retry: false,
  })

  // Fetch entity counts (fallback if stats endpoint doesn't exist)
  const countsQuery = useQuery({
    queryKey: ["entity-counts"],
    queryFn: fetchCounts,
  })

  // Fetch EPG history
  const historyQuery = useQuery({
    queryKey: ["epg-history"],
    queryFn: fetchEPGHistory,
    retry: false,
  })

  const handleGenerateEPG = async () => {
    toast.loading("Generating EPG...", { id: "epg-generate" })
    try {
      const result = await api.post<{ programmes_count: number }>("/epg/generate")
      toast.success(`Generated ${result.programmes_count} programmes`, {
        id: "epg-generate",
      })
      statsQuery.refetch()
      historyQuery.refetch()
    } catch {
      toast.error("EPG generation failed", { id: "epg-generate" })
    }
  }

  const handleRefreshCache = async () => {
    toast.loading("Refreshing cache...", { id: "cache-refresh" })
    try {
      await api.post("/cache/refresh")
      toast.success("Cache refresh complete", { id: "cache-refresh" })
    } catch {
      toast.error("Cache refresh failed", { id: "cache-refresh" })
    }
  }

  const stats = statsQuery.data
  const counts = countsQuery.data
  const history = historyQuery.data

  // Check if we're in "getting started" mode
  const isGettingStarted =
    (counts?.teams ?? 0) === 0 && (counts?.templates ?? 0) === 0

  return (
    <div className="space-y-4">
      {/* Page Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Dashboard</h1>
          <p className="text-muted-foreground text-sm">
            Overview of your EPG system
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <span className="text-xs text-muted-foreground uppercase tracking-wide">
            Quick Actions
          </span>
          <div className="flex items-center gap-2 p-2 border rounded-lg bg-muted/30">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => navigate("/templates/new")}
            >
              <FileText className="h-4 w-4 mr-1" />
              Create Template
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => navigate("/teams/import")}
            >
              <Download className="h-4 w-4 mr-1" />
              Import Teams
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => navigate("/event-groups/import")}
            >
              <Plus className="h-4 w-4 mr-1" />
              Import Event Group
            </Button>
            <Button size="sm" onClick={handleGenerateEPG}>
              <Rocket className="h-4 w-4 mr-1" />
              Generate EPG
            </Button>
          </div>
        </div>
      </div>

      {/* 4 Quadrants Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Teams Quadrant */}
        <Quadrant
          title="Teams"
          onManageClick={() => navigate("/teams")}
        >
          <StatTile
            value={stats?.teams.total ?? counts?.teams ?? "—"}
            label="Total"
          />
          <StatTile
            value={stats?.teams.leagues?.length ?? "—"}
            label="Leagues"
            tooltipTitle="Leagues"
            tooltipRows={stats?.teams.leagues?.map((l) => ({
              label: l.league,
              value: l.count,
              logo: l.logo_url ?? undefined,
            }))}
          />
          <StatTile
            value={stats?.teams.active ?? "—"}
            label="Active"
          />
          <StatTile
            value={stats?.teams.assigned ?? "—"}
            label="Assigned"
          />
        </Quadrant>

        {/* Event Groups Quadrant */}
        <Quadrant
          title="Event Groups"
          onManageClick={() => navigate("/event-groups")}
        >
          <StatTile
            value={stats?.event_groups.total ?? counts?.groups ?? "—"}
            label="Groups"
            tooltipTitle="Event Groups"
            tooltipRows={stats?.event_groups.groups?.map((g) => ({
              label: g.name,
              value: `${g.matched}/${g.total}`,
            }))}
          />
          <StatTile
            value={stats?.event_groups.leagues?.length ?? "—"}
            label="Leagues"
            tooltipTitle="Leagues"
            tooltipRows={stats?.event_groups.leagues?.map((l) => ({
              label: l.league,
              value: l.count,
              logo: l.logo_url ?? undefined,
            }))}
          />
          <StatTile
            value={stats?.event_groups.streams_total ?? "—"}
            label="Streams"
          />
          <StatTile
            value={stats?.event_groups.streams_matched ?? "—"}
            label="Matched"
            sublabel={
              stats?.event_groups.match_percent
                ? `${stats.event_groups.match_percent}%`
                : undefined
            }
            tooltipTitle="Match Rate by Group"
            tooltipRows={stats?.event_groups.groups?.map((g) => ({
              label: g.name,
              value: `${g.matched}/${g.total} (${Math.round((g.matched / g.total) * 100) || 0}%)`,
            }))}
          />
        </Quadrant>

        {/* EPG Quadrant */}
        <Quadrant
          title="EPG"
          onManageClick={() => navigate("/epg")}
        >
          <StatTile
            value={stats?.epg.channels_total ?? "—"}
            label="Channels"
            tooltipTitle="Breakdown"
            tooltipRows={[
              { label: "Team-based", value: stats?.epg.channels_team ?? 0 },
              { label: "Event-based", value: stats?.epg.channels_event ?? 0 },
            ]}
          />
          <StatTile
            value={stats?.epg.events_total ?? "—"}
            label="Events"
            tooltipTitle="Breakdown"
            tooltipRows={[
              { label: "Team-based", value: stats?.epg.events_team ?? 0 },
              { label: "Event-based", value: stats?.epg.events_event ?? 0 },
            ]}
          />
          <StatTile
            value={stats?.epg.filler_total ?? "—"}
            label="Filler"
            tooltipTitle="Breakdown"
            tooltipRows={[
              { label: "Pregame", value: stats?.epg.filler_pregame ?? 0 },
              { label: "Postgame", value: stats?.epg.filler_postgame ?? 0 },
              { label: "Idle", value: stats?.epg.filler_idle ?? 0 },
            ]}
          />
          <StatTile
            value={stats?.epg.programmes_total ?? "—"}
            label="Total"
          />
        </Quadrant>

        {/* Channels Quadrant */}
        <Quadrant
          title="Channels"
          onManageClick={() => navigate("/channels")}
        >
          <StatTile
            value={stats?.channels.active ?? "—"}
            label="Active"
          />
          <StatTile
            value={stats?.channels.with_logos ?? "—"}
            label="Logos"
          />
          <StatTile
            value={stats?.channels.groups ?? "—"}
            label="Groups"
            tooltipTitle="Channel Groups"
            tooltipRows={stats?.channels.group_breakdown?.map((g) => ({
              label: g.name,
              value: g.count,
            }))}
          />
          <StatTile
            value={stats?.channels.deleted_24h ?? "—"}
            label="Deleted 24h"
          />
        </Quadrant>
      </div>

      {/* EPG Generation History */}
      {history && history.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold mb-3">EPG Generation History</h2>
          <div className="border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-muted">
                <tr>
                  <th className="text-left p-3 font-medium">Generated At</th>
                  <th className="text-center p-3 font-medium">Type</th>
                  <th className="text-center p-3 font-medium">Events</th>
                  <th className="text-center p-3 font-medium">Filler</th>
                  <th className="text-center p-3 font-medium">Total</th>
                  <th className="text-center p-3 font-medium">Duration</th>
                  <th className="text-center p-3 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {history.map((entry) => (
                  <tr key={entry.id} className="border-t hover:bg-muted/30">
                    <td className="p-3">
                      {new Date(entry.started_at).toLocaleString()}
                    </td>
                    <td className="text-center p-3 text-xs text-muted-foreground">
                      {entry.run_type === "team_epg" ? "Team" : "Event"}
                    </td>
                    <td className="text-center p-3">{entry.programmes?.events ?? 0}</td>
                    <td className="text-center p-3 text-muted-foreground">
                      {(entry.programmes?.pregame ?? 0) + (entry.programmes?.postgame ?? 0) + (entry.programmes?.idle ?? 0)}
                    </td>
                    <td className="text-center p-3 font-medium">{entry.programmes?.total ?? 0}</td>
                    <td className="text-center p-3">
                      {entry.duration_ms
                        ? entry.duration_ms >= 60000
                          ? `${Math.floor(entry.duration_ms / 60000)}m ${Math.round((entry.duration_ms % 60000) / 1000)}s`
                          : `${Math.round(entry.duration_ms / 1000)}s`
                        : "—"}
                    </td>
                    <td className="text-center p-3">
                      <span
                        className={cn(
                          "inline-block px-2 py-0.5 rounded text-xs font-medium",
                          entry.status === "completed" &&
                            "bg-green-500/20 text-green-600",
                          entry.status === "error" &&
                            "bg-red-500/20 text-red-600",
                          entry.status === "running" &&
                            "bg-yellow-500/20 text-yellow-600"
                        )}
                      >
                        {entry.status === "completed" && "✓"}
                        {entry.status === "error" && "✗"}
                        {entry.status === "running" && "⏳"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Getting Started Guide */}
      {isGettingStarted && (
        <div className="border border-primary/50 bg-primary/5 rounded-lg p-6">
          <h3 className="text-lg font-semibold text-primary mb-4">
            Getting Started
          </h3>
          <div className="grid md:grid-cols-2 gap-6">
            <div>
              <h4 className="font-medium text-sm text-foreground mb-2">1. Configure Settings</h4>
              <ul className="space-y-1 text-sm text-muted-foreground list-disc list-inside">
                <li>Connect to Dispatcharr (optional but recommended)</li>
                <li>Set EPG output path and timezone</li>
                <li>Configure sport durations</li>
              </ul>
              <Button
                variant="link"
                size="sm"
                className="px-0 mt-1"
                onClick={() => navigate("/settings")}
              >
                Go to Settings →
              </Button>
            </div>
            <div>
              <h4 className="font-medium text-sm text-foreground mb-2">2. Create Templates</h4>
              <ul className="space-y-1 text-sm text-muted-foreground list-disc list-inside">
                <li>Define title/description formats using variables</li>
                <li>Configure pregame/postgame filler</li>
                <li>Create templates for team EPG and/or event EPG</li>
              </ul>
              <Button
                variant="link"
                size="sm"
                className="px-0 mt-1"
                onClick={() => navigate("/templates")}
              >
                Go to Templates →
              </Button>
            </div>
            <div>
              <h4 className="font-medium text-sm text-foreground mb-2">3. Add Teams (Team EPG)</h4>
              <ul className="space-y-1 text-sm text-muted-foreground list-disc list-inside">
                <li>Import teams from the cache by league</li>
                <li>Assign templates to teams</li>
                <li>Each team gets its own channel</li>
              </ul>
              <Button
                variant="link"
                size="sm"
                className="px-0 mt-1"
                onClick={() => navigate("/teams/import")}
              >
                Import Teams →
              </Button>
            </div>
            <div>
              <h4 className="font-medium text-sm text-foreground mb-2">4. Create Event Groups (Event EPG)</h4>
              <ul className="space-y-1 text-sm text-muted-foreground list-disc list-inside">
                <li>Import groups from M3U accounts</li>
                <li>Match streams to sports events</li>
                <li>Dynamic channels based on live events</li>
              </ul>
              <Button
                variant="link"
                size="sm"
                className="px-0 mt-1"
                onClick={() => navigate("/event-groups/import")}
              >
                Import Event Groups →
              </Button>
            </div>
          </div>
          <div className="mt-4 pt-4 border-t border-primary/20">
            <p className="text-sm text-muted-foreground">
              <strong className="text-foreground">Finally:</strong> Click{" "}
              <span className="inline-flex items-center gap-1">
                <Rocket className="h-3 w-3" /> Generate EPG
              </span>{" "}
              to create your XMLTV file.
            </p>
          </div>
        </div>
      )}

      {/* Cache Refresh Button */}
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={handleRefreshCache}>
          <RefreshCw className="h-4 w-4 mr-2" />
          Refresh Cache
        </Button>
      </div>
    </div>
  )
}
