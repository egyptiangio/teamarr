import { lazy, Suspense } from "react"
import { BrowserRouter, Routes, Route, Navigate, useParams, useLocation } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { LoaderCircle } from "lucide-react"
import { MainLayout } from "@/layouts/MainLayout"
import { EpgLayout } from "@/components/EpgLayout"
import { ChannelsLayout } from "@/components/ChannelsLayout"
import { GenerationProvider } from "@/contexts/GenerationContext"
import { StartupOverlay } from "@/components/StartupOverlay"
import { Dashboard } from "@/pages/Dashboard"

/**
 * Every page below the landing route is code-split (#737).
 *
 * All 19 pages used to be imported statically through the `@/pages` barrel, so
 * the build emitted one 1.03 MB bundle and every visitor downloaded, parsed and
 * compiled the whole app to look at the dashboard.
 *
 * Two rules keep it that way:
 *   - Import the page MODULE, never the `@/pages` barrel. Going through the
 *     barrel pulls every page into the same chunk and quietly undoes this.
 *   - Dashboard stays eager. It is the landing route, so lazy-loading it just
 *     adds a round trip to the most common entry point.
 *
 * The pages use named exports, hence the `.then` unwrap.
 */
const Subscriptions = lazy(() =>
  import("@/pages/Subscriptions").then((m) => ({ default: m.Subscriptions })),
)
const DetectionLibrary = lazy(() =>
  import("@/pages/DetectionLibrary").then((m) => ({ default: m.DetectionLibrary })),
)
const Templates = lazy(() =>
  import("@/pages/Templates").then((m) => ({ default: m.Templates })),
)
const TemplateForm = lazy(() =>
  import("@/pages/TemplateForm").then((m) => ({ default: m.TemplateForm })),
)
const EpgOutput = lazy(() =>
  import("@/pages/EpgOutput").then((m) => ({ default: m.EpgOutput })),
)
const Teams = lazy(() => import("@/pages/Teams").then((m) => ({ default: m.Teams })))
const TeamImport = lazy(() =>
  import("@/pages/TeamImport").then((m) => ({ default: m.TeamImport })),
)
const EventGroups = lazy(() =>
  import("@/pages/EventGroups").then((m) => ({ default: m.EventGroups })),
)
const EventGroupForm = lazy(() =>
  import("@/pages/EventGroupForm").then((m) => ({ default: m.EventGroupForm })),
)
const EventGroupImport = lazy(() =>
  import("@/pages/EventGroupImport").then((m) => ({ default: m.EventGroupImport })),
)
const Settings = lazy(() => import("@/pages/Settings").then((m) => ({ default: m.Settings })))
const ChannelLifecycle = lazy(() =>
  import("@/pages/channels/ChannelLifecycle").then((m) => ({ default: m.ChannelLifecycle })),
)
const ChannelConsolidation = lazy(() =>
  import("@/pages/channels/ChannelConsolidation").then((m) => ({
    default: m.ChannelConsolidation,
  })),
)
const ChannelNumbering = lazy(() =>
  import("@/pages/channels/ChannelNumbering").then((m) => ({ default: m.ChannelNumbering })),
)
const ChannelStreamPriority = lazy(() =>
  import("@/pages/channels/ChannelStreamPriority").then((m) => ({
    default: m.ChannelStreamPriority,
  })),
)
const ChannelDispatcharrOutput = lazy(() =>
  import("@/pages/channels/ChannelDispatcharrOutput").then((m) => ({
    default: m.ChannelDispatcharrOutput,
  })),
)

/**
 * Shown while a route chunk downloads.
 *
 * Deliberately plain: chunks are served from the same origin and are small, so
 * on anything but a cold first visit this is a flash. A skeleton that mimicked
 * page structure would be more distracting than a spinner, not less.
 */
function RouteFallback() {
  return (
    <div className="flex items-center justify-center py-24" role="status" aria-label="Loading">
      <LoaderCircle className="h-6 w-6 animate-spin text-muted-foreground" />
    </div>
  )
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60, // 1 minute
      retry: 1,
    },
  },
})

/**
 * Redirect that forwards :params and the query string from the matched
 * (legacy) URL to its new home, so bookmarks and in-app navigate() calls to
 * old paths keep working after the v2.7.0 IA route rename.
 */
function Redirect({ to }: { to: string }) {
  const params = useParams()
  const { search } = useLocation()
  let path = to
  for (const [key, value] of Object.entries(params)) {
    path = path.replace(`:${key}`, value ?? "")
  }
  return <Navigate to={path + search} replace />
}

function AppContent() {
  return (
    <>
      <StartupOverlay />
      <BrowserRouter>
        <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/" element={<MainLayout />}>
            <Route index element={<Dashboard />} />

            {/* ① Sources (was Event Groups) */}
            <Route path="sources" element={<EventGroups />} />
            <Route path="sources/new" element={<EventGroupForm />} />
            <Route path="sources/:groupId" element={<EventGroupForm />} />
            <Route path="sources/import" element={<EventGroupImport />} />

            {/* ② Subscriptions (Global Defaults + Custom Leagues) */}
            <Route path="subscriptions" element={<Subscriptions />} />
            <Route path="subscriptions/leagues" element={<Redirect to="/subscriptions" />} />

            {/* ③ Matching (was Detection Library) */}
            <Route path="matching" element={<DetectionLibrary />} />

            {/* ④ EPG — Templates (default, with assignments folded in) + Team EPG + EPG Output */}
            <Route path="epg" element={<Redirect to="/epg/templates" />} />
            {/* Editor pages are standalone full-screen (no EPG header/SubNav) */}
            <Route path="epg/templates/new" element={<TemplateForm />} />
            <Route path="epg/templates/:templateId" element={<TemplateForm />} />
            <Route path="epg/teams/import" element={<TeamImport />} />
            {/* SubNav views share the EPG layout (fixed "EPG" header + SubNav) */}
            <Route element={<EpgLayout />}>
              <Route path="epg/templates" element={<Templates />} />
              <Route path="epg/assignments" element={<Redirect to="/epg/templates" />} />
              <Route path="epg/teams" element={<Teams />} />
              <Route path="epg/output" element={<EpgOutput />} />
            </Route>

            {/* ⑤ Channels — Lifecycle + Consolidation + Numbering + Stream Priority + Dispatcharr Output */}
            <Route path="channels" element={<Redirect to="/channels/lifecycle" />} />
            <Route element={<ChannelsLayout />}>
              <Route path="channels/lifecycle" element={<ChannelLifecycle />} />
              <Route path="channels/consolidation" element={<ChannelConsolidation />} />
              <Route path="channels/numbering" element={<ChannelNumbering />} />
              <Route path="channels/stream-priority" element={<ChannelStreamPriority />} />
              <Route path="channels/output" element={<ChannelDispatcharrOutput />} />
            </Route>

            {/* Settings (system/integration) */}
            <Route path="settings" element={<Settings />} />

            {/* Legacy URL redirects — keep bookmarks & in-app links working */}
            <Route path="event-groups" element={<Redirect to="/sources" />} />
            <Route path="event-groups/new" element={<Redirect to="/sources/new" />} />
            <Route path="event-groups/:groupId" element={<Redirect to="/sources/:groupId" />} />
            <Route path="event-groups/import" element={<Redirect to="/sources/import" />} />
            <Route path="teams" element={<Redirect to="/epg/teams" />} />
            <Route path="teams/import" element={<Redirect to="/epg/teams/import" />} />
            <Route path="custom-leagues" element={<Redirect to="/subscriptions/leagues" />} />
            <Route path="detection-library" element={<Redirect to="/matching" />} />
            <Route path="templates" element={<Redirect to="/epg/templates" />} />
            <Route path="templates/new" element={<Redirect to="/epg/templates/new" />} />
            <Route path="templates/:templateId" element={<Redirect to="/epg/templates/:templateId" />} />
          </Route>
        </Routes>
        </Suspense>
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
