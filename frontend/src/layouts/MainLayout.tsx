import { Link, NavLink, Outlet } from "react-router-dom"
import { Moon, Sun } from "lucide-react"
import { useEffect, useState } from "react"
import { Toaster } from "sonner"
import { useQuery } from "@tanstack/react-query"
import { useUpdateCheckSettings, useCheckForUpdates } from "../hooks/useSettings"

const NAV_ITEMS = [
  { to: "/", label: "Dashboard" },
  { to: "/templates", label: "Templates" },
  { to: "/teams", label: "Teams" },
  { to: "/event-groups", label: "Event Groups" },
  { to: "/regular-tv", label: "Regular TV Groups" },
  { to: "/epg", label: "EPG" },
  { to: "/channels", label: "Channels" },
  { to: "/settings", label: "Settings" },
]

async function fetchHealth(): Promise<{ status: string; version: string }> {
  const resp = await fetch("/health")
  return resp.json()
}

export function MainLayout() {
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    const saved = localStorage.getItem("theme")
    return (saved as "dark" | "light") || "dark"
  })

  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    staleTime: Infinity, // Version won't change during session
  })

  const version = healthQuery.data?.version || "v2.0.0"

  // Update check
  const updateSettingsQuery = useUpdateCheckSettings()
  const updateInfoQuery = useCheckForUpdates(updateSettingsQuery.data?.enabled ?? false)
  const updateAvailable = updateInfoQuery.data?.update_available ?? false

  useEffect(() => {
    document.documentElement.classList.remove("light", "dark")
    document.documentElement.classList.add(theme)
    localStorage.setItem("theme", theme)
  }, [theme])

  const toggleTheme = () => {
    setTheme((t) => (t === "dark" ? "light" : "dark"))
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Navbar */}
      <nav className="border-b border-border bg-secondary/50 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-[1440px] mx-auto px-4">
          <div className="flex items-center justify-between h-12">
            {/* Brand */}
            <Link to="/" className="flex items-center gap-2">
              <img
                src="/logo.svg"
                alt="Teamarr"
                className="h-7 w-7"
                onError={(e) => {
                  e.currentTarget.style.display = "none"
                }}
              />
              <div className="flex flex-col">
                <span className="font-semibold leading-tight">
                  Teamarr
                </span>
                <span className="text-[10px] text-muted-foreground leading-tight hidden sm:block">
                  Sports EPG Generator for Dispatcharr
                </span>
              </div>
            </Link>

            {/* Nav Links */}
            <div className="flex items-center gap-1">
              {NAV_ITEMS.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    `px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-primary/10 text-primary"
                        : "text-muted-foreground hover:text-foreground hover:bg-accent"
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </div>

            {/* Right side */}
            <div className="flex items-center gap-3">
              <Link
                to="/settings?tab=advanced"
                className="flex items-center gap-1.5 text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded hover:bg-muted/80 transition-colors"
                title={updateAvailable ? "Update available - click to view" : version}
              >
                {version}
                {updateAvailable && (
                  <span className="flex h-2 w-2 rounded-full bg-amber-500" />
                )}
              </Link>
              <button
                onClick={toggleTheme}
                className="p-2 rounded-md hover:bg-accent transition-colors"
                title="Toggle theme"
              >
                {theme === "dark" ? (
                  <Moon className="h-4 w-4" />
                ) : (
                  <Sun className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>
        </div>
      </nav>

      {/* Main Content */}
      <main className="max-w-[1440px] mx-auto px-4 py-4">
        <Outlet />
      </main>

      {/* Footer */}
      <footer className="border-t border-border mt-auto">
        <div className="max-w-[1440px] mx-auto px-4 py-3">
          <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
            <img
              src="/logo.svg"
              alt=""
              className="h-4 w-4 opacity-50"
              onError={(e) => {
                e.currentTarget.style.display = "none"
              }}
            />
            <span className="flex items-center gap-1.5">
              Teamarr - Dynamic Sports EPG Generator for Dispatcharr | {version}
              {updateAvailable && (
                <span className="flex h-2 w-2 rounded-full bg-amber-500" title="Update available" />
              )}
              {window.location.port && ` | Port ${window.location.port}`}
            </span>
          </div>
        </div>
      </footer>

      {/* Toast notifications - themed styling for all toasts */}
      {/* Position bottom-right to avoid overlapping with top-right UI elements like save buttons */}
      <Toaster
        position="bottom-right"
        toastOptions={{
          className: "!bg-background !text-foreground !border !border-border !rounded-lg !shadow-lg !overflow-hidden",
          style: {
            padding: "12px 16px",
            fontSize: "14px",
            width: "450px",
            maxWidth: "450px",
            wordWrap: "break-word",
            overflowWrap: "break-word",
          },
        }}
      />
    </div>
  )
}
