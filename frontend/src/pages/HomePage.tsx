import { ConversationChat } from "../components/ask/ConversationChat";

const EXAMPLES = [
  "What will be affected if we change the customer ingestion pipeline?",
  "Why did this workflow fail?",
  "Which repositories depend on this service?",
  "What are the highest-risk dependencies?",
  "What documentation explains this system?",
];

const CAPABILITIES = [
  { label: "Understand", hint: "how a system works and why" },
  { label: "Investigate", hint: "what happened and what's connected" },
  { label: "Assess impact", hint: "what breaks if this changes" },
] as const;

/**
 * Home — GraphForge's conversational engineering investigation.
 *
 * "I don't need to know where the information lives. I just ask
 * GraphForge" — and a follow-up question builds on what was already
 * established, rather than starting a new independent report. See
 * `app.services.conversation_service` on the backend for the state model
 * this renders: every assistant turn is either a fresh deterministic
 * grounding (blast radius / dependency search) or reasoning over the
 * conversation's own accumulated investigation state — never a bare LLM
 * guess and never a silent hand-off to a different agent mid-conversation.
 *
 * Every other page in the product (Architecture, Repositories, Workflow
 * History, ...) is unchanged; a turn's action buttons are the only way
 * out of the conversation, by explicit user choice. The chat UI itself
 * lives in `ConversationChat` — shared with Migration Assistant, which
 * is this exact same loop under `mode: "migration"`, not a second agent.
 */
export function HomePage() {
  return (
    <ConversationChat
      mode="general"
      brandTitle="GraphForge"
      eyebrow="Engineering Intelligence"
      subheading="Ask anything about your engineering ecosystem."
      examples={EXAMPLES}
      capabilities={CAPABILITIES}
    />
  );
}
