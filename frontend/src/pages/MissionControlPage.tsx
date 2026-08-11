import { Plus } from "lucide-react";
import { Link } from "react-router-dom";
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
  return (
    <div className="flex flex-col gap-8">
      {/* ── Header ──────────────────────────────────────────────── */}
      {/* The page-level search box this used to render was a second,
          visually distinct entry point to the exact same CommandPalette
          the Topbar's "Jump to…" already opens — two search affordances on
          one screen reads as indecision, not capability. This slot is more
          useful spent on the one action a command center should always
          surface: starting new work. */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-semibold tracking-[0.14em] text-accent-fg uppercase">
            Engineering intelligence
          </p>
          <h1 className="mt-1 font-display text-3xl font-bold tracking-tight text-fg">
            Mission Control
          </h1>
          <p className="mt-1.5 text-sm text-fg-muted">
            What GraphForge found, what it&apos;s doing, and what needs you.
          </p>
        </div>
        <Link
          to="/workflows/new"
          className="focus-ring flex items-center justify-center gap-2 rounded-lg bg-accent-solid px-4 py-2.5 text-sm font-semibold text-accent-on-solid shadow-sm transition-colors hover:brightness-110 sm:w-auto"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          New workflow
        </Link>
      </div>

      {/* ── Needs attention + Active missions — above the fold ──── */}
      {/* items-start: CSS Grid's default `stretch` was forcing Needs
          Attention's <section> to match Active Missions' taller natural
          height, leaving ~679px of empty space below its own (internally
          capped-and-scrollable) content. Each card should size to its own
          content instead of its sibling's. */}
      {/* Asymmetric, not a 50/50 split: Needs Attention is a compact list
          that reads fine narrow, while Active Missions renders a 6-stage
          pipeline tracker per mission that was getting crushed into
          10px-label icons in an even half. Giving it the wider column is
          what actually fixed the "cramped, truncated stage labels" problem
          — a 50/50 split just relocated it. */}
      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(320px,420px)_1fr]">
        <NeedsAttentionPanel />
        <ActiveMissionsPanel />
      </div>

      {/* ── Agent insights ──────────────────────────────────────── */}
      <AgentInsightsPanel />

      {/* ── Knowledge coverage + system health ──────────────────── */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <KnowledgeCoveragePanel />
        <SystemHealthSummary />
      </div>

      {/* ── Recent activity ──────────────────────────────────────── */}
      <RecentActivityFeed />
    </div>
  );
}
