import { useState, useMemo } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { api } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import { Loader2, Tv, Eye, Plus, AlertCircle, Info, Check } from "lucide-react"
import { LeaguePicker } from "@/components/LeaguePicker"
import { ChannelProfileSelector } from "@/components/ChannelProfileSelector"
import { StreamProfileSelector } from "@/components/StreamProfileSelector"
import type { EPGSource } from "@/api/settings"

// Types
interface M3UAccount {
  id: number
  name: string
  url?: string
}

interface M3UGroup {
  id: number
  name: string
  stream_count?: number
}

interface Stream {
  id: number
  name: string
}

interface EnabledGroup {
  id: number
  m3u_group_id: number | null
  m3u_account_id: number | null
}

interface SelectedGroup {
  m3u_account_id: number
  m3u_account_name: string
  m3u_group_id: number
  m3u_group_name: string
  stream_count?: number
}

interface Template {
  id: number
  name: string
  template_type: string
}

interface ChannelGroup {
  id: number
  name: string
}

interface BulkCreateResponse {
  total_created: number
  total_failed: number
  created: Array<{ group_id: number; name: string; success: boolean }>
}

// Fetch functions
async function fetchM3UAccounts(): Promise<M3UAccount[]> {
  return api.get("/dispatcharr/m3u-accounts")
}

async function fetchM3UGroups(accountId: number): Promise<M3UGroup[]> {
  return api.get(`/dispatcharr/m3u-accounts/${accountId}/groups`)
}

async function fetchGroupStreams(
  accountId: number,
  groupId: number
): Promise<Stream[]> {
  return api.get(`/dispatcharr/m3u-accounts/${accountId}/groups/${groupId}/streams`)
}

async function fetchEnabledGroups(): Promise<EnabledGroup[]> {
  const response = await api.get<{ groups: EnabledGroup[] }>("/groups?include_disabled=true")
  return response.groups
}

async function fetchTemplates(): Promise<Template[]> {
  const response = await api.get<{ templates: Template[] }>("/templates")
  return response.templates
}

async function fetchChannelGroups(): Promise<ChannelGroup[]> {
  return api.get("/dispatcharr/channel-groups")
}

export function EventGroupImport() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [selectedAccount, setSelectedAccount] = useState<M3UAccount | null>(null)
  const [searchTerm, setSearchTerm] = useState("")
  const [previewGroup, setPreviewGroup] = useState<M3UGroup | null>(null)

  // Bulk selection state - persists across accounts and search
  const [selectedGroups, setSelectedGroups] = useState<Map<string, SelectedGroup>>(new Map())

  // Bulk import modal state
  const [showBulkModal, setShowBulkModal] = useState(false)
  const [bulkMode, setBulkMode] = useState<"single" | "multi">("single")
  const [bulkLeagues, setBulkLeagues] = useState<Set<string>>(new Set())
  const [bulkTemplateId, setBulkTemplateId] = useState<number | null>(null)
  const [bulkChannelGroupId, setBulkChannelGroupId] = useState<number | null>(null)
  const [bulkChannelGroupMode, setBulkChannelGroupMode] = useState<'static' | 'sport' | 'league'>('static')
  const [bulkChannelProfileIds, setBulkChannelProfileIds] = useState<(number | string)[]>([])
  const [bulkStreamProfileId, setBulkStreamProfileId] = useState<number | null>(null)
  const [bulkChannelSortOrder, setBulkChannelSortOrder] = useState<string>("time")
  const [bulkOverlapHandling, setBulkOverlapHandling] = useState<string>("add_stream")
  const [bulkCreateUnmatchedChannels, setBulkCreateUnmatchedChannels] = useState(false)
  const [bulkUnmatchedChannelEpgSourceId, setBulkUnmatchedChannelEpgSourceId] = useState<number | null>(null)
  const [bulkEnabled, setBulkEnabled] = useState(true)
  const [bulkImporting, setBulkImporting] = useState(false)

  // Queries
  const accountsQuery = useQuery({
    queryKey: ["dispatcharr-m3u-accounts"],
    queryFn: fetchM3UAccounts,
  })

  const groupsQuery = useQuery({
    queryKey: ["dispatcharr-m3u-groups", selectedAccount?.id],
    queryFn: () => fetchM3UGroups(selectedAccount!.id),
    enabled: !!selectedAccount,
  })

  const enabledQuery = useQuery({
    queryKey: ["event-groups-enabled"],
    queryFn: fetchEnabledGroups,
  })

  const streamsQuery = useQuery({
    queryKey: ["dispatcharr-group-streams", selectedAccount?.id, previewGroup?.id],
    queryFn: () => fetchGroupStreams(selectedAccount!.id, previewGroup!.id),
    enabled: !!selectedAccount && !!previewGroup,
  })

  const templatesQuery = useQuery({
    queryKey: ["templates"],
    queryFn: fetchTemplates,
  })

  const channelGroupsQuery = useQuery({
    queryKey: ["dispatcharr-channel-groups"],
    queryFn: fetchChannelGroups,
  })

  const epgSourcesQuery = useQuery({
    queryKey: ["dispatcharr-epg-sources"],
    queryFn: async () => {
      const res = await api.get<any>(
        "/dispatcharr/epg-sources?include_dummy=true",
      )
      return res.sources || []
    },
  })

  // Get set of already-enabled (account_id, group_id) pairs
  const enabledGroupKeys = new Set(
    (enabledQuery.data ?? [])
      .filter((g) => g.m3u_group_id !== null && g.m3u_account_id !== null)
      .map((g) => `${g.m3u_account_id}:${g.m3u_group_id}`)
  )

  // Filter groups by search (preserving original order from Dispatcharr)
  const filteredGroups = (groupsQuery.data ?? []).filter((g) =>
    g.name.toLowerCase().includes(searchTerm.toLowerCase())
  )

  // Get selectable groups (not already enabled)
  const selectableGroups = filteredGroups.filter(
    (g) => !enabledGroupKeys.has(`${selectedAccount?.id}:${g.id}`)
  )

  // Check if all visible selectable groups are selected
  const allVisibleSelected = selectedAccount && selectableGroups.length > 0 &&
    selectableGroups.every((g) => selectedGroups.has(`${selectedAccount.id}:${g.id}`))

  // Filter templates by type
  const eventTemplates = (templatesQuery.data ?? []).filter(
    (t) => t.template_type === "event"
  )

  // Selection helpers
  const toggleGroupSelection = (group: M3UGroup) => {
    if (!selectedAccount) return
    const key = `${selectedAccount.id}:${group.id}`
    const newSelected = new Map(selectedGroups)
    if (newSelected.has(key)) {
      newSelected.delete(key)
    } else {
      newSelected.set(key, {
        m3u_account_id: selectedAccount.id,
        m3u_account_name: selectedAccount.name,
        m3u_group_id: group.id,
        m3u_group_name: group.name,
        stream_count: group.stream_count,
      })
    }
    setSelectedGroups(newSelected)
  }

  const selectAllVisible = () => {
    if (!selectedAccount) return
    const newSelected = new Map(selectedGroups)
    for (const group of selectableGroups) {
      const key = `${selectedAccount.id}:${group.id}`
      if (!newSelected.has(key)) {
        newSelected.set(key, {
          m3u_account_id: selectedAccount.id,
          m3u_account_name: selectedAccount.name,
          m3u_group_id: group.id,
          m3u_group_name: group.name,
          stream_count: group.stream_count,
        })
      }
    }
    setSelectedGroups(newSelected)
  }

  const deselectAllVisible = () => {
    if (!selectedAccount) return
    const newSelected = new Map(selectedGroups)
    for (const group of selectableGroups) {
      newSelected.delete(`${selectedAccount.id}:${group.id}`)
    }
    setSelectedGroups(newSelected)
  }

  const clearAllSelections = () => {
    setSelectedGroups(new Map())
  }

  // Get selection summary by account
  const selectionByAccount = useMemo(() => {
    const byAccount: Record<string, number> = {}
    for (const [, group] of selectedGroups) {
      byAccount[group.m3u_account_name] = (byAccount[group.m3u_account_name] || 0) + 1
    }
    return byAccount
  }, [selectedGroups])

  // Handle single import (existing behavior)
  const handleImport = (group: M3UGroup) => {
    const params = new URLSearchParams({
      m3u_group_id: String(group.id),
      m3u_group_name: group.name,
      m3u_account_id: String(selectedAccount!.id),
      m3u_account_name: selectedAccount!.name,
    })
    navigate(`/event-groups/new?${params.toString()}`)
  }

  // Handle bulk import
  const handleBulkImport = async () => {
    if (bulkLeagues.size === 0) return

    setBulkImporting(true)
    try {
      const response = await api.post<BulkCreateResponse>("/groups/bulk", {
        groups: Array.from(selectedGroups.values()).map((g) => ({
          m3u_group_id: g.m3u_group_id,
          m3u_group_name: g.m3u_group_name,
          m3u_account_id: g.m3u_account_id,
          m3u_account_name: g.m3u_account_name,
        })),
        settings: {
          group_mode: bulkMode,
          leagues: Array.from(bulkLeagues),
          template_id: bulkTemplateId,
          channel_group_id: bulkChannelGroupMode === 'static' ? bulkChannelGroupId : null,
          channel_group_mode: bulkChannelGroupMode,
          channel_profile_ids: bulkChannelProfileIds.length > 0 ? bulkChannelProfileIds : null,
          stream_profile_id: bulkStreamProfileId,
          channel_sort_order: bulkChannelSortOrder,
          overlap_handling: bulkOverlapHandling,
          create_unmatched_channels: bulkCreateUnmatchedChannels,
          unmatched_channel_epg_source_id: bulkUnmatchedChannelEpgSourceId,
          enabled: bulkEnabled,
        },
      })

      // Refresh queries
      await queryClient.invalidateQueries({ queryKey: ["event-groups-enabled"] })
      await queryClient.invalidateQueries({ queryKey: ["event-groups"] })

      // Clear selections and close modal
      setSelectedGroups(new Map())
      setShowBulkModal(false)

      // Show success or navigate
      if (response.total_created > 0) {
        navigate("/event-groups")
      }
    } catch (error) {
      console.error("Bulk import failed:", error)
    } finally {
      setBulkImporting(false)
    }
  }

  // Reset bulk modal state
  const openBulkModal = () => {
    setBulkMode("single")
    setBulkLeagues(new Set())
    setBulkTemplateId(null)
    setBulkChannelGroupId(null)
    setBulkChannelGroupMode('static')
    setBulkChannelProfileIds([])
    setBulkChannelSortOrder("time")
    setBulkOverlapHandling("add_stream")
    setBulkCreateUnmatchedChannels(false)
    setBulkUnmatchedChannelEpgSourceId(null)
    setBulkEnabled(true)
    setShowBulkModal(true)
  }

  const isDispatcharrConfigured = accountsQuery.data && accountsQuery.data.length > 0

  return (
    <div className="flex h-[calc(100vh-4rem)] overflow-hidden">
      {/* Left Sidebar - M3U Accounts */}
      <div className="w-60 border-r bg-muted/30 overflow-y-auto flex-shrink-0">
        <div className="p-3 border-b">
          <h2 className="text-xs font-semibold uppercase text-muted-foreground">
            M3U Accounts
          </h2>
        </div>

        {accountsQuery.isLoading ? (
          <div className="flex items-center justify-center p-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : accountsQuery.error ? (
          <div className="p-4 text-center">
            <AlertCircle className="h-8 w-8 text-destructive mx-auto mb-2" />
            <p className="text-sm text-destructive">Connection failed</p>
            <p className="text-xs text-muted-foreground mt-1">
              Check Dispatcharr settings
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => navigate("/settings")}
            >
              Settings
            </Button>
          </div>
        ) : !isDispatcharrConfigured ? (
          <div className="p-4 text-center">
            <Tv className="h-8 w-8 text-muted-foreground mx-auto mb-2" />
            <p className="text-sm text-muted-foreground">No M3U accounts found</p>
            <p className="text-xs text-muted-foreground mt-1">
              Add accounts in Dispatcharr
            </p>
          </div>
        ) : (
          <div className="py-1">
            {[...accountsQuery.data].sort((a, b) => a.name.localeCompare(b.name)).map((account) => {
              const accountSelectionCount = Array.from(selectedGroups.values())
                .filter((g) => g.m3u_account_id === account.id).length
              return (
                <button
                  key={account.id}
                  onClick={() => {
                    setSelectedAccount(account)
                    setSearchTerm("")
                  }}
                  className={cn(
                    "w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-muted/50 border-l-2 border-transparent",
                    selectedAccount?.id === account.id &&
                    "bg-muted border-l-primary"
                  )}
                >
                  <Tv className="h-4 w-4 text-muted-foreground" />
                  <span className="truncate flex-1 text-left">{account.name}</span>
                  {accountSelectionCount > 0 && (
                    <Badge variant="secondary" className="h-5 text-xs">
                      {accountSelectionCount}
                    </Badge>
                  )}
                </button>
              )
            })}
          </div>
        )}
      </div>

      {/* Main Content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {!selectedAccount ? (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            <div className="text-center">
              <h3 className="text-lg font-medium mb-1">Select an M3U account</h3>
              <p className="text-sm">
                Choose an account from the sidebar to view and import groups
              </p>
            </div>
          </div>
        ) : (
          <>
            {/* Header */}
            <div className="border-b p-4">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h1 className="text-xl font-bold">{selectedAccount.name}</h1>
                  <p className="text-sm text-muted-foreground">
                    {groupsQuery.data?.length ?? 0} groups
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {selectableGroups.length > 0 && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={allVisibleSelected ? deselectAllVisible : selectAllVisible}
                    >
                      {allVisibleSelected ? "Deselect All" : "Select All"}
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => groupsQuery.refetch()}
                    disabled={groupsQuery.isFetching}
                  >
                    {groupsQuery.isFetching ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      "Reload"
                    )}
                  </Button>
                </div>
              </div>
              <Input
                placeholder="Search groups..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="max-w-md"
              />
            </div>

            {/* Groups Grid */}
            <div className="flex-1 overflow-y-auto p-4 pb-20">
              {groupsQuery.isLoading ? (
                <div className="flex items-center justify-center p-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : groupsQuery.error ? (
                <div className="text-center text-destructive p-8">
                  Failed to load groups
                </div>
              ) : filteredGroups.length === 0 ? (
                <div className="text-center text-muted-foreground p-8">
                  {searchTerm ? "No groups match your search" : "No groups found"}
                </div>
              ) : (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-2">
                  {filteredGroups.map((group) => {
                    const key = `${selectedAccount.id}:${group.id}`
                    const isEnabled = enabledGroupKeys.has(key)
                    const isSelected = selectedGroups.has(key)

                    return (
                      <div
                        key={group.id}
                        className={cn(
                          "p-3 rounded-md border transition-colors relative",
                          isEnabled
                            ? "opacity-60 border-green-500/50 bg-green-500/5"
                            : isSelected
                              ? "border-primary bg-primary/5"
                              : "hover:border-primary/50"
                        )}
                      >
                        {/* Checkbox for non-enabled groups */}
                        {!isEnabled && (
                          <div className="absolute top-2 left-2">
                            <Checkbox
                              checked={isSelected}
                              onClick={(e) => {
                                e.stopPropagation()
                                toggleGroupSelection(group)
                              }}
                            />
                          </div>
                        )}

                        <div className="flex items-start justify-between gap-2 mb-2 ml-6">
                          <div className="min-w-0 flex-1">
                            <div className="font-medium text-sm truncate flex items-center gap-1">
                              {group.name}
                              {isEnabled && (
                                <span className="inline-flex items-center gap-0.5 text-[10px] bg-green-500/20 text-green-600 px-1 rounded">
                                  <Check className="h-2.5 w-2.5" />
                                  Imported
                                </span>
                              )}
                            </div>
                            <div className="text-xs text-muted-foreground">
                              ID: {group.id}
                            </div>
                          </div>
                        </div>

                        <div className="flex items-center justify-between ml-6">
                          <span className="text-xs text-muted-foreground">
                            {group.stream_count ?? "?"} streams
                          </span>
                          <div className="flex gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 px-2"
                              onClick={(e) => {
                                e.stopPropagation()
                                setPreviewGroup(group)
                              }}
                            >
                              <Eye className="h-3.5 w-3.5" />
                            </Button>
                            {!isEnabled && (
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 px-2 text-green-600 hover:text-green-700 hover:bg-green-500/10"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  handleImport(group)
                                }}
                              >
                                <Plus className="h-3.5 w-3.5" />
                              </Button>
                            )}
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {/* Floating Action Bar */}
      {selectedGroups.size > 0 && (
        <div className="fixed bottom-0 left-60 right-0 border-t bg-background p-3 flex items-center justify-between shadow-lg z-50">
          <div className="flex items-center gap-4">
            <Checkbox
              checked={selectedGroups.size > 0}
              onClick={clearAllSelections}
            />
            <span className="text-sm font-medium">
              {selectedGroups.size} selected
              {Object.keys(selectionByAccount).length > 1 && (
                <span className="text-muted-foreground ml-1">
                  ({Object.entries(selectionByAccount).map(([name, count]) => `${count} from ${name}`).join(", ")})
                </span>
              )}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={clearAllSelections}>
              Clear All
            </Button>
            <Button onClick={openBulkModal}>
              Import {selectedGroups.size} Groups
            </Button>
          </div>
        </div>
      )}

      {/* Preview Modal */}
      <Dialog open={!!previewGroup} onOpenChange={() => setPreviewGroup(null)}>
        <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col" onClose={() => setPreviewGroup(null)}>
          <DialogHeader>
            <DialogTitle>Preview: {previewGroup?.name}</DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-hidden flex flex-col">
            {streamsQuery.isLoading ? (
              <div className="flex items-center justify-center p-8">
                <Loader2 className="h-6 w-6 animate-spin" />
              </div>
            ) : streamsQuery.error ? (
              <div className="text-center text-destructive p-8">
                Failed to load streams
              </div>
            ) : (
              <>
                <div className="text-sm text-muted-foreground mb-3">
                  {streamsQuery.data?.length ?? 0} streams
                </div>
                <div className="flex-1 overflow-y-auto border rounded-md">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-muted">
                      <tr>
                        <th className="text-left p-2 font-medium">Stream Name</th>
                        <th className="text-left p-2 font-medium w-24">ID</th>
                      </tr>
                    </thead>
                    <tbody>
                      {streamsQuery.data?.map((stream) => (
                        <tr key={stream.id} className="border-t">
                          <td className="p-2 truncate max-w-md" title={stream.name}>
                            {stream.name}
                          </td>
                          <td className="p-2 text-muted-foreground">
                            {stream.id}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Bulk Import Modal */}
      <Dialog open={showBulkModal} onOpenChange={setShowBulkModal}>
        <DialogContent className="max-w-3xl max-h-[85vh] flex flex-col" onClose={() => setShowBulkModal(false)}>
          <DialogHeader>
            <DialogTitle>Import {selectedGroups.size} Groups</DialogTitle>
            <DialogDescription>
              <div className="flex items-start gap-2 mt-2 p-3 bg-muted/50 rounded-md">
                <Info className="h-4 w-4 text-muted-foreground mt-0.5 flex-shrink-0" />
                <span className="text-sm text-muted-foreground">
                  All groups will use the same settings. You can customize individual groups after import.
                </span>
              </div>
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto space-y-6 px-1">
            {/* Group Type */}
            <div className="space-y-3">
              <Label className="text-sm font-medium">Group Type</Label>
              <div className="grid grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={() => {
                    setBulkMode("single")
                    setBulkLeagues(new Set())
                  }}
                  className={cn(
                    "flex flex-col items-start p-3 rounded-lg border-2 text-left transition-all",
                    bulkMode === "single"
                      ? "border-primary bg-primary/5"
                      : "border-border hover:border-primary/50"
                  )}
                >
                  <span className="font-medium text-sm">Single League</span>
                  <span className="text-xs text-muted-foreground mt-1">
                    One league per group (NFL, EPL, etc.)
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setBulkMode("multi")
                    setBulkLeagues(new Set())
                  }}
                  className={cn(
                    "flex flex-col items-start p-3 rounded-lg border-2 text-left transition-all",
                    bulkMode === "multi"
                      ? "border-primary bg-primary/5"
                      : "border-border hover:border-primary/50"
                  )}
                >
                  <span className="font-medium text-sm">Multi-Sport</span>
                  <span className="text-xs text-muted-foreground mt-1">
                    Multiple leagues (ESPN+, etc.)
                  </span>
                </button>
              </div>
            </div>

            {/* League Selection */}
            <div className="space-y-3">
              <Label className="text-sm font-medium">
                {bulkMode === "single" ? "League" : "Leagues"}
              </Label>

              <LeaguePicker
                selectedLeagues={Array.from(bulkLeagues)}
                onSelectionChange={(leagues) => setBulkLeagues(new Set(leagues))}
                singleSelect={bulkMode === "single"}
                maxHeight="max-h-48"
                maxBadges={5}
              />
            </div>

            {/* Settings */}
            <div className="space-y-4">
              <Label className="text-sm font-medium">Settings</Label>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground">Template</Label>
                  <Select
                    value={bulkTemplateId?.toString() || ""}
                    onChange={(e) => setBulkTemplateId(e.target.value ? parseInt(e.target.value) : null)}
                  >
                    <option value="">None</option>
                    {eventTemplates.map((t) => (
                      <option key={t.id} value={t.id}>{t.name}</option>
                    ))}
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground">Channel Group</Label>
                  <div className="space-y-2">
                    {/* Static group option */}
                    <div>
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="radio"
                          name="bulk_channel_group_mode"
                          checked={bulkChannelGroupMode === "static"}
                          onChange={() => setBulkChannelGroupMode("static")}
                          className="accent-primary"
                        />
                        <span className="text-sm">Existing group</span>
                      </label>
                      <div className={`mt-1 ml-6 ${bulkChannelGroupMode !== "static" ? "opacity-40 pointer-events-none" : ""}`}>
                        <Select
                          value={bulkChannelGroupId?.toString() ?? ""}
                          onChange={(e) => setBulkChannelGroupId(e.target.value ? parseInt(e.target.value) : null)}
                          disabled={bulkChannelGroupMode !== "static"}
                        >
                          <option value="">None</option>
                          {(channelGroupsQuery.data ?? []).map((g) => (
                            <option key={g.id} value={g.id}>{g.name}</option>
                          ))}
                        </Select>
                      </div>
                    </div>

                    {/* Dynamic group options */}
                    <div className="border-t pt-2 mt-2">
                      <div className="text-xs font-medium text-muted-foreground mb-1">Dynamic Groups</div>
                      <label className="flex items-center gap-2 cursor-pointer py-1">
                        <input
                          type="radio"
                          name="bulk_channel_group_mode"
                          checked={bulkChannelGroupMode === "sport"}
                          onChange={() => {
                            setBulkChannelGroupMode("sport")
                            setBulkChannelGroupId(null)
                          }}
                          className="accent-primary"
                        />
                        <div>
                          <code className="text-xs font-medium bg-muted px-1 rounded">{"{sport}"}</code>
                          <span className="text-xs text-muted-foreground ml-1">by sport name</span>
                        </div>
                      </label>
                      <label className="flex items-center gap-2 cursor-pointer py-1">
                        <input
                          type="radio"
                          name="bulk_channel_group_mode"
                          checked={bulkChannelGroupMode === "league"}
                          onChange={() => {
                            setBulkChannelGroupMode("league")
                            setBulkChannelGroupId(null)
                          }}
                          className="accent-primary"
                        />
                        <div>
                          <code className="text-xs font-medium bg-muted px-1 rounded">{"{league}"}</code>
                          <span className="text-xs text-muted-foreground ml-1">by league name</span>
                        </div>
                      </label>
                    </div>
                  </div>
                </div>
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground">Channel Profiles</Label>
                  <ChannelProfileSelector
                    selectedIds={bulkChannelProfileIds}
                    onChange={setBulkChannelProfileIds}
                  />
                </div>
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground">Stream Profile</Label>
                  <StreamProfileSelector
                    value={bulkStreamProfileId}
                    onChange={setBulkStreamProfileId}
                  />
                </div>
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground">Enabled</Label>
                  <div className="flex items-center gap-2 h-9">
                    <Switch
                      checked={bulkEnabled}
                      onCheckedChange={setBulkEnabled}
                    />
                    <span className="text-sm">{bulkEnabled ? "Yes" : "No"}</span>
                  </div>
                </div>
              </div>

              {/* Multi-sport options */}
              {bulkMode === "multi" && (
                <div className="grid grid-cols-2 gap-4 pt-2 border-t">
                  <div className="space-y-2">
                    <Label className="text-xs text-muted-foreground">Channel Sort Order</Label>
                    <Select
                      value={bulkChannelSortOrder}
                      onChange={(e) => setBulkChannelSortOrder(e.target.value)}
                    >
                      <option value="time">Time</option>
                      <option value="sport_time">Sport → Time</option>
                      <option value="league_time">League → Time</option>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label className="text-xs text-muted-foreground">Overlap Handling</Label>
                    <Select
                      value={bulkOverlapHandling}
                      onChange={(e) => setBulkOverlapHandling(e.target.value)}
                    >
                      <option value="add_stream">Add stream to existing channel</option>
                      <option value="add_only">Add only (skip if no existing)</option>
                      <option value="create_all">Create all (ignore overlap)</option>
                      <option value="skip">Skip overlapping events</option>
                    </Select>
                  </div>
                </div>
              )}

              {/* Unmatched Stream Handling */}
              <div className="pt-2 border-t space-y-3">
                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label className="text-sm">Create Channels for Unmatched Streams</Label>
                    <p className="text-xs text-muted-foreground">
                      Create channels for streams that don't match any events
                    </p>
                  </div>
                  <Switch
                    checked={bulkCreateUnmatchedChannels}
                    onCheckedChange={setBulkCreateUnmatchedChannels}
                  />
                </div>

                {bulkCreateUnmatchedChannels && (
                  <div className="space-y-2 pt-2">
                    <Label className="text-xs text-muted-foreground">Dummy EPG Source</Label>
                    <Select
                      value={bulkUnmatchedChannelEpgSourceId?.toString() || ""}
                      onChange={(e) => setBulkUnmatchedChannelEpgSourceId(e.target.value ? parseInt(e.target.value) : null)}
                    >
                      <option value="">Select a dummy EPG source...</option>
                      {(epgSourcesQuery.data ?? []).filter((s: EPGSource) => s.source_type === "dummy").map((source: EPGSource) => (
                        <option key={source.id} value={source.id}>
                          {source.name}
                        </option>
                      ))}
                    </Select>
                    <p className="text-xs text-muted-foreground">
                      Channels created for unmatched streams will use this EPG source for placeholder data.
                      {epgSourcesQuery.data && !(epgSourcesQuery.data as EPGSource[]).some((s: EPGSource) => s.source_type === "dummy") && (
                        <span className="text-destructive block mt-1">
                          No dummy EPG sources found. Please create one in Dispatcharr first.
                        </span>
                      )}
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* Groups to import */}
            <div className="space-y-2">
              <Label className="text-sm font-medium">Groups to import</Label>
              <div className="max-h-32 overflow-y-auto border rounded-md p-2 space-y-1">
                {Array.from(selectedGroups.values()).map((group) => (
                  <div key={`${group.m3u_account_id}:${group.m3u_group_id}`} className="flex items-center justify-between text-sm">
                    <span className="truncate">{group.m3u_group_name}</span>
                    <span className="text-xs text-muted-foreground ml-2 flex-shrink-0">
                      {group.stream_count ?? "?"} streams
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-4 border-t">
            <Button variant="ghost" onClick={() => setShowBulkModal(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleBulkImport}
              disabled={bulkImporting || bulkLeagues.size === 0}
            >
              {bulkImporting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                  Importing...
                </>
              ) : (
                `Import ${selectedGroups.size} Groups`
              )}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
