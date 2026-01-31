import { createContext, useContext } from "react"

export interface GenerationStatus {
  in_progress: boolean
  status: string
  message: string
  percent: number
  phase: string
  current: number
  total: number
  item_name: string
  started_at: string | null
  completed_at: string | null
  error: string | null
  result: {
    success?: boolean
    programmes_count?: number
    teams_processed?: number
    groups_processed?: number
    duration_seconds?: number
    run_id?: number
  }
}

export interface GenerationContextValue {
  startGeneration: (onComplete?: (result: GenerationStatus["result"]) => void) => void
  isGenerating: boolean
}

export const GenerationContext = createContext<GenerationContextValue | null>(null)

export function useGenerationProgress() {
  const context = useContext(GenerationContext)
  if (!context) {
    throw new Error("useGenerationProgress must be used within a GenerationProvider")
  }
  return context
}
