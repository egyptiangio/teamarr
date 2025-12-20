import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  getSettings,
  getDispatcharrSettings,
  updateDispatcharrSettings,
  testDispatcharrConnection,
  getDispatcharrStatus,
  getDispatcharrEPGSources,
  getLifecycleSettings,
  updateLifecycleSettings,
  getSchedulerSettings,
  updateSchedulerSettings,
  getSchedulerStatus,
  triggerSchedulerRun,
  getEPGSettings,
  updateEPGSettings,
  getDurationSettings,
  updateDurationSettings,
  getReconciliationSettings,
  updateReconciliationSettings,
  getDisplaySettings,
  updateDisplaySettings,
  getExceptionKeywords,
  createExceptionKeyword,
  updateExceptionKeyword,
  deleteExceptionKeyword,
} from "@/api/settings"
import type {
  DispatcharrSettings,
  LifecycleSettings,
  SchedulerSettings,
  EPGSettings,
  DurationSettings,
  ReconciliationSettings,
  DisplaySettings,
} from "@/api/settings"

export function useSettings() {
  return useQuery({
    queryKey: ["settings"],
    queryFn: getSettings,
  })
}

export function useDispatcharrSettings() {
  return useQuery({
    queryKey: ["settings", "dispatcharr"],
    queryFn: getDispatcharrSettings,
  })
}

export function useUpdateDispatcharrSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: Partial<DispatcharrSettings>) =>
      updateDispatcharrSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] })
    },
  })
}

export function useTestDispatcharrConnection() {
  return useMutation({
    mutationFn: (data?: { url?: string; username?: string; password?: string }) =>
      testDispatcharrConnection(data),
  })
}

export function useDispatcharrStatus() {
  return useQuery({
    queryKey: ["dispatcharr", "status"],
    queryFn: getDispatcharrStatus,
    refetchInterval: 30000, // Refresh every 30 seconds
  })
}

export function useDispatcharrEPGSources(enabled: boolean = true) {
  return useQuery({
    queryKey: ["dispatcharr", "epg-sources"],
    queryFn: getDispatcharrEPGSources,
    enabled, // Only fetch when Dispatcharr is configured
    staleTime: 60000, // 1 minute
  })
}

export function useLifecycleSettings() {
  return useQuery({
    queryKey: ["settings", "lifecycle"],
    queryFn: getLifecycleSettings,
  })
}

export function useUpdateLifecycleSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: LifecycleSettings) => updateLifecycleSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] })
    },
  })
}

export function useSchedulerSettings() {
  return useQuery({
    queryKey: ["settings", "scheduler"],
    queryFn: getSchedulerSettings,
  })
}

export function useUpdateSchedulerSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: SchedulerSettings) => updateSchedulerSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] })
      queryClient.invalidateQueries({ queryKey: ["scheduler"] })
    },
  })
}

export function useSchedulerStatus() {
  return useQuery({
    queryKey: ["scheduler", "status"],
    queryFn: getSchedulerStatus,
    refetchInterval: 10000, // Refresh every 10 seconds
  })
}

export function useTriggerSchedulerRun() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: triggerSchedulerRun,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["scheduler"] })
      queryClient.invalidateQueries({ queryKey: ["groups"] })
    },
  })
}

export function useEPGSettings() {
  return useQuery({
    queryKey: ["settings", "epg"],
    queryFn: getEPGSettings,
  })
}

export function useUpdateEPGSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: EPGSettings) => updateEPGSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] })
    },
  })
}

export function useDurationSettings() {
  return useQuery({
    queryKey: ["settings", "durations"],
    queryFn: getDurationSettings,
  })
}

export function useUpdateDurationSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: DurationSettings) => updateDurationSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] })
    },
  })
}

export function useReconciliationSettings() {
  return useQuery({
    queryKey: ["settings", "reconciliation"],
    queryFn: getReconciliationSettings,
  })
}

export function useUpdateReconciliationSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: ReconciliationSettings) =>
      updateReconciliationSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] })
    },
  })
}

export function useDisplaySettings() {
  return useQuery({
    queryKey: ["settings", "display"],
    queryFn: getDisplaySettings,
  })
}

export function useUpdateDisplaySettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: DisplaySettings) => updateDisplaySettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] })
    },
  })
}

export function useExceptionKeywords(includeDisabled: boolean = false) {
  return useQuery({
    queryKey: ["keywords", { includeDisabled }],
    queryFn: () => getExceptionKeywords(includeDisabled),
  })
}

export function useCreateExceptionKeyword() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: { keywords: string; behavior: string; display_name?: string; enabled?: boolean }) =>
      createExceptionKeyword(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["keywords"] })
    },
  })
}

export function useUpdateExceptionKeyword() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<{ keywords: string; behavior: string; display_name: string | null; enabled: boolean }> }) =>
      updateExceptionKeyword(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["keywords"] })
    },
  })
}

export function useDeleteExceptionKeyword() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (id: number) => deleteExceptionKeyword(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["keywords"] })
    },
  })
}
