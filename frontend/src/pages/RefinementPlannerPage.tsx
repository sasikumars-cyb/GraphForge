import { ConversationChat } from "../components/ask/ConversationChat";

const EXAMPLES = [
  "Refine PROT-5263",
  "Break this feature into stories: [paste requirement]",
  "What work are we missing?",
  "Is this ready for refinement?",
];

const CAPABILITIES = [
  { label: "Understand", hint: "objective, scope, constraints, unknowns" },
  { label: "Decompose", hint: "epics, stories, tasks, spikes" },
  { label: "Connect & validate", hint: "dependencies, readiness, test strategy" },
] as const;

/**
 * Refinement Planner — turning a requirement into a refinement-ready
 * engineering plan, as a "refinement"-mode conversation on the exact
 * same loop `HomePage`/Migration Assistant use (`ConversationChat`/
 * `ConversationService`), not a separate agent bolted onto the AI
 * Workspace. See `app.services.refinement_grounding` for what's actually
 * fetched (a real Jira issue) vs. computed (critical path,
 * parallelizable work, "what if X slips" impact) vs. proposed by the LLM
 * (the work breakdown itself, grounded in that real input).
 *
 * "Show dependencies" hands off to the interactive work-item dependency
 * graph (`RefinementGraphPage`) — the conversation's own investigation,
 * visualized, not a different tool.
 */
export function RefinementPlannerPage() {
  return (
    <ConversationChat
      mode="refinement"
      brandTitle="Refinement Planner"
      eyebrow="Requirements → Stories → Dependencies → Spikes → Validation"
      subheading="What are you refining?"
      examples={EXAMPLES}
      capabilities={CAPABILITIES}
      inputPlaceholder="Paste a requirement, reference a Jira issue, or describe a feature…"
      pendingLabel="Reading the requirement and discovering engineering context…"
    />
  );
}
