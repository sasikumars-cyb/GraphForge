import { useSearchParams } from "react-router-dom";
import {
  Building2,
  Brain,
  Plug,
  Wrench,
  Shield,
  Settings2,
  type LucideIcon,
} from "lucide-react";

import { useAuth } from "../../app/auth-context";
import { WorkspaceSection } from "./WorkspaceSection";
import { AIWorkspaceSection } from "./AIWorkspaceSection";
import { IntegrationsSection } from "./IntegrationsSection";
import { ToolRegistrySection } from "./ToolRegistrySection";
import { SecuritySection } from "./SecuritySection";
import { AdvancedSection } from "./AdvancedSection";

interface SettingsTab {
  id: string;
  label: string;
  icon: LucideIcon;
  description: string;
  adminOnly: boolean;
}

const TABS: SettingsTab[] = [
  {
    id: "workspace",
    label: "Workspace",
    icon: Building2,
    description: "Organization, notifications, preferences",
    adminOnly: false,
  },
  {
    id: "ai",
    label: "AI Workspace",
    icon: Brain,
    description: "Providers, profiles, models, health",
    adminOnly: true,
  },
  {
    id: "integrations",
    label: "Integrations",
    icon: Plug,
    description: "External systems connected to GraphForge",
    adminOnly: false,
  },
  {
    id: "tools",
    label: "Tool Registry",
    icon: Wrench,
    description: "Capabilities available to agents",
    adminOnly: true,
  },
  {
    id: "security",
    label: "Security",
    icon: Shield,
    description: "Credentials, secrets, access",
    adminOnly: true,
  },
  {
    id: "advanced",
    label: "Advanced",
    icon: Settings2,
    description: "Diagnostics, logs, feature flags",
    adminOnly: true,
  },
];

function TabContent({ tabId }: { tabId: string }) {
  switch (tabId) {
    case "workspace":
      return <WorkspaceSection />;
    case "ai":
      return <AIWorkspaceSection />;
    case "integrations":
      return <IntegrationsSection />;
    case "tools":
      return <ToolRegistrySection />;
    case "security":
      return <SecuritySection />;
    case "advanced":
      return <AdvancedSection />;
    default:
      return null;
  }
}

export function SettingsShell() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  // Filter tabs based on role.
  const visibleTabs = TABS.filter((tab) => !tab.adminOnly || isAdmin);

  // Tab selection lives in the URL, not local state — the GitHub "Add
  // Connection" flow does a full-page redirect to GitHub and back (OAuth
  // can't be done via XHR), which would otherwise reset the tab to the
  // first one and strand the user on Workspace after connecting.
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  // The OAuth callback redirects here with ?github=connected|error and no
  // ?tab — land back on Integrations, where that outcome is shown.
  const defaultTabId = searchParams.has("github") ? "integrations" : (visibleTabs[0]?.id ?? "workspace");
  const activeTab = visibleTabs.some((t) => t.id === requestedTab) ? (requestedTab as string) : defaultTabId;
  const current = visibleTabs.find((t) => t.id === activeTab) ?? visibleTabs[0];

  function setActiveTab(tabId: string) {
    const next = new URLSearchParams(searchParams);
    next.set("tab", tabId);
    setSearchParams(next, { replace: true });
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div>
        <h2 className="text-xl font-semibold text-slate-50">Settings</h2>
        <p className="mt-1 text-sm text-slate-400">
          Configure GraphForge platform, AI providers, integrations, and preferences.
        </p>
      </div>

      <div className="flex flex-col gap-6 lg:flex-row">
        {/* Sidebar navigation */}
        <nav
          className="flex shrink-0 flex-row gap-1 overflow-x-auto lg:w-56 lg:flex-col"
          aria-label="Settings sections"
        >
          {visibleTabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`group flex items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition-colors whitespace-nowrap ${
                activeTab === tab.id
                  ? "bg-brand-500/10 text-brand-200 ring-1 ring-inset ring-brand-500/30"
                  : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"
              }`}
            >
              <tab.icon
                className={`h-4 w-4 shrink-0 ${
                  activeTab === tab.id ? "text-brand-400" : "text-slate-500 group-hover:text-slate-300"
                }`}
                aria-hidden="true"
              />
              <span className="hidden lg:inline">{tab.label}</span>
              <span className="lg:hidden">{tab.label}</span>
            </button>
          ))}
        </nav>

        {/* Content area */}
        <div className="min-w-0 flex-1">
          {current && (
            <>
              {/* Section header */}
              <div className="mb-5 flex items-center gap-3">
                <div className="rounded-lg bg-brand-500/10 p-2 ring-1 ring-inset ring-brand-500/30">
                  <current.icon className="h-5 w-5 text-brand-400" aria-hidden="true" />
                </div>
                <div>
                  <h3 className="text-base font-semibold text-slate-100">{current.label}</h3>
                  <p className="text-xs text-slate-400">{current.description}</p>
                </div>
              </div>

              <TabContent tabId={activeTab} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
