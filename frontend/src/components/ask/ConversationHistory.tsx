import { useEffect, useRef, useState } from "react";
import { History } from "lucide-react";
import { useAuth } from "../../app/auth-context";
import { listConversations } from "../../lib/api/conversations";
import type { ConversationMode, ConversationSummary } from "../../types/conversation";

const HISTORY_LIMIT = 5;

function relativeTime(iso: string): string {
  const deltaMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.round(deltaMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

/**
 * The Home page's history icon — its own small surface, not a page (Ask
 * GraphForge's investigations aren't part of Workflow History: they're
 * conversations, not agent runs). Fetched lazily on open rather than kept
 * live in HomePage's own state, since it's a one-off lookup a user makes
 * to jump back into a recent investigation, not something that needs to
 * track the current conversation's own updates.
 */
export function ConversationHistory({
  onSelect,
  activeConversationId,
  mode,
}: {
  onSelect: (conversationId: string) => void;
  activeConversationId?: string | null;
  /** Scopes the list to one surface — omit for Ask GraphForge's general
   * history, pass "migration" for Migration Assistant's own. */
  mode?: ConversationMode;
}) {
  const { token } = useAuth();
  const [isOpen, setIsOpen] = useState(false);
  const [items, setItems] = useState<ConversationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen || !token) return;
    const controller = new AbortController();
    listConversations(token, HISTORY_LIMIT, mode, controller.signal)
      .then(setItems)
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Could not load history.");
      });
    return () => controller.abort();
  }, [isOpen, token, mode]);

  useEffect(() => {
    if (!isOpen) return;
    function handlePointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setIsOpen(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen((v) => !v)}
        aria-label="Recent investigations"
        aria-expanded={isOpen}
        className="flex h-8 w-8 items-center justify-center rounded-lg text-fg-muted transition-colors hover:bg-surface-raised hover:text-fg-secondary"
      >
        <History className="h-4 w-4" aria-hidden="true" />
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full z-10 mt-2 w-72 rounded-xl border border-line-muted bg-surface p-1.5 shadow-lg">
          <p className="px-2.5 py-1.5 text-xs font-semibold uppercase tracking-wide text-fg-muted">
            Recent investigations
          </p>
          {error && <p className="px-2.5 py-2 text-xs text-danger-fg">{error}</p>}
          {!error && items === null && (
            <p className="px-2.5 py-2 text-xs text-fg-muted">Loading…</p>
          )}
          {items !== null && items.length === 0 && (
            <p className="px-2.5 py-2 text-xs text-fg-muted">No investigations yet.</p>
          )}
          {items?.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                setIsOpen(false);
                onSelect(item.id);
              }}
              className={`flex w-full flex-col items-start gap-0.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-surface-raised ${
                item.id === activeConversationId ? "bg-accent-bg" : ""
              }`}
            >
              <span className="w-full truncate text-sm text-fg">{item.title}</span>
              <span className="text-[11px] text-fg-muted">{relativeTime(item.updated_at)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
