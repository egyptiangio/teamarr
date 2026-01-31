/**
 * V1 to V2 Upgrade Landing Page
 *
 * Shown when a V1 database is detected. Offers users the choice to:
 * 1. Archive their V1 database and start fresh with V2
 * 2. Go back to V1 using the archived Docker tag
 */

import { useState, useEffect, useCallback } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  Archive,
  ArrowLeft,
  CheckCircle2,
  Download,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"

interface MigrationStatus {
  is_v1_database: boolean
  has_archived_backup: boolean
  database_path: string
  backup_path: string | null
}

interface ArchiveResult {
  success: boolean
  message: string
  backup_path: string | null
}

async function fetchMigrationStatus(): Promise<MigrationStatus> {
  const response = await fetch("/api/v1/migration/status")
  if (!response.ok) throw new Error("Failed to fetch migration status")
  return response.json()
}

async function archiveDatabase(): Promise<ArchiveResult> {
  const response = await fetch("/api/v1/migration/archive", {
    method: "POST",
  })
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || "Failed to archive database")
  }
  return response.json()
}

async function triggerRestart(): Promise<{ success: boolean }> {
  const response = await fetch("/api/v1/migration/restart", {
    method: "POST",
  })
  if (!response.ok) {
    throw new Error("Failed to trigger restart")
  }
  return response.json()
}

async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch("/health", {
      method: "GET",
      // Short timeout for polling
      signal: AbortSignal.timeout(2000)
    })
    if (!response.ok) return false
    const data = await response.json()
    // Check if backend is fully ready (not in migration mode)
    return data.status === "healthy"
  } catch {
    return false
  }
}

export function V1UpgradePage() {
  const queryClient = useQueryClient()
  const [justArchived, setJustArchived] = useState(false)
  const [isRestarting, setIsRestarting] = useState(false)
  const [restartProgress, setRestartProgress] = useState(0)
  const [restartError, setRestartError] = useState<string | null>(null)

  // Check if backup already exists (e.g., page was refreshed after archive)
  const { data: status } = useQuery({
    queryKey: ["migration-status"],
    queryFn: fetchMigrationStatus,
    enabled: !isRestarting,
  })

  // Show success state if already archived OR just archived
  const showSuccessState = justArchived || (status?.has_archived_backup && !status?.is_v1_database)

  const archiveMutation = useMutation({
    mutationFn: archiveDatabase,
    onSuccess: () => {
      setJustArchived(true)
      queryClient.invalidateQueries({ queryKey: ["migration-status"] })
    },
  })

  const handleDownloadBackup = () => {
    window.open("/api/v1/migration/download-backup", "_blank")
  }

  // Poll for backend health after restart
  const pollForReady = useCallback(async () => {
    const maxAttempts = 60 // 60 seconds max
    let attempts = 0

    // Wait a moment for shutdown
    await new Promise(resolve => setTimeout(resolve, 1500))

    while (attempts < maxAttempts) {
      attempts++
      setRestartProgress(Math.min(95, (attempts / maxAttempts) * 100))

      const isReady = await checkHealth()
      if (isReady) {
        setRestartProgress(100)
        // Small delay to show 100% then redirect
        await new Promise(resolve => setTimeout(resolve, 500))
        window.location.href = "/"
        return
      }

      // Wait 1 second between polls
      await new Promise(resolve => setTimeout(resolve, 1000))
    }

    // Timeout - show manual restart instructions
    setRestartError("Backend did not restart automatically. Please restart your container manually.")
    setIsRestarting(false)
  }, [])

  const handleProceedToV2 = async () => {
    setIsRestarting(true)
    setRestartProgress(0)
    setRestartError(null)

    try {
      // Trigger backend restart
      await triggerRestart()
      // Start polling for readiness
      pollForReady()
    } catch (error) {
      console.error(error)
      setRestartError("Failed to trigger restart. Please restart your container manually.")
      setIsRestarting(false)
    }
  }

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      // Nothing to cleanup
    }
  }, [])

  // Show restarting state
  if (isRestarting) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center p-4">
        <div className="max-w-md w-full space-y-6">
          <div className="text-center space-y-4">
            <div className="flex justify-center">
              <div className="h-16 w-16 rounded-full bg-primary/10 flex items-center justify-center">
                <RefreshCw className="h-8 w-8 text-primary animate-spin" />
              </div>
            </div>
            <h1 className="text-2xl font-bold">Restarting Teamarr</h1>
            <p className="text-muted-foreground">
              Initializing V2 database and services...
            </p>
          </div>

          {/* Progress bar */}
          <div className="space-y-2">
            <div className="h-2 bg-secondary rounded-full overflow-hidden">
              <div
                className="h-full bg-primary transition-all duration-300 ease-out"
                style={{ width: `${restartProgress}%` }}
              />
            </div>
            <p className="text-center text-sm text-muted-foreground">
              {restartProgress < 10 ? "Shutting down..." :
               restartProgress < 30 ? "Waiting for restart..." :
               restartProgress < 80 ? "Initializing V2..." :
               restartProgress < 100 ? "Almost ready..." :
               "Ready!"}
            </p>
          </div>

          {restartError && (
            <Card className="border-warning/50 bg-warning/5">
              <CardContent className="p-4">
                <div className="space-y-3">
                  <p className="text-sm text-warning">{restartError}</p>
                  <div className="bg-secondary/50 rounded-md p-3 font-mono text-xs">
                    docker compose restart teamarr
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => window.location.reload()}
                    className="w-full"
                  >
                    <RefreshCw className="h-4 w-4" />
                    Refresh Page
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <div className="max-w-2xl w-full space-y-6">
        {/* Header */}
        <div className="text-center space-y-3">
          <div className="flex justify-center">
            <div className="h-16 w-16 rounded-full bg-primary/10 flex items-center justify-center">
              <Sparkles className="h-8 w-8 text-primary" />
            </div>
          </div>
          <h1 className="text-3xl font-bold">Welcome to Teamarr V2</h1>
          <p className="text-muted-foreground text-lg">
            We're excited you're upgrading to the next generation of Teamarr!
          </p>
        </div>

        {/* Notice Card */}
        <Card className="border-warning/50 bg-warning/5">
          <CardContent className="p-5">
            <div className="flex gap-4">
              <AlertTriangle className="h-6 w-6 text-warning shrink-0 mt-0.5" />
              <div className="space-y-2">
                <h2 className="font-semibold text-lg">V1 Database Detected</h2>
                <p className="text-muted-foreground text-sm leading-relaxed">
                  We've detected that your data directory contains a Teamarr V1
                  database. Unfortunately, <strong>there is no automatic migration
                  path</strong> from V1 to V2 due to significant architectural changes.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        {!showSuccessState ? (
          <>
            {/* Options */}
            <div className="space-y-3">
              <h3 className="font-medium text-center text-muted-foreground">
                Choose how you'd like to proceed:
              </h3>

              {/* Option 1: Start Fresh */}
              <Card className="hover:border-primary/50 transition-colors">
                <CardContent className="p-5">
                  <div className="flex gap-4">
                    <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                      <Archive className="h-5 w-5 text-primary" />
                    </div>
                    <div className="flex-1 space-y-3">
                      <div>
                        <h3 className="font-semibold">Start Fresh with V2</h3>
                        <p className="text-sm text-muted-foreground mt-1">
                          Archive your V1 database and begin with a clean V2 setup.
                          Your V1 data will be safely backed up and available for
                          download.
                        </p>
                      </div>
                      <ul className="text-xs text-muted-foreground space-y-1">
                        <li className="flex items-center gap-2">
                          <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                          V1 database preserved as downloadable backup
                        </li>
                        <li className="flex items-center gap-2">
                          <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                          Access all new V2 features and improvements
                        </li>
                        <li className="flex items-center gap-2">
                          <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                          Reconfigure teams and templates from scratch
                        </li>
                      </ul>
                      <Button
                        onClick={() => archiveMutation.mutate()}
                        disabled={archiveMutation.isPending}
                        className="w-full"
                      >
                        {archiveMutation.isPending ? (
                          <>
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Archiving...
                          </>
                        ) : (
                          <>
                            <Archive className="h-4 w-4" />
                            Archive V1 & Start Fresh
                          </>
                        )}
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Option 2: Go Back to V1 */}
              <Card className="hover:border-secondary transition-colors">
                <CardContent className="p-5">
                  <div className="flex gap-4">
                    <div className="h-10 w-10 rounded-lg bg-secondary flex items-center justify-center shrink-0">
                      <ArrowLeft className="h-5 w-5 text-muted-foreground" />
                    </div>
                    <div className="flex-1 space-y-3">
                      <div>
                        <h3 className="font-semibold">Continue Using V1</h3>
                        <p className="text-sm text-muted-foreground mt-1">
                          If you're not ready to migrate, you can continue using
                          V1. Update your Docker compose file to use the archived
                          version tag.
                        </p>
                      </div>
                      <div className="bg-secondary/50 rounded-md p-3 font-mono text-xs">
                        <span className="text-muted-foreground">image:</span>{" "}
                        <span className="text-primary">ghcr.io/egyptiangio/teamarr:1.4.9-archive</span>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Note: V1 will continue to function but will not receive
                        any future updates or bug fixes.
                      </p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>
          </>
        ) : (
          /* Success State - Two clear options */
          <>
            <Card className="border-success/50 bg-success/5">
              <CardContent className="p-5">
                <div className="flex gap-4 items-center">
                  <div className="h-10 w-10 rounded-full bg-success/10 flex items-center justify-center shrink-0">
                    <CheckCircle2 className="h-5 w-5 text-success" />
                  </div>
                  <div>
                    <h2 className="font-semibold">Database Archived Successfully</h2>
                    <p className="text-sm text-muted-foreground">
                      Your V1 database has been safely backed up.
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>

            <div className="space-y-3">
              <h3 className="font-medium text-center text-muted-foreground">
                What would you like to do?
              </h3>

              {/* Option 1: Download Backup */}
              <Card className="hover:border-primary/50 transition-colors">
                <CardContent className="p-5">
                  <div className="flex gap-4">
                    <div className="h-10 w-10 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                      <Download className="h-5 w-5 text-primary" />
                    </div>
                    <div className="flex-1 space-y-3">
                      <div>
                        <h3 className="font-semibold">Download V1 Backup</h3>
                        <p className="text-sm text-muted-foreground mt-1">
                          Save a copy of your V1 database for your records. You can
                          use this to restore V1 if needed, or reference your old
                          configuration.
                        </p>
                      </div>
                      <Button variant="outline" onClick={handleDownloadBackup} className="w-full">
                        <Download className="h-4 w-4" />
                        Download Backup
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Option 2: Proceed to V2 */}
              <Card className="hover:border-success/50 transition-colors">
                <CardContent className="p-5">
                  <div className="flex gap-4">
                    <div className="h-10 w-10 rounded-lg bg-success/10 flex items-center justify-center shrink-0">
                      <Sparkles className="h-5 w-5 text-success" />
                    </div>
                    <div className="flex-1 space-y-3">
                      <div>
                        <h3 className="font-semibold">Launch Teamarr V2</h3>
                        <p className="text-sm text-muted-foreground mt-1">
                          Start fresh with Teamarr V2. The application will restart
                          automatically to initialize your new database.
                        </p>
                      </div>
                      <Button onClick={handleProceedToV2} className="w-full" variant="success">
                        <Sparkles className="h-4 w-4" />
                        Launch Teamarr V2
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>
          </>
        )}

        {/* Error State */}
        {archiveMutation.isError && (
          <Card className="border-destructive/50 bg-destructive/5">
            <CardContent className="p-4">
              <p className="text-sm text-destructive">
                {archiveMutation.error instanceof Error
                  ? archiveMutation.error.message
                  : "An error occurred while archiving the database"}
              </p>
            </CardContent>
          </Card>
        )}

        {/* Footer */}
        <p className="text-center text-xs text-muted-foreground">
          Questions or issues? Visit our{" "}
          <a
            href="https://github.com/egyptiangio/teamarr"
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            GitHub repository
          </a>{" "}
          for documentation and support.
        </p>
      </div>
    </div>
  )
}
