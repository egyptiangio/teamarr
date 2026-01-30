import { useState, useEffect, useMemo, useRef } from "react"
import { toast } from "sonner"
import cronstrue from "cronstrue"
import { getSportDisplayName } from "@/lib/utils"
import {
  Loader2,
  Save,
  TestTube,
  Play,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Database,
  Plus,
  Trash2,
  Download,
  Upload,
  Pencil,
  Check,
  X,
  RefreshCw,
  ExternalLink,
  Brain,
} from "lucide-react"
import {
  ChannelProfileSelector,
  profileIdsToApi,
  apiToProfileIds,
} from "@/components/ChannelProfileSelector"
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
  useAPISettings,
  useUpdateAPISettings,
  useTeamFilterSettings,
  useUpdateTeamFilterSettings,
  useExceptionKeywords,
  useCreateExceptionKeyword,
  useDeleteExceptionKeyword,
  useChannelNumberingSettings,
  useUpdateChannelNumberingSettings,
  useUpdateCheckSettings,
  useUpdateUpdateCheckSettings,
  useCheckForUpdates,
  useForceCheckForUpdates,
  useAIStatus,
  useAISettings,
  useUpdateAISettings,
  useTestParse,
} from "@/hooks/useSettings"
import { TeamPicker } from "@/components/TeamPicker"
import { SortPriorityManager } from "@/components/SortPriorityManager"
import { StreamOrderingManager } from "@/components/StreamOrderingManager"
import { getLeagues, getSports } from "@/api/teams"
import { downloadBackup, restoreBackup } from "@/api/backup"
import { useQuery } from "@tanstack/react-query"
import { useCacheStatus, useRefreshCache } from "@/hooks/useEPG"
import { useDateFormat } from "@/hooks/useDateFormat"
import type {
  DispatcharrSettings,
  LifecycleSettings,
  SchedulerSettings,
  EPGSettings,
  DurationSettings,
  DisplaySettings,
  ReconciliationSettings,
  TeamFilterSettings,
  ChannelNumberingSettings,
  UpdateCheckSettings,
  AISettings,
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

type SettingsTab = "general" | "teams" | "events" | "channels" | "epg" | "integrations" | "advanced"

const TABS: { id: SettingsTab; label: string }[] = [
  { id: "general", label: "General" },
  { id: "teams", label: "Teams" },
  { id: "events", label: "Event Groups" },
  { id: "channels", label: "Channel Management" },
  { id: "epg", label: "EPG Generation" },
  { id: "integrations", label: "Dispatcharr" },
  { id: "advanced", label: "Advanced" },
]

export function Settings() {
  const [activeTab, setActiveTab] = useState<SettingsTab>("general")
  const { data: settings, isLoading, error, refetch } = useSettings()
  const dispatcharrStatus = useDispatcharrStatus()
  const epgSourcesQuery = useDispatcharrEPGSources(dispatcharrStatus.data?.connected ?? false)

  // Fetch channel profiles for conversion helpers
  const channelProfilesQuery = useQuery({
    queryKey: ["dispatcharr-channel-profiles"],
    queryFn: async () => {
      const response = await fetch("/api/v1/dispatcharr/channel-profiles")
      if (!response.ok) return []
      return response.json() as Promise<{ id: number; name: string }[]>
    },
    enabled: dispatcharrStatus.data?.connected ?? false,
    retry: false,
  })
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

  // API settings (cache refresh on startup)
  const { data: apiSettingsData } = useAPISettings()
  const updateAPISettings = useUpdateAPISettings()

  // Exception keywords
  const keywordsQuery = useExceptionKeywords()
  const createKeyword = useCreateExceptionKeyword()
  const deleteKeyword = useDeleteExceptionKeyword()

  // Team filter settings
  const { data: teamFilterData } = useTeamFilterSettings()
  const updateTeamFilter = useUpdateTeamFilterSettings()

  // Channel numbering settings
  const { data: channelNumberingData } = useChannelNumberingSettings()
  const updateChannelNumbering = useUpdateChannelNumberingSettings()

  // Update check settings
  const { data: updateCheckData } = useUpdateCheckSettings()
  const updateUpdateCheck = useUpdateUpdateCheckSettings()
  const updateInfoQuery = useCheckForUpdates(updateCheckData?.enabled ?? true)
  const forceCheckUpdates = useForceCheckForUpdates()
  const { formatDateTime } = useDateFormat()

  const { data: leaguesData } = useQuery({
    queryKey: ["cache", "leagues"],
    queryFn: () => getLeagues(),
  })

  // Fetch sport display names from database (single source of truth)
  const { data: sportsData } = useQuery({
    queryKey: ["sports"],
    queryFn: getSports,
    staleTime: 1000 * 60 * 60, // 1 hour
  })
  const sportsMap = sportsData?.sports

  // Local form state
  const [dispatcharr, setDispatcharr] = useState<Partial<DispatcharrSettings>>({})
  const [lifecycle, setLifecycle] = useState<LifecycleSettings | null>(null)
  const [scheduler, setScheduler] = useState<SchedulerSettings | null>(null)
  const [epg, setEPG] = useState<EPGSettings | null>(null)
  const [durations, setDurations] = useState<DurationSettings | null>(null)
  const [display, setDisplay] = useState<DisplaySettings | null>(null)
  const [reconciliation, setReconciliation] = useState<ReconciliationSettings | null>(null)
  const [teamFilter, setTeamFilter] = useState<TeamFilterSettings>({
    enabled: true,
    include_teams: null,
    exclude_teams: null,
    mode: "include",
  })
  const [channelNumbering, setChannelNumbering] = useState<ChannelNumberingSettings>({
    numbering_mode: "strict_block",
    sorting_scope: "per_group",
    sort_by: "time",
  })
  const [updateCheck, setUpdateCheck] = useState<UpdateCheckSettings>({
    enabled: true,
    notify_stable: true,
    notify_dev: true,
    github_owner: "Pharaoh-Labs",
    github_repo: "teamarr",
    dev_branch: "dev",
    auto_detect_branch: true,
  })
  const [newKeyword, setNewKeyword] = useState({ label: "", match_terms: "", behavior: "consolidate" })

  // AI Settings
  const aiStatusQuery = useAIStatus()
  const { data: aiSettingsData } = useAISettings()
  const updateAI = useUpdateAISettings()
  const testParseMutation = useTestParse()
  const [aiSettings, setAISettings] = useState<AISettings>({
    enabled: false,
    providers: {
      ollama: { enabled: false, url: "http://localhost:11434", model: "qwen2.5:7b", timeout: 180 },
      openai: { enabled: false, api_key: "", model: "gpt-4o-mini", timeout: 60, organization: "" },
      anthropic: { enabled: false, api_key: "", model: "claude-3-5-sonnet-20241022", timeout: 60 },
      grok: { enabled: false, api_key: "", model: "grok-2-latest", timeout: 60 },
      // Free-tier providers
      groq: { enabled: false, api_key: "", model: "llama-3.1-8b-instant", timeout: 60 },
      gemini: { enabled: false, api_key: "", model: "gemini-2.0-flash", timeout: 60 },
      openrouter: { enabled: false, api_key: "", model: "meta-llama/llama-3.1-8b-instruct:free", timeout: 60, site_url: "", app_name: "Teamarr" },
    },
    task_assignments: {
      pattern_learning: "ollama",
      stream_parsing: "ollama",
      event_cards: "ollama",
      team_matching: "ollama",
      description_gen: "ollama",
    },
    batch_size: 10,
    learn_patterns: true,
    fallback_to_regex: true,
    ollama_url: "http://localhost:11434",
    model: "qwen2.5:7b",
    timeout: 180,
    use_for_parsing: true,
    use_for_matching: false,
  })
  const [testStreams, setTestStreams] = useState("")
  const [editingKeyword, setEditingKeyword] = useState<{ id: number; label: string; match_terms: string } | null>(null)

  // Local state for channel range inputs (allows free typing)
  const [channelRangeStart, setChannelRangeStart] = useState("")
  const [channelRangeEnd, setChannelRangeEnd] = useState("")

  // Selected profile IDs for display (converted from API format)
  const [selectedProfileIds, setSelectedProfileIds] = useState<(number | string)[]>([])

  // Backup & Restore state
  const [isRestoring, setIsRestoring] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const initializedRef = useRef(false)

  // Initialize local state from settings (only once on initial load)
  useEffect(() => {
    if (settings && !initializedRef.current) {
      initializedRef.current = true
      setDispatcharr({
        enabled: settings.dispatcharr.enabled,
        url: settings.dispatcharr.url,
        username: settings.dispatcharr.username,
        password: "", // Don't show masked password
        epg_id: settings.dispatcharr.epg_id,
        default_channel_profile_ids: settings.dispatcharr.default_channel_profile_ids,
        cleanup_unused_logos: settings.dispatcharr.cleanup_unused_logos,
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

  // Sync team filter state when data loads
  useEffect(() => {
    if (teamFilterData) {
      setTeamFilter(teamFilterData)
    }
  }, [teamFilterData])

  // Sync channel numbering state when data loads
  useEffect(() => {
    if (channelNumberingData) {
      setChannelNumbering(channelNumberingData)
    }
  }, [channelNumberingData])

  // Sync update check state when data loads
  useEffect(() => {
    if (updateCheckData) {
      setUpdateCheck(updateCheckData)
    }
  }, [updateCheckData])

  // Sync AI settings state when data loads
  useEffect(() => {
    if (aiSettingsData) {
      setAISettings(aiSettingsData)
    }
  }, [aiSettingsData])

  // Sync channel range inputs from lifecycle on initial load only
  const channelRangeInitializedRef = useRef(false)
  useEffect(() => {
    if (lifecycle && !channelRangeInitializedRef.current) {
      channelRangeInitializedRef.current = true
      setChannelRangeStart(lifecycle.channel_range_start?.toString() ?? "101")
      setChannelRangeEnd(lifecycle.channel_range_end?.toString() ?? "")
    }
  }, [lifecycle])

  // Convert API profile IDs to display IDs when profiles are loaded
  useEffect(() => {
    if (channelProfilesQuery.data && settings) {
      const allProfileIds = channelProfilesQuery.data.map(p => p.id)
      const displayIds = apiToProfileIds(
        settings.dispatcharr.default_channel_profile_ids,
        allProfileIds
      )
      setSelectedProfileIds(displayIds)
    }
  }, [channelProfilesQuery.data, settings])

  // Get league slugs for TeamPicker
  const availableLeagues = useMemo(() =>
    leaguesData?.leagues?.map(l => l.slug) ?? [],
    [leaguesData]
  )

  const handleSaveDispatcharr = async () => {
    try {
      // Convert selected profile IDs to API format
      // All selected → null (backend sends [0] sentinel to Dispatcharr)
      // None selected → [] (no profiles)
      // Some selected → those specific IDs
      const allProfileIds = channelProfilesQuery.data?.map(p => p.id) ?? []
      const profileIdsToSave = profileIdsToApi(selectedProfileIds, allProfileIds)

      // Only send password if it was changed
      const data: Partial<DispatcharrSettings> = {
        enabled: dispatcharr.enabled,
        url: dispatcharr.url,
        username: dispatcharr.username,
        epg_id: dispatcharr.epg_id,
        default_channel_profile_ids: profileIdsToSave,
        cleanup_unused_logos: dispatcharr.cleanup_unused_logos,
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

  const handleSaveAISettings = async () => {
    try {
      await updateAI.mutateAsync(aiSettings)
      toast.success("AI settings saved")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save AI settings")
    }
  }

  const handleTestParse = async () => {
    const streams = testStreams.split("\n").filter(s => s.trim())
    if (streams.length === 0) {
      toast.error("Enter at least one stream name to test")
      return
    }
    try {
      const result = await testParseMutation.mutateAsync(streams)
      if (result.success) {
        toast.success(`Parsed ${result.results.length} streams`)
      } else {
        toast.error(result.error || "Parse test failed")
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Parse test failed")
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

  const handleSaveChannelNumbering = async () => {
    try {
      // Save both channel numbering AND lifecycle settings (channel range is in lifecycle)
      const promises: Promise<unknown>[] = [
        updateChannelNumbering.mutateAsync(channelNumbering),
      ]
      if (lifecycle) {
        promises.push(updateLifecycle.mutateAsync(lifecycle))
      }
      await Promise.all(promises)
      toast.success("Channel numbering settings saved")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save")
    }
  }

  const handleAddKeyword = async () => {
    if (!newKeyword.label.trim()) {
      toast.error("Please enter a label")
      return
    }
    if (!newKeyword.match_terms.trim()) {
      toast.error("Please enter at least one match term")
      return
    }
    try {
      await createKeyword.mutateAsync(newKeyword)
      setNewKeyword({ label: "", match_terms: "", behavior: "consolidate" })
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

  const handleSaveKeywordEdit = async () => {
    if (!editingKeyword || !editingKeyword.label.trim()) {
      toast.error("Label cannot be empty")
      return
    }
    if (!editingKeyword.match_terms.trim()) {
      toast.error("Match terms cannot be empty")
      return
    }
    try {
      await fetch(`/api/v1/keywords/${editingKeyword.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: editingKeyword.label, match_terms: editingKeyword.match_terms }),
      })
      keywordsQuery.refetch()
      setEditingKeyword(null)
      toast.success("Keyword updated")
    } catch (err) {
      toast.error("Failed to update keyword")
    }
  }

  const handleDownloadBackup = () => {
    downloadBackup()
    toast.success("Backup download started")
  }

  const handleRestoreBackup = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    if (!file.name.endsWith(".db")) {
      toast.error("Invalid file type. Please upload a .db file.")
      return
    }

    setIsRestoring(true)
    try {
      const result = await restoreBackup(file)
      toast.success(result.message)
      if (result.backup_path) {
        toast.info(`Pre-restore backup saved at: ${result.backup_path}`)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to restore backup")
    } finally {
      setIsRestoring(false)
      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = ""
      }
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
      <>
      <div className="mb-4">
        <h2 className="text-lg font-semibold">General Settings</h2>
        <p className="text-sm text-muted-foreground">Configure timezone, time format, and display preferences</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>System Settings</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            {/* Left column: Timezones */}
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="ui-timezone">UI Display Timezone</Label>
                <Input
                  id="ui-timezone"
                  value={settings?.ui_timezone ?? "America/New_York"}
                  disabled
                  readOnly
                  className="bg-muted cursor-not-allowed"
                />
                <p className="text-xs text-muted-foreground">
                  This can be changed by setting the TZ environment variable
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="epg-timezone">EPG Output Timezone</Label>
                <Input
                  id="epg-timezone"
                  value={epg?.epg_timezone ?? "America/New_York"}
                  onChange={(e) => epg && setEPG({ ...epg, epg_timezone: e.target.value })}
                />
                <p className="text-xs text-muted-foreground">
                  Used for template variables like {"{game_time}"}
                </p>
              </div>
            </div>

            {/* Right column: Time Format and Show Timezone */}
            <div className="space-y-4">
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
                <p className="text-xs text-muted-foreground">
                  Applies to UI display and EPG output
                </p>
              </div>
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <Switch
                    checked={display?.show_timezone ?? true}
                    onCheckedChange={(checked) =>
                      display && setDisplay({ ...display, show_timezone: checked })
                    }
                  />
                  <Label>Show Timezone Abbreviation</Label>
                </div>
                <p className="text-xs text-muted-foreground">
                  Applies to UI display and EPG output
                </p>
              </div>
            </div>
          </div>

          {/* Info box when timezones differ */}
          {settings?.ui_timezone_source === "env" &&
           settings?.ui_timezone !== epg?.epg_timezone && (
            <div className="p-3 bg-blue-50 dark:bg-blue-950 border border-blue-200 dark:border-blue-800 rounded-md text-sm">
              <p className="font-medium text-blue-900 dark:text-blue-100">Two timezones configured:</p>
              <ul className="list-disc list-inside mt-1 text-blue-800 dark:text-blue-200">
                <li><strong>UI Display</strong>: {settings.ui_timezone} (from $TZ)</li>
                <li><strong>EPG Output</strong>: {epg?.epg_timezone} (user setting)</li>
              </ul>
            </div>
          )}

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
      </>
      )}

      {/* Teams Tab */}
      {activeTab === "teams" && (
      <>
      <div className="mb-4">
        <h2 className="text-lg font-semibold">Team Based Streams</h2>
        <p className="text-sm text-muted-foreground">Configure settings for team-based EPG generation</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Channel Settings</CardTitle>
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
      </>
      )}

      {/* Event Groups Tab */}
      {activeTab === "events" && (
      <>
      <div className="mb-4">
        <h2 className="text-lg font-semibold">Event Based Streams</h2>
        <p className="text-sm text-muted-foreground">Configure settings for event-based EPG generation (Event Groups)</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Event Matching</CardTitle>
          <CardDescription>
            Configure how streams are matched to sporting events
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
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
              <p className="text-xs text-muted-foreground">
                How to handle multiple streams for the same event
              </p>
            </div>
          </div>

          <Button
            onClick={async () => {
              try {
                const promises: Promise<unknown>[] = []
                if (epg) promises.push(updateEPG.mutateAsync(epg))
                if (reconciliation) promises.push(updateReconciliation.mutateAsync(reconciliation))
                await Promise.all(promises)
                toast.success("Event matching settings saved")
              } catch (err) {
                toast.error(err instanceof Error ? err.message : "Failed to save")
              }
            }}
            disabled={updateEPG.isPending || updateReconciliation.isPending}
          >
            {(updateEPG.isPending || updateReconciliation.isPending) ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-1" />
            )}
            Save
          </Button>
        </CardContent>
      </Card>

      {/* Exception Keywords Card */}
      {reconciliation?.default_duplicate_event_handling === "consolidate" && (
      <Card>
        <CardHeader>
          <CardTitle>Exception Keywords</CardTitle>
          <CardDescription>
            Streams matching these terms get special handling during consolidation. The label is used for channel naming and the {"{exception_keyword}"} template variable.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="border rounded-md">
            <table className="w-full text-sm">
              <thead className="bg-muted">
                <tr>
                  <th className="px-3 py-2 text-left font-medium w-32">Label</th>
                  <th className="px-3 py-2 text-left font-medium">Match Terms (comma-separated)</th>
                  <th className="px-3 py-2 text-left font-medium w-40">Behavior</th>
                  <th className="px-3 py-2 w-20"></th>
                </tr>
              </thead>
              <tbody>
                {keywordsQuery.data?.keywords.map((kw) => (
                  <tr key={kw.id} className="border-t">
                    <td className="px-3 py-2">
                      {editingKeyword?.id === kw.id ? (
                        <Input
                          value={editingKeyword.label}
                          onChange={(e) => setEditingKeyword({ ...editingKeyword, label: e.target.value })}
                          className="h-8"
                          autoFocus
                          placeholder="Label"
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleSaveKeywordEdit()
                            if (e.key === "Escape") setEditingKeyword(null)
                          }}
                        />
                      ) : (
                        <span className="font-medium">{kw.label}</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {editingKeyword?.id === kw.id ? (
                        <Input
                          value={editingKeyword.match_terms}
                          onChange={(e) => setEditingKeyword({ ...editingKeyword, match_terms: e.target.value })}
                          className="h-8"
                          placeholder="Terms to match"
                          onKeyDown={(e) => {
                            if (e.key === "Enter") handleSaveKeywordEdit()
                            if (e.key === "Escape") setEditingKeyword(null)
                          }}
                        />
                      ) : (
                        <span className="text-muted-foreground">{kw.match_terms}</span>
                      )}
                    </td>
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
                        disabled={editingKeyword?.id === kw.id}
                      >
                        <option value="consolidate">Sub-Consolidate</option>
                        <option value="separate">Separate</option>
                        <option value="ignore">Ignore</option>
                      </Select>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex gap-1">
                        {editingKeyword?.id === kw.id ? (
                          <>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={handleSaveKeywordEdit}
                              title="Save"
                            >
                              <Check className="h-4 w-4 text-green-600" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setEditingKeyword(null)}
                              title="Cancel"
                            >
                              <X className="h-4 w-4 text-muted-foreground" />
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setEditingKeyword({ id: kw.id, label: kw.label, match_terms: kw.match_terms })}
                              title="Edit"
                            >
                              <Pencil className="h-4 w-4 text-muted-foreground" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleDeleteKeyword(kw.id)}
                              disabled={deleteKeyword.isPending}
                              title="Delete"
                            >
                              <Trash2 className="h-4 w-4 text-destructive" />
                            </Button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
                {(!keywordsQuery.data?.keywords || keywordsQuery.data.keywords.length === 0) && (
                  <tr>
                    <td colSpan={4} className="px-3 py-4 text-center text-muted-foreground">
                      No exception keywords defined
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="flex gap-2">
            <Input
              placeholder="Label (e.g., Spanish)"
              value={newKeyword.label}
              onChange={(e) => setNewKeyword({ ...newKeyword, label: e.target.value })}
              className="w-32"
            />
            <Input
              placeholder="Match terms (e.g., Spanish, En Español, ESP)"
              value={newKeyword.match_terms}
              onChange={(e) => setNewKeyword({ ...newKeyword, match_terms: e.target.value })}
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
        </CardContent>
      </Card>
      )}

      {/* Default Team Filter Section */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Default Team Filter</CardTitle>
              <CardDescription>
                Global team filter applied to all event groups that don't have their own filter.
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Label htmlFor="team-filter-enabled" className="text-sm">
                {teamFilter.enabled ? "Enabled" : "Disabled"}
              </Label>
              <Switch
                id="team-filter-enabled"
                checked={teamFilter.enabled}
                onCheckedChange={(checked) => {
                  setTeamFilter({ ...teamFilter, enabled: checked })
                }}
              />
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Show filter config when teams are selected OR always show to allow adding */}
          {/* Mode selector */}
          <div className="flex items-center gap-4">
            <Label>Filter Mode:</Label>
            <div className="flex gap-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="default-team-filter-mode"
                  value="include"
                  checked={teamFilter.mode === "include"}
                  onChange={() => setTeamFilter({ ...teamFilter, mode: "include" })}
                  className="accent-primary"
                />
                <span className="text-sm">Include only selected teams</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="default-team-filter-mode"
                  value="exclude"
                  checked={teamFilter.mode === "exclude"}
                  onChange={() => setTeamFilter({ ...teamFilter, mode: "exclude" })}
                  className="accent-primary"
                />
                <span className="text-sm">Exclude selected teams</span>
              </label>
            </div>
          </div>

          {/* TeamPicker */}
          <TeamPicker
            leagues={availableLeagues}
            selectedTeams={
              teamFilter.mode === "include"
                ? (teamFilter.include_teams ?? [])
                : (teamFilter.exclude_teams ?? [])
            }
            onSelectionChange={(teams) => {
              if (teamFilter.mode === "include") {
                setTeamFilter({ ...teamFilter, include_teams: teams, exclude_teams: [] })  // Send [] to clear
              } else {
                setTeamFilter({ ...teamFilter, exclude_teams: teams, include_teams: [] })  // Send [] to clear
              }
            }}
            placeholder="Search teams to add to default filter..."
          />

          {/* Status message and Save button */}
          <div className="flex justify-between items-center">
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">
                {!teamFilter.enabled
                  ? "Team filtering is disabled. All events will be matched."
                  : !(teamFilter.include_teams?.length || teamFilter.exclude_teams?.length)
                    ? "No teams selected. All events will be matched."
                    : teamFilter.mode === "include"
                      ? `Only events involving ${teamFilter.include_teams?.length} selected team(s) will be matched.`
                      : `Events involving ${teamFilter.exclude_teams?.length} selected team(s) will be excluded.`}
              </p>
              {teamFilter.enabled && (teamFilter.include_teams?.length || teamFilter.exclude_teams?.length) ? (
                <p className="text-xs text-muted-foreground italic">
                  Filter only applies to leagues where you've made selections.
                </p>
              ) : null}
            </div>
            <Button
              onClick={() => {
                updateTeamFilter.mutate({
                  enabled: teamFilter.enabled,
                  include_teams: teamFilter.include_teams,
                  exclude_teams: teamFilter.exclude_teams,
                  mode: teamFilter.mode,
                  clear_include_teams: teamFilter.mode === "exclude" || !teamFilter.include_teams?.length,
                  clear_exclude_teams: teamFilter.mode === "include" || !teamFilter.exclude_teams?.length,
                }, {
                  onSuccess: () => toast.success("Default team filter saved"),
                  onError: () => toast.error("Failed to save team filter"),
                })
              }}
              disabled={updateTeamFilter.isPending}
            >
              {updateTeamFilter.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : (
                <Save className="h-4 w-4 mr-2" />
              )}
              Save Default Filter
            </Button>
          </div>
        </CardContent>
      </Card>
      </>
      )}

      {/* Channel Management Tab */}
      {activeTab === "channels" && (
      <>
      <div className="mb-4">
        <h2 className="text-lg font-semibold">Channel Management</h2>
        <p className="text-sm text-muted-foreground">Configure channel lifecycle, numbering, and sorting</p>
      </div>

      {/* Channel Lifecycle */}
      <Card>
        <CardHeader>
          <CardTitle>Channel Lifecycle</CardTitle>
          <CardDescription>
            Configure when channels are created and deleted for event groups
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="ch-create-timing">Channel Create Timing</Label>
              <Select
                id="ch-create-timing"
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
              <p className="text-xs text-muted-foreground">
                When to create channels before events
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="ch-delete-timing">Channel Delete Timing</Label>
              <Select
                id="ch-delete-timing"
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
              <p className="text-xs text-muted-foreground">
                When to delete channels after events
              </p>
            </div>
          </div>

          <Button
            onClick={async () => {
              if (!lifecycle) return
              try {
                await updateLifecycle.mutateAsync(lifecycle)
                toast.success("Channel lifecycle settings saved")
              } catch (err) {
                toast.error(err instanceof Error ? err.message : "Failed to save")
              }
            }}
            disabled={updateLifecycle.isPending}
          >
            {updateLifecycle.isPending ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-1" />
            )}
            Save
          </Button>
        </CardContent>
      </Card>

      {/* Channel Numbering - Cascading Settings */}
      <Card>
        <CardHeader>
          <CardTitle>Channel Numbering</CardTitle>
          <CardDescription>
            Configure how channel numbers are assigned and sorted for Auto groups
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Channel Range */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="ch-range-start-num">Channel Range Start</Label>
              <Input
                id="ch-range-start-num"
                type="number"
                min={1}
                value={channelRangeStart}
                onChange={(e) => setChannelRangeStart(e.target.value)}
                onBlur={(e) => {
                  if (!lifecycle) return
                  const val = parseInt(e.target.value)
                  if (!isNaN(val) && val >= 1) {
                    setChannelRangeStart(val.toString())
                    setLifecycle({ ...lifecycle, channel_range_start: val })
                  } else {
                    // Reset to current lifecycle value if invalid
                    setChannelRangeStart(lifecycle.channel_range_start?.toString() ?? "101")
                  }
                }}
              />
              <p className="text-xs text-muted-foreground">
                First channel number for auto-assigned channels
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="ch-range-end-num">Channel Range End</Label>
              <Input
                id="ch-range-end-num"
                type="number"
                min={1}
                value={channelRangeEnd}
                onChange={(e) => setChannelRangeEnd(e.target.value)}
                onBlur={(e) => {
                  if (!lifecycle) return
                  if (e.target.value === "") {
                    setChannelRangeEnd("")
                    setLifecycle({ ...lifecycle, channel_range_end: null })
                  } else {
                    const val = parseInt(e.target.value)
                    if (!isNaN(val) && val >= 1) {
                      setChannelRangeEnd(val.toString())
                      setLifecycle({ ...lifecycle, channel_range_end: val })
                    } else {
                      setChannelRangeEnd(lifecycle.channel_range_end?.toString() ?? "")
                    }
                  }
                }}
                placeholder="No limit"
              />
              <p className="text-xs text-muted-foreground">
                Last channel number (leave empty for no limit)
              </p>
            </div>
          </div>

          {/* Level 1: Numbering Mode - Tab-style layout */}
          <div className="space-y-0">
            <Label className="text-sm font-medium mb-3 block">Numbering Mode</Label>
            <div className="grid grid-cols-3 gap-0">
              {/* Strict Block */}
              <label className={`flex flex-col p-3 border-2 cursor-pointer transition-colors rounded-tl-lg ${
                channelNumbering.numbering_mode === "strict_block"
                  ? "border-primary border-b-0 bg-muted/30 relative z-10"
                  : "border-border border-b-primary/20 hover:border-muted-foreground/50 bg-background"
              }`}>
                <div className="flex items-center gap-2 mb-1">
                  <input
                    type="radio"
                    name="numbering-mode"
                    value="strict_block"
                    checked={channelNumbering.numbering_mode === "strict_block"}
                    onChange={() =>
                      setChannelNumbering({
                        ...channelNumbering,
                        numbering_mode: "strict_block",
                        sorting_scope: "per_group",
                      })
                    }
                    className="accent-primary"
                  />
                  <span className="font-medium text-sm">Strict Block Reservation</span>
                </div>
                <p className="text-xs text-muted-foreground leading-tight">
                  Reserves blocks by total theoretical stream count per group. Large gaps, minimal drift. Best for stable assignments. Per-group sorting only.
                </p>
              </label>

              {/* Rational Block */}
              <label className={`flex flex-col p-3 border-2 border-l-0 cursor-pointer transition-colors ${
                channelNumbering.numbering_mode === "rational_block"
                  ? "border-primary border-b-0 bg-muted/30 relative z-10"
                  : "border-border border-b-primary/20 hover:border-muted-foreground/50 bg-background"
              }`}>
                <div className="flex items-center gap-2 mb-1">
                  <input
                    type="radio"
                    name="numbering-mode"
                    value="rational_block"
                    checked={channelNumbering.numbering_mode === "rational_block"}
                    onChange={() =>
                      setChannelNumbering({
                        ...channelNumbering,
                        numbering_mode: "rational_block",
                      })
                    }
                    className="accent-primary"
                  />
                  <span className="font-medium text-sm">Rational Block Reservation</span>
                </div>
                <p className="text-xs text-muted-foreground leading-tight">
                  Reserves blocks by actual channel count. Smaller gaps, low drift. Balanced approach.
                </p>
              </label>

              {/* Strict Compact */}
              <label className={`flex flex-col p-3 border-2 border-l-0 cursor-pointer transition-colors rounded-tr-lg ${
                channelNumbering.numbering_mode === "strict_compact"
                  ? "border-primary border-b-0 bg-muted/30 relative z-10"
                  : "border-border border-b-primary/20 hover:border-muted-foreground/50 bg-background"
              }`}>
                <div className="flex items-center gap-2 mb-1">
                  <input
                    type="radio"
                    name="numbering-mode"
                    value="strict_compact"
                    checked={channelNumbering.numbering_mode === "strict_compact"}
                    onChange={() =>
                      setChannelNumbering({
                        ...channelNumbering,
                        numbering_mode: "strict_compact",
                      })
                    }
                    className="accent-primary"
                  />
                  <span className="font-medium text-sm">Strict Compact Numbering</span>
                </div>
                <p className="text-xs text-muted-foreground leading-tight">
                  No reservation, sequential numbers. No gaps, higher drift risk. Maximizes density.
                </p>
              </label>
            </div>

            {/* Sub-options panel - connected to tabs */}
            <div className="rounded-b-lg border-2 border-primary border-t-0 bg-muted/30 p-4 space-y-4 -mt-[2px]">
              <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <span>Sorting Options</span>
              </div>

            {/* For strict_block: just show sort by options */}
            {channelNumbering.numbering_mode === "strict_block" && (
              <div className="space-y-3">
                <Label className="text-sm">Sort Channels By</Label>
                <div className="flex gap-3">
                  <label className={`flex-1 flex items-center gap-2 p-2.5 rounded-md border cursor-pointer transition-colors ${
                    channelNumbering.sort_by === "time" ? "border-primary bg-background" : "border-transparent bg-background/50 hover:bg-background"
                  }`}>
                    <input type="radio" name="sort-by-strict" value="time"
                      checked={channelNumbering.sort_by === "time"}
                      onChange={() => setChannelNumbering({ ...channelNumbering, sort_by: "time" })}
                      className="accent-primary" />
                    <span className="text-sm">Event Time</span>
                  </label>
                  <label className={`flex-1 flex items-center gap-2 p-2.5 rounded-md border cursor-pointer transition-colors ${
                    channelNumbering.sort_by === "sport_league_time" ? "border-primary bg-background" : "border-transparent bg-background/50 hover:bg-background"
                  }`}>
                    <input type="radio" name="sort-by-strict" value="sport_league_time"
                      checked={channelNumbering.sort_by === "sport_league_time"}
                      onChange={() => setChannelNumbering({ ...channelNumbering, sort_by: "sport_league_time" })}
                      className="accent-primary" />
                    <span className="text-sm">Sport → League → Time</span>
                  </label>
                  <label className={`flex-1 flex items-center gap-2 p-2.5 rounded-md border cursor-pointer transition-colors ${
                    channelNumbering.sort_by === "stream_order" ? "border-primary bg-background" : "border-transparent bg-background/50 hover:bg-background"
                  }`}>
                    <input type="radio" name="sort-by-strict" value="stream_order"
                      checked={channelNumbering.sort_by === "stream_order"}
                      onChange={() => setChannelNumbering({ ...channelNumbering, sort_by: "stream_order" })}
                      className="accent-primary" />
                    <span className="text-sm">Stream Order</span>
                  </label>
                </div>
                {channelNumbering.sort_by === "sport_league_time" && (
                  <SortPriorityManager currentSortBy="sport_league_time" showWhenSortBy="sport_league_time" />
                )}
              </div>
            )}

            {/* For rational_block and strict_compact: show scope then sort by */}
            {(channelNumbering.numbering_mode === "rational_block" || channelNumbering.numbering_mode === "strict_compact") && (
              <div className="space-y-4">
                {/* Sorting Scope */}
                <div className="space-y-3">
                  <Label className="text-sm">Sorting Scope</Label>
                  <div className="flex gap-3">
                    <label className={`flex-1 flex flex-col p-2.5 rounded-md border cursor-pointer transition-colors ${
                      channelNumbering.sorting_scope === "per_group" ? "border-primary bg-background" : "border-transparent bg-background/50 hover:bg-background"
                    }`}>
                      <div className="flex items-center gap-2">
                        <input type="radio"
                          name={`scope-${channelNumbering.numbering_mode}`}
                          value="per_group"
                          checked={channelNumbering.sorting_scope === "per_group"}
                          onChange={() => setChannelNumbering({ ...channelNumbering, sorting_scope: "per_group" })}
                          className="accent-primary" />
                        <span className="text-sm font-medium">Per Group</span>
                      </div>
                      <span className="text-xs text-muted-foreground ml-5">Sort within each event group separately</span>
                    </label>
                    <label className={`flex-1 flex flex-col p-2.5 rounded-md border cursor-pointer transition-colors ${
                      channelNumbering.sorting_scope === "global" ? "border-primary bg-background" : "border-transparent bg-background/50 hover:bg-background"
                    }`}>
                      <div className="flex items-center gap-2">
                        <input type="radio"
                          name={`scope-${channelNumbering.numbering_mode}`}
                          value="global"
                          checked={channelNumbering.sorting_scope === "global"}
                          onChange={() => setChannelNumbering({ ...channelNumbering, sorting_scope: "global", sort_by: "sport_league_time" })}
                          className="accent-primary" />
                        <span className="text-sm font-medium">Global</span>
                      </div>
                      <span className="text-xs text-muted-foreground ml-5">Sort all channels by sport/league priority</span>
                    </label>
                  </div>
                </div>

                {/* Sort By - only for per_group scope */}
                {channelNumbering.sorting_scope === "per_group" && (
                  <div className="space-y-3">
                    <Label className="text-sm">Sort Channels By</Label>
                    <div className="flex gap-3">
                      <label className={`flex-1 flex items-center gap-2 p-2.5 rounded-md border cursor-pointer transition-colors ${
                        channelNumbering.sort_by === "time" ? "border-primary bg-background" : "border-transparent bg-background/50 hover:bg-background"
                      }`}>
                        <input type="radio" name={`sort-by-${channelNumbering.numbering_mode}`} value="time"
                          checked={channelNumbering.sort_by === "time"}
                          onChange={() => setChannelNumbering({ ...channelNumbering, sort_by: "time" })}
                          className="accent-primary" />
                        <span className="text-sm">Event Time</span>
                      </label>
                      <label className={`flex-1 flex items-center gap-2 p-2.5 rounded-md border cursor-pointer transition-colors ${
                        channelNumbering.sort_by === "sport_league_time" ? "border-primary bg-background" : "border-transparent bg-background/50 hover:bg-background"
                      }`}>
                        <input type="radio" name={`sort-by-${channelNumbering.numbering_mode}`} value="sport_league_time"
                          checked={channelNumbering.sort_by === "sport_league_time"}
                          onChange={() => setChannelNumbering({ ...channelNumbering, sort_by: "sport_league_time" })}
                          className="accent-primary" />
                        <span className="text-sm">Sport → League → Time</span>
                      </label>
                      <label className={`flex-1 flex items-center gap-2 p-2.5 rounded-md border cursor-pointer transition-colors ${
                        channelNumbering.sort_by === "stream_order" ? "border-primary bg-background" : "border-transparent bg-background/50 hover:bg-background"
                      }`}>
                        <input type="radio" name={`sort-by-${channelNumbering.numbering_mode}`} value="stream_order"
                          checked={channelNumbering.sort_by === "stream_order"}
                          onChange={() => setChannelNumbering({ ...channelNumbering, sort_by: "stream_order" })}
                          className="accent-primary" />
                        <span className="text-sm">Stream Order</span>
                      </label>
                    </div>
                  </div>
                )}

                {/* Sort Priority Manager - show when using sport_league_time sorting */}
                {(channelNumbering.sorting_scope === "global" || channelNumbering.sort_by === "sport_league_time") && (
                  <SortPriorityManager currentSortBy="sport_league_time" showWhenSortBy="sport_league_time" />
                )}
              </div>
            )}
          </div>
          </div>

          <div className="pt-4 border-t">
            <Button
              onClick={handleSaveChannelNumbering}
              disabled={updateChannelNumbering.isPending || updateLifecycle.isPending}
            >
              {(updateChannelNumbering.isPending || updateLifecycle.isPending) ? (
                <Loader2 className="h-4 w-4 mr-1 animate-spin" />
              ) : (
                <Save className="h-4 w-4 mr-1" />
              )}
              Save
            </Button>
            <p className="text-xs text-muted-foreground mt-2">
              Channel numbers will be updated on the next EPG generation.
            </p>
          </div>
        </CardContent>
      </Card>

      {/* Stream Ordering */}
      <StreamOrderingManager />
      </>
      )}

      {/* EPG Generation Tab */}
      {activeTab === "epg" && (
      <>
      <div className="mb-4">
        <h2 className="text-lg font-semibold">EPG Generation</h2>
        <p className="text-sm text-muted-foreground">Configure EPG output, scheduling, and game durations</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Output Settings</CardTitle>
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
              <Label htmlFor="epg-lookback">EPG Start (Hours Ago)</Label>
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
          <CardTitle>Default Durations</CardTitle>
          <CardDescription>Default event durations by sport (in hours)</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-4 gap-4">
            {durations &&
              Object.entries(durations).map(([sport, hours]) => (
                <div key={sport} className="space-y-1">
                  <Label htmlFor={`duration-${sport}`}>
                    {getSportDisplayName(sport, sportsMap)}
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

      {/* Dispatcharr Integration Tab */}
      {activeTab === "integrations" && (
      <>
      <div className="mb-4">
        <h2 className="text-lg font-semibold">Dispatcharr Integration</h2>
        <p className="text-sm text-muted-foreground">Configure connection to Dispatcharr for channel management</p>
      </div>
      {/* Card 1: Connection Settings */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Connection Settings</CardTitle>
              <CardDescription>Server URL and credentials</CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Button onClick={handleTestConnection} variant="outline" size="sm" disabled={testConnection.isPending}>
                {testConnection.isPending ? (
                  <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                ) : (
                  <TestTube className="h-4 w-4 mr-1" />
                )}
                Test
              </Button>
              {dispatcharrStatus.data?.connected ? (
                <Badge variant="success" className="gap-1">
                  <CheckCircle className="h-3 w-3" /> Connected
                </Badge>
              ) : dispatcharrStatus.data?.configured && dispatcharrStatus.data?.error ? (
                <Badge variant="destructive" className="gap-1" title={dispatcharrStatus.data.error}>
                  <AlertTriangle className="h-3 w-3" /> Error
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
          {/* Connection error banner */}
          {dispatcharrStatus.data?.configured && dispatcharrStatus.data?.error && (
            <div className="flex items-start gap-2 p-3 bg-destructive/10 border border-destructive/20 rounded-lg">
              <AlertTriangle className="h-4 w-4 text-destructive mt-0.5 shrink-0" />
              <div className="text-sm">
                <p className="font-medium text-destructive">Connection Failed</p>
                <p className="text-muted-foreground">{dispatcharrStatus.data.error}</p>
              </div>
            </div>
          )}

          {/* Enable */}
          <div className="flex items-center gap-2">
            <Switch
              checked={dispatcharr.enabled ?? false}
              onCheckedChange={(checked) => setDispatcharr({ ...dispatcharr, enabled: checked })}
            />
            <Label>Enable Dispatcharr Integration</Label>
          </div>

          {/* URL */}
          <div className="space-y-2">
            <Label htmlFor="dispatcharr-url">URL</Label>
            <Input
              id="dispatcharr-url"
              value={dispatcharr.url ?? ""}
              onChange={(e) => setDispatcharr({ ...dispatcharr, url: e.target.value })}
              placeholder="http://localhost:9191"
            />
          </div>

          {/* Credentials */}
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

          {/* Save button */}
          <Button onClick={handleSaveDispatcharr} disabled={updateDispatcharr.isPending}>
            {updateDispatcharr.isPending ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-1" />
            )}
            Save
          </Button>
        </CardContent>
      </Card>

      {/* Card 2: EPG Source */}
      <Card>
        <CardHeader>
          <CardTitle>EPG Source</CardTitle>
          <CardDescription>Link channels to an EPG source in Dispatcharr</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
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
            <p className="text-xs text-muted-foreground">
              Associate Teamarr-managed channels with this EPG source in Dispatcharr.
            </p>
          </div>

          <Button onClick={handleSaveDispatcharr} disabled={updateDispatcharr.isPending}>
            {updateDispatcharr.isPending ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-1" />
            )}
            Save
          </Button>
        </CardContent>
      </Card>

      {/* Card 3: Default Channel Profiles */}
      <Card>
        <CardHeader>
          <CardTitle>Default Channel Profiles</CardTitle>
          <CardDescription>Profiles assigned to new channels</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <ChannelProfileSelector
              selectedIds={selectedProfileIds}
              onChange={setSelectedProfileIds}
              disabled={!dispatcharrStatus.data?.connected}
            />
            <p className="text-xs text-muted-foreground">
              These defaults apply to all groups unless overridden in individual group settings.
              Profile assignment is enforced on every EPG generation run.
            </p>
          </div>

          <Button onClick={handleSaveDispatcharr} disabled={updateDispatcharr.isPending}>
            {updateDispatcharr.isPending ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-1" />
            )}
            Save
          </Button>
        </CardContent>
      </Card>

      {/* Card 4: Logo Cleanup */}
      <Card>
        <CardHeader>
          <CardTitle>Logo Cleanup</CardTitle>
          <CardDescription>Remove unused logos from Dispatcharr</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Switch
                checked={dispatcharr.cleanup_unused_logos ?? false}
                onCheckedChange={(checked) => setDispatcharr({ ...dispatcharr, cleanup_unused_logos: checked })}
              />
              <Label>Clean up unused logos after generation</Label>
            </div>
            <p className="text-xs text-muted-foreground">
              When enabled, removes <strong>all</strong> unused logos from Dispatcharr after EPG generation.
              This affects all unused logos, not just ones uploaded by Teamarr.
            </p>
          </div>

          <Button onClick={handleSaveDispatcharr} disabled={updateDispatcharr.isPending}>
            {updateDispatcharr.isPending ? (
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

      {/* Advanced Tab */}
      {activeTab === "advanced" && (
      <>
      <div className="mb-4">
        <h2 className="text-lg font-semibold">Advanced</h2>
        <p className="text-sm text-muted-foreground">Advanced configuration options</p>
      </div>

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

          <div className="space-y-2 pt-2 border-t">
            <Label htmlFor="startup-cache-refresh">Startup Cache Refresh</Label>
            <Select
              id="startup-cache-refresh"
              value={String(apiSettingsData?.startup_cache_max_age_days ?? 0)}
              onChange={(e) => {
                updateAPISettings.mutate({
                  startup_cache_max_age_days: parseInt(e.target.value, 10),
                })
              }}
            >
              <option value="0">Disabled (only refresh if empty)</option>
              <option value="1">Auto-refresh if older than 1 day</option>
              <option value="3">Auto-refresh if older than 3 days</option>
              <option value="7">Auto-refresh if older than 7 days</option>
            </Select>
            <p className="text-xs text-muted-foreground">
              Automatically refresh team/league cache on startup if it's older than the selected age.
            </p>
          </div>

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
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>TheSportsDB API Key</CardTitle>
          <CardDescription>Optional premium API key for higher rate limits</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="tsdb-api-key">API Key</Label>
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
            Save
          </Button>
        </CardContent>
      </Card>

      {/* AI/Ollama Integration */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Brain className="h-5 w-5" />
                AI Integration (Ollama)
              </CardTitle>
              <CardDescription>Use local AI for intelligent stream parsing and pattern learning</CardDescription>
            </div>
            <div className="flex items-center gap-2">
              {aiStatusQuery.data?.available ? (
                <Badge variant="success" className="gap-1">
                  <CheckCircle className="h-3 w-3" /> Connected
                </Badge>
              ) : aiStatusQuery.data?.enabled ? (
                <Badge variant="destructive" className="gap-1" title={aiStatusQuery.data?.error || "Not responding"}>
                  <AlertTriangle className="h-3 w-3" /> Unavailable
                </Badge>
              ) : (
                <Badge variant="secondary">Disabled</Badge>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Connection error banner */}
          {aiStatusQuery.data?.enabled && aiStatusQuery.data?.error && (
            <div className="flex items-start gap-2 p-3 bg-destructive/10 border border-destructive/20 rounded-lg">
              <AlertTriangle className="h-4 w-4 text-destructive mt-0.5 shrink-0" />
              <div className="text-sm">
                <p className="font-medium text-destructive">Connection Failed</p>
                <p className="text-muted-foreground">{aiStatusQuery.data.error}</p>
              </div>
            </div>
          )}

          {/* Enable Toggle */}
          <div className="flex items-center gap-2">
            <Switch
              checked={aiSettings.enabled}
              onCheckedChange={(checked) => setAISettings({ ...aiSettings, enabled: checked })}
            />
            <Label>Enable AI Integration</Label>
          </div>

          {/* Settings (shown when enabled) */}
          {aiSettings.enabled && (
            <>
              {/* Providers Section */}
              <div className="space-y-4 pt-2 border-t">
                <div>
                  <Label className="text-sm font-medium">AI Providers</Label>
                  <p className="text-xs text-muted-foreground">Configure one or more AI providers. Providers marked FREE have generous free tiers.</p>
                </div>

                {/* Ollama (Local) */}
                <div className="p-3 border rounded-lg space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={aiSettings.providers.ollama.enabled}
                        onCheckedChange={(checked) => setAISettings({
                          ...aiSettings,
                          providers: { ...aiSettings.providers, ollama: { ...aiSettings.providers.ollama, enabled: checked } }
                        })}
                      />
                      <span className="font-medium">Ollama (Local)</span>
                      <span className="text-xs px-1.5 py-0.5 bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300 rounded">FREE</span>
                    </div>
                  </div>
                  {aiSettings.providers.ollama.enabled && (
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">URL</Label>
                        <Input
                          value={aiSettings.providers.ollama.url}
                          onChange={(e) => setAISettings({
                            ...aiSettings,
                            ollama_url: e.target.value,
                            providers: { ...aiSettings.providers, ollama: { ...aiSettings.providers.ollama, url: e.target.value } }
                          })}
                          placeholder="http://localhost:11434"
                          className="h-8 text-sm"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Model</Label>
                        <Input
                          value={aiSettings.providers.ollama.model}
                          onChange={(e) => setAISettings({
                            ...aiSettings,
                            model: e.target.value,
                            providers: { ...aiSettings.providers, ollama: { ...aiSettings.providers.ollama, model: e.target.value } }
                          })}
                          placeholder="qwen2.5:7b"
                          className="h-8 text-sm"
                        />
                      </div>
                    </div>
                  )}
                </div>

                {/* Groq (FREE) */}
                <div className="p-3 border rounded-lg space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={aiSettings.providers.groq.enabled}
                        onCheckedChange={(checked) => setAISettings({
                          ...aiSettings,
                          providers: { ...aiSettings.providers, groq: { ...aiSettings.providers.groq, enabled: checked } }
                        })}
                      />
                      <span className="font-medium">Groq (Llama)</span>
                      <span className="text-xs px-1.5 py-0.5 bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300 rounded">FREE</span>
                    </div>
                  </div>
                  {aiSettings.providers.groq.enabled && (
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">API Key</Label>
                        <Input
                          type="password"
                          value={aiSettings.providers.groq.api_key}
                          onChange={(e) => setAISettings({
                            ...aiSettings,
                            providers: { ...aiSettings.providers, groq: { ...aiSettings.providers.groq, api_key: e.target.value } }
                          })}
                          placeholder="gsk_..."
                          className="h-8 text-sm"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Model</Label>
                        <Input
                          value={aiSettings.providers.groq.model}
                          onChange={(e) => setAISettings({
                            ...aiSettings,
                            providers: { ...aiSettings.providers, groq: { ...aiSettings.providers.groq, model: e.target.value } }
                          })}
                          placeholder="llama-3.1-8b-instant"
                          className="h-8 text-sm"
                        />
                      </div>
                    </div>
                  )}
                </div>

                {/* Google Gemini (FREE) */}
                <div className="p-3 border rounded-lg space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={aiSettings.providers.gemini.enabled}
                        onCheckedChange={(checked) => setAISettings({
                          ...aiSettings,
                          providers: { ...aiSettings.providers, gemini: { ...aiSettings.providers.gemini, enabled: checked } }
                        })}
                      />
                      <span className="font-medium">Google Gemini</span>
                      <span className="text-xs px-1.5 py-0.5 bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300 rounded">FREE</span>
                    </div>
                  </div>
                  {aiSettings.providers.gemini.enabled && (
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">API Key</Label>
                        <Input
                          type="password"
                          value={aiSettings.providers.gemini.api_key}
                          onChange={(e) => setAISettings({
                            ...aiSettings,
                            providers: { ...aiSettings.providers, gemini: { ...aiSettings.providers.gemini, api_key: e.target.value } }
                          })}
                          placeholder="AIza..."
                          className="h-8 text-sm"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Model</Label>
                        <Input
                          value={aiSettings.providers.gemini.model}
                          onChange={(e) => setAISettings({
                            ...aiSettings,
                            providers: { ...aiSettings.providers, gemini: { ...aiSettings.providers.gemini, model: e.target.value } }
                          })}
                          placeholder="gemini-2.0-flash"
                          className="h-8 text-sm"
                        />
                      </div>
                    </div>
                  )}
                </div>

                {/* OpenRouter (FREE tier available) */}
                <div className="p-3 border rounded-lg space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={aiSettings.providers.openrouter.enabled}
                        onCheckedChange={(checked) => setAISettings({
                          ...aiSettings,
                          providers: { ...aiSettings.providers, openrouter: { ...aiSettings.providers.openrouter, enabled: checked } }
                        })}
                      />
                      <span className="font-medium">OpenRouter</span>
                      <span className="text-xs px-1.5 py-0.5 bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300 rounded">FREE</span>
                    </div>
                  </div>
                  {aiSettings.providers.openrouter.enabled && (
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">API Key</Label>
                        <Input
                          type="password"
                          value={aiSettings.providers.openrouter.api_key}
                          onChange={(e) => setAISettings({
                            ...aiSettings,
                            providers: { ...aiSettings.providers, openrouter: { ...aiSettings.providers.openrouter, api_key: e.target.value } }
                          })}
                          placeholder="sk-or-..."
                          className="h-8 text-sm"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Model</Label>
                        <Input
                          value={aiSettings.providers.openrouter.model}
                          onChange={(e) => setAISettings({
                            ...aiSettings,
                            providers: { ...aiSettings.providers, openrouter: { ...aiSettings.providers.openrouter, model: e.target.value } }
                          })}
                          placeholder="meta-llama/llama-3.1-8b-instruct:free"
                          className="h-8 text-sm"
                        />
                      </div>
                    </div>
                  )}
                </div>

                {/* OpenAI (Paid) */}
                <div className="p-3 border rounded-lg space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={aiSettings.providers.openai.enabled}
                        onCheckedChange={(checked) => setAISettings({
                          ...aiSettings,
                          providers: { ...aiSettings.providers, openai: { ...aiSettings.providers.openai, enabled: checked } }
                        })}
                      />
                      <span className="font-medium">OpenAI (ChatGPT)</span>
                      <span className="text-xs px-1.5 py-0.5 bg-yellow-100 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded">PAID</span>
                    </div>
                  </div>
                  {aiSettings.providers.openai.enabled && (
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">API Key</Label>
                        <Input
                          type="password"
                          value={aiSettings.providers.openai.api_key}
                          onChange={(e) => setAISettings({
                            ...aiSettings,
                            providers: { ...aiSettings.providers, openai: { ...aiSettings.providers.openai, api_key: e.target.value } }
                          })}
                          placeholder="sk-..."
                          className="h-8 text-sm"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Model</Label>
                        <Input
                          value={aiSettings.providers.openai.model}
                          onChange={(e) => setAISettings({
                            ...aiSettings,
                            providers: { ...aiSettings.providers, openai: { ...aiSettings.providers.openai, model: e.target.value } }
                          })}
                          placeholder="gpt-4o-mini"
                          className="h-8 text-sm"
                        />
                      </div>
                    </div>
                  )}
                </div>

                {/* Anthropic (Paid) */}
                <div className="p-3 border rounded-lg space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={aiSettings.providers.anthropic.enabled}
                        onCheckedChange={(checked) => setAISettings({
                          ...aiSettings,
                          providers: { ...aiSettings.providers, anthropic: { ...aiSettings.providers.anthropic, enabled: checked } }
                        })}
                      />
                      <span className="font-medium">Anthropic (Claude)</span>
                      <span className="text-xs px-1.5 py-0.5 bg-yellow-100 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300 rounded">PAID</span>
                    </div>
                  </div>
                  {aiSettings.providers.anthropic.enabled && (
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">API Key</Label>
                        <Input
                          type="password"
                          value={aiSettings.providers.anthropic.api_key}
                          onChange={(e) => setAISettings({
                            ...aiSettings,
                            providers: { ...aiSettings.providers, anthropic: { ...aiSettings.providers.anthropic, api_key: e.target.value } }
                          })}
                          placeholder="sk-ant-..."
                          className="h-8 text-sm"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Model</Label>
                        <Input
                          value={aiSettings.providers.anthropic.model}
                          onChange={(e) => setAISettings({
                            ...aiSettings,
                            providers: { ...aiSettings.providers, anthropic: { ...aiSettings.providers.anthropic, model: e.target.value } }
                          })}
                          placeholder="claude-3-5-sonnet-20241022"
                          className="h-8 text-sm"
                        />
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* Task Assignments */}
              <div className="space-y-3 pt-2 border-t">
                <div>
                  <Label className="text-sm font-medium">Task Assignments</Label>
                  <p className="text-xs text-muted-foreground">Assign each AI task to a specific provider.</p>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <Label className="text-xs">Pattern Learning</Label>
                    <select
                      className="w-full h-8 px-2 text-sm border rounded-md bg-background"
                      value={aiSettings.task_assignments.pattern_learning}
                      onChange={(e) => setAISettings({
                        ...aiSettings,
                        task_assignments: { ...aiSettings.task_assignments, pattern_learning: e.target.value }
                      })}
                    >
                      <option value="ollama">Ollama (Local)</option>
                      <option value="groq">Groq (Llama)</option>
                      <option value="gemini">Google Gemini</option>
                      <option value="openrouter">OpenRouter</option>
                      <option value="openai">OpenAI</option>
                      <option value="anthropic">Anthropic</option>
                    </select>
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">Stream Parsing</Label>
                    <select
                      className="w-full h-8 px-2 text-sm border rounded-md bg-background"
                      value={aiSettings.task_assignments.stream_parsing}
                      onChange={(e) => setAISettings({
                        ...aiSettings,
                        task_assignments: { ...aiSettings.task_assignments, stream_parsing: e.target.value }
                      })}
                    >
                      <option value="ollama">Ollama (Local)</option>
                      <option value="groq">Groq (Llama)</option>
                      <option value="gemini">Google Gemini</option>
                      <option value="openrouter">OpenRouter</option>
                      <option value="openai">OpenAI</option>
                      <option value="anthropic">Anthropic</option>
                    </select>
                  </div>
                </div>
              </div>

              {/* General Options */}
              <div className="space-y-2 pt-2 border-t">
                <Label className="text-sm font-medium">Options</Label>
                <div className="grid grid-cols-2 gap-4">
                  <div className="flex items-center gap-2">
                    <Switch
                      checked={aiSettings.learn_patterns}
                      onCheckedChange={(checked) => setAISettings({ ...aiSettings, learn_patterns: checked })}
                    />
                    <Label className="text-sm">Learn regex patterns from AI</Label>
                  </div>
                  <div className="flex items-center gap-2">
                    <Switch
                      checked={aiSettings.fallback_to_regex}
                      onCheckedChange={(checked) => setAISettings({ ...aiSettings, fallback_to_regex: checked })}
                    />
                    <Label className="text-sm">Fall back to builtin regex</Label>
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="batch-size" className="text-sm">Batch size</Label>
                    <Input
                      id="batch-size"
                      type="number"
                      min={1}
                      max={50}
                      value={aiSettings.batch_size}
                      onChange={(e) => setAISettings({ ...aiSettings, batch_size: parseInt(e.target.value) || 10 })}
                      className="w-20"
                    />
                  </div>
                </div>
              </div>

              {/* Test Parse Section */}
              <div className="space-y-2 pt-2 border-t">
                <Label>Test AI Parsing</Label>
                <textarea
                  className="w-full h-24 p-2 text-sm border rounded-md font-mono resize-none"
                  value={testStreams}
                  onChange={(e) => setTestStreams(e.target.value)}
                  placeholder="Paste stream names here (one per line) to test AI parsing..."
                />
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleTestParse}
                    disabled={testParseMutation.isPending || !aiStatusQuery.data?.available}
                  >
                    {testParseMutation.isPending ? (
                      <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                    ) : (
                      <TestTube className="h-4 w-4 mr-1" />
                    )}
                    Test Parse
                  </Button>
                </div>

                {/* Test Results */}
                {testParseMutation.data?.results && testParseMutation.data.results.length > 0 && (
                  <div className="mt-2 p-2 bg-muted/50 rounded-md text-xs font-mono max-h-48 overflow-auto">
                    {testParseMutation.data.results.map((result, i) => (
                      <div key={i} className="mb-2 pb-2 border-b last:border-0">
                        <div className="text-muted-foreground truncate">{result.stream}</div>
                        <div className="grid grid-cols-2 gap-1 mt-1">
                          {result.team1 && <span>Team 1: <strong>{result.team1}</strong></span>}
                          {result.team2 && <span>Team 2: <strong>{result.team2}</strong></span>}
                          {result.league && <span>League: <strong>{result.league}</strong></span>}
                          {result.sport && <span>Sport: <strong>{result.sport}</strong></span>}
                          <span className={result.confidence >= 0.7 ? "text-green-600" : result.confidence >= 0.5 ? "text-yellow-600" : "text-red-600"}>
                            Confidence: {(result.confidence * 100).toFixed(0)}%
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}

          <Button onClick={handleSaveAISettings} disabled={updateAI.isPending}>
            {updateAI.isPending ? (
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

      {/* Advanced Tab */}
      {activeTab === "advanced" && (
      <>
      <div className="mb-4">
        <h2 className="text-lg font-semibold">Advanced</h2>
        <p className="text-sm text-muted-foreground">Advanced configuration options</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>XMLTV Generator Metadata</CardTitle>
          <CardDescription>Customize XMLTV output file metadata</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="xmltv-name">XMLTV Generator Name</Label>
              <Input
                id="xmltv-name"
                value={display?.xmltv_generator_name ?? "Teamarr"}
                onChange={(e) => display && setDisplay({ ...display, xmltv_generator_name: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="xmltv-url">XMLTV Generator URL</Label>
              <Input
                id="xmltv-url"
                value={display?.xmltv_generator_url ?? "https://github.com/Pharaoh-Labs/teamarr"}
                onChange={(e) => display && setDisplay({ ...display, xmltv_generator_url: e.target.value })}
                placeholder="https://github.com/Pharaoh-Labs/teamarr"
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

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Update Notifications</CardTitle>
              <CardDescription>Check for new versions of Teamarr</CardDescription>
            </div>
            {updateInfoQuery.data?.update_available && (
              <Badge variant="warning">Update Available</Badge>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Current Version and Update Status */}
          <div className="flex items-center justify-between p-3 bg-muted/50 rounded-lg">
            <div>
              <p className="text-sm font-medium">
                Current Version: {updateInfoQuery.data?.current_version ?? "Loading..."}
              </p>
              {updateInfoQuery.data?.update_available && updateInfoQuery.data?.latest_version && (
                <p className="text-sm text-muted-foreground">
                  Latest: {updateInfoQuery.data.latest_version}
                  {updateInfoQuery.data.build_type === "dev" && " (dev)"}
                  {updateInfoQuery.data.latest_date && (
                    <span className="ml-2 text-xs">
                      ({formatDateTime(updateInfoQuery.data.latest_date)})
                    </span>
                  )}
                </p>
              )}
              {!updateInfoQuery.data?.update_available && updateInfoQuery.data?.latest_date && (
                <p className="text-xs text-muted-foreground">
                  Released: {formatDateTime(updateInfoQuery.data.latest_date)}
                </p>
              )}
              {updateInfoQuery.data?.checked_at && (
                <p className="text-xs text-muted-foreground">
                  Last checked: {formatRelativeTime(updateInfoQuery.data.checked_at)}
                </p>
              )}
            </div>
            <div className="flex gap-2">
              {updateInfoQuery.data?.update_available && updateInfoQuery.data?.download_url && (
                <Button
                  variant="default"
                  size="sm"
                  onClick={() => window.open(updateInfoQuery.data!.download_url!, "_blank")}
                >
                  <ExternalLink className="h-4 w-4 mr-1" />
                  View Update
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => forceCheckUpdates.mutate()}
                disabled={forceCheckUpdates.isPending}
              >
                {forceCheckUpdates.isPending ? (
                  <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4 mr-1" />
                )}
                Check Now
              </Button>
            </div>
          </div>

          {/* Update Check Settings */}
          <div className="space-y-4 pt-2 border-t">
            <div className="flex items-center gap-2">
              <Switch
                checked={updateCheck.enabled}
                onCheckedChange={(checked) => setUpdateCheck({ ...updateCheck, enabled: checked })}
              />
              <Label>Enable Automatic Update Checks</Label>
            </div>

            {updateCheck.enabled && (
              <>
                <div className="flex items-center gap-4 pl-6">
                  <div className="flex items-center gap-2">
                    <Switch
                      checked={updateCheck.notify_stable}
                      onCheckedChange={(checked) =>
                        setUpdateCheck({ ...updateCheck, notify_stable: checked })
                      }
                    />
                    <Label className="text-sm">Notify about stable releases</Label>
                  </div>
                  <div className="flex items-center gap-2">
                    <Switch
                      checked={updateCheck.notify_dev}
                      onCheckedChange={(checked) =>
                        setUpdateCheck({ ...updateCheck, notify_dev: checked })
                      }
                    />
                    <Label className="text-sm">Notify about dev builds</Label>
                  </div>
                </div>
              </>
            )}
          </div>

          <Button
            onClick={() => {
              updateUpdateCheck.mutate(updateCheck, {
                onSuccess: () => toast.success("Update check settings saved"),
                onError: () => toast.error("Failed to save update check settings"),
              })
            }}
            disabled={updateUpdateCheck.isPending}
          >
            {updateUpdateCheck.isPending ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-1" />
            )}
            Save
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Backup & Restore</CardTitle>
          <CardDescription>Download a backup of your database or restore from a previous backup</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex gap-4">
            <div className="flex-1">
              <Label className="text-sm font-medium">Download Backup</Label>
              <p className="text-xs text-muted-foreground mb-2">
                Download a copy of your current database including all teams, templates, groups, and settings.
              </p>
              <Button variant="outline" onClick={handleDownloadBackup}>
                <Download className="h-4 w-4 mr-2" />
                Download Backup
              </Button>
            </div>
            <div className="flex-1">
              <Label className="text-sm font-medium">Restore Backup</Label>
              <p className="text-xs text-muted-foreground mb-2">
                Upload a .db file to restore. A backup of your current data will be created first.
              </p>
              <input
                ref={fileInputRef}
                type="file"
                accept=".db"
                onChange={handleRestoreBackup}
                className="hidden"
              />
              <Button
                variant="outline"
                onClick={() => fileInputRef.current?.click()}
                disabled={isRestoring}
              >
                {isRestoring ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <Upload className="h-4 w-4 mr-2" />
                )}
                {isRestoring ? "Restoring..." : "Restore Backup"}
              </Button>
            </div>
          </div>
          <div className="rounded-md bg-amber-500/10 border border-amber-500/20 p-3">
            <div className="flex gap-2">
              <AlertTriangle className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
              <div className="text-sm text-amber-500">
                <p className="font-medium">Warning</p>
                <p className="text-xs">Restoring a backup will replace ALL current data. The application will need to be restarted for changes to take effect.</p>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
      </>
      )}

      </div>
    </div>
  )
}
