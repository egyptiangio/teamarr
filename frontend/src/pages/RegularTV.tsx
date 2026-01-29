import { useState, useEffect } from "react"
import { useQueryClient, useMutation, useQuery } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Import, Loader2, Trash2, Pencil, Save, Play, ChevronDown, ChevronUp, Copy, Check, Download, CheckCircle, XCircle } from "lucide-react"
import { api } from "@/api/client"
import { useDispatcharrStatus, useDispatcharrEPGSources } from "@/hooks/useSettings"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

interface GeneratePlaylistResponse {
  matched_streams: number
  excluded_streams: number
  warnings?: string[]
}

export function RegularTV() {
  const navigate = useNavigate()
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false)
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false)
  const [selectedGroup, setSelectedGroup] = useState<{ id: number; name: string } | null>(null)
  const [editForm, setEditForm] = useState<{ id: number; name: string; epg_source_id: number | null; enabled: boolean }>({ id: 0, name: "", epg_source_id: null, enabled: true })
  const [showM3U, setShowM3U] = useState(false)
  const [showExcludedM3U, setShowExcludedM3U] = useState(false)
  const [copied, setCopied] = useState(false)
  const [copiedExcluded, setCopiedExcluded] = useState(false)
  const queryClient = useQueryClient()
  
  const dispatcharrStatus = useDispatcharrStatus()
  const epgSourcesQuery = useDispatcharrEPGSources(dispatcharrStatus.data?.connected ?? false)

  const regularTVSettingsQuery = useQuery({
    queryKey: ["regular-tv-settings"],
    queryFn: async () => {
      const res = await fetch("/api/v1/regular-tv/settings")
      if (!res.ok) throw new Error("Failed to fetch Regular TV Group settings")
      return res.json() as Promise<{
        lookback_hours: number
        lookahead_hours: number
        epg_source_id: number | null
      }>
    },
  })

  const regularTVGroupsQuery = useQuery({
    queryKey: ["regular-tv-groups"],
    queryFn: async () => {
      const res = await fetch("/api/v1/regular-tv/groups")
      if (!res.ok) throw new Error("Failed to fetch regular TV groups")
      return res.json() as Promise<{ 
        id: number; name: string; m3u_account_id: number; epg_source_id: number | null; enabled: boolean 
      }[]>
    },
  })

  const m3uQuery = useQuery({
    queryKey: ["regular-tv-m3u"],
    queryFn: async () => {
      const res = await fetch("/api/v1/regular-tv/playlist")
      // If the endpoint doesn't exist yet (404) or file is missing, return empty playlist
      if (res.status === 404) return "#EXTM3U\n"
      if (!res.ok) throw new Error("Failed to fetch playlist")
      return res.text()
    },
    enabled: showM3U,
    retry: false,
  })

  const excludedM3uQuery = useQuery({
    queryKey: ["regular-tv-excluded-m3u"],
    queryFn: async () => {
      const res = await fetch("/api/v1/regular-tv/playlist/excluded")
      if (res.status === 404) return "#EXTM3U\n"
      if (!res.ok) throw new Error("Failed to fetch excluded playlist")
      return res.text()
    },
    enabled: showExcludedM3U,
    retry: false,
  })

  const regularTVStatsQuery = useQuery({
    queryKey: ["regular-tv-stats"],
    queryFn: async () => {
      const res = await fetch("/api/v1/regular-tv/stats")
      if (!res.ok) return null
      return res.json() as Promise<{ included: number; excluded: number }>
    },
  })

  const handleCopyM3U = async () => {
    if (m3uQuery.data) {
      await navigator.clipboard.writeText(m3uQuery.data)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
      toast.success("M3U copied to clipboard")
    }
  }

  const handleDownloadM3U = () => {
    if (!m3uQuery.data) return
    const blob = new Blob([m3uQuery.data], { type: "text/plain" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = "regular-tv.m3u"
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    toast.success("M3U downloaded")
  }

  const handleCopyExcludedM3U = async () => {
    if (excludedM3uQuery.data) {
      await navigator.clipboard.writeText(excludedM3uQuery.data)
      setCopiedExcluded(true)
      setTimeout(() => setCopiedExcluded(false), 2000)
      toast.success("Excluded M3U copied to clipboard")
    }
  }

  const handleDownloadExcludedM3U = () => {
    if (!excludedM3uQuery.data) return
    const blob = new Blob([excludedM3uQuery.data], { type: "text/plain" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = "regular-tv-excluded.m3u"
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    toast.success("Excluded M3U downloaded")
  }

  const deleteRegularTVGroup = useMutation({
    mutationFn: async (groupId: number) => {
      const res = await fetch(`/api/v1/regular-tv/groups/${groupId}`, { method: "DELETE" })
      if (!res.ok) throw new Error("Failed to delete group")
    },
    onSuccess: () => {
      toast.success("Group deleted successfully")
      queryClient.invalidateQueries({ queryKey: ["regular-tv-groups"] })
      queryClient.invalidateQueries({ queryKey: ["regular-tv-groups-imported"] })
    },
    onError: (err) => toast.error(err.message),
  })

  const updateRegularTVGroup = useMutation({
    mutationFn: async (data: { id: number; name: string; epg_source_id: number | null; enabled: boolean }) => {
      const res = await fetch(`/api/v1/regular-tv/groups/${data.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ 
          name: data.name, 
          epg_source_id: data.epg_source_id,
          enabled: data.enabled
        }),
      })
      if (!res.ok) throw new Error("Failed to update group")
      return res.json()
    },
    onSuccess: () => {
      toast.success("Group updated successfully")
      queryClient.invalidateQueries({ queryKey: ["regular-tv-groups"] })
      setIsEditDialogOpen(false)
    },
    onError: (err) => toast.error(err.message),
  })

  const generatePlaylist = useMutation({
    mutationFn: async () => {
      // Use the api client for consistency and better error handling
      return api.post<GeneratePlaylistResponse>("/regular-tv/generate")
    },
    onMutate: () => {
      const toastId = toast.loading("Generating M3U Playlist...")
      return { toastId }
    },
    onSuccess: (data, variables, context) => {
      toast.dismiss(context?.toastId)

      if (data.matched_streams === undefined || data.excluded_streams === undefined) {
        toast.error("Server returned invalid response. Please restart your backend server to apply recent code changes.")
        return
      }

      // Update counts
      setStreamCounts({
        included: data.matched_streams,
        excluded: data.excluded_streams,
      })

      const totalStreams = data.matched_streams + data.excluded_streams

      if (totalStreams === 0) {
        if (data.warnings && data.warnings.length > 0) {
          toast.warning(`Generated 0 streams: ${data.warnings[0]}`)
        } else {
          toast.warning("Playlist generated with 0 streams. Check EPG settings.")
        }
      } else if (typeof data.matched_streams === "number") {
        toast.success(`Playlist generated: ${data.matched_streams} streams created`)
      } else {
        toast.success("Playlist generated successfully")
      }
      queryClient.invalidateQueries({ queryKey: ["regular-tv-m3u"] })
      queryClient.invalidateQueries({ queryKey: ["regular-tv-excluded-m3u"] })
      queryClient.invalidateQueries({ queryKey: ["regular-tv-stats"] })
      setShowM3U(false)
    },
    onError: (err: any, variables, context) => {
      toast.dismiss(context?.toastId)
      const message = err?.response?.data?.detail || err.message || "Failed to generate playlist"
      toast.error(message)
    },
  })

  const [streamCounts, setStreamCounts] = useState<{ included: number; excluded: number } | null>(null)

  useEffect(() => {
    if (regularTVStatsQuery.data) {
      setStreamCounts(regularTVStatsQuery.data)
    }
  }, [regularTVStatsQuery.data])

  const handleDelete = () => {
    if (selectedGroup) {
      deleteRegularTVGroup.mutate(selectedGroup.id)
      setIsDeleteDialogOpen(false)
      setSelectedGroup(null)
    }
  }

  const handleEdit = (group: any) => {
    setEditForm({
      id: group.id,
      name: group.name,
      epg_source_id: group.epg_source_id,
      enabled: group.enabled
    })
    setIsEditDialogOpen(true)
  }

  const handleSaveEdit = () => {
    updateRegularTVGroup.mutate(editForm)
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-semibold">Regular TV Groups</h1>
        <div className="flex items-center gap-4">
          {streamCounts && (
            <div className="flex gap-4 text-sm border-r pr-4">
              <div className="flex items-center gap-1.5">
                <CheckCircle className="h-4 w-4 text-green-500" />
                <span>{streamCounts.included} Included</span>
              </div>
              <div className="flex items-center gap-1.5">
                <XCircle className="h-4 w-4 text-red-500" />
                <span>{streamCounts.excluded} Excluded</span>
              </div>
            </div>
          )}
          <div className="flex gap-2">
            <Button onClick={() => {
              generatePlaylist.mutate();
            }} disabled={generatePlaylist.isPending}>
              {generatePlaylist.isPending ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Play className="h-4 w-4 mr-2" />}
              Generate M3U
            </Button>
            <Button onClick={() => navigate("/regular-tv/import")}>
              <Import className="h-4 w-4 mr-2" />
              Import
            </Button>
          </div>
        </div>
      </div>


      <Card>
        <CardHeader>
          <CardTitle>Imported Groups</CardTitle>
        </CardHeader>
        <CardContent>
          {regularTVGroupsQuery.isLoading ? (
            <div className="flex justify-center p-4"><Loader2 className="h-6 w-6 animate-spin" /></div>
          ) : regularTVGroupsQuery.isError ? (
            <div className="text-red-500 text-center p-4">Error loading groups.</div>
          ) : regularTVGroupsQuery.data && regularTVGroupsQuery.data.length > 0 ? (
            <div className="divide-y">
              {regularTVGroupsQuery.data.map(group => (
                <div key={group.id} className="flex items-center justify-between p-3">
                  <div className="flex flex-col">
                    <span className="font-medium flex items-center gap-2">
                      {group.name}
                      {!group.enabled && <span className="text-xs text-muted-foreground bg-muted px-1.5 py-0.5 rounded">Disabled</span>}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      EPG: {(() => {
                        const effectiveId = group.epg_source_id ?? regularTVSettingsQuery.data?.epg_source_id
                        if (!effectiveId) return "None"
                        const source = epgSourcesQuery.data?.sources?.find(s => s.id === effectiveId)
                        const name = source?.name || `ID: ${effectiveId}`
                        return group.epg_source_id ? name : `${name} (Default)`
                      })()}
                    </span>
                  </div>
                  <div className="flex gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => handleEdit(group)}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => {
                        setSelectedGroup(group)
                        setIsDeleteDialogOpen(true)
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center text-muted-foreground p-6">
              No groups imported yet.
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <div 
          className="flex items-center justify-between p-6 cursor-pointer hover:bg-muted/50 transition-colors"
          onClick={() => setShowM3U(!showM3U)}
        >
          <CardTitle className="text-lg">View M3U</CardTitle>
          {showM3U ? <ChevronUp className="h-5 w-5" /> : <ChevronDown className="h-5 w-5" />}
        </div>
        {showM3U && (
          <CardContent className="pt-0 border-t">
            {m3uQuery.isLoading ? (
              <div className="flex justify-center p-8"><Loader2 className="h-6 w-6 animate-spin" /></div>
            ) : m3uQuery.isError ? (
              <div className="text-destructive text-center p-8">Error loading playlist.</div>
            ) : (
              <div className="space-y-4 pt-4">
                <div className="flex justify-end gap-2">
                   <Button variant="outline" size="sm" onClick={(e) => { e.stopPropagation(); handleDownloadM3U(); }}>
                     <Download className="h-4 w-4 mr-2" />
                     Download
                   </Button>
                   <Button variant="outline" size="sm" onClick={(e) => { e.stopPropagation(); handleCopyM3U(); }}>
                     {copied ? <Check className="h-4 w-4 mr-2" /> : <Copy className="h-4 w-4 mr-2" />}
                     Copy Content
                   </Button>
                </div>
                <div className="bg-muted p-4 rounded-md overflow-auto max-h-[500px] text-xs font-mono whitespace-pre">
                  {m3uQuery.data || "# No playlist generated yet"}
                </div>
              </div>
            )}
          </CardContent>
        )}
      </Card>

      <Card>
        <div 
          className="flex items-center justify-between p-6 cursor-pointer hover:bg-muted/50 transition-colors"
          onClick={() => setShowExcludedM3U(!showExcludedM3U)}
        >
          <CardTitle className="text-lg">View Excluded M3U</CardTitle>
          {showExcludedM3U ? <ChevronUp className="h-5 w-5" /> : <ChevronDown className="h-5 w-5" />}
        </div>
        {showExcludedM3U && (
          <CardContent className="pt-0 border-t">
            {excludedM3uQuery.isLoading ? (
              <div className="flex justify-center p-8"><Loader2 className="h-6 w-6 animate-spin" /></div>
            ) : excludedM3uQuery.isError ? (
              <div className="text-destructive text-center p-8">Error loading excluded playlist.</div>
            ) : (
              <div className="space-y-4 pt-4">
                <div className="flex justify-end gap-2">
                   <Button variant="outline" size="sm" onClick={(e) => { e.stopPropagation(); handleDownloadExcludedM3U(); }}>
                     <Download className="h-4 w-4 mr-2" />
                     Download
                   </Button>
                   <Button variant="outline" size="sm" onClick={(e) => { e.stopPropagation(); handleCopyExcludedM3U(); }}>
                     {copiedExcluded ? <Check className="h-4 w-4 mr-2" /> : <Copy className="h-4 w-4 mr-2" />}
                     Copy Content
                   </Button>
                </div>
                <div className="bg-muted p-4 rounded-md overflow-auto max-h-[500px] text-xs font-mono whitespace-pre">
                  {excludedM3uQuery.data || "# No excluded playlist generated yet"}
                </div>
              </div>
            )}
          </CardContent>
        )}
      </Card>

      <Dialog open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Are you sure?</DialogTitle>
            <DialogDescription>
              This action cannot be undone. This will permanently delete the
              <span className="font-bold"> {selectedGroup?.name}</span> group.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setIsDeleteDialogOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={handleDelete}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isEditDialogOpen} onOpenChange={setIsEditDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Group</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="edit-name">Name</Label>
              <Input 
                id="edit-name" 
                value={editForm.name} 
                onChange={(e) => setEditForm({...editForm, name: e.target.value})} 
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="edit-epg">EPG Source</Label>
              <select
                id="edit-epg"
                className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                value={editForm.epg_source_id?.toString() ?? "0"}
                onChange={(e) => setEditForm({...editForm, epg_source_id: e.target.value === "0" ? null : parseInt(e.target.value)})}
                disabled={!dispatcharrStatus.data?.connected}
              >
                <option value="0">Default (Global Setting)</option>
                {epgSourcesQuery.data?.sources?.map((source) => (
                  <option key={source.id} value={source.id}>
                    {source.name} ({source.source_type})
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2">
              <Switch 
                id="edit-enabled"
                checked={editForm.enabled}
                onCheckedChange={(checked) => setEditForm({...editForm, enabled: checked})}
              />
              <Label htmlFor="edit-enabled">Enabled</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setIsEditDialogOpen(false)}>Cancel</Button>
            <Button onClick={handleSaveEdit} disabled={updateRegularTVGroup.isPending}>
              {updateRegularTVGroup.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
