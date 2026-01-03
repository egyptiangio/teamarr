import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { api } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import { Loader2, Tv, Eye, Plus, Check, AlertCircle } from "lucide-react"

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

// Fetch M3U accounts from Dispatcharr
async function fetchM3UAccounts(): Promise<M3UAccount[]> {
  return api.get("/dispatcharr/m3u-accounts")
}

// Fetch groups for an M3U account
async function fetchM3UGroups(accountId: number): Promise<M3UGroup[]> {
  return api.get(`/dispatcharr/m3u-accounts/${accountId}/groups`)
}

// Fetch streams in a group (for preview)
async function fetchGroupStreams(
  accountId: number,
  groupId: number
): Promise<Stream[]> {
  return api.get(`/dispatcharr/m3u-accounts/${accountId}/groups/${groupId}/streams`)
}

// Fetch enabled event groups to check which M3U groups are already imported
async function fetchEnabledGroups(): Promise<EnabledGroup[]> {
  const response = await api.get<{ groups: EnabledGroup[] }>("/groups?include_disabled=true")
  return response.groups
}

export function EventGroupImport() {
  const navigate = useNavigate()
  const [selectedAccount, setSelectedAccount] = useState<M3UAccount | null>(null)
  const [searchTerm, setSearchTerm] = useState("")
  const [previewGroup, setPreviewGroup] = useState<M3UGroup | null>(null)

  // Fetch M3U accounts
  const accountsQuery = useQuery({
    queryKey: ["dispatcharr-m3u-accounts"],
    queryFn: fetchM3UAccounts,
  })

  // Fetch groups for selected account
  const groupsQuery = useQuery({
    queryKey: ["dispatcharr-m3u-groups", selectedAccount?.id],
    queryFn: () => fetchM3UGroups(selectedAccount!.id),
    enabled: !!selectedAccount,
  })

  // Fetch enabled groups
  const enabledQuery = useQuery({
    queryKey: ["event-groups-enabled"],
    queryFn: fetchEnabledGroups,
  })

  // Fetch streams for preview
  const streamsQuery = useQuery({
    queryKey: ["dispatcharr-group-streams", selectedAccount?.id, previewGroup?.id],
    queryFn: () => fetchGroupStreams(selectedAccount!.id, previewGroup!.id),
    enabled: !!selectedAccount && !!previewGroup,
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

  const handleImport = (group: M3UGroup) => {
    // Navigate to event group form with M3U group data
    const params = new URLSearchParams({
      m3u_group_id: String(group.id),
      m3u_group_name: group.name,
      m3u_account_id: String(selectedAccount!.id),
      m3u_account_name: selectedAccount!.name,
    })
    navigate(`/event-groups/new?${params.toString()}`)
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
            {[...accountsQuery.data].sort((a, b) => a.name.localeCompare(b.name)).map((account) => (
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
              </button>
            ))}
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
              <Input
                placeholder="Search groups..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="max-w-md"
              />
            </div>

            {/* Groups Grid */}
            <div className="flex-1 overflow-y-auto p-4">
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
                    const isEnabled = enabledGroupKeys.has(`${selectedAccount!.id}:${group.id}`)

                    return (
                      <div
                        key={group.id}
                        className={cn(
                          "p-3 rounded-md border transition-colors",
                          isEnabled
                            ? "opacity-60 border-green-500/50 bg-green-500/5"
                            : "hover:border-primary/50 cursor-pointer"
                        )}
                        onClick={() => !isEnabled && handleImport(group)}
                      >
                        <div className="flex items-start justify-between gap-2 mb-2">
                          <div className="min-w-0 flex-1">
                            <div className="font-medium text-sm truncate flex items-center gap-1">
                              {group.name}
                              {isEnabled && (
                                <span className="inline-flex items-center gap-0.5 text-[10px] bg-green-500/20 text-green-600 px-1 rounded">
                                  <Check className="h-2.5 w-2.5" />
                                  Enabled
                                </span>
                              )}
                            </div>
                            <div className="text-xs text-muted-foreground">
                              ID: {group.id}
                            </div>
                          </div>
                        </div>

                        <div className="flex items-center justify-between">
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
    </div>
  )
}
