import { ConversationChat } from "../components/ask/ConversationChat";

const EXAMPLES = [
  "Migrate the customer ingestion database from PostgreSQL to BigQuery",
  "Move our Spark jobs to Databricks",
  "Upgrade Python 3.9 to Python 3.12",
  "Replace service X with service Y",
];

const CAPABILITIES = [
  { label: "Discover", hint: "what's actually wired to this technology" },
  { label: "Assess", hint: "direct, indirect, and blast radius" },
  { label: "Plan & validate", hint: "phased plan, grounded test strategy" },
] as const;

/**
 * Migration Assistant — dependency-aware migration planning, as a
 * "migration"-mode conversation on the exact same loop `HomePage` uses
 * (`ConversationChat`/`ConversationService`), not a separate agent
 * bolted onto the AI Workspace. See `app.services.migration_grounding`
 * for what's actually graph-derived (direct/indirect scope, risk
 * findings) versus reasoned (constraint follow-ups, test/plan guidance)
 * each turn.
 *
 * The user should never feel routed to a different tool: "Explore
 * impact"/"View dependency graph"/"Create migration plan"/"Validate
 * migration" are the same deep-link action pattern every other
 * conversational turn already uses to hand off into the deeper
 * workspace (Architecture, Planning, Testing) — one investigation,
 * multiple views.
 */
export function MigrationAssistantPage() {
  return (
    <ConversationChat
      mode="migration"
      brandTitle="Migration Assistant"
      eyebrow="Dependency-Aware Migration Planning"
      subheading="What are you migrating?"
      examples={EXAMPLES}
      capabilities={CAPABILITIES}
      inputPlaceholder="Describe the migration…"
      pendingLabel="Tracing the dependency graph…"
    />
  );
}
