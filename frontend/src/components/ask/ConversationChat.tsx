import { useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { ArrowUp, RotateCcw } from "lucide-react";
import { Logomark } from "../layout/Logomark";
import { AskAnswer, type DisplayAnswer } from "./AskAnswer";
import { ConversationHistory } from "./ConversationHistory";
import { useAuth } from "../../app/auth-context";
import {
  startConversation,
  postConversationMessage,
  getConversation,
} from "../../lib/api/conversations";
import type { Conversation, ConversationMessage, ConversationMode } from "../../types/conversation";

/** Maps one assistant turn onto `AskAnswer`'s shared display shape.
 * Every field here already exists on `ConversationTurnPayload` — no
 * reshaping beyond picking the display list off `impact`. */
function toDisplayAnswer(message: ConversationMessage): DisplayAnswer {
  const payload = message.payload;
  return {
    answer: message.content,
    why: payload?.why ?? "",
    evidence: payload?.evidence ?? [],
    impact: payload?.impact
      ? {
          severity: payload.impact.severity,
          summary: payload.impact.summary,
          affected: [
            ...payload.impact.affected_repositories,
            ...payload.impact.affected_apis,
            ...payload.impact.affected_databases,
            ...payload.impact.affected_queues,
          ].slice(0, 12),
        }
      : undefined,
    entities: payload?.entities,
    workItems: payload?.refinement?.work_items.map((item) => ({
      id: item.id,
      title: item.title,
      type: item.type,
      status: item.status,
    })),
    readiness: payload?.refinement?.readiness
      ? {
          level: payload.refinement.readiness.level,
          score: payload.refinement.readiness.score,
        }
      : undefined,
    actions: (payload?.actions ?? []).map((a) => ({ label: a.label, href: a.href })),
    degraded: payload?.degraded,
  };
}

export interface ConversationChatProps {
  /** Which grounding/prompt the backend applies to every turn — see
   * `app.services.conversation_service`. Same conversation table, same
   * state model, either mode. */
  mode: ConversationMode;
  brandTitle: string;
  eyebrow: string;
  subheading: string;
  examples: string[];
  capabilities?: readonly { label: string; hint: string }[];
  inputPlaceholder?: string;
  followUpPlaceholder?: string;
  pendingLabel?: string;
}

/**
 * The conversational investigation loop's UI — shared by Ask GraphForge
 * (`mode: "general"`) and Migration Assistant (`mode: "migration"`) so
 * the two never drift into two different chat experiences. Every prop
 * beyond `mode` is presentation only (copy, examples); the state
 * machine — start, follow up, reset, resume from history — is identical
 * either way, because the backend conversation loop it talks to is the
 * same endpoint either way.
 */
export function ConversationChat({
  mode,
  brandTitle,
  eyebrow,
  subheading,
  examples,
  capabilities,
  inputPlaceholder = "Ask GraphForge anything…",
  followUpPlaceholder = "Ask a follow-up…",
  pendingLabel = "Investigating…",
}: ConversationChatProps) {
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [input, setInput] = useState("");
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollAnchorRef = useRef<HTMLDivElement | null>(null);
  const resumedRef = useRef(false);

  useEffect(() => {
    scrollAnchorRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [conversation]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || !token || pending) return;

    setInput("");
    setError(null);
    setPending(true);
    try {
      if (conversation === null) {
        const result = await startConversation(token, trimmed, mode);
        setConversation(result);
      } else {
        const result = await postConversationMessage(token, conversation.id, trimmed);
        setConversation(result);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      setInput(trimmed);
    } finally {
      setPending(false);
    }
  }

  function askExample(example: string) {
    setInput(example);
  }

  function reset() {
    setInput("");
    setConversation(null);
    setError(null);
  }

  async function handleSelectHistory(conversationId: string) {
    if (!token || conversationId === conversation?.id) return;
    setError(null);
    setPending(true);
    try {
      const result = await getConversation(token, conversationId);
      setConversation(result);
      setInput("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load that investigation.");
    } finally {
      setPending(false);
    }
  }

  // "Show dependencies" (and any other action that hands off to a
  // dedicated page) links back here with `?resume=<conversationId>` —
  // without this, "Back to conversation" would always land on a blank
  // new chat, silently discarding the exact investigation the user just
  // came from. Runs once per mount (`resumedRef`), same load path as
  // picking the conversation from history.
  useEffect(() => {
    const resumeId = searchParams.get("resume");
    if (!resumeId || !token || resumedRef.current) return;
    resumedRef.current = true;
    setPending(true);
    getConversation(token, resumeId)
      .then((result) => setConversation(result))
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load that investigation."))
      .finally(() => setPending(false));
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("resume");
      return next;
    }, { replace: true });
  }, [searchParams, token, setSearchParams]);

  const showEmptyState = conversation === null;

  return (
    <div className="relative mx-auto flex h-full w-full max-w-3xl flex-col">
      {showEmptyState && (
        <div className="absolute right-0 top-4">
          <ConversationHistory onSelect={handleSelectHistory} mode={mode} />
        </div>
      )}
      {showEmptyState ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-8 py-16 text-center">
          <div className="flex flex-col items-center gap-4">
            <Logomark className="h-12 w-12" />
            <div>
              <h1 className="font-display text-2xl font-semibold tracking-tight text-fg">
                {brandTitle}
              </h1>
              <p className="mt-1 text-sm font-medium uppercase tracking-wide text-accent-fg">
                {eyebrow}
              </p>
            </div>
            <p className="max-w-md text-sm text-fg-muted">{subheading}</p>
          </div>

          <form onSubmit={handleSubmit} className="w-full">
            <AskInput
              value={input}
              onChange={setInput}
              disabled={pending}
              autoFocus
              placeholder={inputPlaceholder}
            />
          </form>
          {error && <p className="text-sm text-danger-fg">{error}</p>}

          <div className="flex flex-wrap justify-center gap-2">
            {examples.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => askExample(example)}
                className="rounded-full border border-line-muted bg-surface px-3.5 py-1.5 text-xs text-fg-secondary transition-colors hover:border-accent-line hover:text-fg"
              >
                {example}
              </button>
            ))}
          </div>

          {capabilities && (
            <div className="flex gap-8 pt-2">
              {capabilities.map((c) => (
                <div key={c.label} className="text-center">
                  <p className="text-xs font-semibold text-fg-secondary">{c.label}</p>
                  <p className="mt-0.5 text-[11px] text-fg-muted">{c.hint}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="flex h-full flex-col">
          <div className="flex shrink-0 items-center justify-between gap-4 border-b border-line-muted py-3">
            <div className="flex items-center gap-2 text-sm font-medium text-fg-secondary">
              <Logomark className="h-5 w-5" />
              {brandTitle}
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <ConversationHistory
                onSelect={handleSelectHistory}
                activeConversationId={conversation.id}
                mode={mode}
              />
              <button
                type="button"
                onClick={reset}
                className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary"
              >
                <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                New chat
              </button>
            </div>
          </div>

          <div className="flex flex-1 flex-col gap-5 overflow-y-auto py-5">
            {conversation.messages.map((message) =>
              message.role === "user" ? (
                <div key={message.id} className="flex justify-end">
                  <p className="max-w-[80%] rounded-2xl bg-neutral-bg px-4 py-2 text-sm text-fg">
                    {message.content}
                  </p>
                </div>
              ) : (
                <div key={message.id} className="flex items-start gap-2.5">
                  <Logomark className="mt-0.5 h-6 w-6 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <AskAnswer data={toDisplayAnswer(message)} />
                  </div>
                </div>
              ),
            )}
            {pending && (
              <div className="flex items-center gap-2.5">
                <Logomark className="h-6 w-6 shrink-0 animate-pulse" />
                <p className="text-sm text-fg-muted">{pendingLabel}</p>
              </div>
            )}
            {error && <p className="text-sm text-danger-fg">{error}</p>}
            <div ref={scrollAnchorRef} />
          </div>

          <div className="shrink-0 border-t border-line-muted py-4">
            <form onSubmit={handleSubmit}>
              <AskInput
                value={input}
                onChange={setInput}
                disabled={pending}
                placeholder={followUpPlaceholder}
              />
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

function AskInput({
  value,
  onChange,
  disabled,
  autoFocus,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
  autoFocus?: boolean;
  placeholder: string;
}) {
  return (
    <div className="flex items-end gap-2 rounded-2xl border border-line-muted bg-surface p-3 shadow-sm transition-colors focus-within:border-accent-line">
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.currentTarget.form?.requestSubmit();
            e.preventDefault();
          }
        }}
        rows={1}
        autoFocus={autoFocus}
        disabled={disabled}
        placeholder={placeholder}
        className="max-h-40 min-h-[2.5rem] flex-1 resize-none bg-transparent px-2 py-1.5 text-base text-fg placeholder:text-fg-muted focus:outline-none disabled:opacity-60"
      />
      <button
        type="submit"
        disabled={disabled || value.trim() === ""}
        aria-label="Send"
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-solid text-accent-on-solid transition-colors hover:brightness-110 disabled:opacity-40"
      >
        <ArrowUp className="h-4 w-4" aria-hidden="true" />
      </button>
    </div>
  );
}
