import React, { useState, useRef } from "react"
import { useQuery } from "@tanstack/react-query"
import { ChevronDown, Search, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { fetchVariables, type Variable, type VariableCategory } from "@/api/variables"

interface VariablePickerProps {
  onSelect: (variable: string) => void
  buttonLabel?: string
  compact?: boolean
}

export function VariablePicker({
  onSelect,
  buttonLabel = "Insert Variable",
  compact = false,
}: VariablePickerProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [search, setSearch] = useState("")
  const [expandedCategory, setExpandedCategory] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ["variables"],
    queryFn: fetchVariables,
    staleTime: Infinity, // Variables don't change during session
  })

  const filteredCategories = (() => {
    if (!data?.categories) return []
    if (!search.trim()) return data.categories

    const searchLower = search.toLowerCase()
    return data.categories
      .map((cat) => ({
        ...cat,
        variables: cat.variables.filter(
          (v) =>
            v.name.toLowerCase().includes(searchLower) ||
            v.description.toLowerCase().includes(searchLower)
        ),
      }))
      .filter((cat) => cat.variables.length > 0)
  })()

  const handleSelect = (variable: Variable, suffix?: string) => {
    const varName = suffix && suffix !== "base" ? `${variable.name}${suffix}` : variable.name
    onSelect(`{${varName}}`)
    setIsOpen(false)
    setSearch("")
  }

  if (isLoading) {
    return (
      <Button variant="outline" size={compact ? "sm" : "default"} disabled>
        Loading...
      </Button>
    )
  }

  return (
    <div className="relative">
      <Button
        variant="outline"
        size={compact ? "sm" : "default"}
        onClick={() => setIsOpen(!isOpen)}
        className="gap-1"
      >
        {buttonLabel}
        <ChevronDown className="h-3 w-3" />
      </Button>

      {isOpen && (
        <>
          {/* Backdrop */}
          <div className="fixed inset-0 z-40" onClick={() => setIsOpen(false)} />

          {/* Dropdown */}
          <div className="absolute right-0 top-full mt-1 z-50 w-80 max-h-96 rounded-md border bg-popover shadow-lg">
            {/* Search */}
            <div className="p-2 border-b">
              <div className="relative">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Search variables..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="pl-8 pr-8 h-8"
                  autoFocus
                />
                {search && (
                  <button
                    onClick={() => setSearch("")}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
              </div>
              <div className="text-xs text-muted-foreground mt-1">
                {data?.total} variables available
              </div>
            </div>

            {/* Categories */}
            <div className="overflow-y-auto max-h-72">
              {filteredCategories.map((category) => (
                <CategorySection
                  key={category.name}
                  category={category}
                  isExpanded={expandedCategory === category.name || search.length > 0}
                  onToggle={() =>
                    setExpandedCategory(
                      expandedCategory === category.name ? null : category.name
                    )
                  }
                  onSelect={handleSelect}
                />
              ))}

              {filteredCategories.length === 0 && (
                <div className="p-4 text-center text-sm text-muted-foreground">
                  No variables found
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

interface CategorySectionProps {
  category: VariableCategory
  isExpanded: boolean
  onToggle: () => void
  onSelect: (variable: Variable, suffix?: string) => void
}

function CategorySection({
  category,
  isExpanded,
  onToggle,
  onSelect,
}: CategorySectionProps) {
  return (
    <div className="border-b last:border-b-0">
      <button
        onClick={onToggle}
        className="w-full px-3 py-2 flex items-center justify-between text-sm font-medium hover:bg-accent/50 transition-colors"
      >
        <span>{category.name}</span>
        <span className="text-xs text-muted-foreground">
          {category.variables.length}
        </span>
      </button>

      {isExpanded && (
        <div className="px-2 pb-2">
          {category.variables.map((variable) => (
            <VariableItem
              key={variable.name}
              variable={variable}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  )
}

interface VariableItemProps {
  variable: Variable
  onSelect: (variable: Variable, suffix?: string) => void
}

function VariableItem({ variable, onSelect }: VariableItemProps) {
  const [showSuffixPopup, setShowSuffixPopup] = useState(false)
  const [popupPosition, setPopupPosition] = useState({ top: 0, left: 0 })
  const buttonRef = useRef<HTMLButtonElement>(null)
  const hasSuffixes = variable.suffixes.length > 1

  const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
    if (hasSuffixes) {
      e.stopPropagation()
      // Calculate position for fixed popup
      if (buttonRef.current) {
        const rect = buttonRef.current.getBoundingClientRect()
        setPopupPosition({
          top: rect.bottom + 4,
          left: rect.left,
        })
      }
      setShowSuffixPopup(!showSuffixPopup)
    } else {
      // Single suffix - apply it directly
      const suffix = variable.suffixes[0]
      onSelect(variable, suffix === "base" ? undefined : suffix)
    }
  }

  const handleSuffixSelect = (suffix: string) => {
    onSelect(variable, suffix === "base" ? undefined : suffix)
    setShowSuffixPopup(false)
  }

  const suffixLabels: Record<string, { label: string; desc: string }> = {
    "base": { label: "base", desc: "current game OR not game-dependent" },
    ".next": { label: ".next", desc: "next game" },
    ".last": { label: ".last", desc: "last game" },
  }

  // For display: show full variable name including suffix for single-suffix vars
  const displayName = hasSuffixes
    ? variable.name  // Multi-suffix shows base name with dropdown
    : variable.suffixes[0] === "base"
      ? variable.name  // Base-only shows just the name
      : `${variable.name}${variable.suffixes[0]}`  // Single non-base suffix shows full name

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        onClick={handleClick}
        className="w-full text-left rounded-md hover:bg-accent/50 transition-colors px-2 py-1"
      >
        <div className="flex items-center justify-between">
          <div className="flex-1 min-w-0">
            <code className="text-xs font-mono text-primary">
              {"{" + displayName + "}"}
            </code>
            {variable.description && (
              <p className="text-xs text-muted-foreground truncate">
                {variable.description}
              </p>
            )}
          </div>
          {hasSuffixes && (
            <ChevronDown className={`h-3 w-3 ml-2 flex-shrink-0 text-muted-foreground transition-transform ${showSuffixPopup ? 'rotate-180' : ''}`} />
          )}
        </div>
      </button>

      {/* Suffix selector popup - uses fixed positioning to escape overflow context */}
      {showSuffixPopup && hasSuffixes && (
        <>
          {/* Backdrop to close popup */}
          <div
            className="fixed inset-0 z-[60]"
            onClick={(e) => {
              e.stopPropagation()
              setShowSuffixPopup(false)
            }}
          />

          {/* Popup - fixed position to escape overflow:hidden */}
          <div
            className="fixed z-[70] min-w-[220px] bg-popover border rounded-md shadow-lg overflow-hidden"
            style={{ top: popupPosition.top, left: popupPosition.left }}
          >
            {/* Header */}
            <div className="px-3 py-2 bg-accent/50 border-b font-semibold text-sm">
              {variable.name}
            </div>

            {/* Options */}
            <div className="p-1">
              {variable.suffixes.map((suffix) => {
                const info = suffixLabels[suffix] || { label: suffix, desc: "" }
                const varText = suffix === "base" ? variable.name : `${variable.name}${suffix}`

                return (
                  <button
                    key={suffix}
                    onClick={(e) => {
                      e.stopPropagation()
                      handleSuffixSelect(suffix)
                    }}
                    className="w-full text-left px-3 py-2 rounded hover:bg-accent/50 transition-colors"
                  >
                    <code className="text-sm font-mono text-primary font-semibold">
                      {"{" + varText + "}"}
                    </code>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {info.desc}
                    </p>
                  </button>
                )
              })}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
