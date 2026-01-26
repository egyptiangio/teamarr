import { useQuery } from "@tanstack/react-query"
import { X, Download, AlertCircle } from "lucide-react"
import { useState, useEffect } from "react"
import { getUpdateStatus, type UpdateInfo } from "@/api/updates"

/**
 * Update notification banner component
 * 
 * Displays a dismissible banner when updates are available
 * - Checks for updates periodically (every 6 hours)
 * - Shows different styling for stable vs dev builds
 * - Can be dismissed by user (persists dismissal in localStorage)
 */
export function UpdateNotification() {
  const [dismissed, setDismissed] = useState<string | null>(() => {
    // Get last dismissed version from localStorage
    return localStorage.getItem("update-dismissed-version")
  })

  // Query update status every 6 hours
  const { data: updateInfo } = useQuery<UpdateInfo>({
    queryKey: ["update-status"],
    queryFn: () => getUpdateStatus(false),
    refetchInterval: 6 * 60 * 60 * 1000, // 6 hours
    staleTime: 6 * 60 * 60 * 1000,
    retry: 1,
  })

  // Reset dismissed state if a new version is available
  useEffect(() => {
    if (
      updateInfo?.update_available &&
      updateInfo.latest_version &&
      updateInfo.latest_version !== dismissed
    ) {
      // New version available, clear dismissal
      if (dismissed) {
        setDismissed(null)
        localStorage.removeItem("update-dismissed-version")
      }
    }
  }, [updateInfo, dismissed])

  // Don't show if:
  // - No update info
  // - No update available
  // - User dismissed this version
  if (
    !updateInfo ||
    !updateInfo.update_available ||
    !updateInfo.latest_version ||
    dismissed === updateInfo.latest_version
  ) {
    return null
  }

  // Check notification settings only if enabled
  if (updateInfo.settings.enabled) {
    // Don't show dev updates if notify_dev_updates is disabled
    if (
      updateInfo.build_type === "dev" &&
      !updateInfo.settings.notify_dev_updates
    ) {
      return null
    }

    // Don't show stable updates if notify_stable_updates is disabled
    if (
      updateInfo.build_type === "stable" &&
      !updateInfo.settings.notify_stable_updates
    ) {
      return null
    }
  }

  const handleDismiss = () => {
    if (updateInfo.latest_version) {
      setDismissed(updateInfo.latest_version)
      localStorage.setItem("update-dismissed-version", updateInfo.latest_version)
    }
  }

  const isDevBuild = updateInfo.build_type === "dev"
  const bgColor = isDevBuild
    ? "bg-blue-500/10 border-blue-500/30"
    : "bg-green-500/10 border-green-500/30"
  const textColor = isDevBuild
    ? "text-blue-600 dark:text-blue-400"
    : "text-green-600 dark:text-green-400"
  const iconColor = isDevBuild
    ? "text-blue-500"
    : "text-green-500"

  return (
    <div
      className={`border-b border-t ${bgColor} px-4 py-3 sticky top-12 z-40 backdrop-blur-sm`}
    >
      <div className="max-w-[1440px] mx-auto flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <AlertCircle className={`h-5 w-5 ${iconColor} flex-shrink-0`} />
          <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-2">
            <span className={`text-sm font-medium ${textColor}`}>
              {isDevBuild ? "Dev build update available" : "New version available"}
            </span>
            <span className="text-xs text-muted-foreground">
              {updateInfo.current_version} → {updateInfo.latest_version}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {updateInfo.download_url && (
            <a
              href={updateInfo.download_url}
              target="_blank"
              rel="noopener noreferrer"
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${textColor} hover:bg-current/10`}
            >
              <Download className="h-3.5 w-3.5" />
              {isDevBuild ? "Pull Image" : "View Release"}
            </a>
          )}
          <button
            onClick={handleDismiss}
            className="p-1.5 rounded-md hover:bg-current/10 transition-colors text-muted-foreground hover:text-foreground"
            title="Dismiss"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
