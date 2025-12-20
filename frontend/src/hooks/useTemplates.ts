import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  listTemplates,
  getTemplate,
  createTemplate,
  updateTemplate,
  deleteTemplate,
} from "@/api/templates"
import type { TemplateCreate, TemplateUpdate } from "@/api/templates"

export function useTemplates() {
  return useQuery({
    queryKey: ["templates"],
    queryFn: listTemplates,
  })
}

export function useTemplate(templateId: number) {
  return useQuery({
    queryKey: ["template", templateId],
    queryFn: () => getTemplate(templateId),
    enabled: templateId > 0,
  })
}

export function useCreateTemplate() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: TemplateCreate) => createTemplate(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["templates"] })
    },
  })
}

export function useUpdateTemplate() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      templateId,
      data,
    }: {
      templateId: number
      data: TemplateUpdate
    }) => updateTemplate(templateId, data),
    onSuccess: (_, { templateId }) => {
      queryClient.invalidateQueries({ queryKey: ["templates"] })
      queryClient.invalidateQueries({ queryKey: ["template", templateId] })
    },
  })
}

export function useDeleteTemplate() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (templateId: number) => deleteTemplate(templateId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["templates"] })
    },
  })
}
