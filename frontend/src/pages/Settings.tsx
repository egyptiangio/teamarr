import { useState, useEffect, useMemo } from "react"
import { toast } from "sonner"
import cronstrue from "cronstrue"
import {
  Loader2,
  Save,
  TestTube,
  Play,
  CheckCircle,
  XCircle,
  Database,
  Plus,
  Trash2,
  Upload,
  ArrowRight,
} from "lucide-react"
import { useGenerationProgress } from "@/contexts/GenerationContext"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import {
  useSettings,
  useUpdateDispatcharrSettings,
  useTestDispatcharrConnection,
  useDispatcharrStatus,
  useDispatcharrEPGSources,
  useUpdateLifecycleSettings,
  useUpdateSchedulerSettings,
  useSchedulerStatus,
  useUpdateEPGSettings,
  useUpdateDurationSettings,
  useUpdateDisplaySettings,
  useUpdateReconciliationSettings,
  useExceptionKeywords,
  useCreateExceptionKeyword,
  useDeleteExceptionKeyword,
} from "@/hooks/useSettings"
import { useCacheStatus, useRefreshCache } from "@/hooks/useEPG"
import type {
  DispatcharrSettings,
  LifecycleSettings,
  SchedulerSettings,
  EPGSettings,
  DurationSettings,
  DisplaySettings,
  ReconciliationSettings,
} from "@/api/settings"

function formatRelativeTime(dateStr: string | null): string {
  if (!dateStr) return "Never"
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffMins < 1) return "Just now"
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  return `${diffDays}d ago`
}

function CronPreview({ expression }: { expression: string }) {
  const humanReadable = useMemo(() => {
    try {
      return cronstrue.toString(expression, {
        throwExceptionOnParseError: false,
        verbose: true,
      })
    } catch {
      return null
    }
  }, [expression])

  if (!humanReadable) {
    return (
      <p className="text-xs text-destructive">Invalid cron expression</p>
    )
  }

  return (
    <p className="text-xs text-muted-foreground">
      {humanReadable}
    </p>
  )
}

type SettingsTab = "general" | "teams" | "events" | "epg" | "integrations" | "advanced"

const TABS: { id: SettingsTab; label: string }[] = [
  { id: "general", label: "General" },
  { id: "teams", label: "Teams" },
  { id: "events", label: "Event Groups" },
  { id: "epg", label: "EPG Generation" },
  { id: "integrations", label: "Integrations" },
  { id: "advanced", label: "Advanced" },
]

export function Settings() {
  const [activeTab, setActiveTab] = useState<SettingsTab>("general")
  const { data: settings, isLoading, error, refetch } = useSettings()
  const dispatcharrStatus = useDispatcharrStatus()
  const epgSourcesQuery = useDispatcharrEPGSources(dispatcharrStatus.data?.connected ?? false)
  const schedulerStatus = useSchedulerStatus()
  const { data: cacheStatus, refetch: refetchCache } = useCacheStatus()
  const refreshCacheMutation = useRefreshCache()
  const { startGeneration, isGenerating } = useGenerationProgress()

  const updateDispatcharr = useUpdateDispatcharrSettings()
  const testConnection = useTestDispatcharrConnection()
  const updateLifecycle = useUpdateLifecycleSettings()
  const updateScheduler = useUpdateSchedulerSettings()
  const updateEPG = useUpdateEPGSettings()
  const updateDurations = useUpdateDurationSettings()
  const updateDisplay = useUpdateDisplaySettings()
  const updateReconciliation = useUpdateReconciliationSettings()

  // Exception keywords
  const keywordsQuery = useExceptionKeywords()
  const createKeyword = useCreateExceptionKeyword()
  const deleteKeyword = useDeleteExceptionKeyword()

  // V1 Migration state
  const [v1DbPath, setV1DbPath] = useState("/v1-data/teamarr.db")
  const [isMigrating, setIsMigrating] = useState<string | null>(null)
  const [migrationResults, setMigrationResults] = useState<{
    templates?: { count: number; items: string[] }
    teams?: { count: number; items: string[] }
    groups?: { count: number; items: string[] }
  } | null>(null)

  // Local form state
  const [dispatcharr, setDispatcharr] = useState<Partial<DispatcharrSettings>>({})
  const [lifecycle, setLifecycle] = useState<LifecycleSettings | null>(null)
  const [scheduler, setScheduler] = useState<SchedulerSettings | null>(null)
  const [epg, setEPG] = useState<EPGSettings | null>(null)
  const [durations, setDurations] = useState<DurationSettings | null>(null)
  const [display, setDisplay] = useState<DisplaySettings | null>(null)
  const [reconciliation, setReconciliation] = useState<ReconciliationSettings | null>(null)
  const [newKeyword, setNewKeyword] = useState({ keywords: "", behavior: "consolidate" })

  // Initialize local state from settings
  useEffect(() => {
    if (settings) {
      setDispatcharr({
        enabled: settings.dispatcharr.enabled,
        url: settings.dispatcharr.url,
        username: settings.dispatcharr.username,
        password: "", // Don't show masked password
        epg_id: settings.dispatcharr.epg_id,
      })
      setLifecycle(settings.lifecycle)
      setScheduler(settings.scheduler)
      setEPG(settings.epg)
      setDurations(settings.durations)
      if ((settings as unknown as { display?: DisplaySettings }).display) {
        setDisplay((settings as unknown as { display: DisplaySettings }).display)
      }
      if ((settings as unknown as { reconciliation?: ReconciliationSettings }).reconciliation) {
        setReconciliation((settings as unknown as { reconciliation: ReconciliationSettings }).reconciliation)
      }
    }
  }, [settings])

  const handleSaveDispatcharr = async () => {
    try {
      // Only send password if it was changed
      const data: Partial<DispatcharrSettings> = {
        enabled: dispatcharr.enabled,
        url: dispatcharr.url,
        username: dispatcharr.username,
        epg_id: dispatcharr.epg_id,
      }
      if (dispatcharr.password) {
        data.password = dispatcharr.password
      }
      await updateDispatcharr.mutateAsync(data)
      toast.success("Dispatcharr settings saved")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save")
    }
  }

  const handleTestConnection = async () => {
    try {
      const result = await testConnection.mutateAsync({
        url: dispatcharr.url || undefined,
        username: dispatcharr.username || undefined,
        password: dispatcharr.password || undefined,
      })
      if (result.success) {
        toast.success(`Connected! ${result.account_count} accounts, ${result.group_count} groups, ${result.channel_count} channels`)
      } else {
        toast.error(result.error || "Connection failed")
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Connection test failed")
    }
  }

  const handleTriggerRun = () => {
    // Use the same streaming endpoint as "Generate EPG" - full workflow with progress
    startGeneration()
  }

  const handleSaveDurations = async () => {
    if (!durations) return
    try {
      await updateDurations.mutateAsync(durations)
      toast.success("Duration settings saved")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save")
    }
  }

  const handleRefreshCache = async () => {
    try {
      const result = await refreshCacheMutation.mutateAsync()
      toast.success(result.message)
      refetchCache()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to start cache refresh")
    }
  }

  const handleSaveDisplay = async () => {
    if (!display) return
    try {
      await updateDisplay.mutateAsync(display)
      toast.success("Display settings saved")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save")
    }
  }

  // Combined save for sections that need both EPG and Display settings
  const handleSaveEPGAndDisplay = async () => {
    try {
      const promises: Promise<unknown>[] = []
      if (display) promises.push(updateDisplay.mutateAsync(display))
      if (epg) promises.push(updateEPG.mutateAsync(epg))
      await Promise.all(promises)
      toast.success("Settings saved")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save")
    }
  }

  // Combined save for event group settings
  const handleSaveEventGroupSettings = async () => {
    try {
      const promises: Promise<unknown>[] = []
      if (epg) promises.push(updateEPG.mutateAsync(epg))
      if (lifecycle) promises.push(updateLifecycle.mutateAsync(lifecycle))
      if (reconciliation) promises.push(updateReconciliation.mutateAsync(reconciliation))
      await Promise.all(promises)
      toast.success("Settings saved")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save")
    }
  }

  // Combined save for scheduler settings
  const handleSaveSchedulerSettings = async () => {
    try {
      const promises: Promise<unknown>[] = []
      if (epg) promises.push(updateEPG.mutateAsync(epg))
      if (scheduler) promises.push(updateScheduler.mutateAsync(scheduler))
      await Promise.all(promises)
      toast.success("Settings saved")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save")
    }
  }

  const handleAddKeyword = async () => {
    if (!newKeyword.keywords.trim()) {
      toast.error("Please enter at least one keyword")
      return
    }
    try {
      await createKeyword.mutateAsync(newKeyword)
      setNewKeyword({ keywords: "", behavior: "consolidate" })
      toast.success("Keyword added")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to add keyword")
    }
  }

  const handleDeleteKeyword = async (id: number) => {
    try {
      await deleteKeyword.mutateAsync(id)
      toast.success("Keyword deleted")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete keyword")
    }
  }

  const handleMigrateV1 = async (type: "templates" | "teams" | "groups" | "all") => {
    if (!v1DbPath.trim()) {
      toast.error("Please enter the path to your V1 database")
      return
    }
    setIsMigrating(type)
    try {
      const endpoint = type === "all"
        ? "/api/v1/templates/migrate-v1-all"
        : `/api/v1/templates/migrate-v1${type === "templates" ? "" : `-${type}`}`

      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ v1_db_path: v1DbPath }),
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || "Migration failed")
      }

      // Update results based on type
      if (type === "all") {
        setMigrationResults({
          templates: { count: data.templates_migrated, items: [] },
          teams: { count: data.teams_migrated, items: [] },
          groups: { count: data.groups_migrated, items: [] },
        })
      } else if (type === "templates") {
        setMigrationResults(prev => ({
          ...prev,
          templates: { count: data.migrated_count, items: data.templates || [] },
        }))
      } else if (type === "teams") {
        setMigrationResults(prev => ({
          ...prev,
          teams: { count: data.migrated_count, items: data.teams || [] },
        }))
      } else if (type === "groups") {
        setMigrationResults(prev => ({
          ...prev,
          groups: { count: data.migrated_count, items: data.groups || [] },
        }))
      }

      if (type === "all") {
        const total = data.templates_migrated + data.teams_migrated + data.groups_migrated
        if (total > 0) {
          toast.success(data.message)
        } else {
          toast.info(data.message)
        }
      } else if (data.migrated_count > 0) {
        toast.success(data.message)
      } else {
        toast.info(data.message)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Migration failed")
    } finally {
      setIsMigrating(null)
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="space-y-2">
        <h1 className="text-xl font-bold">Settings</h1>
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-destructive">Error loading settings: {error.message}</p>
            <Button className="mt-4" onClick={() => refetch()}>
              Retry
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div>
        <h1 className="text-xl font-bold">Settings</h1>
        <p className="text-sm text-muted-foreground">Configure Teamarr application settings</p>
      </div>

      {/* Tab Navigation */}
      <div className="flex gap-1 border-b border-border pb-px">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-3 py-1.5 text-sm font-medium rounded-t transition-colors ${
              activeTab === tab.id
                ? "bg-card text-foreground border border-border border-b-card -mb-px"
                : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="space-y-3 min-h-[400px]">

      {/* General Tab */}
      {activeTab === "general" && (
      <Card>
        <CardHeader>
          <CardTitle>System Settings</CardTitle>
          <CardDescription>Configure timezone, time format, and display preferences</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label htmlFor="epg-timezone">Timezone</Label>
              <Input
                id="epg-timezone"
                value={epg?.epg_timezone ?? "America/New_York"}
                onChange={(e) => epg && setEPG({ ...epg, epg_timezone: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label>Time Format</Label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant={display?.time_format === "12h" ? "default" : "outline"}
                  size="sm"
                  onClick={() => display && setDisplay({ ...display, time_format: "12h" })}
                >
                  12-hour
                </Button>
                <Button
                  type="button"
                  variant={display?.time_format === "24h" ? "default" : "outline"}
                  size="sm"
                  onClick={() => display && setDisplay({ ...display, time_format: "24h" })}
                >
                  24-hour
                </Button>
              </div>
            </div>
            <div className="space-y-2">
              <div className="flex items-center gap-2 pt-6">
                <Switch
                  checked={display?.show_timezone ?? true}
                  onCheckedChange={(checked) =>
                    display && setDisplay({ ...display, show_timezone: checked })
                  }
                />
                <Label>Show Timezone</Label>
              </div>
            </div>
          </div>

          <Button
            onClick={handleSaveEPGAndDisplay}
            disabled={updateDisplay.isPending || updateEPG.isPending}
          >
            {(updateDisplay.isPending || updateEPG.isPending) ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-1" />
            )}
            Save
          </Button>
        </CardContent>
      </Card>
      )}

      {/* Teams Tab */}
      {activeTab === "teams" && (
      <Card>
        <CardHeader>
          <CardTitle>Team Based Streams</CardTitle>
          <CardDescription>Configure settings for team-based EPG generation</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label htmlFor="team-schedule-days">Schedule Days Ahead</Label>
              <Select
                id="team-schedule-days"
                value={String(epg?.team_schedule_days_ahead ?? 30)}
                onChange={(e) =>
                  epg && setEPG({ ...epg, team_schedule_days_ahead: parseInt(e.target.value) })
                }
              >
                <option value="7">7 days</option>
                <option value="14">14 days</option>
                <option value="30">30 days</option>
                <option value="60">60 days</option>
                <option value="90">90 days</option>
              </Select>
              <p className="text-xs text-muted-foreground">
                How far to fetch team schedules (for .next variables)
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="midnight-mode">Midnight Crossover</Label>
              <Select
                id="midnight-mode"
                value={epg?.midnight_crossover_mode ?? "postgame"}
                onChange={(e) =>
                  epg && setEPG({ ...epg, midnight_crossover_mode: e.target.value })
                }
              >
                <option value="postgame">Show postgame filler</option>
                <option value="idle">Show idle filler</option>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="channel-id-format">Channel ID Format</Label>
              <Input
                id="channel-id-format"
                value={display?.channel_id_format ?? "{team_name_pascal}.{league}"}
                onChange={(e) => display && setDisplay({ ...display, channel_id_format: e.target.value })}
                className="font-mono text-sm"
              />
              <p className="text-xs text-muted-foreground">
                {"{team_name}"}, {"{league}"}, {"{league_id}"}
              </p>
            </div>
          </div>

          <Button
            onClick={handleSaveEPGAndDisplay}
            disabled={updateEPG.isPending || updateDisplay.isPending}
          >
            {(updateEPG.isPending || updateDisplay.isPending) ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-1" />
            )}
            Save
          </Button>
        </CardContent>
      </Card>
      )}

      {/* Event Groups Tab */}
      {activeTab === "events" && (
      <Card>
        <CardHeader>
          <CardTitle>Event Based Streams</CardTitle>
          <CardDescription>Configure settings for event-based EPG generation (Event Groups)</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label htmlFor="event-lookahead">Event Lookahead</Label>
              <Select
                id="event-lookahead"
                value={String(epg?.event_match_days_ahead ?? 3)}
                onChange={(e) =>
                  epg && setEPG({ ...epg, event_match_days_ahead: parseInt(e.target.value) })
                }
              >
                <option value="1">1 day</option>
                <option value="3">3 days</option>
                <option value="7">7 days</option>
                <option value="14">14 days</option>
                <option value="30">30 days</option>
              </Select>
              <p className="text-xs text-muted-foreground">
                How far ahead to match streams to events
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="create-timing">Channel Create Timing</Label>
              <Select
                id="create-timing"
                value={lifecycle?.channel_create_timing ?? "same_day"}
                onChange={(e) =>
                  lifecycle && setLifecycle({ ...lifecycle, channel_create_timing: e.target.value })
                }
              >
                <option value="stream_available">When stream available</option>
                <option value="same_day">Same day</option>
                <option value="day_before">Day before</option>
                <option value="2_days_before">2 days before</option>
                <option value="3_days_before">3 days before</option>
                <option value="1_week_before">1 week before</option>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="delete-timing">Channel Delete Timing</Label>
              <Select
                id="delete-timing"
                value={lifecycle?.channel_delete_timing ?? "day_after"}
                onChange={(e) =>
                  lifecycle && setLifecycle({ ...lifecycle, channel_delete_timing: e.target.value })
                }
              >
                <option value="stream_removed">When stream removed</option>
                <option value="6_hours_after">6 hours after end of event</option>
                <option value="same_day">Same day</option>
                <option value="day_after">Day after</option>
                <option value="2_days_after">2 days after</option>
                <option value="3_days_after">3 days after</option>
                <option value="1_week_after">1 week after</option>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label htmlFor="duplicate-handling">Duplicate Handling</Label>
              <Select
                id="duplicate-handling"
                value={reconciliation?.default_duplicate_event_handling ?? "consolidate"}
                onChange={(e) =>
                  reconciliation && setReconciliation({ ...reconciliation, default_duplicate_event_handling: e.target.value })
                }
              >
                <option value="consolidate">Consolidate</option>
                <option value="separate">Separate</option>
                <option value="ignore">Ignore</option>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="channel-range-start">Channel Range Start</Label>
              <Input
                id="channel-range-start"
                type="number"
                min={1}
                value={lifecycle?.channel_range_start ?? 101}
                onChange={(e) =>
                  lifecycle &&
                  setLifecycle({
                    ...lifecycle,
                    channel_range_start: parseInt(e.target.value) || 101,
                  })
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="channel-range-end">Channel Range End</Label>
              <Input
                id="channel-range-end"
                type="number"
                min={1}
                value={lifecycle?.channel_range_end ?? ""}
                onChange={(e) =>
                  lifecycle &&
                  setLifecycle({
                    ...lifecycle,
                    channel_range_end: e.target.value ? parseInt(e.target.value) : null,
                  })
                }
                placeholder="No limit"
              />
            </div>
          </div>

          {/* Exception Keywords Table */}
          {reconciliation?.default_duplicate_event_handling === "consolidate" && (
            <div className="space-y-3 pt-3 border-t">
              <Label>Exception Keywords</Label>
              <p className="text-xs text-muted-foreground">
                Streams matching these keywords get special handling during consolidation
              </p>

              <div className="border rounded-md">
                <table className="w-full text-sm">
                  <thead className="bg-muted">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium">Keywords (comma-separated)</th>
                      <th className="px-3 py-2 text-left font-medium w-40">Behavior</th>
                      <th className="px-3 py-2 w-12"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {keywordsQuery.data?.keywords.map((kw) => (
                      <tr key={kw.id} className="border-t">
                        <td className="px-3 py-2">{kw.keywords}</td>
                        <td className="px-3 py-2">
                          <Select
                            value={kw.behavior}
                            onChange={async (e) => {
                              const newBehavior = e.target.value
                              try {
                                await fetch(`/api/v1/keywords/${kw.id}`, {
                                  method: "PUT",
                                  headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify({ behavior: newBehavior }),
                                })
                                keywordsQuery.refetch()
                                toast.success(`Updated behavior to "${newBehavior}"`)
                              } catch (err) {
                                toast.error("Failed to update keyword behavior")
                              }
                            }}
                            className="w-40 h-8"
                          >
                            <option value="consolidate">Sub-Consolidate</option>
                            <option value="separate">Separate</option>
                            <option value="ignore">Ignore</option>
                          </Select>
                        </td>
                        <td className="px-3 py-2">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleDeleteKeyword(kw.id)}
                            disabled={deleteKeyword.isPending}
                          >
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </td>
                      </tr>
                    ))}
                    {(!keywordsQuery.data?.keywords || keywordsQuery.data.keywords.length === 0) && (
                      <tr>
                        <td colSpan={3} className="px-3 py-4 text-center text-muted-foreground">
                          No exception keywords defined
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              <div className="flex gap-2">
                <Input
                  placeholder="e.g., Spanish, En Español, ESP"
                  value={newKeyword.keywords}
                  onChange={(e) => setNewKeyword({ ...newKeyword, keywords: e.target.value })}
                  className="flex-1"
                />
                <Select
                  value={newKeyword.behavior}
                  onChange={(e) => setNewKeyword({ ...newKeyword, behavior: e.target.value })}
                  className="w-40"
                >
                  <option value="consolidate">Sub-Consolidate</option>
                  <option value="separate">Separate</option>
                  <option value="ignore">Ignore</option>
                </Select>
                <Button onClick={handleAddKeyword} disabled={createKeyword.isPending}>
                  {createKeyword.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Plus className="h-4 w-4" />
                  )}
                </Button>
              </div>
            </div>
          )}

          <Button
            onClick={handleSaveEventGroupSettings}
            disabled={updateEPG.isPending || updateLifecycle.isPending || updateReconciliation.isPending}
          >
            {(updateEPG.isPending || updateLifecycle.isPending || updateReconciliation.isPending) ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-1" />
            )}
            Save
          </Button>
        </CardContent>
      </Card>
      )}

      {/* EPG Generation Tab */}
      {activeTab === "epg" && (
      <>
      <Card>
        <CardHeader>
          <CardTitle>EPG Generation</CardTitle>
          <CardDescription>Configure EPG output and scheduling</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label htmlFor="epg-output-path">Output Path</Label>
              <Input
                id="epg-output-path"
                value={epg?.epg_output_path ?? "./teamarr.xml"}
                onChange={(e) => epg && setEPG({ ...epg, epg_output_path: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="epg-days-ahead">Output Days Ahead</Label>
              <Input
                id="epg-days-ahead"
                type="number"
                min={1}
                value={epg?.epg_output_days_ahead ?? 14}
                onChange={(e) =>
                  epg && setEPG({ ...epg, epg_output_days_ahead: parseInt(e.target.value) || 14 })
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="epg-lookback">Lookback Hours</Label>
              <Input
                id="epg-lookback"
                type="number"
                min={0}
                value={epg?.epg_lookback_hours ?? 6}
                onChange={(e) =>
                  epg && setEPG({ ...epg, epg_lookback_hours: parseInt(e.target.value) || 6 })
                }
              />
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Switch
              checked={epg?.include_final_events ?? false}
              onCheckedChange={(checked) =>
                epg && setEPG({ ...epg, include_final_events: checked })
              }
            />
            <Label>Include completed/final events in EPG</Label>
          </div>

          {/* Scheduled Generation */}
          <div className="space-y-4 pt-2 border-t">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Switch
                  checked={scheduler?.enabled ?? false}
                  onCheckedChange={(checked) =>
                    scheduler && setScheduler({ ...scheduler, enabled: checked })
                  }
                />
                <Label>Enable Scheduled Generation</Label>
              </div>
              <Badge variant={schedulerStatus.data?.running ? "success" : "secondary"}>
                {schedulerStatus.data?.running ? "Running" : "Stopped"}
              </Badge>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="cron-expression">Cron Expression</Label>
                <Input
                  id="cron-expression"
                  value={epg?.cron_expression ?? "0 * * * *"}
                  onChange={(e) => epg && setEPG({ ...epg, cron_expression: e.target.value })}
                  className="font-mono"
                  placeholder="0 * * * *"
                />
                <CronPreview expression={epg?.cron_expression ?? "0 * * * *"} />
              </div>
              <div className="space-y-2">
                <Label>Last Run</Label>
                <p className="text-sm text-muted-foreground pt-2">
                  {schedulerStatus.data?.last_run ?? "Never"}
                </p>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => epg && setEPG({ ...epg, cron_expression: "0 * * * *" })}
              >
                Every Hour
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => epg && setEPG({ ...epg, cron_expression: "0 */2 * * *" })}
              >
                Every 2 Hours
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => epg && setEPG({ ...epg, cron_expression: "0 */4 * * *" })}
              >
                Every 4 Hours
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => epg && setEPG({ ...epg, cron_expression: "0 */6 * * *" })}
              >
                Every 6 Hours
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => epg && setEPG({ ...epg, cron_expression: "0 0 * * *" })}
              >
                Daily at Midnight
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => epg && setEPG({ ...epg, cron_expression: "0 6 * * *" })}
              >
                Daily at 6 AM
              </Button>
            </div>
          </div>

          <div className="flex gap-2">
            <Button onClick={handleTriggerRun} variant="outline" disabled={isGenerating}>
              {isGenerating ? (
                <Loader2 className="h-4 w-4 mr-1 animate-spin" />
              ) : (
                <Play className="h-4 w-4 mr-1" />
              )}
              Run Now
            </Button>
            <Button
              onClick={handleSaveSchedulerSettings}
              disabled={updateEPG.isPending || updateScheduler.isPending}
            >
              {(updateEPG.isPending || updateScheduler.isPending) ? (
                <Loader2 className="h-4 w-4 mr-1 animate-spin" />
              ) : (
                <Save className="h-4 w-4 mr-1" />
              )}
              Save
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Game Durations</CardTitle>
          <CardDescription>Default game durations by sport (in hours)</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-4 gap-4">
            {durations &&
              Object.entries(durations).map(([sport, hours]) => (
                <div key={sport} className="space-y-1">
                  <Label htmlFor={`duration-${sport}`} className="capitalize">
                    {sport}
                  </Label>
                  <Input
                    id={`duration-${sport}`}
                    type="number"
                    step="0.5"
                    min={0.5}
                    value={hours}
                    onChange={(e) =>
                      setDurations({
                        ...durations,
                        [sport]: parseFloat(e.target.value) || 3,
                      })
                    }
                  />
                </div>
              ))}
          </div>

          <Button onClick={handleSaveDurations} disabled={updateDurations.isPending}>
            {updateDurations.isPending ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-1" />
            )}
            Save
          </Button>
        </CardContent>
      </Card>
      </>
      )}

      {/* Integrations Tab */}
      {activeTab === "integrations" && (
      <>
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Dispatcharr Integration</CardTitle>
              <CardDescription>Configure connection to Dispatcharr for channel management</CardDescription>
            </div>
            <div className="flex items-center gap-2">
              {dispatcharrStatus.data?.connected ? (
                <Badge variant="success" className="gap-1">
                  <CheckCircle className="h-3 w-3" /> Connected
                </Badge>
              ) : dispatcharrStatus.data?.configured ? (
                <Badge variant="warning" className="gap-1">
                  <XCircle className="h-3 w-3" /> Disconnected
                </Badge>
              ) : (
                <Badge variant="secondary">Not Configured</Badge>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2">
            <Switch
              checked={dispatcharr.enabled ?? false}
              onCheckedChange={(checked) => setDispatcharr({ ...dispatcharr, enabled: checked })}
            />
            <Label>Enable Dispatcharr Integration</Label>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="dispatcharr-url">URL</Label>
              <Input
                id="dispatcharr-url"
                value={dispatcharr.url ?? ""}
                onChange={(e) => setDispatcharr({ ...dispatcharr, url: e.target.value })}
                placeholder="http://localhost:5000"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="dispatcharr-epg">EPG Source</Label>
              <Select
                id="dispatcharr-epg"
                value={dispatcharr.epg_id?.toString() ?? ""}
                onChange={(e) =>
                  setDispatcharr({
                    ...dispatcharr,
                    epg_id: e.target.value ? parseInt(e.target.value) : null,
                  })
                }
                disabled={!dispatcharrStatus.data?.connected}
              >
                <option value="">Select EPG source...</option>
                {epgSourcesQuery.data?.sources?.map((source) => (
                  <option key={source.id} value={source.id}>
                    {source.name} ({source.source_type})
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="dispatcharr-username">Username</Label>
              <Input
                id="dispatcharr-username"
                value={dispatcharr.username ?? ""}
                onChange={(e) => setDispatcharr({ ...dispatcharr, username: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="dispatcharr-password">Password</Label>
              <Input
                id="dispatcharr-password"
                type="password"
                value={dispatcharr.password ?? ""}
                onChange={(e) => setDispatcharr({ ...dispatcharr, password: e.target.value })}
                placeholder="Leave blank to keep current"
              />
            </div>
          </div>

          <div className="flex gap-2">
            <Button onClick={handleTestConnection} variant="outline" disabled={testConnection.isPending}>
              {testConnection.isPending ? (
                <Loader2 className="h-4 w-4 mr-1 animate-spin" />
              ) : (
                <TestTube className="h-4 w-4 mr-1" />
              )}
              Test Connection
            </Button>
            <Button onClick={handleSaveDispatcharr} disabled={updateDispatcharr.isPending}>
              {updateDispatcharr.isPending ? (
                <Loader2 className="h-4 w-4 mr-1 animate-spin" />
              ) : (
                <Save className="h-4 w-4 mr-1" />
              )}
              Save
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Database className="h-5 w-5" />
                Local Caching
              </CardTitle>
              <CardDescription>Cache of teams and leagues from ESPN and TheSportsDB</CardDescription>
            </div>
            {cacheStatus?.is_stale && (
              <Badge variant="warning">Stale</Badge>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-4 gap-4">
            <div className="text-center">
              <div className="text-2xl font-bold">{cacheStatus?.leagues_count ?? 0}</div>
              <div className="text-xs text-muted-foreground">Leagues</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold">{cacheStatus?.teams_count ?? 0}</div>
              <div className="text-xs text-muted-foreground">Teams</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold">
                {cacheStatus?.refresh_duration_seconds
                  ? `${cacheStatus.refresh_duration_seconds.toFixed(1)}s`
                  : "-"}
              </div>
              <div className="text-xs text-muted-foreground">Last Refresh Duration</div>
            </div>
            <div className="text-center">
              <div className="text-sm font-medium">
                {formatRelativeTime(cacheStatus?.last_refresh ?? null)}
              </div>
              <div className="text-xs text-muted-foreground">Last Refresh</div>
            </div>
          </div>

          {cacheStatus?.is_empty && (
            <div className="text-center py-2 text-muted-foreground">
              Cache is empty. Refresh to populate with teams and leagues.
            </div>
          )}

          {cacheStatus?.last_error && (
            <div className="text-sm text-destructive">
              Last error: {cacheStatus.last_error}
            </div>
          )}

          <Button
            onClick={handleRefreshCache}
            disabled={refreshCacheMutation.isPending || cacheStatus?.refresh_in_progress}
            className="w-full"
          >
            {(refreshCacheMutation.isPending || cacheStatus?.refresh_in_progress) && (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            )}
            {cacheStatus?.refresh_in_progress ? "Refreshing..." : "Refresh Cache"}
          </Button>

          {/* TSDB API Key */}
          <div className="space-y-2 pt-4 border-t">
            <Label htmlFor="tsdb-api-key">TheSportsDB API Key (Optional)</Label>
            <Input
              id="tsdb-api-key"
              type="password"
              value={display?.tsdb_api_key ?? ""}
              onChange={(e) => display && setDisplay({ ...display, tsdb_api_key: e.target.value })}
              placeholder="Leave blank to use free tier"
            />
            <p className="text-xs text-muted-foreground">
              Premium key ($9/mo) gives higher rate limits. Free tier works for most users.
              Get a key at <a href="https://www.thesportsdb.com/pricing" target="_blank" rel="noopener noreferrer" className="underline">thesportsdb.com/pricing</a>
            </p>
          </div>

          <Button onClick={handleSaveDisplay} disabled={updateDisplay.isPending}>
            {updateDisplay.isPending ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-1" />
            )}
            Save API Key
          </Button>
        </CardContent>
      </Card>
      </>
      )}

      {/* Advanced Tab */}
      {activeTab === "advanced" && (
      <>
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Upload className="h-5 w-5" />
                Import from V1
              </CardTitle>
              <CardDescription>Migrate templates, teams, and event groups from Teamarr V1</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="bg-muted/50 rounded-lg p-4 space-y-3">
            <p className="text-sm font-medium">Setup Instructions:</p>
            <ol className="text-sm text-muted-foreground list-decimal list-inside space-y-1">
              <li>Add a volume mount in docker-compose.yml: <code className="bg-secondary px-1 rounded">- ./v1-data:/v1-data:ro</code></li>
              <li>Copy your V1 <code className="bg-secondary px-1 rounded">teamarr.db</code> to the v1-data folder</li>
              <li>Enter the container path below and run migration</li>
            </ol>
          </div>

          <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-4 space-y-2">
            <p className="text-sm font-medium text-blue-400">Migration Order (Recommended):</p>
            <ol className="text-sm text-muted-foreground list-decimal list-inside space-y-1">
              <li><strong>Templates first</strong> — Creates the templates that teams and groups reference</li>
              <li><strong>Teams second</strong> — Imports team channels with template assignments</li>
              <li><strong>Event Groups last</strong> — Imports groups with parent/child relationships</li>
            </ol>
            <p className="text-xs text-muted-foreground mt-2">
              Or use "Migrate All" to run all three in the correct order automatically.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="v1-db-path">V1 Database Path</Label>
            <Input
              id="v1-db-path"
              value={v1DbPath}
              onChange={(e) => setV1DbPath(e.target.value)}
              placeholder="/v1-data/teamarr.db"
              className="font-mono text-sm"
            />
          </div>

          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => handleMigrateV1("all")}
              disabled={!!isMigrating}
            >
              {isMigrating === "all" ? (
                <Loader2 className="h-4 w-4 mr-1 animate-spin" />
              ) : (
                <ArrowRight className="h-4 w-4 mr-1" />
              )}
              Migrate All
            </Button>
            <Button
              variant="outline"
              onClick={() => handleMigrateV1("templates")}
              disabled={!!isMigrating}
            >
              {isMigrating === "templates" ? (
                <Loader2 className="h-4 w-4 mr-1 animate-spin" />
              ) : null}
              Templates Only
            </Button>
            <Button
              variant="outline"
              onClick={() => handleMigrateV1("teams")}
              disabled={!!isMigrating}
            >
              {isMigrating === "teams" ? (
                <Loader2 className="h-4 w-4 mr-1 animate-spin" />
              ) : null}
              Teams Only
            </Button>
            <Button
              variant="outline"
              onClick={() => handleMigrateV1("groups")}
              disabled={!!isMigrating}
            >
              {isMigrating === "groups" ? (
                <Loader2 className="h-4 w-4 mr-1 animate-spin" />
              ) : null}
              Event Groups Only
            </Button>
          </div>

          {migrationResults && (
            <div className="bg-secondary/50 rounded-lg p-3 space-y-2">
              <p className="text-sm font-medium">Migration Results:</p>
              <div className="grid grid-cols-3 gap-4 text-sm">
                <div>
                  <span className="text-muted-foreground">Templates:</span>{" "}
                  <span className="font-medium">{migrationResults.templates?.count ?? 0}</span>
                </div>
                <div>
                  <span className="text-muted-foreground">Teams:</span>{" "}
                  <span className="font-medium">{migrationResults.teams?.count ?? 0}</span>
                </div>
                <div>
                  <span className="text-muted-foreground">Event Groups:</span>{" "}
                  <span className="font-medium">{migrationResults.groups?.count ?? 0}</span>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Advanced Settings</CardTitle>
          <CardDescription>XMLTV generator metadata</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="xmltv-name">XMLTV Generator Name</Label>
              <Input
                id="xmltv-name"
                value={display?.xmltv_generator_name ?? "Teamarr v2"}
                onChange={(e) => display && setDisplay({ ...display, xmltv_generator_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="xmltv-url">XMLTV Generator URL</Label>
              <Input
                id="xmltv-url"
                value={display?.xmltv_generator_url ?? ""}
                onChange={(e) => display && setDisplay({ ...display, xmltv_generator_url: e.target.value })}
                placeholder="https://github.com/..."
              />
            </div>
          </div>

          <Button onClick={handleSaveDisplay} disabled={updateDisplay.isPending}>
            {updateDisplay.isPending ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-1" />
            )}
            Save
          </Button>
        </CardContent>
      </Card>
      </>
      )}

      </div>
    </div>
  )
}
