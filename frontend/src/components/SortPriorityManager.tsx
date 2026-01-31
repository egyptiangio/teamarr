import { useMemo } from "react"
import { toast } from "sonner"
import { Loader2, Wand2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  HierarchicalSortable,
  type HierarchicalItem,
  type GroupedItem,
} from "@/components/ui/hierarchical-sortable"
import {
  useSortPriorities,
  useReorderSortPriorities,
  useAutoPopulateSortPriorities,
} from "@/hooks/useSortPriorities"
import type { SortPriorityReorderItem } from "@/api/sortPriorities"

interface SortPriorityManagerProps {
  showWhenSortBy?: string
  currentSortBy: string
}

export function SortPriorityManager({ showWhenSortBy = "sport_league_time", currentSortBy }: SortPriorityManagerProps) {
  const { data: priorities, isLoading, refetch } = useSortPriorities()
  const reorderMutation = useReorderSortPriorities()
  const autoPopulateMutation = useAutoPopulateSortPriorities()
  const shouldRender = currentSortBy === showWhenSortBy

  // Transform priorities to HierarchicalItem format
  // First, build a map of sport codes to display names from sport-level entries
  const sportDisplayNames = useMemo(() => {
    if (!priorities) return new Map<string, string>()
    const names = new Map<string, string>()
    for (const p of priorities) {
      // Sport-level entries (league_code is null) have the sport display name
      if (p.league_code === null && p.display_name) {
        names.set(p.sport, p.display_name)
      }
    }
    return names
  }, [priorities])

  const items: HierarchicalItem[] = useMemo(() => {
    if (!priorities) return []
    return priorities.map(p => ({
      id: p.id,
      group: p.sport,
      groupLabel: sportDisplayNames.get(p.sport) || p.sport,
      child: p.league_code,
      sortPriority: p.sort_priority,
      label: p.display_name || p.league_code || p.sport,
      metadata: {
        channel_count: p.channel_count,
      },
    }))
  }, [priorities, sportDisplayNames])

  const handleReorder = async (newOrder: Array<{ group: string; child: string | null; priority: number }>) => {
    const reorderData: SortPriorityReorderItem[] = newOrder.map(item => ({
      sport: item.group,
      league_code: item.child,
      priority: item.priority,
    }))

    try {
      await reorderMutation.mutateAsync(reorderData)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to reorder")
      refetch()
    }
  }

  const handleAutoPopulate = async () => {
    try {
      const result = await autoPopulateMutation.mutateAsync()
      if (result.added > 0) {
        toast.success(`Added ${result.added} sport/league priorities`)
      } else {
        toast.info(result.message)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to auto-populate")
    }
  }

  // Render extra content for group headers (league count + channel count)
  const renderGroupExtra = (group: GroupedItem) => {
    const channelCount = group.children.reduce((sum, child) => {
      const count = (child.metadata?.channel_count as number) || 0
      return sum + count
    }, (group.groupItem?.metadata?.channel_count as number) || 0)

    return (
      <>
        <span className="text-xs text-muted-foreground">
          {group.children.length} league{group.children.length !== 1 ? "s" : ""}
        </span>
        {channelCount > 0 && (
          <span className="text-xs text-muted-foreground">
            ({channelCount} ch)
          </span>
        )}
      </>
    )
  }

  // Render extra content for child items (channel count)
  const renderChildExtra = (item: HierarchicalItem) => {
    const channelCount = item.metadata?.channel_count as number | undefined
    if (channelCount === null || channelCount === undefined) return null
    return (
      <span className="text-xs text-muted-foreground">
        {channelCount} ch
      </span>
    )
  }

  // Don't render if sort_by doesn't match
  if (!shouldRender) {
    return null
  }

  if (isLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base">Sort Priority Order</CardTitle>
            <CardDescription>
              Drag sports to reorder. Expand to reorder leagues within each sport.
            </CardDescription>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={handleAutoPopulate}
            disabled={autoPopulateMutation.isPending}
          >
            {autoPopulateMutation.isPending ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Wand2 className="h-4 w-4 mr-1" />
            )}
            Auto-populate
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <HierarchicalSortable
          items={items}
          onReorder={handleReorder}
          renderGroupExtra={renderGroupExtra}
          renderChildExtra={renderChildExtra}
          emptyMessage="No sort priorities configured. Click 'Auto-populate' to add all active sports/leagues."
        />
      </CardContent>
    </Card>
  )
}
