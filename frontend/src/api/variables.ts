import { api } from "./client"

export interface Variable {
  name: string
  description: string
  suffixes: string[]
}

export interface VariableCategory {
  name: string
  variables: Variable[]
}

export interface VariablesResponse {
  total: number
  categories: VariableCategory[]
  available_sports: string[]
}

export interface SamplesResponse {
  sport: string
  available_sports: string[]
  samples: Record<string, string>
}

export interface Condition {
  name: string
  description: string
  requires_value: boolean
  value_type?: "number" | "string"
}

export interface ConditionsResponse {
  conditions: Condition[]
}

export async function fetchVariables(): Promise<VariablesResponse> {
  return api.get("/variables")
}

export async function fetchConditions(templateType: string = "team"): Promise<ConditionsResponse> {
  return api.get(`/variables/conditions?template_type=${encodeURIComponent(templateType)}`)
}

export async function fetchSamples(sport: string = "NBA"): Promise<SamplesResponse> {
  return api.get(`/variables/samples?sport=${encodeURIComponent(sport)}`)
}
