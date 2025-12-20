import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  listManagedChannels,
  getManagedChannel,
  deleteManagedChannel,
  syncLifecycle,
  getReconciliationStatus,
  runReconciliation,
  getPendingDeletions,
} from "@/api/channels"

export function useManagedChannels(groupId?: number, includeDeleted = false) {
  return useQuery({
    queryKey: ["managedChannels", { groupId, includeDeleted }],
    queryFn: () => listManagedChannels(groupId, includeDeleted),
    refetchInterval: 30000, // Refresh every 30s
  })
}

export function useManagedChannel(channelId: number) {
  return useQuery({
    queryKey: ["managedChannel", channelId],
    queryFn: () => getManagedChannel(channelId),
    enabled: channelId > 0,
  })
}

export function useDeleteManagedChannel() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (channelId: number) => deleteManagedChannel(channelId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["managedChannels"] })
    },
  })
}

export function useSyncLifecycle() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: syncLifecycle,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["managedChannels"] })
    },
  })
}

export function useReconciliationStatus(groupIds?: number[]) {
  return useQuery({
    queryKey: ["reconciliationStatus", groupIds],
    queryFn: () => getReconciliationStatus(groupIds),
  })
}

export function useRunReconciliation() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ autoFix, groupIds }: { autoFix: boolean; groupIds?: number[] }) =>
      runReconciliation(autoFix, groupIds),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["managedChannels"] })
      queryClient.invalidateQueries({ queryKey: ["reconciliationStatus"] })
    },
  })
}

export function usePendingDeletions() {
  return useQuery({
    queryKey: ["pendingDeletions"],
    queryFn: getPendingDeletions,
    refetchInterval: 60000, // Check every minute
  })
}
