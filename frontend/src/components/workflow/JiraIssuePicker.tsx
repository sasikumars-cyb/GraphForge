import { useEffect, useRef, useState } from "react";
import { Search, Loader2 } from "lucide-react";
import { useAuth } from "../../app/auth-context";
import { searchJiraIssues, type JiraIssueResult } from "../../lib/api/jira";

interface JiraIssuePickerProps {
  /** Called with a deterministic "Jira: KEY — summary" reference to append
   * to the objective — guaranteed to match extract_issue_key's pattern
   * (see jira_tool.py), unlike a user pasting a key/URL by hand and hoping
   * it's recognized. This is the actual fix for "no structured Jira entry
   * point" — browsing and clicking, not copy-pasting a key into a
   * textarea. */
  onSelect: (reference: string) => void;
}

const DEBOUNCE_MS = 350;

/** Real-time Jira search-and-select — empty results (no match, or Jira
 * not configured for REST search; see jira.py's search endpoint) render as
 * a quiet "no matches" state, not an error, since this is an optional
 * affordance on top of the free-text objective, not a required step. */
export function JiraIssuePicker({ onSelect }: JiraIssuePickerProps) {
  const { token } = useAuth();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<JiraIssueResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const debounceRef = useRef<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    if (!token || query.trim().length < 2) {
      setResults([]);
      setIsSearching(false);
      return;
    }
    setIsSearching(true);
    const controller = new AbortController();
    debounceRef.current = window.setTimeout(async () => {
      try {
        const found = await searchJiraIssues(token, query.trim(), controller.signal);
        if (controller.signal.aborted) return;
        setResults(found);
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setResults([]);
      } finally {
        if (!controller.signal.aborted) setIsSearching(false);
      }
    }, DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
      // Cancels the in-flight request itself (not just the pending
      // debounce timer) — without this, a slow response to an older
      // keystroke can still resolve after a newer one and clobber
      // `results` with stale matches.
      controller.abort();
    };
  }, [query, token]);

  // Close on outside click and Escape — this was only closeable before by
  // selecting a result, leaving the dropdown open (and blocking whatever's
  // behind it) if the user clicked elsewhere or pressed Escape instead.
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

  function handleSelect(issue: JiraIssueResult) {
    onSelect(`Jira: ${issue.key} — ${issue.summary}`);
    setQuery("");
    setResults([]);
    setIsOpen(false);
  }

  return (
    <div className="relative" ref={containerRef}>
      <label htmlFor="jira-search" className="block text-sm font-medium text-fg-secondary">
        Link a Jira issue <span className="text-fg-muted">(optional)</span>
      </label>
      <div className="relative mt-2">
        <Search className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-fg-muted" aria-hidden="true" />
        <input
          id="jira-search"
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setIsOpen(true);
          }}
          onFocus={() => setIsOpen(true)}
          placeholder="Search by summary, e.g. rate limiting"
          className="w-full rounded-lg border border-line bg-surface-raised py-2.5 pr-3 pl-9 text-sm text-fg placeholder-fg-subtle focus:border-accent-line "
        />
        {isSearching && (
          <Loader2 className="absolute top-1/2 right-3 h-4 w-4 -translate-y-1/2 animate-spin text-fg-muted" aria-hidden="true" />
        )}
      </div>

      {isOpen && query.trim().length >= 2 && !isSearching && (
        <div className="absolute z-10 mt-1 w-full overflow-hidden rounded-lg border border-line bg-surface shadow-lg">
          {results.length === 0 ? (
            <p className="px-3 py-2.5 text-xs text-fg-muted">
              No matching issues found (or Jira isn't connected for search).
            </p>
          ) : (
            <ul className="max-h-64 overflow-y-auto">
              {results.map((issue) => (
                <li key={issue.key}>
                  <button
                    type="button"
                    onClick={() => handleSelect(issue)}
                    className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-surface-raised"
                  >
                    <span className="flex items-center gap-2 text-xs">
                      <span className="rounded bg-surface-raised px-1.5 py-0.5 font-mono font-semibold text-accent-fg">
                        {issue.key}
                      </span>
                      <span className="text-fg-muted">{issue.issue_type}</span>
                      <span className="text-fg-muted">·</span>
                      <span className="text-fg-muted">{issue.status}</span>
                    </span>
                    <span className="text-sm text-fg-secondary">{issue.summary}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
