import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  listTeams,
  getTeam,
  createTeam,
  updateTeam,
  deleteTeam,
  searchTeams,
} from "@/api/teams"
import type { TeamCreate, TeamUpdate } from "@/api/teams"

export function useTeams(activeOnly = false) {
  return useQuery({
    queryKey: ["teams", { activeOnly }],
    queryFn: () => listTeams(activeOnly),
  })
}

export function useTeam(teamId: number) {
  return useQuery({
    queryKey: ["team", teamId],
    queryFn: () => getTeam(teamId),
    enabled: teamId > 0,
  })
}

export function useCreateTeam() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: TeamCreate) => createTeam(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["teams"] })
    },
  })
}

export function useUpdateTeam() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ teamId, data }: { teamId: number; data: TeamUpdate }) =>
      updateTeam(teamId, data),
    onSuccess: (_, { teamId }) => {
      queryClient.invalidateQueries({ queryKey: ["teams"] })
      queryClient.invalidateQueries({ queryKey: ["team", teamId] })
    },
  })
}

export function useDeleteTeam() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (teamId: number) => deleteTeam(teamId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["teams"] })
    },
  })
}

export function useTeamSearch(query: string, league?: string, sport?: string) {
  return useQuery({
    queryKey: ["teamSearch", query, league, sport],
    queryFn: () => searchTeams(query, league, sport),
    enabled: query.length >= 2,
  })
}
