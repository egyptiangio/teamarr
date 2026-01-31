import { useState, useMemo } from "react"
import { toast } from "sonner"
import {
  Plus,
  Trash2,
  Loader2,
  Save,
  AlertCircle,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import {
  useStreamOrderingSettings,
  useUpdateStreamOrderingSettings,
} from "@/hooks/useSettings"
import { useGroups } from "@/hooks/useGroups"

const RULE_TYPES = [
  { value: "m3u", label: "M3U Account", description: "Match streams by M3U account name" },
  { value: "group", label: "Event Group", description: "Match streams by event group name" },
  { value: "regex", label: "Regex Pattern", description: "Match streams by regex against stream name" },
] as const

interface RuleFormData {
  type: "m3u" | "group" | "regex"
  value: string
  priority: number
}

function RuleRow({
  rule,
  index,
  onUpdate,
  onDelete,
  m3uAccounts,
  groupNames,
}: {
  rule: RuleFormData
  index: number
  onUpdate: (index: number, rule: RuleFormData) => void
  onDelete: (index: number) => void
  m3uAccounts: string[]
  groupNames: string[]
}) {
  const handleTypeChange = (newType: RuleFormData["type"]) => {
    // Clear value when type changes since options are different
    onUpdate(index, { ...rule, type: newType, value: "" })
  }

  return (
    <div className="flex items-center gap-2 p-2 rounded-md border bg-card">
      <div className="flex-1 grid grid-cols-12 gap-2 items-center">
        <div className="col-span-2">
          <Select
            value={rule.type}
            onChange={(e) => handleTypeChange(e.target.value as RuleFormData["type"])}
          >
            {RULE_TYPES.map(type => (
              <option key={type.value} value={type.value}>{type.label}</option>
            ))}
          </Select>
        </div>

        <div className="col-span-7">
          {rule.type === "m3u" ? (
            <Select
              value={rule.value}
              onChange={(e) => onUpdate(index, { ...rule, value: e.target.value })}
            >
              <option value="">Select M3U account...</option>
              {m3uAccounts.map(account => (
                <option key={account} value={account}>{account}</option>
              ))}
            </Select>
          ) : rule.type === "group" ? (
            <Select
              value={rule.value}
              onChange={(e) => onUpdate(index, { ...rule, value: e.target.value })}
            >
              <option value="">Select event group...</option>
              {groupNames.map(name => (
                <option key={name} value={name}>{name}</option>
              ))}
            </Select>
          ) : (
            <Input
              value={rule.value}
              onChange={(e) => onUpdate(index, { ...rule, value: e.target.value })}
              placeholder="Regex pattern (e.g., .*HD.*)"
            />
          )}
        </div>

        <div className="col-span-2">
          <Input
            type="number"
            min={1}
            max={99}
            value={rule.priority}
            onChange={(e) => {
              const val = parseInt(e.target.value)
              if (!isNaN(val) && val >= 1 && val <= 99) {
                onUpdate(index, { ...rule, priority: val })
              }
            }}
            className="text-center"
          />
        </div>

        <div className="col-span-1 flex justify-end">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onDelete(index)}
            className="h-8 w-8 text-destructive hover:text-destructive"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}

export function StreamOrderingManager() {
  const { data: settings, isLoading, error } = useStreamOrderingSettings()
  const updateSettings = useUpdateStreamOrderingSettings()
  const { data: groupsData } = useGroups(true) // Include disabled groups

  const defaultRules = useMemo<RuleFormData[]>(() => {
    if (!settings?.rules) return []
    return settings.rules.map((rule) => ({
      type: rule.type,
      value: rule.value,
      priority: rule.priority,
    }))
  }, [settings])

  const [draftRules, setDraftRules] = useState<RuleFormData[] | null>(null)
  const rules = draftRules ?? defaultRules
  const hasChanges = draftRules !== null

  // Extract unique M3U account names and group names from groups
  const { m3uAccounts, groupNames } = useMemo(() => {
    if (!groupsData?.groups) {
      return { m3uAccounts: [], groupNames: [] }
    }

    const accounts = new Set<string>()
    const names = new Set<string>()

    for (const group of groupsData.groups) {
      if (group.m3u_account_name) {
        accounts.add(group.m3u_account_name)
      }
      if (group.name) {
        names.add(group.name)
      }
    }

    return {
      m3uAccounts: Array.from(accounts).sort(),
      groupNames: Array.from(names).sort(),
    }
  }, [groupsData])

  const handleAddRule = () => {
    // Find next available priority
    const usedPriorities = new Set(rules.map(r => r.priority))
    let nextPriority = 1
    while (usedPriorities.has(nextPriority) && nextPriority < 99) {
      nextPriority++
    }

    setDraftRules([...rules, { type: "m3u", value: "", priority: nextPriority }])
  }

  const handleUpdateRule = (index: number, updatedRule: RuleFormData) => {
    const newRules = [...rules]
    newRules[index] = updatedRule
    setDraftRules(newRules)
  }

  const handleDeleteRule = (index: number) => {
    setDraftRules(rules.filter((_, i) => i !== index))
  }

  const handleSave = async () => {
    // Validate rules
    const validRules = rules.filter(r => r.value.trim())
    const invalidRules = rules.filter(r => !r.value.trim())

    if (invalidRules.length > 0) {
      toast.error("Please fill in all rule values or remove empty rules")
      return
    }

    try {
      await updateSettings.mutateAsync({
        rules: validRules.map(r => ({
          type: r.type,
          value: r.value.trim(),
          priority: r.priority,
        })),
      })
      toast.success("Stream ordering rules saved")
      setDraftRules(null)
    } catch (error) {
      console.error(error)
      toast.error("Failed to save stream ordering rules")
    }
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

  if (error) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-8 text-destructive">
          <AlertCircle className="h-5 w-5" />
          <span>Failed to load stream ordering settings</span>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Stream Ordering</CardTitle>
        <CardDescription>
          Prioritize streams within channels based on M3U account, event group, or custom patterns.
          Lower priority numbers appear first. Streams not matching any rule are sorted to the end.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {rules.length > 0 && (
          <div className="space-y-2">
            {/* Header row */}
            <div className="grid grid-cols-12 gap-2 px-2 text-xs font-medium text-muted-foreground">
              <div className="col-span-2">Type</div>
              <div className="col-span-7">Value</div>
              <div className="col-span-2 text-center">Priority</div>
              <div className="col-span-1"></div>
            </div>

            {/* Rules */}
            {rules
              .slice()
              .sort((a, b) => a.priority - b.priority)
              .map((rule, index) => (
                <RuleRow
                  key={index}
                  rule={rule}
                  index={rules.indexOf(rule)}
                  onUpdate={handleUpdateRule}
                  onDelete={handleDeleteRule}
                  m3uAccounts={m3uAccounts}
                  groupNames={groupNames}
                />
              ))}
          </div>
        )}

        {rules.length === 0 && (
          <div className="text-center py-6 text-muted-foreground">
            <p className="text-sm">No ordering rules configured.</p>
            <p className="text-xs mt-1">Streams will be ordered by addition time.</p>
          </div>
        )}

        <div className="flex items-center justify-between pt-2">
          <Button variant="outline" onClick={handleAddRule}>
            <Plus className="h-4 w-4 mr-1" />
            Add Rule
          </Button>

          <Button
            onClick={handleSave}
            disabled={updateSettings.isPending || !hasChanges}
          >
            {updateSettings.isPending ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-1" />
            )}
            Save
          </Button>
        </div>

        <p className="text-xs text-muted-foreground">
          Changes take effect on the next EPG generation. Existing channel streams will be reordered.
        </p>
      </CardContent>
    </Card>
  )
}
