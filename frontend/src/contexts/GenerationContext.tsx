import { useState, useEffect, useCallback, useRef, type ReactNode } from "react"
import { toast } from "sonner"
import { GenerationContext, type GenerationStatus } from "@/contexts/generation-context"

const TOAST_ID = "epg-generation"

// Progress description component for toast
function ProgressDescription({ status }: { status: GenerationStatus | null }) {
  const percent = status?.percent ?? 0
  const itemName = status?.item_name
  const current = status?.current ?? 0
  const total = status?.total ?? 0

  // Check if this is stream-level progress (contains ✓ or ✗)
  const isStreamProgress = itemName && (itemName.includes("✓") || itemName.includes("✗"))

  return (
    <div className="space-y-2 mt-1 w-[356px]">
      {/* Progress bar - fixed width to prevent layout shift */}
      <div className="h-1.5 w-full bg-muted rounded-full overflow-hidden">
        <div
          className="h-full bg-primary transition-all duration-300"
          style={{ width: `${percent}%` }}
        />
      </div>
      {/* Current item - fixed width container, text can wrap */}
      {itemName && (
        <div className="text-xs text-muted-foreground break-words">
          {isStreamProgress ? (
            itemName
          ) : (
            <>{itemName}{total > 0 && ` (${current}/${total})`}</>
          )}
        </div>
      )}
    </div>
  )
}

function getPhaseLabel(status: GenerationStatus | null): string {
  if (!status) return "Starting..."
  switch (status.phase) {
    case "teams":
      return "Processing Teams"
    case "groups":
      return "Processing Event Groups"
    case "saving":
      return "Saving XMLTV"
    case "dispatcharr":
      return "Syncing with Dispatcharr"
    case "lifecycle":
      return "Processing Channels"
    case "reconciliation":
      return "Running Reconciliation"
    case "cleanup":
      return "Cleaning Up"
    case "complete":
      return "Complete"
    default:
      return status.message || "Processing..."
  }
}

export function GenerationProvider({ children }: { children: ReactNode }) {
  const [isGenerating, setIsGenerating] = useState(false)
  const onCompleteRef = useRef<((result: GenerationStatus["result"]) => void) | null>(null)
  const pollIntervalRef = useRef<number | null>(null)
  const backgroundPollRef = useRef<number | null>(null)

  const updateToast = useCallback((status: GenerationStatus | null, isStarting: boolean = false) => {
    const phase = isStarting ? "Starting EPG generation..." : getPhaseLabel(status)
    const percent = status?.percent ?? 0
    const title = isStarting ? phase : `${phase} — ${percent}%`

    // Use standard toast.loading with description containing progress bar
    toast.loading(title, {
      id: TOAST_ID,
      duration: Infinity,
      description: status ? <ProgressDescription status={status} /> : undefined,
    })
  }, [])

  const handleComplete = useCallback((data: GenerationStatus) => {
    setIsGenerating(false)

    // Convert to success or error toast
    if (data.status === "complete") {
      const result = data.result
      toast.success("EPG Generated", {
        id: TOAST_ID,
        description: `${result.programmes_count} programmes in ${result.duration_seconds}s`,
        duration: 5000,
      })
    } else {
      toast.error("Generation Failed", {
        id: TOAST_ID,
        description: data.error || "Unknown error",
        duration: 8000,
      })
    }

    if (data.status === "complete" && onCompleteRef.current) {
      onCompleteRef.current(data.result)
      onCompleteRef.current = null
    }
  }, [])

  const reconnectToGeneration = useCallback(() => {
    // Use polling instead of SSE for reconnection (more reliable)
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current)
    }

    console.log("[EPG] Starting poll for generation status")

    const poll = () => {
      console.log("[EPG] Fetching status...")
      fetch("/api/v1/epg/generate/status")
        .then((res) => {
          console.log("[EPG] Response received:", res.status)
          return res.json()
        })
        .then((data: GenerationStatus) => {
          console.log("[EPG] Poll result:", data.status, data.percent + "%", data.phase)
          updateToast(data)

          if (data.status === "complete" || data.status === "error") {
            console.log("[EPG] Generation finished:", data.status)
            if (pollIntervalRef.current) {
              clearInterval(pollIntervalRef.current)
              pollIntervalRef.current = null
            }
            handleComplete(data)
          }
        })
        .catch((err) => {
          console.error("[EPG] Poll error:", err)
        })
    }

    // Poll immediately and then every 500ms
    poll()
    pollIntervalRef.current = window.setInterval(poll, 500)
  }, [updateToast, handleComplete])

  const startGeneration = useCallback((onComplete?: (result: GenerationStatus["result"]) => void) => {
    if (isGenerating) {
      toast.error("Generation already in progress")
      return
    }

    setIsGenerating(true)
    onCompleteRef.current = onComplete || null

    // Create initial toast
    updateToast(null as unknown as GenerationStatus, true)

    // Trigger generation via SSE endpoint (fire-and-forget, we'll poll for progress)
    // SSE doesn't work reliably through Vite's proxy (buffering issues)
    // so we use polling instead of reading the SSE stream
    fetch("/api/v1/epg/generate/stream").catch((err) => {
      console.error("Generation request failed:", err)
    })

    // Start polling immediately for progress updates
    reconnectToGeneration()
  }, [isGenerating, updateToast, reconnectToGeneration])

  // Check for in-progress generation on mount and periodically
  // This detects scheduled runs that start while the UI is open
  useEffect(() => {
    const checkStatus = () => {
      fetch("/api/v1/epg/generate/status")
        .then((res) => res.json())
        .then((data: GenerationStatus) => {
          if (data.in_progress && !isGenerating) {
            // Generation started (likely scheduled run), connect to it
            setIsGenerating(true)
            reconnectToGeneration()
          }
        })
        .catch(console.error)
    }

    // Check immediately on mount
    checkStatus()

    // Poll every 5 seconds to detect scheduled runs
    backgroundPollRef.current = window.setInterval(checkStatus, 5000)

    return () => {
      if (backgroundPollRef.current) {
        clearInterval(backgroundPollRef.current)
      }
      // Don't clear pollIntervalRef here - it's managed by reconnectToGeneration/handleComplete
    }
  }, [isGenerating, reconnectToGeneration])

  return (
    <GenerationContext.Provider value={{ startGeneration, isGenerating }}>
      {children}
    </GenerationContext.Provider>
  )
}

