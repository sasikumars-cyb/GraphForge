import { apiFetch } from "./client";
import type { Conversation, ConversationMode, ConversationSummary } from "../../types/conversation";

/** Starts a new investigation — the first user message, grounded via the
 * same deterministic path `POST /ask` uses, then framed conversationally.
 * `mode: "migration"` opts into Migration Assistant's own grounding/
 * prompt (see `app.services.conversation_service` on the backend) —
 * same endpoint, same conversation table, just a different lens. */
export function startConversation(
  token: string,
  question: string,
  mode: ConversationMode = "general",
): Promise<Conversation> {
  return apiFetch<Conversation>("/conversations", {
    method: "POST",
    token,
    body: { question, mode },
  });
}

/** Continues an investigation. The backend re-grounds only if this
 * message names a new repository; otherwise it reasons over the
 * conversation's own accumulated state — no identifiers need repeating. */
export function postConversationMessage(
  token: string,
  conversationId: string,
  message: string,
): Promise<Conversation> {
  return apiFetch<Conversation>(`/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    token,
    body: { message },
  });
}

/** The last few investigations, most recently active first — what the
 * history icon shows. Summaries only (no messages), so this stays cheap
 * to fetch on every dropdown open. `mode` scopes it to one surface —
 * Migration Assistant's history icon shouldn't list Ask GraphForge's
 * general investigations, and vice versa. */
export function listConversations(
  token: string,
  limit = 5,
  mode?: ConversationMode,
  signal?: AbortSignal,
): Promise<ConversationSummary[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (mode) params.set("mode", mode);
  return apiFetch<ConversationSummary[]>(`/conversations?${params.toString()}`, { token, signal });
}

export function getConversation(
  token: string,
  conversationId: string,
  signal?: AbortSignal,
): Promise<Conversation> {
  return apiFetch<Conversation>(`/conversations/${encodeURIComponent(conversationId)}`, {
    token,
    signal,
  });
}
