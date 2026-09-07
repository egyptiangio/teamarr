import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { toast } from "sonner"
import { SaveButton } from "@/components/ui/save-button"
import { ToggleCard } from "@/components/ui/toggle-card"
import { Label } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Select } from "@/components/ui/select"
import { CheckboxListPicker } from "@/components/ui/checkbox-list-picker"
import {
  useFeedSeparationSettings,
  useUpdateFeedSeparationSettings,
} from "@/hooks/useSettings"
import { useSports } from "@/hooks/useSports"
import { useSubscription } from "@/hooks/useSubscription"
import { getLeagues } from "@/api/teams"
import { getSportDisplayName } from "@/lib/utils"
import type { FeedSeparationSettings } from "@/api/settings"

/**
 * Feed Separation — detect HOME/AWAY or team-name labels in stream names and
 * split into separate channels per broadcast feed. Lifted out of Settings into
 * the Channels home (v2.7.0 IA); self-contained via its own hooks.
 */
export function FeedSeparationCard() {
  const { data: feedSeparationData } = useFeedSeparationSettings()
  const updateFeedSeparation = useUpdateFeedSeparationSettings()

  const [feedSeparation, setFeedSeparation] = useState<FeedSeparationSettings>({
    enabled: false,
    sports: [],
    home_terms: [],
    away_terms: [],
    detect_team_names: false,
    label_style: "team_name",
  })

  // Sport options come from the subscribed leagues, not the full sports table —
  // offering to scope separation to a sport the user has no leagues for is noise.
  const { data: sportsData } = useSports()
  const sportsMap = useMemo(() => sportsData?.sports ?? {}, [sportsData])

  const { data: subscription } = useSubscription()
  const { data: leaguesData } = useQuery({
    queryKey: ["leagues"],
    queryFn: () => getLeagues(),
  })

  const sportItems = useMemo(() => {
    const subscribed = subscription?.leagues ?? []
    const codes = new Set(
      (leaguesData?.leagues ?? [])
        .filter((l) => subscribed.includes(l.slug))
        .map((l) => l.sport)
        .filter(Boolean)
    )
    // Before the league cache loads (or on a fresh install with no subscription)
    // fall back to every known sport, so the picker is never empty while a
    // saved selection is on screen.
    const source = codes.size > 0 ? [...codes] : Object.keys(sportsMap)
    return source
      .map((sport) => ({ value: sport, label: getSportDisplayName(sport, sportsMap) }))
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [subscription, leaguesData, sportsMap])

  // Sync the form from the server data during render (React's "adjusting
  // state when a prop changes" pattern) — re-seeds on every refetch, exactly
  // like the previous effect, without the extra effect render pass.
  const [syncedFeedSeparationData, setSyncedFeedSeparationData] =
    useState<typeof feedSeparationData>(undefined)
  if (feedSeparationData && feedSeparationData !== syncedFeedSeparationData) {
    setSyncedFeedSeparationData(feedSeparationData)
    setFeedSeparation(feedSeparationData)
  }

  return (
    <ToggleCard
      title="Feed Separation"
      description="Detect HOME/AWAY or team name labels in stream names and create separate channels per broadcast feed"
      enabled={feedSeparation.enabled}
      onEnabledChange={(checked) => setFeedSeparation({ ...feedSeparation, enabled: checked })}
      footer={
        <SaveButton
          onClick={() =>
            updateFeedSeparation.mutate(feedSeparation, {
              onSuccess: () => toast.success("Feed separation settings saved"),
              onError: () => toast.error("Failed to save feed separation settings"),
            })
          }
          pending={updateFeedSeparation.isPending}
        />
      }
    >
      <div className="space-y-4 pl-2 border-l-2 border-muted ml-1">
        {/* Sports scope — first because it gates everything below it */}
            <div className="space-y-1.5">
              <Label>Apply To Sports</Label>
              <CheckboxListPicker
                selected={feedSeparation.sports}
                onChange={(sports) => setFeedSeparation({ ...feedSeparation, sports })}
                items={sportItems}
                searchPlaceholder="Search sports..."
                maxHeight="max-h-36"
              />
              <p className="text-xs text-muted-foreground">
                Only split feeds for these sports. Leave empty to apply to every sport.
                Home/away feeds are a regional-sports-network thing — useful for baseball,
                basketball, hockey and football, and meaningless for racing, combat sports,
                tennis and golf.
              </p>
            </div>

            {/* Home terms */}
            <div className="space-y-1.5">
              <Label htmlFor="feed-home-terms">Home Feed Terms</Label>
              <Input
                id="feed-home-terms"
                value={feedSeparation.home_terms.join(", ")}
                onChange={(e) =>
                  setFeedSeparation({
                    ...feedSeparation,
                    home_terms: e.target.value.split(",").map((t) => t.trim()).filter(Boolean),
                  })
                }
                placeholder="HOME"
              />
              <p className="text-xs text-muted-foreground">
                Comma-separated terms that indicate a home feed (e.g., HOME, Home Feed)
              </p>
            </div>

            {/* Away terms */}
            <div className="space-y-1.5">
              <Label htmlFor="feed-away-terms">Away Feed Terms</Label>
              <Input
                id="feed-away-terms"
                value={feedSeparation.away_terms.join(", ")}
                onChange={(e) =>
                  setFeedSeparation({
                    ...feedSeparation,
                    away_terms: e.target.value.split(",").map((t) => t.trim()).filter(Boolean),
                  })
                }
                placeholder="AWAY"
              />
              <p className="text-xs text-muted-foreground">
                Comma-separated terms that indicate an away feed (e.g., AWAY, Away Feed)
              </p>
            </div>

            {/* Detect team names toggle */}
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label>Detect Team Names</Label>
                <p className="text-xs text-muted-foreground">
                  Also detect team names as feed indicators (e.g., &quot;Orioles Feed&quot; resolves to home team)
                </p>
              </div>
              <Switch
                checked={feedSeparation.detect_team_names}
                onCheckedChange={(checked) =>
                  setFeedSeparation({ ...feedSeparation, detect_team_names: checked })
                }
              />
            </div>

            {/* Label style */}
            <div className="space-y-1.5">
              <Label htmlFor="feed-label-style">Feed Label Style</Label>
              <Select
                id="feed-label-style"
                value={feedSeparation.label_style}
                onChange={(e) =>
                  setFeedSeparation({
                    ...feedSeparation,
                    label_style: e.target.value as FeedSeparationSettings["label_style"],
                  })
                }
              >
                <option value="team_name">Team Name (e.g., &quot;Orioles Feed&quot;)</option>
                <option value="short_name">Short Name (e.g., &quot;BAL Feed&quot;)</option>
                <option value="home_away">Home/Away (e.g., &quot;Home Feed&quot;)</option>
              </Select>
              <p className="text-xs text-muted-foreground">
                How feed labels appear in channel names
              </p>
            </div>
          </div>
      </ToggleCard>
  )
}
