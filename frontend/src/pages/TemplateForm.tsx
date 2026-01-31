import { useState, useEffect, useRef, useMemo, useCallback } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { toast } from "sonner"
import { ArrowLeft, Loader2, Save, ChevronDown, Search, X, BookOpen, Download, Upload, Trash2, ChevronRight, AlertTriangle } from "lucide-react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import { Checkbox } from "@/components/ui/checkbox"
import {
  getTemplate,
  createTemplate,
  updateTemplate,
  type TemplateCreate,
  type FillerContent,
  type ConditionalSettings,
  type IdleOffseasonSettings,
  type XmltvFlags,
  type ConditionalDescription,
} from "@/api/templates"
import { fetchVariables, fetchSamples, fetchConditions, type VariableCategory } from "@/api/variables"
import {
  buildValidVariableSet,
  validateTemplate,
} from "@/utils/templateValidation"
import { usePresets, useCreatePreset, useDeletePreset } from "@/hooks/usePresets"
import type { ConditionPreset } from "@/api/presets"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"

type Tab = "basic" | "defaults" | "conditions" | "fillers" | "xmltv"

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: "basic", label: "Basic Info", icon: "📋" },
  { id: "defaults", label: "Defaults", icon: "✏️" },
  { id: "conditions", label: "Conditions", icon: "🎯" },
  { id: "fillers", label: "Fillers", icon: "📅" },
  { id: "xmltv", label: "Other EPG Options", icon: "⚙️" },
]

// Default filler content
const DEFAULT_PREGAME: FillerContent = {
  title: "Coming up: {league} {sport} starting at {game_time.next}",
  subtitle: "{away_team} at {home_team}",
  description: "The {away_team_record.next} {away_team.next} travel to {venue_city} to play the {home_team_record.next} {home_team.next} today at {game_time.next}.",
  art_url: null,
}

const DEFAULT_POSTGAME: FillerContent = {
  title: "{league} {sport}: {team_name} Postgame Recap",
  subtitle: "{away_team.last} at {home_team.last}",
  description: "{team_name} {result_text.last} the {opponent.last} {final_score.last}",
  art_url: null,
}

const DEFAULT_IDLE: FillerContent = {
  title: "No {team_name} Game Today",
  subtitle: "Next game: {game_date.next} at {game_time.next} {vs_at.next} the {opponent.next}",
  description: "Next game: {game_date.next} at {game_time.next} vs {opponent.next}",
  art_url: null,
}

const DEFAULT_FORM: TemplateCreate = {
  name: "",
  template_type: "team",
  title_format: "{league} {sport}",
  subtitle_template: "{away_team} at {home_team}",
  description_template: "{matchup} | {venue_full}",
  program_art_url: null,
  game_duration_mode: "sport",
  game_duration_override: null,
  xmltv_flags: { new: true, live: false, date: false },
  xmltv_video: { enabled: false, quality: "HDTV" },
  xmltv_categories: ["Sports"],
  categories_apply_to: "events",
  pregame_enabled: true,
  pregame_fallback: DEFAULT_PREGAME,
  postgame_enabled: true,
  postgame_fallback: DEFAULT_POSTGAME,
  postgame_conditional: { enabled: true, description_final: null, description_not_final: null },
  idle_enabled: true,
  idle_content: DEFAULT_IDLE,
  idle_conditional: { enabled: true, description_final: null, description_not_final: null },
  idle_offseason: { title_enabled: false, title: null, subtitle_enabled: false, subtitle: null, description_enabled: false, description: null },
  conditional_descriptions: [],
  event_channel_name: "{away_team} @ {home_team}",
  event_channel_logo_url: null,
}

// Default sample data (used before API loads)
const DEFAULT_SAMPLE_DATA: Record<string, string> = {
  team_name: "Detroit Lions",
  opponent: "Chicago Bears",
  league: "NFL",
  sport: "Football",
}

// Helper to create resolveTemplate function with custom sample data
function createResolver(sampleData: Record<string, string>) {
  return function resolveTemplate(template: string): string {
    if (!template) return ""
    return template.replace(/\{([^}]+)\}/g, (match, varName) => {
      return sampleData[varName] || sampleData[varName.toLowerCase()] || match
    })
  }
}

export function TemplateForm() {
  const { templateId } = useParams<{ templateId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const isEdit = !!templateId

  const [activeTab, setActiveTab] = useState<Tab>("basic")
  const [draftFormData, setDraftFormData] = useState<TemplateCreate | null>(null)
  const [typeConfirmed, setTypeConfirmed] = useState(isEdit)
  const [lastFocusedField, setLastFocusedField] = useState<string | null>(null)
  const [previewSport, setPreviewSport] = useState("NBA")

  // Refs for template fields
  const fieldRefs = useRef<Record<string, HTMLInputElement | HTMLTextAreaElement | null>>({})
  const registerFieldRef = useCallback((id: string, element: HTMLInputElement | HTMLTextAreaElement | null) => {
    fieldRefs.current = { ...fieldRefs.current, [id]: element }
  }, [])

  // Fetch existing template if editing
  const { data: template, isLoading: isLoadingTemplate } = useQuery({
    queryKey: ["template", templateId],
    queryFn: () => getTemplate(Number(templateId)),
    enabled: isEdit,
  })

  // Fetch variables for picker
  const { data: variablesData } = useQuery({
    queryKey: ["variables"],
    queryFn: fetchVariables,
    staleTime: Infinity,
  })

  // Fetch sample data for preview (sport-specific)
  const { data: samplesData } = useQuery({
    queryKey: ["samples", previewSport],
    queryFn: () => fetchSamples(previewSport),
    staleTime: 5 * 60 * 1000, // 5 minutes
  })

  // Create resolver with current sample data
  const sampleData = samplesData?.samples ?? DEFAULT_SAMPLE_DATA
  const resolveTemplate = createResolver(sampleData)
  const availableSports = samplesData?.available_sports ?? variablesData?.available_sports ?? ["NBA", "NFL", "MLB", "NHL"]

  // Build validation set from variables data
  const validationData = (() => {
    if (!variablesData?.categories) {
      return { validNames: new Set<string>(), baseNames: new Set<string>() }
    }
    return buildValidVariableSet(variablesData.categories)
  })()

  // Helper to merge filler content with defaults, ensuring no null values
  const mergeFillerContent = useCallback((content: FillerContent | null, defaults: FillerContent): FillerContent => {
    if (!content) return defaults
    return {
      title: content.title ?? defaults.title,
      subtitle: content.subtitle ?? defaults.subtitle,
      description: content.description ?? defaults.description,
      art_url: content.art_url ?? defaults.art_url,
    }
  }, [])

  const templateFormData = useMemo<TemplateCreate>(() => {
    if (!template) return DEFAULT_FORM
    return {
      name: template.name,
      template_type: template.template_type,
      sport: template.sport,
      league: template.league,
      title_format: template.title_format || "",
      subtitle_template: template.subtitle_template,
      description_template: template.description_template,
      program_art_url: template.program_art_url,
      game_duration_mode: template.game_duration_mode || "sport",
      game_duration_override: template.game_duration_override,
      xmltv_flags: template.xmltv_flags || { new: true, live: false, date: false },
      xmltv_video: template.xmltv_video || { enabled: false, quality: "HDTV" },
      xmltv_categories: template.xmltv_categories || ["Sports"],
      categories_apply_to: template.categories_apply_to || "events",
      pregame_enabled: template.pregame_enabled ?? true,
      pregame_fallback: mergeFillerContent(template.pregame_fallback, DEFAULT_PREGAME),
      postgame_enabled: template.postgame_enabled ?? true,
      postgame_fallback: mergeFillerContent(template.postgame_fallback, DEFAULT_POSTGAME),
      postgame_conditional: template.postgame_conditional || { enabled: true, description_final: null, description_not_final: null },
      idle_enabled: template.idle_enabled ?? true,
      idle_content: mergeFillerContent(template.idle_content, DEFAULT_IDLE),
      idle_conditional: template.idle_conditional || { enabled: true, description_final: null, description_not_final: null },
      idle_offseason: template.idle_offseason || { title_enabled: false, title: null, subtitle_enabled: false, subtitle: null, description_enabled: false, description: null },
      conditional_descriptions: template.conditional_descriptions || [],
      event_channel_name: template.event_channel_name,
      event_channel_logo_url: template.event_channel_logo_url,
    }
  }, [mergeFillerContent, template])

  const formData = draftFormData ?? templateFormData
  const setFormData = useCallback(
    (updater: TemplateCreate | ((prev: TemplateCreate) => TemplateCreate)) => {
      setDraftFormData((prev) => {
        const base = prev ?? templateFormData
        return typeof updater === "function"
          ? (updater as (prev: TemplateCreate) => TemplateCreate)(base)
          : updater
      })
    },
    [templateFormData]
  )

  const createMutation = useMutation({
    mutationFn: createTemplate,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["templates"] })
      toast.success(`Created template "${formData.name}"`)
      navigate("/templates")
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Failed to create template")
    },
  })

  const updateMutation = useMutation({
    mutationFn: (data: TemplateCreate) => updateTemplate(Number(templateId), data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["templates"] })
      queryClient.invalidateQueries({ queryKey: ["template", templateId] })
      toast.success(`Updated template "${formData.name}"`)
      navigate("/templates")
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Failed to update template")
    },
  })

  const handleSubmit = () => {
    if (!formData.name.trim()) {
      toast.error("Name is required")
      setActiveTab("basic")
      return
    }

    if (isEdit) {
      updateMutation.mutate(formData)
    } else {
      createMutation.mutate(formData)
    }
  }

  const insertVariable = (varName: string) => {
    if (!lastFocusedField) return
    const field = fieldRefs.current[lastFocusedField]
    if (!field) return

    const start = field.selectionStart || 0
    const end = field.selectionEnd || 0
    const value = (field as HTMLInputElement).value || ""
    const variable = `{${varName}}`
    const newValue = value.substring(0, start) + variable + value.substring(end)

    // Update the form data based on the field name
    updateFieldValue(lastFocusedField, newValue)

    // Restore focus and cursor position
    setTimeout(() => {
      field.focus()
      const newPos = start + variable.length
      field.setSelectionRange(newPos, newPos)
    }, 0)
  }

  const updateFieldValue = (fieldName: string, value: string) => {
    // Handle nested fields like pregame_fallback.title
    const parts = fieldName.split(".")
    if (parts.length === 1) {
      setFormData((prev) => ({ ...prev, [fieldName]: value }))
    } else if (parts.length === 2) {
      const [parent, child] = parts
      setFormData((prev) => {
        const parentObj = (prev as unknown as Record<string, Record<string, unknown> | null>)[parent]
        return {
          ...prev,
          [parent]: {
            ...parentObj,
            [child]: value,
          },
        }
      })
    }
  }

  const isPending = createMutation.isPending || updateMutation.isPending

  if (isEdit && isLoadingTemplate) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  // Type selection gate for new templates
  if (!isEdit && !typeConfirmed) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="sm" onClick={() => navigate("/templates")}>
            <ArrowLeft className="h-4 w-4 mr-1" />
            Back
          </Button>
          <h1 className="text-2xl font-bold">Create New Template</h1>
        </div>

        <Card>
          <CardHeader className="text-center">
            <CardTitle>Step 1: Choose Template Type</CardTitle>
            <p className="text-sm text-muted-foreground">
              This determines what fields are available and cannot be changed later.
            </p>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4 mb-6">
              <button
                type="button"
                onClick={() => setFormData((prev) => ({ ...prev, template_type: "team" }))}
                className={`p-4 rounded-lg border-2 text-left transition-all ${
                  formData.template_type === "team"
                    ? "border-primary bg-primary/10"
                    : "border-border hover:border-primary/50"
                }`}
              >
                <div className="flex items-start gap-3">
                  <span className="text-2xl">👤</span>
                  <div>
                    <strong className="block">Team Template</strong>
                    <span className="text-sm text-muted-foreground">
                      For individual teams - generates pregame, game, postgame, and idle programs based on team schedules
                    </span>
                  </div>
                </div>
              </button>
              <button
                type="button"
                onClick={() => setFormData((prev) => ({ ...prev, template_type: "event" }))}
                className={`p-4 rounded-lg border-2 text-left transition-all ${
                  formData.template_type === "event"
                    ? "border-primary bg-primary/10"
                    : "border-border hover:border-primary/50"
                }`}
              >
                <div className="flex items-start gap-3">
                  <span className="text-2xl">📺</span>
                  <div>
                    <strong className="block">Event Template</strong>
                    <span className="text-sm text-muted-foreground">
                      For Dispatcharr M3U groups - matches streams like "Giants @ Cowboys" to ESPN events
                    </span>
                  </div>
                </div>
              </button>
            </div>
            <div className="text-center">
              <Button onClick={() => setTypeConfirmed(true)} size="lg">
                Continue →
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  const isTeamTemplate = formData.template_type === "team"

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="sm" onClick={() => navigate("/templates")}>
            <ArrowLeft className="h-4 w-4 mr-1" />
            Back
          </Button>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-bold">
                {isEdit ? `Edit Template: ${template?.name}` : "Create Template"}
              </h1>
              <span
                className={`px-2 py-0.5 rounded text-xs font-medium ${
                  isTeamTemplate ? "bg-secondary text-secondary-foreground" : "bg-blue-500/20 text-blue-400"
                }`}
              >
                {isTeamTemplate ? "👤 Team" : "📺 Event"}
              </span>
            </div>
          </div>
        </div>
        <Button onClick={handleSubmit} disabled={isPending}>
          {isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
          <Save className="h-4 w-4 mr-1" />
          Save Template
        </Button>
      </div>

      {/* Template Type Banner (edit mode) */}
      {isEdit && (
        <div className={`px-4 py-2 rounded-lg mb-4 flex items-center gap-3 ${
          isTeamTemplate ? "bg-secondary/50 border border-secondary" : "bg-blue-500/10 border border-blue-500/30"
        }`}>
          <span className="text-lg">{isTeamTemplate ? "👤" : "📺"}</span>
          <div>
            <span className="font-semibold">{isTeamTemplate ? "Team Template" : "Event Template"}</span>
            <span className="text-muted-foreground text-sm ml-2">
              {isTeamTemplate
                ? "One channel per team with team-specific variables"
                : "Dynamic channels based on live events"}
            </span>
          </div>
          <span className="ml-auto text-xs text-muted-foreground">Type cannot be changed after creation</span>
        </div>
      )}

      {/* Tabs - outside grid so picker aligns with content */}
      <div className="flex gap-2 border-b border-border mb-4 flex-wrap">
        {TABS
          .filter((tab) => tab.id !== "conditions" || isTeamTemplate) // Hide conditions tab for event templates
          .map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab.id
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {tab.icon} {tab.label}
          </button>
        ))}
      </div>

      {/* Main content with sidebar */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-3 items-start">
        {/* Form area */}
        <div className="lg:col-span-4">
          {/* Tab content */}
          {activeTab === "basic" && (
            <BasicTab
              formData={formData}
              setFormData={setFormData}
              registerFieldRef={registerFieldRef}
              setLastFocusedField={setLastFocusedField}
              resolveTemplate={resolveTemplate}
              validationData={validationData}
              isTeamTemplate={isTeamTemplate}
            />
          )}
          {activeTab === "defaults" && (
            <DefaultsTab
              formData={formData}
              setFormData={setFormData}
              isTeamTemplate={isTeamTemplate}
              registerFieldRef={registerFieldRef}
              setLastFocusedField={setLastFocusedField}
              resolveTemplate={resolveTemplate}
              validationData={validationData}
            />
          )}
          {activeTab === "conditions" && (
            <ConditionsTab
              formData={formData}
              setFormData={setFormData}
              resolveTemplate={resolveTemplate}
              isTeamTemplate={isTeamTemplate}
              validationData={validationData}
            />
          )}
          {activeTab === "fillers" && (
            <FillersTab
              formData={formData}
              setFormData={setFormData}
              isTeamTemplate={isTeamTemplate}
              registerFieldRef={registerFieldRef}
              setLastFocusedField={setLastFocusedField}
              resolveTemplate={resolveTemplate}
              validationData={validationData}
            />
          )}
          {activeTab === "xmltv" && (
            <XmltvTab formData={formData} setFormData={setFormData} resolveTemplate={resolveTemplate} validationData={validationData} isTeamTemplate={isTeamTemplate} />
          )}
        </div>

        {/* Variable picker sidebar */}
        <div className="lg:col-span-1 sticky top-[4rem]" style={{ height: 'calc(100vh - 4.5rem)' }}>
          <VariableSidebar
            categories={variablesData?.categories || []}
            onInsert={insertVariable}
            lastFocusedField={lastFocusedField}
            isTeamTemplate={isTeamTemplate}
            availableSports={availableSports}
            previewSport={previewSport}
            onSportChange={setPreviewSport}
          />
        </div>
      </div>
    </div>
  )
}

// =============================================================================
// Variable Sidebar (V1 parity)
// =============================================================================

interface VariableSidebarProps {
  categories: VariableCategory[]
  onInsert: (varName: string) => void
  lastFocusedField: string | null
  isTeamTemplate: boolean
  availableSports: string[]
  previewSport: string
  onSportChange: (sport: string) => void
}

// Local storage key for recently used variables
const RECENTLY_USED_KEY = "teamarr_recently_used_vars"

function getRecentlyUsed(): string[] {
  try {
    const stored = localStorage.getItem(RECENTLY_USED_KEY)
    return stored ? JSON.parse(stored) : []
  } catch {
    return []
  }
}

function addToRecentlyUsed(varName: string) {
  try {
    const recent = getRecentlyUsed().filter((v) => v !== varName)
    recent.unshift(varName)
    localStorage.setItem(RECENTLY_USED_KEY, JSON.stringify(recent.slice(0, 10)))
  } catch {
    // Ignore storage errors
  }
}

// Determine suffix class for color coding
function getSuffixClass(suffixes: string[]): string {
  if (suffixes.length === 1) {
    if (suffixes[0] === "base") return "var-base"
    if (suffixes[0] === ".last") return "var-last"
    if (suffixes[0] === ".next") return "var-next"
  } else if (suffixes.length === 2 && suffixes.includes("base") && suffixes.includes(".next")) {
    return "var-next" // base+next = odds variables
  } else if (suffixes.length >= 3) {
    return "var-all" // all three contexts
  }
  return "var-all" // default
}

interface Variable {
  name: string
  description: string
  suffixes: string[]
}

function VariableSidebar({ categories, onInsert, lastFocusedField, isTeamTemplate, availableSports, previewSport, onSportChange }: VariableSidebarProps) {
  const [search, setSearch] = useState("")
  const [expandedCat, setExpandedCat] = useState<string | null>(null)
  const [recentlyUsed, setRecentlyUsed] = useState<string[]>(() => getRecentlyUsed())
  const [suffixPopup, setSuffixPopup] = useState<{ varName: string; suffixes: string[]; x: number; y: number } | null>(null)

  // Build a map of variable name -> variable for quick lookup
  const variableMap = useMemo(() => {
    const map: Record<string, Variable> = {}
    categories.forEach((cat) => {
      cat.variables.forEach((v) => {
        map[v.name] = v
      })
    })
    return map
  }, [categories])

  const filteredCategories = useMemo(() => {
    if (!search.trim()) return categories
    const q = search.toLowerCase()
    return categories
      .map((cat) => ({
        ...cat,
        variables: cat.variables.filter(
          (v) => v.name.toLowerCase().includes(q) || v.description.toLowerCase().includes(q)
        ),
      }))
      .filter((cat) => cat.variables.length > 0)
  }, [categories, search])

  const handleInsert = (varName: string, suffix?: string) => {
    const fullVar = suffix && suffix !== "base" ? `${varName}${suffix}` : varName
    onInsert(fullVar)
    addToRecentlyUsed(fullVar)
    setRecentlyUsed(getRecentlyUsed())
    setSuffixPopup(null)
  }

  const handleVariableClick = (e: React.MouseEvent, v: Variable) => {
    e.stopPropagation() // Prevent immediate close from document handler

    // For event templates or single-suffix variables, insert directly
    if (!isTeamTemplate || v.suffixes.length <= 1) {
      const suffix = v.suffixes.length === 1 && v.suffixes[0] !== "base" ? v.suffixes[0] : undefined
      handleInsert(v.name, suffix)
      return
    }

    // For team templates with multiple suffixes, show popup
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    setSuffixPopup({
      varName: v.name,
      suffixes: v.suffixes,
      x: rect.left,
      y: rect.bottom + 4,
    })
  }

  // Close popup when clicking outside
  useEffect(() => {
    if (!suffixPopup) return
    const handleClick = (e: MouseEvent) => {
      // Don't close if clicking inside the popup
      const popup = document.getElementById('suffix-popup')
      if (popup && popup.contains(e.target as Node)) return
      setSuffixPopup(null)
    }
    // Use setTimeout to avoid the click that opened the popup from closing it
    const timer = setTimeout(() => {
      document.addEventListener("click", handleClick)
    }, 0)
    return () => {
      clearTimeout(timer)
      document.removeEventListener("click", handleClick)
    }
  }, [suffixPopup])

  const totalVars = categories.reduce((sum, cat) => sum + cat.variables.length, 0)

  return (
    <Card className="h-full overflow-y-auto">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          📝 Template Variables
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Template Type + Sport Selector */}
        <div className="space-y-2">
          <div className="flex items-center gap-2 px-2 py-1.5 bg-secondary/50 rounded text-xs">
            <span className="text-muted-foreground">Showing vars for:</span>
            <span className="font-semibold text-primary">
              {isTeamTemplate ? "👤 Team" : "📺 Event"}
            </span>
          </div>
          <div className="flex items-center gap-2 px-2 py-1.5 bg-secondary/50 rounded text-xs">
            <span className="text-muted-foreground">Preview sport:</span>
            <Select
              value={previewSport}
              onChange={(e) => onSportChange(e.target.value)}
              className="h-6 w-20 text-xs bg-transparent border-0 text-primary font-semibold"
            >
              {availableSports.map((sport) => (
                <option key={sport} value={sport}>
                  {sport}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {/* Suffix Guide (team templates only) */}
        {isTeamTemplate ? (
          <div className="p-2 bg-secondary/30 rounded border border-border text-xs space-y-2">
            <div className="space-y-0.5 text-muted-foreground">
              <div><code className="text-primary font-mono text-[11px]">{"{variable}"}</code> current game OR not game-dependent</div>
              <div><code className="text-primary font-mono text-[11px]">{"{variable.next}"}</code> next game</div>
              <div><code className="text-primary font-mono text-[11px]">{"{variable.last}"}</code> last game</div>
            </div>
            <div className="flex flex-wrap gap-1 pt-1.5 border-t border-border text-[10px]">
              <span className="px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400 font-semibold">ALL</span>
              <span className="text-muted-foreground">all contexts •</span>
              <span className="px-1.5 py-0.5 rounded bg-gray-500/20 text-gray-400 font-semibold">BASE</span>
              <span className="text-muted-foreground">no suffix •</span>
              <span className="px-1.5 py-0.5 rounded bg-green-500/20 text-green-400 font-semibold">.next</span>
              <span className="text-muted-foreground">base+.next •</span>
              <span className="px-1.5 py-0.5 rounded bg-red-500/20 text-red-400 font-semibold">.last</span>
              <span className="text-muted-foreground">.last only</span>
            </div>
          </div>
        ) : (
          <div className="p-2 bg-secondary/30 rounded text-xs text-muted-foreground">
            Event templates use single-game context. No suffixes needed.
          </div>
        )}

        {/* Search */}
        <div className="relative">
          <Search className="absolute left-2 top-2 h-4 w-4 text-muted-foreground pointer-events-none" />
          <Input
            placeholder="Search variables..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 h-8 text-sm"
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          )}
          <div className="text-[10px] text-muted-foreground mt-1 px-1">
            {totalVars} variables available
          </div>
        </div>

        {/* Recently Used */}
        {recentlyUsed.length > 0 && !search && (
          <details className="group" open>
            <summary className="cursor-pointer text-xs font-medium text-foreground hover:text-primary flex items-center gap-1">
              <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-180" />
              🕒 Recently Used
            </summary>
            <div className="flex flex-wrap gap-1 mt-2">
              {recentlyUsed.slice(0, 8).map((varName) => {
                const baseVar = varName.replace(/\.(next|last)$/, "")
                const v = variableMap[baseVar]
                if (!v) return null
                const suffix = varName.includes(".next") ? ".next" : varName.includes(".last") ? ".last" : "base"
                return (
                  <button
                    key={varName}
                    type="button"
                    onClick={() => handleInsert(baseVar, suffix === "base" ? undefined : suffix)}
                    disabled={!lastFocusedField}
                    className="px-2 py-1 text-[11px] font-mono rounded bg-secondary/50 hover:bg-primary/20 text-primary transition-colors disabled:opacity-50"
                  >
                    {`{${varName}}`}
                  </button>
                )
              })}
            </div>
          </details>
        )}

        {/* Categories */}
        <div className="space-y-1">
          {filteredCategories.map((cat) => (
            <details
              key={cat.name}
              className="group border-b border-border last:border-0"
              open={expandedCat === cat.name || !!search}
            >
              <summary
                onClick={(e) => {
                  e.preventDefault()
                  setExpandedCat(expandedCat === cat.name ? null : cat.name)
                }}
                className="cursor-pointer px-1 py-1.5 flex items-center justify-between text-xs font-medium hover:bg-accent/50 transition-colors"
              >
                <span>{cat.name}</span>
                <span className="text-[10px] text-muted-foreground">{cat.variables.length}</span>
              </summary>
              <div className="flex flex-wrap gap-1 pb-2 pt-1">
                {cat.variables.map((v) => {
                  const suffixClass = isTeamTemplate ? getSuffixClass(v.suffixes) : "var-base"
                  const displayName = !isTeamTemplate || v.suffixes.length <= 1
                    ? v.suffixes.length === 1 && v.suffixes[0] !== "base"
                      ? `${v.name}${v.suffixes[0]}`
                      : v.name
                    : v.name

                  return (
                    <button
                      key={v.name}
                      type="button"
                      onClick={(e) => handleVariableClick(e, v)}
                      disabled={!lastFocusedField}
                      title={v.description}
                      className={`
                        px-2 py-1 text-[11px] font-mono rounded border transition-colors
                        disabled:opacity-50 disabled:cursor-not-allowed
                        ${suffixClass === "var-all" ? "bg-blue-500/15 border-blue-500/30 text-blue-400 hover:bg-blue-500/30 hover:text-white hover:border-blue-500" : ""}
                        ${suffixClass === "var-base" ? "bg-gray-500/15 border-gray-500/30 text-gray-400 hover:bg-gray-500/30 hover:text-white hover:border-gray-500" : ""}
                        ${suffixClass === "var-next" ? "bg-green-500/15 border-green-500/30 text-green-400 hover:bg-green-500/30 hover:text-white hover:border-green-500" : ""}
                        ${suffixClass === "var-last" ? "bg-red-500/15 border-red-500/30 text-red-400 hover:bg-red-500/30 hover:text-white hover:border-red-500" : ""}
                      `}
                    >
                      {displayName}
                    </button>
                  )
                })}
              </div>
            </details>
          ))}

          {filteredCategories.length === 0 && (
            <div className="text-center text-sm text-muted-foreground py-4">
              No variables found
            </div>
          )}
        </div>

        {/* Suffix Selector Popup */}
        {suffixPopup && (
          <div
            id="suffix-popup"
            className="fixed z-[100] bg-popover border border-border rounded-lg shadow-xl p-1.5 w-max"
            style={{ left: suffixPopup.x, top: suffixPopup.y }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="space-y-0.5">
              {suffixPopup.suffixes.map((suffix) => {
                const varText = suffix === "base" ? suffixPopup.varName : `${suffixPopup.varName}${suffix}`
                const desc = suffix === "base" ? "current game" : suffix === ".next" ? "next game" : "last game"
                return (
                  <button
                    key={suffix}
                    type="button"
                    onClick={() => handleInsert(suffixPopup.varName, suffix)}
                    className={`
                      w-full px-2 py-1 text-left rounded transition-colors flex items-center gap-2
                      ${suffix === "base" ? "hover:bg-emerald-500/20" : ""}
                      ${suffix === ".next" ? "hover:bg-blue-500/20" : ""}
                      ${suffix === ".last" ? "hover:bg-amber-500/20" : ""}
                    `}
                  >
                    <code className={`text-xs font-mono font-semibold whitespace-nowrap
                      ${suffix === "base" ? "text-emerald-400" : ""}
                      ${suffix === ".next" ? "text-blue-400" : ""}
                      ${suffix === ".last" ? "text-amber-400" : ""}
                    `}>
                      {`{${varText}}`}
                    </code>
                    <span className="text-[10px] text-muted-foreground whitespace-nowrap">{desc}</span>
                  </button>
                )
              })}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// =============================================================================
// Tab Components
// =============================================================================

interface TabProps {
  formData: TemplateCreate
  setFormData: React.Dispatch<React.SetStateAction<TemplateCreate>>
  registerFieldRef?: (id: string, element: HTMLInputElement | HTMLTextAreaElement | null) => void
  setLastFocusedField?: (field: string | null) => void
  isTeamTemplate?: boolean
  resolveTemplate: (template: string) => string
  validationData?: { validNames: Set<string>; baseNames: Set<string> }
}

// Template field with inline preview and validation
interface TemplateFieldProps {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  helpText?: string
  registerFieldRef?: (id: string, element: HTMLInputElement | HTMLTextAreaElement | null) => void
  setLastFocusedField?: (field: string | null) => void
  multiline?: boolean
  resolveTemplate?: (template: string) => string
  validationData?: { validNames: Set<string>; baseNames: Set<string> }
  isEventTemplate?: boolean
}

// Default resolver that just returns the template unchanged
const defaultResolver = (template: string) => template

function TemplateField({
  id,
  label,
  value,
  onChange,
  placeholder,
  helpText,
  registerFieldRef,
  setLastFocusedField,
  multiline = false,
  resolveTemplate = defaultResolver,
  validationData,
  isEventTemplate = false,
}: TemplateFieldProps) {
  const preview = resolveTemplate(value)

  // Compute validation warnings
  const warnings = useMemo(() => {
    if (!validationData || !value) return []
    return validateTemplate(
      value,
      validationData.validNames,
      validationData.baseNames,
      isEventTemplate
    )
  }, [value, validationData, isEventTemplate])

  const hasWarnings = warnings.length > 0

  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      {multiline ? (
        <Textarea
          id={id}
          ref={(el) => registerFieldRef?.(id, el)}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setLastFocusedField?.(id)}
          placeholder={placeholder}
          className={`font-mono text-sm min-h-[80px] ${hasWarnings ? "border-amber-500/50 focus:border-amber-500" : ""}`}
        />
      ) : (
        <Input
          id={id}
          ref={(el) => registerFieldRef?.(id, el)}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setLastFocusedField?.(id)}
          placeholder={placeholder}
          className={`font-mono text-sm ${hasWarnings ? "border-amber-500/50 focus:border-amber-500" : ""}`}
        />
      )}
      {/* Validation Warnings */}
      {hasWarnings && (
        <div className="mt-1 px-2 py-1.5 bg-amber-500/10 border border-amber-500/30 rounded-sm">
          <div className="flex items-start gap-1.5">
            <AlertTriangle className="h-3.5 w-3.5 text-amber-500 flex-shrink-0 mt-0.5" />
            <div className="space-y-0.5">
              {warnings.map((w, i) => (
                <p key={i} className="text-xs text-amber-400">
                  {w.message}
                </p>
              ))}
            </div>
          </div>
        </div>
      )}
      {value && (
        <div className="mt-1 px-2 py-1 bg-secondary/50 border-l-2 border-primary rounded-sm">
          <span className="text-[10px] text-muted-foreground uppercase font-semibold mr-2">Preview:</span>
          <span className="text-sm italic">{preview || "(empty)"}</span>
        </div>
      )}
      {helpText && <p className="text-xs text-muted-foreground">{helpText}</p>}
    </div>
  )
}

function BasicTab({ formData, setFormData, registerFieldRef, setLastFocusedField }: TabProps) {
  return (
    <div className="space-y-6">
      {/* Template Name */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Template Name</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-1">
            <Label htmlFor="name">Name *</Label>
            <Input
              id="name"
              ref={(el) => registerFieldRef?.("name", el)}
              value={formData.name}
              onChange={(e) => setFormData((prev) => ({ ...prev, name: e.target.value }))}
              onFocus={() => setLastFocusedField?.("name")}
              placeholder="e.g., NFL Default, NBA Premium"
            />
          </div>
        </CardContent>
      </Card>

      {/* Event Duration */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Event Duration</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground mb-3">
            Global and Per-Sport Defaults can be changed in Settings
          </p>
          <div className="space-y-2">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="duration_mode"
                checked={formData.game_duration_mode === "sport"}
                onChange={() => setFormData((prev) => ({ ...prev, game_duration_mode: "sport", game_duration_override: null }))}
                className="accent-primary"
              />
              <span>Use Per-Sport Default</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="duration_mode"
                checked={formData.game_duration_mode === "default"}
                onChange={() => setFormData((prev) => ({ ...prev, game_duration_mode: "default", game_duration_override: null }))}
                className="accent-primary"
              />
              <span>Use Global Default</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="duration_mode"
                checked={formData.game_duration_mode === "custom"}
                onChange={() => setFormData((prev) => ({ ...prev, game_duration_mode: "custom" }))}
                className="accent-primary"
              />
              <span>Custom:</span>
              <Input
                type="number"
                step="0.25"
                min="1"
                max="8"
                value={formData.game_duration_override ?? ""}
                onChange={(e) =>
                  setFormData((prev) => ({
                    ...prev,
                    game_duration_mode: "custom",
                    game_duration_override: e.target.value ? parseFloat(e.target.value) : null,
                  }))
                }
                disabled={formData.game_duration_mode !== "custom"}
                className="w-20 h-8"
                placeholder="3.5"
              />
              <span>hours</span>
            </label>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function DefaultsTab({ formData, setFormData, isTeamTemplate, registerFieldRef, setLastFocusedField, resolveTemplate, validationData }: TabProps) {
  const isEventTemplate = !isTeamTemplate
  // Extract fallback descriptions from conditional_descriptions (priority === 100)
  const fallbacks = useMemo(() => {
    const all = formData.conditional_descriptions || []
    return all
      .filter((c) => c.priority === 100)
      .map((c) => ({ label: c.label || "Default", template: c.template }))
  }, [formData.conditional_descriptions])

  // If no fallbacks exist, use description_template as the single fallback
  const effectiveFallbacks = fallbacks.length > 0 ? fallbacks :
    formData.description_template ? [{ label: "Default", template: formData.description_template }] : []

  const [expandedFallbacks, setExpandedFallbacks] = useState<Set<number>>(new Set([0]))

  const addFallback = () => {
    const newLabel = effectiveFallbacks.length === 0 ? "Default" : `Default ${effectiveFallbacks.length + 1}`
    const newFallback: ConditionalDescription = {
      condition: "",
      template: "",
      priority: 100,
      label: newLabel,
    }
    // Get non-fallback conditions
    const nonFallbacks = (formData.conditional_descriptions || []).filter((c) => c.priority !== 100)
    setFormData((prev) => ({
      ...prev,
      conditional_descriptions: [...nonFallbacks, ...getFallbacksAsConditions(), newFallback],
      description_template: null, // Clear single description when using fallbacks
    }))
    setExpandedFallbacks((prev) => new Set([...prev, effectiveFallbacks.length]))
  }

  const getFallbacksAsConditions = (): ConditionalDescription[] => {
    return effectiveFallbacks.map((f) => ({
      condition: "",
      template: f.template,
      priority: 100,
      label: f.label,
    }))
  }

  const updateFallback = (index: number, field: "label" | "template", value: string) => {
    const updated = [...effectiveFallbacks]
    updated[index] = { ...updated[index], [field]: value }
    // Convert back to conditional_descriptions
    const nonFallbacks = (formData.conditional_descriptions || []).filter((c) => c.priority !== 100)
    const fallbackConditions = updated.map((f) => ({
      condition: "",
      template: f.template,
      priority: 100,
      label: f.label,
    }))
    setFormData((prev) => ({
      ...prev,
      conditional_descriptions: [...nonFallbacks, ...fallbackConditions],
      description_template: null, // Clear single description when using fallbacks
    }))
  }

  const removeFallback = (index: number) => {
    if (effectiveFallbacks.length <= 1) {
      toast.error("At least one default description is required")
      return
    }
    const updated = effectiveFallbacks.filter((_, i) => i !== index)
    const nonFallbacks = (formData.conditional_descriptions || []).filter((c) => c.priority !== 100)
    const fallbackConditions = updated.map((f) => ({
      condition: "",
      template: f.template,
      priority: 100,
      label: f.label,
    }))
    setFormData((prev) => ({
      ...prev,
      conditional_descriptions: [...nonFallbacks, ...fallbackConditions],
      description_template: null,
    }))
    setExpandedFallbacks((prev) => {
      const next = new Set(prev)
      next.delete(index)
      return next
    })
  }

  const toggleFallback = (index: number) => {
    setExpandedFallbacks((prev) => {
      const next = new Set(prev)
      if (next.has(index)) {
        next.delete(index)
      } else {
        next.add(index)
      }
      return next
    })
  }

  return (
    <div className="space-y-6">
      {/* Channel Name & Logo (Event templates only) */}
      {!isTeamTemplate && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Channel Name & Logo</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <TemplateField
              id="event_channel_name"
              label="Channel Name Template"
              value={formData.event_channel_name || ""}
              onChange={(v) => setFormData((prev) => ({ ...prev, event_channel_name: v || null }))}
              placeholder="{away_team} @ {home_team}"
              helpText="Name for auto-created Dispatcharr channels"
              registerFieldRef={registerFieldRef}
              setLastFocusedField={setLastFocusedField}
              resolveTemplate={resolveTemplate}
              validationData={validationData}
              isEventTemplate={isEventTemplate}
            />
            <TemplateField
              id="event_channel_logo_url"
              label="Channel Logo URL Template"
              value={formData.event_channel_logo_url || ""}
              onChange={(v) => setFormData((prev) => ({ ...prev, event_channel_logo_url: v || null }))}
              placeholder="Optional"
              helpText="Optional. Static URL or template with variables."
              registerFieldRef={registerFieldRef}
              setLastFocusedField={setLastFocusedField}
              resolveTemplate={resolveTemplate}
              validationData={validationData}
              isEventTemplate={isEventTemplate}
            />
          </CardContent>
        </Card>
      )}

      {/* Title & Subtitle */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Title & Subtitle</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <TemplateField
            id="title_format"
            label="Program Title Template *"
            value={formData.title_format || ""}
            onChange={(v) => setFormData((prev) => ({ ...prev, title_format: v }))}
            placeholder="{league} {sport}"
            registerFieldRef={registerFieldRef}
            setLastFocusedField={setLastFocusedField}
            resolveTemplate={resolveTemplate}
            validationData={validationData}
            isEventTemplate={isEventTemplate}
          />
          <TemplateField
            id="subtitle_template"
            label="Program Subtitle Template"
            value={formData.subtitle_template || ""}
            onChange={(v) => setFormData((prev) => ({ ...prev, subtitle_template: v || null }))}
            placeholder="{away_team} at {home_team}"
            registerFieldRef={registerFieldRef}
            setLastFocusedField={setLastFocusedField}
            resolveTemplate={resolveTemplate}
            validationData={validationData}
            isEventTemplate={isEventTemplate}
          />
        </CardContent>
      </Card>

      {/* Default Descriptions (Multiple with randomization) */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Default Description Templates</CardTitle>
          <p className="text-sm text-muted-foreground mt-1">
            Used when no conditions match. If multiple defaults exist, one is randomly selected.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          {effectiveFallbacks.length === 0 ? (
            <p className="text-sm text-muted-foreground italic">
              No default descriptions yet. Click "Add Default Description" to get started.
            </p>
          ) : (
            effectiveFallbacks.map((fallback, index) => (
              <div key={index} className="border rounded-lg overflow-hidden">
                {/* Header */}
                <div
                  className="flex items-center justify-between px-3 py-2 bg-muted/50 cursor-pointer hover:bg-muted/70"
                  onClick={() => toggleFallback(index)}
                >
                  <div className="flex items-center gap-2">
                    <ChevronRight
                      className={`h-4 w-4 transition-transform ${expandedFallbacks.has(index) ? "rotate-90" : ""}`}
                    />
                    <span className="font-medium text-sm">{fallback.label || "Untitled"}</span>
                    <span className="text-xs text-muted-foreground">(Priority: 100)</span>
                  </div>
                  {effectiveFallbacks.length > 1 && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 w-7 p-0 text-destructive hover:text-destructive"
                      onClick={(e) => {
                        e.stopPropagation()
                        removeFallback(index)
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  )}
                </div>
                {/* Body */}
                {expandedFallbacks.has(index) && (
                  <div className="p-3 space-y-3 border-t">
                    <div className="space-y-1.5">
                      <Label className="text-sm">Label *</Label>
                      <Input
                        value={fallback.label}
                        onChange={(e) => updateFallback(index, "label", e.target.value)}
                        placeholder="e.g., 'Generic', 'Exciting', 'Classic'"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-sm">Description Template *</Label>
                      <Textarea
                        value={fallback.template}
                        onChange={(e) => updateFallback(index, "template", e.target.value)}
                        placeholder="{matchup} | {venue_full}"
                        rows={3}
                      />
                      {fallback.template && (
                        <p className="text-xs text-muted-foreground">
                          Preview: {resolveTemplate(fallback.template)}
                        </p>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))
          )}
          <Button variant="outline" size="sm" onClick={addFallback} className="mt-2">
            + Add Default Description
          </Button>
        </CardContent>
      </Card>

      {/* Program Art */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Program Art</CardTitle>
        </CardHeader>
        <CardContent>
          <TemplateField
            id="program_art_url"
            label="Program Art URL Template"
            value={formData.program_art_url || ""}
            onChange={(v) => setFormData((prev) => ({ ...prev, program_art_url: v || null }))}
            placeholder="Optional. Leave blank to disable program art."
            helpText="Optional. Static URL or template with variables."
            registerFieldRef={registerFieldRef}
            setLastFocusedField={setLastFocusedField}
            resolveTemplate={resolveTemplate}
            validationData={validationData}
            isEventTemplate={isEventTemplate}
          />
        </CardContent>
      </Card>
    </div>
  )
}

function ConditionsTab({ formData, setFormData, resolveTemplate, isTeamTemplate }: TabProps) {
  // Filter out fallback descriptions (priority=100) - they're managed on Defaults tab
  const conditions = useMemo(() => {
    return (formData.conditional_descriptions || []).filter((c) => c.priority !== 100)
  }, [formData.conditional_descriptions])
  const [showPresetDialog, setShowPresetDialog] = useState(false)
  const [showSaveDialog, setShowSaveDialog] = useState(false)
  const [presetName, setPresetName] = useState("")
  const [presetDescription, setPresetDescription] = useState("")
  const [expandedConditions, setExpandedConditions] = useState<Set<number>>(new Set())

  // Fetch available conditions from API (filtered by template type)
  const templateType = isTeamTemplate ? "team" : "event"
  const { data: conditionsData } = useQuery({
    queryKey: ["conditions", templateType],
    queryFn: () => fetchConditions(templateType),
    staleTime: 5 * 60 * 1000, // 5 minutes - allow refetch when conditions change
  })
  const availableConditions = conditionsData?.conditions || []

  // Presets hooks
  const { data: presets, isLoading: presetsLoading } = usePresets()
  const createPresetMutation = useCreatePreset()
  const deletePresetMutation = useDeletePreset()

  // Get fallbacks to preserve when modifying conditions
  const getFallbacks = () => (formData.conditional_descriptions || []).filter((c) => c.priority === 100)

  const addCondition = () => {
    // Default to first available condition, or is_home as fallback
    const defaultCondition = availableConditions.length > 0 ? availableConditions[0].name : "is_home"
    const newCondition: ConditionalDescription = {
      condition: defaultCondition,
      template: "",
      priority: 50, // Default conditional priority (not 100 which is for fallbacks)
    }
    const fallbacks = getFallbacks()
    setFormData((prev) => ({
      ...prev,
      conditional_descriptions: [...conditions, newCondition, ...fallbacks],
    }))
    // Auto-expand the new condition
    setExpandedConditions((prev) => new Set([...prev, conditions.length]))
  }

  const updateCondition = (index: number, field: keyof ConditionalDescription, value: string | number) => {
    const updated = [...conditions]
    updated[index] = { ...updated[index], [field]: value }
    const fallbacks = getFallbacks()
    setFormData((prev) => ({
      ...prev,
      conditional_descriptions: [...updated, ...fallbacks],
    }))
  }

  const removeCondition = (index: number) => {
    const updated = conditions.filter((_, i) => i !== index)
    const fallbacks = getFallbacks()
    setFormData((prev) => ({
      ...prev,
      conditional_descriptions: [...updated, ...fallbacks],
    }))
    setExpandedConditions((prev) => {
      const newSet = new Set(prev)
      newSet.delete(index)
      // Shift higher indices down
      const shifted = new Set<number>()
      newSet.forEach((i) => shifted.add(i > index ? i - 1 : i))
      return shifted
    })
  }

  const toggleConditionExpanded = (index: number) => {
    setExpandedConditions((prev) => {
      const newSet = new Set(prev)
      if (newSet.has(index)) {
        newSet.delete(index)
      } else {
        newSet.add(index)
      }
      return newSet
    })
  }

  const applyPreset = (preset: ConditionPreset) => {
    const fallbacks = getFallbacks()
    const presetConditions = preset.conditions.map((c) => ({
      condition: c.condition,
      template: c.template,
      priority: c.priority,
      condition_value: c.condition_value,
    }))
    setFormData((prev) => ({
      ...prev,
      conditional_descriptions: [...presetConditions, ...fallbacks], // Preserve fallbacks
    }))
    setShowPresetDialog(false)
    toast.success(`Applied preset "${preset.name}"`)
  }

  const handleSavePreset = async () => {
    if (!presetName.trim()) {
      toast.error("Preset name is required")
      return
    }
    if (conditions.length === 0) {
      toast.error("Add at least one condition before saving")
      return
    }

    try {
      await createPresetMutation.mutateAsync({
        name: presetName.trim(),
        description: presetDescription.trim() || undefined,
        conditions: conditions.map((c) => ({
          condition: c.condition,
          template: c.template,
          priority: c.priority,
          condition_value: c.condition_value,
        })),
      })
      toast.success(`Saved preset "${presetName}"`)
      setShowSaveDialog(false)
      setPresetName("")
      setPresetDescription("")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save preset")
    }
  }

  const handleDeletePreset = async (preset: ConditionPreset) => {
    if (!confirm(`Delete preset "${preset.name}"?`)) return
    try {
      await deletePresetMutation.mutateAsync(preset.id)
      toast.success(`Deleted preset "${preset.name}"`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete preset")
    }
  }

  // Get condition info for display
  const getConditionInfo = (condName: string) => {
    return availableConditions.find((c) => c.name === condName)
  }

  // Sort conditions by priority for display
  const sortedConditions = [...conditions].map((c, i) => ({ ...c, originalIndex: i })).sort((a, b) => a.priority - b.priority)

  return (
    <div className="space-y-4">
      {/* Preset Library Card */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2">
              <BookOpen className="h-4 w-4" />
              Preset Library
            </CardTitle>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setShowPresetDialog(true)}>
                <Download className="h-3 w-3 mr-1" />
                Load Preset
              </Button>
              <Button variant="outline" size="sm" onClick={() => setShowSaveDialog(true)} disabled={conditions.length === 0}>
                <Upload className="h-3 w-3 mr-1" />
                Save as Preset
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Save and reuse condition configurations across templates. Load a preset to apply its conditions, or save your current setup.
          </p>
        </CardContent>
      </Card>

      {/* Conditions Card */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">🎯 Conditional Descriptions</CardTitle>
            <Button onClick={addCondition} variant="outline" size="sm">
              + Add Condition
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Create dynamic descriptions based on specific conditions. Lower priority numbers are checked first.
            Priority 100 is reserved for fallback descriptions.
          </p>

          {conditions.length === 0 ? (
            <div className="text-center py-8 border-2 border-dashed rounded-lg">
              <p className="text-sm text-muted-foreground">
                No conditions defined. Add conditions to customize descriptions based on game context.
              </p>
              <Button onClick={addCondition} variant="outline" size="sm" className="mt-2">
                + Add First Condition
              </Button>
            </div>
          ) : (
            <div className="space-y-2">
              {sortedConditions.map((cond) => {
                const idx = cond.originalIndex
                const isExpanded = expandedConditions.has(idx)
                const condInfo = getConditionInfo(cond.condition)
                const isFallback = cond.priority >= 100 || cond.condition === "always"

                return (
                  <div
                    key={idx}
                    className={`border rounded-lg overflow-hidden transition-all ${
                      isFallback ? "bg-amber-500/5 border-amber-500/30" : "bg-secondary/30"
                    }`}
                  >
                    {/* Collapsed header */}
                    <div
                      className="flex items-center gap-2 p-2 cursor-pointer hover:bg-secondary/50"
                      onClick={() => toggleConditionExpanded(idx)}
                    >
                      <ChevronRight className={`h-4 w-4 transition-transform ${isExpanded ? "rotate-90" : ""}`} />
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                        isFallback ? "bg-amber-500/20 text-amber-400" : "bg-primary/20 text-primary"
                      }`}>
                        P{cond.priority}
                      </span>
                      <span className="text-sm font-medium flex-1">
                        {condInfo?.description || cond.condition}
                        {cond.condition_value && ` (${cond.condition_value})`}
                        {condInfo?.providers === "espn" && (
                          <span className="ml-1 text-[10px] text-amber-500">(ESPN)</span>
                        )}
                      </span>
                      {cond.template && (
                        <span className="text-xs text-muted-foreground truncate max-w-[200px]">
                          {cond.template.substring(0, 40)}{cond.template.length > 40 ? "..." : ""}
                        </span>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => { e.stopPropagation(); removeCondition(idx) }}
                        className="h-6 w-6 p-0 text-destructive hover:text-destructive"
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>

                    {/* Expanded content */}
                    {isExpanded && (
                      <div className="p-3 pt-0 space-y-3 border-t">
                        <div className="grid grid-cols-3 gap-3 pt-3">
                          <div>
                            <Label className="text-xs">Condition</Label>
                            <Select
                              value={cond.condition}
                              onChange={(e) => updateCondition(idx, "condition", e.target.value)}
                              className="h-8 text-sm"
                            >
                              {availableConditions.length > 0 ? (
                                availableConditions.map((c) => (
                                  <option key={c.name} value={c.name}>
                                    {c.description}{c.providers === "espn" ? " (ESPN only)" : ""}
                                  </option>
                                ))
                              ) : (
                                <>
                                  <option value="is_home">Team is playing at home</option>
                                  <option value="is_away">Team is playing away</option>
                                  <option value="win_streak">Team is on a win streak of N or more games</option>
                                  <option value="loss_streak">Team is on a loss streak of N or more games</option>
                                  <option value="is_ranked">Team is ranked (college sports)</option>
                                  <option value="is_ranked_opponent">Opponent is ranked (college sports)</option>
                                  <option value="is_ranked_matchup">Both teams are ranked (college sports)</option>
                                  <option value="is_top_ten_matchup">Both teams are ranked in top 10</option>
                                  <option value="is_conference_game">Game is a conference matchup</option>
                                  <option value="is_playoff">Game is a playoff/postseason game</option>
                                  <option value="is_preseason">Game is a preseason game</option>
                                  <option value="is_national_broadcast">Game is on national TV</option>
                                  <option value="has_odds">Betting odds are available for the game</option>
                                  <option value="opponent_name_contains">Opponent name contains specific text</option>
                                </>
                              )}
                            </Select>
                          </div>
                          {condInfo?.requires_value && (
                            <div>
                              <Label className="text-xs">Value</Label>
                              <Input
                                type={condInfo.value_type === "number" ? "number" : "text"}
                                min={condInfo.value_type === "number" ? "1" : undefined}
                                value={cond.condition_value || ""}
                                onChange={(e) => updateCondition(idx, "condition_value", e.target.value)}
                                className="h-8 text-sm"
                                placeholder={condInfo.value_type === "number" ? "3" : "value"}
                              />
                            </div>
                          )}
                          <div>
                            <Label className="text-xs">Priority</Label>
                            <Input
                              type="number"
                              min="1"
                              max="100"
                              value={cond.priority}
                              onChange={(e) => updateCondition(idx, "priority", parseInt(e.target.value) || 50)}
                              className="h-8 text-sm"
                            />
                            <p className="text-[10px] text-muted-foreground mt-0.5">
                              Lower = checked first. 100 = fallback
                            </p>
                          </div>
                        </div>
                        <div>
                          <Label className="text-xs">Description Template</Label>
                          <Input
                            value={cond.template}
                            onChange={(e) => updateCondition(idx, "template", e.target.value)}
                            placeholder="{team_name} plays {opponent} at {venue}"
                            className="font-mono text-sm"
                          />
                          {cond.template && (
                            <div className="mt-1 px-2 py-1 bg-secondary/50 border-l-2 border-primary rounded-sm">
                              <span className="text-[10px] text-muted-foreground uppercase font-semibold mr-2">Preview:</span>
                              <span className="text-sm italic">{resolveTemplate(cond.template)}</span>
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Load Preset Dialog */}
      <Dialog open={showPresetDialog} onOpenChange={setShowPresetDialog}>
        <DialogContent className="max-w-lg" onClose={() => setShowPresetDialog(false)}>
          <DialogHeader>
            <DialogTitle>Load Preset</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 max-h-[400px] overflow-y-auto">
            {presetsLoading ? (
              <div className="flex justify-center py-4">
                <Loader2 className="h-5 w-5 animate-spin" />
              </div>
            ) : !presets || presets.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">
                No presets saved yet. Create conditions and save them as a preset.
              </p>
            ) : (
              presets.map((preset) => (
                <div key={preset.id} className="p-3 border rounded-lg hover:bg-secondary/50 transition-colors">
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <h4 className="font-medium">{preset.name}</h4>
                      {preset.description && (
                        <p className="text-xs text-muted-foreground">{preset.description}</p>
                      )}
                      <p className="text-xs text-muted-foreground mt-1">
                        {preset.conditions.length} condition{preset.conditions.length !== 1 ? "s" : ""}
                      </p>
                    </div>
                    <div className="flex gap-1">
                      <Button size="sm" onClick={() => applyPreset(preset)}>
                        Apply
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleDeletePreset(preset)}
                        disabled={deletePresetMutation.isPending}
                      >
                        <Trash2 className="h-3 w-3 text-destructive" />
                      </Button>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowPresetDialog(false)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Save Preset Dialog */}
      <Dialog open={showSaveDialog} onOpenChange={setShowSaveDialog}>
        <DialogContent onClose={() => setShowSaveDialog(false)}>
          <DialogHeader>
            <DialogTitle>Save as Preset</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1">
              <Label htmlFor="preset-name">Name *</Label>
              <Input
                id="preset-name"
                value={presetName}
                onChange={(e) => setPresetName(e.target.value)}
                placeholder="e.g., NBA Home Games"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="preset-description">Description</Label>
              <Input
                id="preset-description"
                value={presetDescription}
                onChange={(e) => setPresetDescription(e.target.value)}
                placeholder="Optional description"
              />
            </div>
            <p className="text-sm text-muted-foreground">
              This will save {conditions.length} condition{conditions.length !== 1 ? "s" : ""} as a reusable preset.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowSaveDialog(false)}>
              Cancel
            </Button>
            <Button onClick={handleSavePreset} disabled={createPresetMutation.isPending}>
              {createPresetMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Save Preset
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function FillersTab({ formData, setFormData, isTeamTemplate, registerFieldRef, setLastFocusedField, resolveTemplate, validationData }: TabProps) {
  const isEventTemplate = !isTeamTemplate
  const pregame = formData.pregame_fallback || DEFAULT_PREGAME
  const postgame = formData.postgame_fallback || DEFAULT_POSTGAME
  const idle = formData.idle_content || DEFAULT_IDLE
  const postgameCond = formData.postgame_conditional || { enabled: false, description_final: null, description_not_final: null }
  const idleCond = formData.idle_conditional || { enabled: false, description_final: null, description_not_final: null }
  const idleOffseason = formData.idle_offseason || { title_enabled: false, title: null, subtitle_enabled: false, subtitle: null, description_enabled: false, description: null }

  const updatePregame = (field: keyof FillerContent, value: string | null) => {
    setFormData((prev) => ({
      ...prev,
      pregame_fallback: { ...pregame, [field]: value },
    }))
  }

  const updatePostgame = (field: keyof FillerContent, value: string | null) => {
    setFormData((prev) => ({
      ...prev,
      postgame_fallback: { ...postgame, [field]: value },
    }))
  }

  const updatePostgameCond = (field: keyof ConditionalSettings, value: boolean | string | null) => {
    setFormData((prev) => ({
      ...prev,
      postgame_conditional: { ...postgameCond, [field]: value },
    }))
  }

  const updateIdle = (field: keyof FillerContent, value: string | null) => {
    setFormData((prev) => ({
      ...prev,
      idle_content: { ...idle, [field]: value },
    }))
  }

  const updateIdleCond = (field: keyof ConditionalSettings, value: boolean | string | null) => {
    setFormData((prev) => ({
      ...prev,
      idle_conditional: { ...idleCond, [field]: value },
    }))
  }

  const updateIdleOffseason = (field: keyof IdleOffseasonSettings, value: boolean | string | null) => {
    setFormData((prev) => ({
      ...prev,
      idle_offseason: { ...idleOffseason, [field]: value },
    }))
  }

  return (
    <div className="space-y-6">
      {/* Pregame */}
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle className="text-base">⏰ Pregame</CardTitle>
          <Switch
            checked={formData.pregame_enabled ?? true}
            onCheckedChange={(checked) => setFormData((prev) => ({ ...prev, pregame_enabled: checked }))}
          />
        </CardHeader>
        {formData.pregame_enabled && (
          <CardContent className="space-y-4">
            <TemplateField
              id="pregame_fallback.title"
              label="Title"
              value={pregame.title}
              onChange={(v) => updatePregame("title", v)}
              registerFieldRef={registerFieldRef}
              setLastFocusedField={setLastFocusedField}
              resolveTemplate={resolveTemplate}
              validationData={validationData}
              isEventTemplate={isEventTemplate}
            />
            <TemplateField
              id="pregame_fallback.subtitle"
              label="Subtitle"
              value={pregame.subtitle || ""}
              onChange={(v) => updatePregame("subtitle", v || null)}
              placeholder="Optional"
              registerFieldRef={registerFieldRef}
              setLastFocusedField={setLastFocusedField}
              resolveTemplate={resolveTemplate}
              validationData={validationData}
              isEventTemplate={isEventTemplate}
            />
            <TemplateField
              id="pregame_fallback.description"
              label="Description"
              value={pregame.description}
              onChange={(v) => updatePregame("description", v)}
              registerFieldRef={registerFieldRef}
              setLastFocusedField={setLastFocusedField}
              resolveTemplate={resolveTemplate}
              validationData={validationData}
              isEventTemplate={isEventTemplate}
            />
            <TemplateField
              id="pregame_fallback.art_url"
              label="Program Art URL"
              value={pregame.art_url || ""}
              onChange={(v) => updatePregame("art_url", v || null)}
              placeholder="Optional"
              registerFieldRef={registerFieldRef}
              setLastFocusedField={setLastFocusedField}
              resolveTemplate={resolveTemplate}
              validationData={validationData}
              isEventTemplate={isEventTemplate}
            />
          </CardContent>
        )}
      </Card>

      {/* Postgame */}
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle className="text-base">📺 Postgame</CardTitle>
          <Switch
            checked={formData.postgame_enabled ?? true}
            onCheckedChange={(checked) => setFormData((prev) => ({ ...prev, postgame_enabled: checked }))}
          />
        </CardHeader>
        {formData.postgame_enabled && (
          <CardContent className="space-y-4">
            <TemplateField
              id="postgame_fallback.title"
              label="Title"
              value={postgame.title}
              onChange={(v) => updatePostgame("title", v)}
              registerFieldRef={registerFieldRef}
              setLastFocusedField={setLastFocusedField}
              resolveTemplate={resolveTemplate}
              validationData={validationData}
              isEventTemplate={isEventTemplate}
            />
            <TemplateField
              id="postgame_fallback.subtitle"
              label="Subtitle"
              value={postgame.subtitle || ""}
              onChange={(v) => updatePostgame("subtitle", v || null)}
              placeholder="Optional"
              registerFieldRef={registerFieldRef}
              setLastFocusedField={setLastFocusedField}
              resolveTemplate={resolveTemplate}
              validationData={validationData}
              isEventTemplate={isEventTemplate}
            />
            <TemplateField
              id="postgame_fallback.description"
              label="Description"
              value={postgame.description}
              onChange={(v) => updatePostgame("description", v)}
              registerFieldRef={registerFieldRef}
              setLastFocusedField={setLastFocusedField}
              resolveTemplate={resolveTemplate}
              validationData={validationData}
              isEventTemplate={isEventTemplate}
            />

            {/* Conditional postgame */}
            <div className="p-3 bg-secondary/30 rounded-lg space-y-3">
              <label className="flex items-center gap-2 cursor-pointer">
                <Checkbox
                  checked={postgameCond.enabled}
                  onCheckedChange={() => updatePostgameCond("enabled", !postgameCond.enabled)}
                />
                <span className="text-sm">Use conditional description based on last game status</span>
              </label>
              {postgameCond.enabled && (
                <>
                  <TemplateField
                    id="postgame_conditional.description_final"
                    label="✓ If last game is final:"
                    value={postgameCond.description_final || ""}
                    onChange={(v) => updatePostgameCond("description_final", v || null)}
                    placeholder="The {team_name} {result_text.last} the {opponent.last} {final_score.last}"
                    registerFieldRef={registerFieldRef}
                    setLastFocusedField={setLastFocusedField}
                    resolveTemplate={resolveTemplate}
                  />
                  <TemplateField
                    id="postgame_conditional.description_not_final"
                    label="⏳ If last game is NOT final:"
                    value={postgameCond.description_not_final || ""}
                    onChange={(v) => updatePostgameCond("description_not_final", v || null)}
                    placeholder="The game between {team_name} and {opponent.last} has not yet ended."
                    registerFieldRef={registerFieldRef}
                    setLastFocusedField={setLastFocusedField}
                    resolveTemplate={resolveTemplate}
                  />
                </>
              )}
            </div>

            <TemplateField
              id="postgame_fallback.art_url"
              label="Program Art URL"
              value={postgame.art_url || ""}
              onChange={(v) => updatePostgame("art_url", v || null)}
              placeholder="Optional"
              registerFieldRef={registerFieldRef}
              setLastFocusedField={setLastFocusedField}
              resolveTemplate={resolveTemplate}
              validationData={validationData}
              isEventTemplate={isEventTemplate}
            />
          </CardContent>
        )}
      </Card>

      {/* Idle Day (Team templates only) */}
      {isTeamTemplate && (
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">💤 Idle Day</CardTitle>
            <Switch
              checked={formData.idle_enabled ?? true}
              onCheckedChange={(checked) => setFormData((prev) => ({ ...prev, idle_enabled: checked }))}
            />
          </CardHeader>
          {formData.idle_enabled && (
            <CardContent className="space-y-4">
              {/* Title with offseason override */}
              <TemplateField
                id="idle_content.title"
                label="Title"
                value={idle.title}
                onChange={(v) => updateIdle("title", v)}
                registerFieldRef={registerFieldRef}
                setLastFocusedField={setLastFocusedField}
                resolveTemplate={resolveTemplate}
              />
              <div className="p-3 bg-secondary/30 rounded-lg space-y-3 -mt-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <Checkbox
                    checked={idleOffseason.title_enabled}
                    onCheckedChange={() => updateIdleOffseason("title_enabled", !idleOffseason.title_enabled)}
                  />
                  <span className="text-sm">Override title when no games in 30-day lookahead</span>
                </label>
                {idleOffseason.title_enabled && (
                  <TemplateField
                    id="idle_offseason.title"
                    label="📅 No upcoming games:"
                    value={idleOffseason.title || ""}
                    onChange={(v) => updateIdleOffseason("title", v || null)}
                    placeholder="Off-Season Programming"
                    registerFieldRef={registerFieldRef}
                    setLastFocusedField={setLastFocusedField}
                    resolveTemplate={resolveTemplate}
                  />
                )}
              </div>

              {/* Subtitle with offseason override */}
              <TemplateField
                id="idle_content.subtitle"
                label="Subtitle"
                value={idle.subtitle || ""}
                onChange={(v) => updateIdle("subtitle", v || null)}
                placeholder="Optional"
                registerFieldRef={registerFieldRef}
                setLastFocusedField={setLastFocusedField}
                resolveTemplate={resolveTemplate}
              />
              <div className="p-3 bg-secondary/30 rounded-lg space-y-3 -mt-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <Checkbox
                    checked={idleOffseason.subtitle_enabled}
                    onCheckedChange={() => updateIdleOffseason("subtitle_enabled", !idleOffseason.subtitle_enabled)}
                  />
                  <span className="text-sm">Override subtitle when no games in 30-day lookahead</span>
                </label>
                {idleOffseason.subtitle_enabled && (
                  <TemplateField
                    id="idle_offseason.subtitle"
                    label="📅 No upcoming games:"
                    value={idleOffseason.subtitle || ""}
                    onChange={(v) => updateIdleOffseason("subtitle", v || null)}
                    placeholder="See you next season!"
                    registerFieldRef={registerFieldRef}
                    setLastFocusedField={setLastFocusedField}
                    resolveTemplate={resolveTemplate}
                  />
                )}
              </div>

              {/* Description with offseason override */}
              <TemplateField
                id="idle_content.description"
                label="Description"
                value={idle.description}
                onChange={(v) => updateIdle("description", v)}
                registerFieldRef={registerFieldRef}
                setLastFocusedField={setLastFocusedField}
                resolveTemplate={resolveTemplate}
              />
              <div className="p-3 bg-secondary/30 rounded-lg space-y-3 -mt-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <Checkbox
                    checked={idleOffseason.description_enabled}
                    onCheckedChange={() => updateIdleOffseason("description_enabled", !idleOffseason.description_enabled)}
                  />
                  <span className="text-sm">Override description when no games in 30-day lookahead</span>
                </label>
                {idleOffseason.description_enabled && (
                  <TemplateField
                    id="idle_offseason.description"
                    label="📅 No upcoming games:"
                    value={idleOffseason.description || ""}
                    onChange={(v) => updateIdleOffseason("description", v || null)}
                    placeholder="No upcoming {team_name} games scheduled."
                    registerFieldRef={registerFieldRef}
                    setLastFocusedField={setLastFocusedField}
                    resolveTemplate={resolveTemplate}
                  />
                )}
              </div>

              {/* Conditional idle (final/not final) */}
              <div className="p-3 bg-secondary/30 rounded-lg space-y-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <Checkbox
                    checked={idleCond.enabled}
                    onCheckedChange={() => updateIdleCond("enabled", !idleCond.enabled)}
                  />
                  <span className="text-sm">Use conditional description based on last game status</span>
                </label>
                {idleCond.enabled && (
                  <>
                    <TemplateField
                      id="idle_conditional.description_final"
                      label="✓ If last game is final:"
                      value={idleCond.description_final || ""}
                      onChange={(v) => updateIdleCond("description_final", v || null)}
                      placeholder="The {team_name} {result_text.last} the {opponent.last} {final_score.last}"
                      registerFieldRef={registerFieldRef}
                      setLastFocusedField={setLastFocusedField}
                      resolveTemplate={resolveTemplate}
                    />
                    <TemplateField
                      id="idle_conditional.description_not_final"
                      label="⏳ If last game is NOT final:"
                      value={idleCond.description_not_final || ""}
                      onChange={(v) => updateIdleCond("description_not_final", v || null)}
                      placeholder="The {team_name} last played against {opponent.last}."
                      registerFieldRef={registerFieldRef}
                      setLastFocusedField={setLastFocusedField}
                      resolveTemplate={resolveTemplate}
                    />
                  </>
                )}
              </div>

              <TemplateField
                id="idle_content.art_url"
                label="Program Art URL"
                value={idle.art_url || ""}
                onChange={(v) => updateIdle("art_url", v || null)}
                placeholder="Optional"
                registerFieldRef={registerFieldRef}
                setLastFocusedField={setLastFocusedField}
                resolveTemplate={resolveTemplate}
              />
            </CardContent>
          )}
        </Card>
      )}
    </div>
  )
}

function XmltvTab({ formData, setFormData }: TabProps) {
  const flags = formData.xmltv_flags || { new: true, live: false, date: false }
  const categories = formData.xmltv_categories || ["Sports"]

  const hasSports = categories.includes("Sports")
  const hasSportVar = categories.includes("{sport}")
  const customCategories = categories.filter((c) => c !== "Sports" && c !== "{sport}")

  // Use local state for the input to preserve user's typing (including spaces)
  // This prevents the input from being cleared when typing words that match base categories
  const [customInput, setCustomInput] = useState<string | null>(null)
  const resolvedCustomInput = customInput ?? customCategories.join(", ")

  const updateFlags = (field: keyof XmltvFlags, value: boolean) => {
    setFormData((prev) => ({
      ...prev,
      xmltv_flags: { ...flags, [field]: value },
    }))
  }

  const toggleCategory = (cat: string, checked: boolean) => {
    const current = formData.xmltv_categories || []
    if (checked) {
      setFormData((prev) => ({ ...prev, xmltv_categories: [...current, cat] }))
    } else {
      setFormData((prev) => ({ ...prev, xmltv_categories: current.filter((c) => c !== cat) }))
    }
  }

  const updateCustomCategories = (value: string) => {
    setCustomInput(value)
    const custom = value
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
    const base = [hasSports && "Sports", hasSportVar && "{sport}"].filter(Boolean) as string[]
    setFormData((prev) => ({ ...prev, xmltv_categories: [...base, ...custom] }))
  }

  return (
    <div className="space-y-6">
      {/* Categories */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">📂 Categories</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>Common Categories</Label>
            <div className="space-y-2">
              <label className="flex items-center gap-2 cursor-pointer">
                <Checkbox checked={hasSports} onCheckedChange={() => toggleCategory("Sports", !hasSports)} />
                <span>Sports</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <Checkbox checked={hasSportVar} onCheckedChange={() => toggleCategory("{sport}", !hasSportVar)} />
                <span>
                  <code>{"{sport}"}</code> - Auto-populates with team's sport (Basketball, Football, etc.)
                </span>
              </label>
            </div>
          </div>

          <div className="space-y-1">
            <Label htmlFor="custom_categories">Custom Categories (comma-separated)</Label>
            <Input
              id="custom_categories"
              value={resolvedCustomInput}
              onChange={(e) => updateCustomCategories(e.target.value)}
              placeholder="e.g., Entertainment, Live Events"
            />
            <p className="text-xs text-muted-foreground">
              Categories shown in EPG guide. Check common ones above or add custom categories.
            </p>
          </div>

          <div className="space-y-1">
            <Label htmlFor="categories_apply_to">Apply Categories To</Label>
            <Select
              id="categories_apply_to"
              value={formData.categories_apply_to || "events"}
              onChange={(e) => setFormData((prev) => ({ ...prev, categories_apply_to: e.target.value }))}
            >
              <option value="all">All Programs (Events + Filler)</option>
              <option value="events">Events Only</option>
            </Select>
            <p className="text-xs text-muted-foreground">
              Choose whether categories apply to all programs or only game events
            </p>
          </div>
        </CardContent>
      </Card>

      {/* Tags */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">🏷️ Tags</CardTitle>
          <p className="text-xs text-muted-foreground">
            Tags only apply to events, not to filler (pregame/postgame/idle).
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <Checkbox checked={flags.new} onCheckedChange={() => updateFlags("new", !flags.new)} />
            <div>
              <span>Include New Tag</span>
              <p className="text-xs text-muted-foreground">Adds &lt;new/&gt; tag to events</p>
            </div>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <Checkbox checked={flags.live} onCheckedChange={() => updateFlags("live", !flags.live)} />
            <div>
              <span>Include Live Tag</span>
              <p className="text-xs text-muted-foreground">Adds &lt;live/&gt; tag to events</p>
            </div>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <Checkbox checked={flags.date} onCheckedChange={() => updateFlags("date", !flags.date)} />
            <div>
              <span>Include Date Tag</span>
              <p className="text-xs text-muted-foreground">Adds &lt;date&gt; tag with air date (YYYYMMDD) to events</p>
            </div>
          </label>
        </CardContent>
      </Card>

      {/* Video Quality */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">📺 Video Quality</CardTitle>
          <p className="text-xs text-muted-foreground">
            XMLTV video element for EPG clients that support quality metadata.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="bg-amber-500/10 border border-amber-500/20 rounded-md p-3 text-xs text-amber-600 dark:text-amber-400">
            <strong>Note:</strong> Teamarr does not detect actual stream resolution. This setting will apply to <strong>all</strong> channels using this template, regardless of their actual quality.
          </div>
          <label className="flex items-center gap-2 cursor-pointer">
            <Checkbox
              checked={formData.xmltv_video?.enabled || false}
              onCheckedChange={() => setFormData(prev => ({
                ...prev,
                xmltv_video: { ...prev.xmltv_video, enabled: !prev.xmltv_video?.enabled }
              }))}
            />
            <div>
              <span>Include Video Element</span>
              <p className="text-xs text-muted-foreground">Adds &lt;video&gt;&lt;quality&gt; element</p>
            </div>
          </label>
          {formData.xmltv_video?.enabled && (
            <div className="pt-2">
              <label className="text-xs font-medium">Quality</label>
              <select
                className="w-full mt-1 px-2 py-1.5 text-sm border rounded-md bg-background"
                value={formData.xmltv_video?.quality || "HDTV"}
                onChange={(e) => setFormData(prev => ({
                  ...prev,
                  xmltv_video: { ...prev.xmltv_video, quality: e.target.value }
                }))}
              >
                <option value="SDTV">SDTV</option>
                <option value="HDTV">HDTV</option>
                <option value="UHD">UHD (4K)</option>
              </select>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
