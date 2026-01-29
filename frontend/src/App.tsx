import { BrowserRouter, Routes, Route } from "react-router-dom"
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query"
import { MainLayout } from "@/layouts/MainLayout"
import { GenerationProvider } from "@/contexts/GenerationContext"
import { StartupOverlay } from "@/components/StartupOverlay"
import {
  AI,
  Dashboard,
  Templates,
  TemplateForm,
  Teams,
  TeamImport,
  EventGroups,
  EventGroupForm,
  EventGroupImport,
  EPG,
  Channels,
  Settings,
  V1UpgradePage,
} from "@/pages"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60, // 1 minute
      retry: 1,
    },
  },
})

interface MigrationStatus {
  is_v1_database: boolean
  has_archived_backup: boolean
  database_path: string
  backup_path: string | null
}

async function fetchMigrationStatus(): Promise<MigrationStatus> {
  const response = await fetch("/api/v1/migration/status")
  if (!response.ok) {
    throw new Error("Failed to fetch migration status")
  }
  return response.json()
}

function AppContent() {
  const { data: migrationStatus, isLoading, isFetching } = useQuery({
    queryKey: ["migration-status"],
    queryFn: fetchMigrationStatus,
    retry: 3,
    retryDelay: 1000,
    staleTime: Infinity, // Only check once per session
  })

  // Check if migration mode is indicated
  const isMigrationMode = migrationStatus?.is_v1_database || migrationStatus?.has_archived_backup

  // Show loading while:
  // 1. Initial load (isLoading)
  // 2. Refetching AND cached data says migration mode (don't trust stale migration data)
  if (isLoading || (isFetching && isMigrationMode)) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="text-center space-y-4">
          <h1 className="text-2xl font-bold">Teamarr</h1>
          <p className="text-sm text-muted-foreground">Checking database...</p>
        </div>
      </div>
    )
  }

  // Show V1 upgrade page if V1 database detected OR has archived backup (so user can download)
  // This check happens BEFORE StartupOverlay to avoid V2 initialization errors
  if (isMigrationMode) {
    return <V1UpgradePage />
  }

  return (
    <>
      <StartupOverlay />
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<MainLayout />}>
            <Route index element={<Dashboard />} />
            <Route path="templates" element={<Templates />} />
            <Route path="templates/new" element={<TemplateForm />} />
            <Route path="templates/:templateId" element={<TemplateForm />} />
            <Route path="teams" element={<Teams />} />
            <Route path="teams/import" element={<TeamImport />} />
            <Route path="event-groups" element={<EventGroups />} />
            <Route path="event-groups/new" element={<EventGroupForm />} />
            <Route path="event-groups/:groupId" element={<EventGroupForm />} />
            <Route path="event-groups/import" element={<EventGroupImport />} />
            <Route path="epg" element={<EPG />} />
            <Route path="channels" element={<Channels />} />
            <Route path="ai" element={<AI />} />
            <Route path="settings" element={<Settings />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </>
  )
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <GenerationProvider>
        <AppContent />
      </GenerationProvider>
    </QueryClientProvider>
  )
}

export default App
