import {
  Activity,
  BriefcaseBusiness,
  CheckCircle2,
  CloudOff,
  Moon,
  Plug,
  ShieldCheck,
  Sun,
  SunMoon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useApprovals, useCapabilities } from "../queries";
import type { ThemePreference } from "../types";
import { InfoTooltip } from "./InfoTooltip";
import { useTheme } from "./ThemeProvider";

const NAVIGATION = [
  { to: "/", label: "Work", icon: BriefcaseBusiness, end: true, secondary: false },
  { to: "/approvals", label: "Approvals", icon: ShieldCheck, end: false, secondary: false },
  { to: "/audit", label: "History", icon: Activity, end: false, secondary: true },
  { to: "/connections", label: "Connections", icon: Plug, end: false, secondary: true },
];

const PAGE_TITLES: Record<string, string> = {
  "/": "Work queue",
  "/approvals": "Approvals",
  "/audit": "History",
  "/connections": "Connections",
};

function useOnlineStatus() {
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const setConnected = () => setOnline(true);
    const setDisconnected = () => setOnline(false);
    window.addEventListener("online", setConnected);
    window.addEventListener("offline", setDisconnected);
    return () => {
      window.removeEventListener("online", setConnected);
      window.removeEventListener("offline", setDisconnected);
    };
  }, []);
  return online;
}

function ThemeControl() {
  const { preference, setPreference } = useTheme();
  const icons = { system: SunMoon, light: Sun, dark: Moon };
  const Icon = icons[preference];
  return (
    <label className="theme-control">
      <span className="sr-only">Theme</span>
      <Icon aria-hidden="true" />
      <select
        value={preference}
        onChange={(event) => setPreference(event.target.value as ThemePreference)}
        aria-label="Theme"
      >
        <option value="system">System</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </label>
  );
}

function Navigation({ mobile = false }: { mobile?: boolean }) {
  const approvals = useApprovals();
  const pending = approvals.data?.items.length ?? 0;
  return (
    <nav className={mobile ? "mobile-nav" : "sidebar-nav"} aria-label="Primary">
      {NAVIGATION.map(({ to, label, icon: Icon, end, secondary }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            [isActive ? "active" : "", secondary ? "nav-link--secondary" : ""]
              .filter(Boolean)
              .join(" ")
          }
        >
          <Icon aria-hidden="true" />
          <span>{label}</span>
          {label === "Approvals" && pending > 0 ? (
            <span className="nav-count" aria-label={`${pending} pending approvals`}>
              {pending}
            </span>
          ) : null}
        </NavLink>
      ))}
    </nav>
  );
}

export function AppShell() {
  const location = useLocation();
  const capabilities = useCapabilities();
  const online = useOnlineStatus();
  const title = location.pathname.startsWith("/runs/")
    ? "Workflow details"
    : PAGE_TITLES[location.pathname] ?? "Relay";
  const demoMode = capabilities.data?.demo_mode ?? capabilities.data?.mode === "demo";

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true">R</span>
          <span>
            <strong>Relay</strong>
            <small>Approval operations</small>
          </span>
        </div>
        <Navigation />
        <div className="sidebar__footer">
          <div className="trust-note">
            <ShieldCheck aria-hidden="true" />
            <span>Write actions always pause for review.</span>
          </div>
          <ThemeControl />
        </div>
      </aside>

      <div className="workspace-shell">
        <header className="topbar">
          <div>
            <p className="eyebrow">Relay workspace</p>
            <h1>{title}</h1>
          </div>
          <div className="topbar__status">
            {demoMode ? (
              <span className="mode-badge">
                <span className="mode-badge__full">Demo workspace</span>
                <span className="mode-badge__short">Demo</span>
                <InfoTooltip label="About the demo workspace">
                  Uses local fixtures and stores approved actions locally. Nothing is sent to a live provider.
                </InfoTooltip>
              </span>
            ) : null}
            <span className={`connection-badge ${online ? "is-online" : "is-offline"}`}>
              {online ? <CheckCircle2 aria-hidden="true" /> : <CloudOff aria-hidden="true" />}
              <span>{online ? "Connected" : "Offline"}</span>
            </span>
            <div className="topbar__theme"><ThemeControl /></div>
          </div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}>
          <Outlet />
        </main>
        <Navigation mobile />
      </div>
    </div>
  );
}
