import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Plus, X, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { cn } from "@/lib/utils"

interface ChannelProfile {
  id: number
  name: string
}

// Preset wildcard options for dynamic profile assignment
const WILDCARD_OPTIONS = [
  { value: "{sport}", label: "{sport}", description: "Add channels to a profile by sport name (e.g., Basketball). Profile created if it doesn't exist." },
  { value: "{league}", label: "{league}", description: "Add channels to a profile by league name (e.g., NBA, NFL). Profile created if it doesn't exist." },
] as const

// Check if a string is a preset wildcard
const PRESET_WILDCARDS = ["{sport}", "{league}"]

async function fetchChannelProfiles(): Promise<ChannelProfile[]> {
  const response = await fetch("/api/v1/dispatcharr/channel-profiles")
  if (!response.ok) {
    if (response.status === 503) return [] // Dispatcharr not connected
    throw new Error("Failed to fetch channel profiles")
  }
  return response.json()
}

async function createChannelProfile(name: string): Promise<ChannelProfile | null> {
  const response = await fetch(
    `/api/v1/dispatcharr/channel-profiles?name=${encodeURIComponent(name)}`,
    { method: "POST" }
  )
  if (!response.ok) return null
  return response.json()
}

interface ChannelProfileSelectorProps {
  /** Currently selected profile IDs and/or wildcards */
  selectedIds: (number | string)[]
  /** Callback when selection changes */
  onChange: (ids: (number | string)[]) => void
  /** Whether Dispatcharr is connected */
  disabled?: boolean
  /** Optional class name */
  className?: string
  /** Whether to show wildcard options (default: true) */
  showWildcards?: boolean
}

/**
 * Channel profile multi-select with inline creation and wildcard support.
 *
 * Behavior:
 * - All profiles checked = all profiles
 * - No profiles checked = no profiles
 * - Some profiles checked = those specific profiles
 * - Wildcards can be combined with static profile selections
 */
export function ChannelProfileSelector({
  selectedIds,
  onChange,
  disabled = false,
  className,
  showWildcards = true,
}: ChannelProfileSelectorProps) {
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState("")
  const [creating, setCreating] = useState(false)
  const [showCustomInput, setShowCustomInput] = useState(false)
  const [customPattern, setCustomPattern] = useState("")

  const { data: profiles = [], isLoading } = useQuery({
    queryKey: ["dispatcharr-channel-profiles"],
    queryFn: fetchChannelProfiles,
    retry: false,
  })

  // Separate numeric IDs from wildcards/patterns
  const numericIds = selectedIds.filter((x): x is number => typeof x === "number")
  const wildcardIds = selectedIds.filter((x): x is string => typeof x === "string")

  // Separate preset wildcards from custom patterns
  const presetWildcards = wildcardIds.filter(w => PRESET_WILDCARDS.includes(w))
  const customPatterns = wildcardIds.filter(w => !PRESET_WILDCARDS.includes(w))

  const selectedSet = new Set(numericIds)
  const allProfilesSelected = profiles.length > 0 && profiles.every(p => selectedSet.has(p.id))
  const noneSelected = selectedIds.length === 0

  const toggleProfile = (id: number) => {
    if (selectedSet.has(id)) {
      onChange(selectedIds.filter(x => x !== id))
    } else {
      onChange([...selectedIds, id])
    }
  }

  const toggleWildcard = (wildcard: string) => {
    if (wildcardIds.includes(wildcard)) {
      onChange(selectedIds.filter(x => x !== wildcard))
    } else {
      onChange([...selectedIds, wildcard])
    }
  }

  const addCustomPattern = () => {
    if (!customPattern.trim()) return
    if (!customPattern.includes("{sport}") && !customPattern.includes("{league}")) {
      toast.error("Pattern must include {sport} or {league}")
      return
    }
    if (selectedIds.includes(customPattern)) {
      toast.error("Pattern already added")
      return
    }
    onChange([...selectedIds, customPattern])
    setCustomPattern("")
    setShowCustomInput(false)
  }

  const removeCustomPattern = (pattern: string) => {
    onChange(selectedIds.filter(x => x !== pattern))
  }

  const selectAllProfiles = () => {
    // Keep existing wildcards, add all profile IDs
    onChange([...wildcardIds, ...profiles.map(p => p.id)])
  }

  const clearAll = () => {
    onChange([])
  }

  const handleCreate = async () => {
    if (!newName.trim()) return
    setCreating(true)
    try {
      const created = await createChannelProfile(newName.trim())
      if (created) {
        toast.success(`Created profile "${created.name}"`)
        // Add to selection
        onChange([...selectedIds, created.id])
        setNewName("")
        setShowCreate(false)
        queryClient.invalidateQueries({ queryKey: ["dispatcharr-channel-profiles"] })
      } else {
        toast.error("Failed to create profile")
      }
    } catch {
      toast.error("Failed to create profile")
    }
    setCreating(false)
  }

  // Count display
  const getCountDisplay = () => {
    const parts: string[] = []
    if (numericIds.length > 0) {
      if (allProfilesSelected) {
        parts.push(`All ${profiles.length} profiles`)
      } else {
        parts.push(`${numericIds.length} profile${numericIds.length !== 1 ? "s" : ""}`)
      }
    }
    if (presetWildcards.length > 0) {
      const wildcardLabels = presetWildcards
        .map(w => WILDCARD_OPTIONS.find(o => o.value === w)?.label || w)
        .join(", ")
      parts.push(wildcardLabels)
    }
    if (customPatterns.length > 0) {
      parts.push(`${customPatterns.length} custom pattern${customPatterns.length !== 1 ? "s" : ""}`)
    }
    return parts.length > 0 ? parts.join(" + ") : "No profiles selected"
  }

  if (isLoading) {
    return (
      <div className={cn("flex items-center justify-center py-4", className)}>
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className={cn("space-y-2", className)}>
      {/* Header with actions */}
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted-foreground">
          {getCountDisplay()}
        </span>
        <div className="flex items-center gap-1">
          {!allProfilesSelected && profiles.length > 0 && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={selectAllProfiles}
              disabled={disabled}
            >
              Select All
            </Button>
          )}
          {!noneSelected && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={clearAll}
              disabled={disabled}
            >
              Clear
            </Button>
          )}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2"
            onClick={() => setShowCreate(!showCreate)}
            disabled={disabled}
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Create new profile */}
      {showCreate && (
        <div className="flex gap-2 p-2 bg-muted/50 rounded-md">
          <Input
            placeholder="New profile name..."
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            className="flex-1 h-8"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault()
                handleCreate()
              }
            }}
          />
          <Button
            type="button"
            size="sm"
            className="h-8"
            disabled={creating || !newName.trim()}
            onClick={handleCreate}
          >
            {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : "Create"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 px-2"
            onClick={() => {
              setShowCreate(false)
              setNewName("")
            }}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Existing Profiles list */}
      <div className="border rounded-md divide-y max-h-48 overflow-y-auto">
        {profiles.length > 0 && (
          <div className="px-3 py-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wide bg-muted/30">
            Existing Profiles
          </div>
        )}
        {profiles.length === 0 ? (
          <div className="p-3 text-sm text-muted-foreground text-center">
            {disabled ? "Dispatcharr not connected" : "No profiles found"}
          </div>
        ) : (
          profiles.map((profile) => {
            const isSelected = selectedSet.has(profile.id)
            return (
              <label
                key={profile.id}
                className={cn(
                  "flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-accent",
                  isSelected && "bg-primary/5",
                  disabled && "opacity-50 cursor-not-allowed"
                )}
              >
                <Checkbox
                  checked={isSelected}
                  onCheckedChange={() => !disabled && toggleProfile(profile.id)}
                  disabled={disabled}
                />
                <span className="text-sm flex-1">{profile.name}</span>
              </label>
            )
          })
        )}
      </div>

      {/* Dynamic Profiles (wildcards and custom patterns) */}
      {showWildcards && (
        <div className="border rounded-md divide-y bg-muted/30">
          <div className="px-3 py-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Dynamic Profiles
          </div>
          {/* Preset wildcards */}
          {WILDCARD_OPTIONS.map((option) => {
            const isSelected = presetWildcards.includes(option.value)
            return (
              <label
                key={option.value}
                className={cn(
                  "flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-accent",
                  isSelected && "bg-primary/5",
                  disabled && "opacity-50 cursor-not-allowed"
                )}
              >
                <Checkbox
                  checked={isSelected}
                  onCheckedChange={() => !disabled && toggleWildcard(option.value)}
                  disabled={disabled}
                />
                <div className="flex-1">
                  <code className="text-sm font-medium bg-muted px-1 rounded">{option.label}</code>
                  <p className="text-xs text-muted-foreground mt-0.5">{option.description}</p>
                </div>
              </label>
            )
          })}

          {/* Custom patterns already added */}
          {customPatterns.map((pattern) => (
            <div
              key={pattern}
              className="flex items-center gap-3 px-3 py-2 bg-primary/5"
            >
              <Checkbox checked disabled />
              <code className="text-sm font-medium bg-muted px-1 rounded flex-1">{pattern}</code>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={() => !disabled && removeCustomPattern(pattern)}
                disabled={disabled}
              >
                <X className="h-3 w-3" />
              </Button>
            </div>
          ))}

          {/* Add custom pattern */}
          {showCustomInput ? (
            <div className="flex gap-2 p-2">
              <Input
                placeholder="Sports | {sport} | {league}"
                value={customPattern}
                onChange={(e) => setCustomPattern(e.target.value)}
                className="flex-1 h-8 font-mono text-sm"
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault()
                    addCustomPattern()
                  }
                }}
              />
              <Button
                type="button"
                size="sm"
                className="h-8"
                disabled={!customPattern.trim()}
                onClick={addCustomPattern}
              >
                Add
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-8 px-2"
                onClick={() => {
                  setShowCustomInput(false)
                  setCustomPattern("")
                }}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          ) : (
            <button
              type="button"
              className="flex items-center gap-2 px-3 py-2 w-full text-left hover:bg-accent text-muted-foreground"
              onClick={() => setShowCustomInput(true)}
              disabled={disabled}
            >
              <Plus className="h-4 w-4" />
              <span className="text-sm">Add custom pattern...</span>
            </button>
          )}

          <div className="px-3 py-1.5 text-xs text-muted-foreground">
            Available: <code className="bg-muted px-1 rounded">{"{sport}"}</code>, <code className="bg-muted px-1 rounded">{"{league}"}</code>
          </div>
        </div>
      )}
    </div>
  )
}

