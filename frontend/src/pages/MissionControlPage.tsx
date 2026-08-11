import { Search } from "lucide-react";
import { usePalette } from "../app/palette-context";
import { NeedsAttentionPanel } from "../components/missionControl/NeedsAttentionPanel";
import { ActiveMissionsPanel } from "../components/missionControl/ActiveMissionsPanel";
import { AgentInsightsPanel } from "../components/missionControl/AgentInsightsPanel";
import { KnowledgeCoveragePanel } from "../components/missionControl/KnowledgeCoveragePanel";
import { SystemHealthSummary } from "../components/missionControl/SystemHealthSummary";
import { RecentActivityFeed } from "../components/missionControl/RecentActivityFeed";

/**
 * Mission Control — GraphForge's engineering intelligence command center,
 * and the `/` route (formerly Control Center; renamed because the page's
 * job has shifted from "here's platform configuration/status" to "here's
 * what's happening and what needs you", see ADR-less design note in the
 * accompanying PR description).
 *
 * What this page is, and pointedly isn't:
 *   - AI Workspace → "I want to work with an agent."
 *   - Runs → "I want to inspect executions."
 *   - Reports → "I want to inspect generated results."
 *   - Mission Control → "I want to understand what's happening across my
 *     engineering system and what deserves my attention."
 *
 * Every section below is its own self-contained, self-fetching component
 * (same pattern the former WaitingOnYouPanel/InFlightWorkflowsPanel
 * already established) rather than one page-level loading gate — so a
 * slow or failed query in one section never blocks the rest of the page,
 * and "Needs your attention" + "Active missions" are visible together
 * without waiting on Recent Activity or Knowledge Coverage to resolve.
 *
 * Every number and label on this page is read directly from a real,
 * existing API response — nothing here is fabricated to make the page
 * look populated; see each panel's own docstring for its exact source
 * and, where relevant, what the backend genuinely doesn't provide yet.
 */
export function MissionControlPage() {
  const { openPalette } = usePalette();

  return (
    <div className="flex flex-col gap-6">
      {/* ── Header ──────────────────────────────────────────────── */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-display text-2xl font-bold tracking-tight text-fg">
            Mission Control
          </h1>
          <p className="mt-1 text-sm text-fg-muted">
            Your engineering intelligence command center
          </p>
        </div>
        {/* Not a second search implementation — this opens the same global
            CommandPalette Topbar's "⌘K" button does (see PaletteContext).
            AI interaction lives in AI Workspace, not here. */}
        <button
          type="button"
          onClick={openPalette}
          className="focus-ring flex items-center gap-2.5 rounded-lg border border-line bg-surface px-3.5 py-2.5 text-sm text-fg-muted shadow-xs transition-colors hover:bg-surface-hover hover:text-fg-secondary sm:w-72"
        >
          <Search className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span className="flex-1 text-left">Search GraphForge…</span>
          <kbd className="shrink-0 rounded border border-line px-1.5 py-0.5 text-[10px] text-fg-subtle">
            ⌘K
          </kbd>
        </button>
      </div>

      {/* ── Needs attention + Active missions — above the fold ──── */}
      {/* items-start: CSS Grid's default `stretch` was forcing Needs
          Attention's <section> to match Active Missions' taller natural
          height, leaving ~679px of empty space below its own (internally
          capped-and-scrollable) content. Each card should size to its own
          content instead of its sibling's. */}
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
        <NeedsAttentionPanel />
        <ActiveMissionsPanel />
      </div>

      {/* ── Agent insights ──────────────────────────────────────── */}
      <AgentInsightsPanel />

      {/* ── Knowledge coverage + system health ──────────────────── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <KnowledgeCoveragePanel />
        <SystemHealthSummary />
      </div>

      {/* ── Recent activity ──────────────────────────────────────── */}
      <RecentActivityFeed />
    </div>
  );
}
