import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  fetchPresets,
  getPreset,
  createPreset,
  updatePreset,
  deletePreset,
} from "@/api/presets"
import type { ConditionPresetCreate } from "@/api/presets"

export function usePresets() {
  return useQuery({
    queryKey: ["presets"],
    queryFn: fetchPresets,
    select: (data) => data.presets,
  })
}

export function usePreset(presetId: number) {
  return useQuery({
    queryKey: ["preset", presetId],
    queryFn: () => getPreset(presetId),
    enabled: presetId > 0,
  })
}

export function useCreatePreset() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: ConditionPresetCreate) => createPreset(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["presets"] })
    },
  })
}

export function useUpdatePreset() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      presetId,
      data,
    }: {
      presetId: number
      data: Partial<ConditionPresetCreate>
    }) => updatePreset(presetId, data),
    onSuccess: (_, { presetId }) => {
      queryClient.invalidateQueries({ queryKey: ["presets"] })
      queryClient.invalidateQueries({ queryKey: ["preset", presetId] })
    },
  })
}

export function useDeletePreset() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (presetId: number) => deletePreset(presetId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["presets"] })
    },
  })
}
