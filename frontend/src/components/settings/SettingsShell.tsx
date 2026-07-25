import { useState } from "react";
import {
  Building2,
  Brain,
  Database,
  Wrench,
  Shield,
  Settings2,
  type LucideIcon,
} from "lucide-react";

import { WorkspaceSection } from "./WorkspaceSection";
import { AIWorkspaceSection } from "./AIWorkspaceSection";
import { KnowledgeSourcesSection } from "./KnowledgeSourcesSection";
import { ToolRegistrySection } from "./ToolRegistrySection";
import { SecuritySection } from "./SecuritySection";
import { AdvancedSection } from "./AdvancedSection";

interface SettingsTab {
  id: string;
  label: string;
  icon: LucideIcon;
  description: string;
}

const TABS: SettingsTab[] = [
  {
    id: "workspace",
    label: "Workspace",
    icon: Building2,
    description: "Organization, notifications, preferences",
  },
  {
    id: "ai",
    label: "AI Workspace",
    icon: Brain,
    description: "Providers, profiles, models, health",
  },
  {
    id: "knowledge",
    label: "Knowledge Sources",
    icon: Database,
    description: "Where GraphForge obtains information",
  },
  {
    id: "tools",
    label: "Tool Registry",
    icon: Wrench,
    description: "Capabilities available to agents",
  },
  {
    id: "security",
    label: "Security",
    icon: Shield,
    description: "Credentials, secrets, access",
  },
  {
    id: "advanced",
    label: "Advanced",
    icon: Settings2,
    description: "Diagnostics, logs, feature flags",
  },
];

function TabContent({ tabId }: { tabId: string }) {
  switch (tabId) {
    case "workspace":
      return <WorkspaceSection />;
    case "ai":
      return <AIWorkspaceSection />;
    case "knowledge":
      return <KnowledgeSourcesSection />;
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
  const [activeTab, setActiveTab] = useState("workspace");
  const current = TABS.find((t) => t.id === activeTab) ?? TABS[0];

  return (
    <div className="flex flex-col gap-6">
      {/* Page header */}
      <div>
        <h2 className="text-xl font-semibold text-slate-50">Settings</h2>
        <p className="mt-1 text-sm text-slate-400">
          Configure GraphForge platform, AI providers, knowledge sources, and integrations.
        </p>
      </div>

      <div className="flex flex-col gap-6 lg:flex-row">
        {/* Sidebar navigation */}
        <nav
          className="flex shrink-0 flex-row gap-1 overflow-x-auto lg:w-56 lg:flex-col"
          aria-label="Settings sections"
        >
          {TABS.map((tab) => (
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
        </div>
      </div>
    </div>
  );
}
