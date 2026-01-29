import { useState, useMemo } from "react"
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { api } from "@/api/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import { Loader2, Tv, Eye, AlertCircle, Check } from "lucide-react"

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

interface RegularTVGroup {
  id: number
  name: string // This is the m3u_group_name
  m3u_account_id: number
}

interface SelectedGroup {
  m3u_account_id: number
  m3u_account_name: string
  m3u_group_id: number // from dispatcharr group, used for unique key
  m3u_group_name: string
  stream_count?: number
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

async function fetchImportedGroups(): Promise<RegularTVGroup[]> {
  return api.get("/regular-tv/groups")
}

export function RegularTVImport() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [selectedAccount, setSelectedAccount] = useState<M3UAccount | null>(null)
  const [searchTerm, setSearchTerm] = useState("")
  const [previewGroup, setPreviewGroup] = useState<M3UGroup | null>(null)
  const [selectedGroups, setSelectedGroups] = useState<Map<string, SelectedGroup>>(new Map())

  // Queries
  const accountsQuery = useQuery({
    queryKey: ["dispatcharr-m3u-accounts"],
    queryFn: fetchM3UAccounts,
  })

  const m3uGroupsQuery = useQuery({
    queryKey: ["dispatcharr-m3u-groups", selectedAccount?.id],
    queryFn: () => fetchM3UGroups(selectedAccount!.id),
    enabled: !!selectedAccount,
  })

  const importedGroupsQuery = useQuery({
    queryKey: ["regular-tv-groups-imported"],
    queryFn: fetchImportedGroups,
  })

  const streamsQuery = useQuery({
    queryKey: ["dispatcharr-group-streams", selectedAccount?.id, previewGroup?.id],
    queryFn: () => fetchGroupStreams(selectedAccount!.id, previewGroup!.id),
    enabled: !!selectedAccount && !!previewGroup,
  })

  const createRegularTVGroups = useMutation({
    mutationFn: async (groups: { name: string; m3u_group_name: string; m3u_account_id: number; m3u_group_id: number }[]) => {
      return api.post("/regular-tv/groups/bulk", { groups })
    },
    onSuccess: () => {
      toast.success("Groups imported successfully")
      queryClient.invalidateQueries({ queryKey: ["regular-tv-groups"] })
      queryClient.invalidateQueries({ queryKey: ["regular-tv-groups-imported"] })
      setSelectedGroups(new Map())
      navigate("/regular-tv")
    },
    onError: (err: Error) => {
      const apiError = err as any
      const message = apiError?.response?.data?.detail || apiError.message || "Failed to import groups"
      toast.error(message)
    },
  })

  const importedGroupKeys = useMemo(() => new Set(
    (importedGroupsQuery.data ?? []).map((g) => `${g.m3u_account_id}:${g.name}`)
  ), [importedGroupsQuery.data])

  const filteredGroups = (m3uGroupsQuery.data ?? []).filter((g) =>
    g.name.toLowerCase().includes(searchTerm.toLowerCase())
  )

  const selectableGroups = filteredGroups.filter(
    (g) => !importedGroupKeys.has(`${selectedAccount?.id}:${g.name}`)
  )

  const allVisibleSelected = selectedAccount && selectableGroups.length > 0 &&
    selectableGroups.every((g) => selectedGroups.has(`${selectedAccount.id}:${g.id}`))

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

  const selectionByAccount = useMemo(() => {
    const byAccount: Record<string, number> = {}
    for (const [, group] of selectedGroups) {
      byAccount[group.m3u_account_name] = (byAccount[group.m3u_account_name] || 0) + 1
    }
    return byAccount
  }, [selectedGroups])

  const handleBulkImport = () => {
    if (selectedGroups.size === 0) return

    const groupsToCreate = Array.from(selectedGroups.values()).map(g => ({
      name: g.m3u_group_name,
      m3u_group_name: g.m3u_group_name,
      m3u_account_id: g.m3u_account_id,
      m3u_group_id: g.m3u_group_id,
    }))
    createRegularTVGroups.mutate(groupsToCreate)
  }

  const isDispatcharrConfigured = accountsQuery.data && accountsQuery.data.length > 0

  return (
    <div className="flex h-[calc(100vh-4rem)] overflow-hidden">
      <div className="w-60 border-r bg-muted/30 overflow-y-auto flex-shrink-0">
        <div className="p-3 border-b flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase text-muted-foreground">
            M3U Accounts
          </h2>
          <Button variant="ghost" size="sm" onClick={() => navigate("/regular-tv")}>Back</Button>
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
            <div className="border-b p-4">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h1 className="text-xl font-bold">{selectedAccount.name}</h1>
                  <p className="text-sm text-muted-foreground">
                    {m3uGroupsQuery.data?.length ?? 0} groups
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
                    onClick={() => m3uGroupsQuery.refetch()}
                    disabled={m3uGroupsQuery.isFetching}
                  >
                    {m3uGroupsQuery.isFetching ? (
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

            <div className="flex-1 overflow-y-auto p-4 pb-20">
              {m3uGroupsQuery.isLoading ? (
                <div className="flex items-center justify-center p-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : m3uGroupsQuery.error ? (
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
                    const isImported = importedGroupKeys.has(`${selectedAccount.id}:${group.name}`)
                    const isSelected = selectedGroups.has(key)

                    return (
                      <div
                        key={group.id}
                        className={cn(
                          "p-3 rounded-md border transition-colors relative",
                          isImported
                            ? "opacity-60 border-green-500/50 bg-green-500/5"
                            : isSelected
                            ? "border-primary bg-primary/5"
                            : "hover:border-primary/50"
                        )}
                      >
                        {!isImported && (
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
                              {isImported && (
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
            <Button onClick={handleBulkImport} disabled={createRegularTVGroups.isPending}>
              {createRegularTVGroups.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Import {selectedGroups.size} Groups
            </Button>
          </div>
        </div>
      )}

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