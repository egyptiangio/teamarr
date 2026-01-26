/**
 * Updates API client
 */

export interface UpdateInfo {
  current_version: string
  latest_version: string | null
  update_available: boolean
  build_type: "stable" | "dev" | "unknown"
  download_url: string | null
  checked_at: string | null
  settings: UpdateSettings
  latest_stable: string | null
  latest_dev: string | null
}

export interface UpdateSettings {
  enabled: boolean
  notify_stable_updates: boolean
  notify_dev_updates: boolean
  github_owner: string
  github_repo: string
  dev_branch: string
  auto_detect_dev_branch: boolean
}

export interface UpdateSettingsRequest {
  enabled?: boolean
  notify_stable_updates?: boolean
  notify_dev_updates?: boolean
  github_owner?: string
  github_repo?: string
  dev_branch?: string
  auto_detect_dev_branch?: boolean
}

/**
 * Get current update status
 */
export async function getUpdateStatus(force = false): Promise<UpdateInfo> {
  const url = force
    ? "/api/v1/updates/status?force=true"
    : "/api/v1/updates/status"
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`Failed to fetch update status: ${response.statusText}`)
  }
  return response.json()
}

/**
 * Update settings
 */
export async function updateSettings(
  settings: UpdateSettingsRequest
): Promise<{ success: boolean; message: string }> {
  const response = await fetch("/api/v1/updates/settings", {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(settings),
  })
  if (!response.ok) {
    throw new Error(`Failed to update settings: ${response.statusText}`)
  }
  return response.json()
}
