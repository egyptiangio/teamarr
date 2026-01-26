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
  getEPGSettings,
  updateEPGSettings,
  getDurationSettings,
  updateDurationSettings,
  getReconciliationSettings,
  updateReconciliationSettings,
  getDisplaySettings,
  updateDisplaySettings,
  getTeamFilterSettings,
  updateTeamFilterSettings,
  getExceptionKeywords,
  createExceptionKeyword,
  updateExceptionKeyword,
  deleteExceptionKeyword,
  getChannelNumberingSettings,
  updateChannelNumberingSettings,
  getStreamOrderingSettings,
  updateStreamOrderingSettings,
} from "@/api/settings"
import type {
  DispatcharrSettings,
  LifecycleSettings,
  SchedulerSettings,
  EPGSettings,
  DurationSettings,
  ReconciliationSettings,
  DisplaySettings,
  TeamFilterSettingsUpdate,
  ChannelNumberingSettingsUpdate,
  StreamOrderingSettingsUpdate,
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

export function useTeamFilterSettings() {
  return useQuery({
    queryKey: ["settings", "team-filter"],
    queryFn: getTeamFilterSettings,
  })
}

export function useUpdateTeamFilterSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: TeamFilterSettingsUpdate) => updateTeamFilterSettings(data),
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
    mutationFn: (data: { label: string; match_terms: string; behavior: string; enabled?: boolean }) =>
      createExceptionKeyword(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["keywords"] })
    },
  })
}

export function useUpdateExceptionKeyword() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<{ label: string; match_terms: string; behavior: string; enabled: boolean }> }) =>
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

export function useChannelNumberingSettings() {
  return useQuery({
    queryKey: ["settings", "channel-numbering"],
    queryFn: getChannelNumberingSettings,
  })
}

export function useUpdateChannelNumberingSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: ChannelNumberingSettingsUpdate) =>
      updateChannelNumberingSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] })
    },
  })
}

export function useStreamOrderingSettings() {
  return useQuery({
    queryKey: ["settings", "stream-ordering"],
    queryFn: getStreamOrderingSettings,
  })
}

export function useUpdateStreamOrderingSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: StreamOrderingSettingsUpdate) =>
      updateStreamOrderingSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] })
    },
  })
}

export function useUpdateCheckStatus() {
  return useQuery({
    queryKey: ["update-status"],
    queryFn: async () => {
      const response = await fetch("/api/v1/updates/status")
      if (!response.ok) throw new Error("Failed to fetch update status")
      return response.json()
    },
    refetchInterval: 6 * 60 * 60 * 1000, // 6 hours
    staleTime: 6 * 60 * 60 * 1000,
  })
}

export function useUpdateCheckSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (data: {
      enabled?: boolean
      check_interval_hours?: number
      notify_stable_updates?: boolean
      notify_dev_updates?: boolean
      github_owner?: string
      github_repo?: string
      ghcr_owner?: string
      ghcr_image?: string
      dev_tag?: string
    }) => {
      const response = await fetch("/api/v1/updates/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      })
      if (!response.ok) throw new Error("Failed to update settings")
      return response.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["update-status"] })
    },
  })
}

