import { useState } from "react";
import { Check, Folder, Pencil, X } from "lucide-react";

/** ADR 0023 §2 — the UI half of `PATCH /repositories/{id}` (`domain`).
 * The backend endpoint and the landing page's own "assign one from a
 * repository's own detail view" copy (`ArchitectureLanding.tsx`) both
 * predate this: this is that detail view, the only place a repository's
 * domain can actually be set. Grouping stays manual by design (see the
 * ADR's "explicitly out of scope: automatic domain inference") — this
 * is the whole surface for it. */
export function DomainEditor({
  domain,
  onSave,
  isSaving,
}: {
  domain: string | null;
  onSave: (domain: string | null) => Promise<void>;
  isSaving: boolean;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(domain ?? "");
  const [error, setError] = useState<string | null>(null);

  function startEditing() {
    setDraft(domain ?? "");
    setError(null);
    setIsEditing(true);
  }

  async function handleSave() {
    const trimmed = draft.trim();
    setError(null);
    try {
      await onSave(trimmed === "" ? null : trimmed);
      setIsEditing(false);
    } catch {
      // Matches this page's own "surface, don't swallow" convention for
      // failed mutations (see ArchitecturePage's summary-load error card).
      setError("Couldn't save. Try again.");
    }
  }

  if (!isEditing) {
    return (
      <button
        type="button"
        onClick={startEditing}
        aria-label={domain ? `Domain: ${domain}. Click to edit.` : "Assign domain"}
        className="group flex items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-xs text-fg-secondary hover:border-line-strong hover:bg-surface-raised"
      >
        <Folder className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />
        {domain ?? "Assign domain"}
        <Pencil
          className="h-3 w-3 text-fg-subtle opacity-0 group-hover:opacity-100"
          aria-hidden="true"
        />
      </button>
    );
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-1.5">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void handleSave();
            if (e.key === "Escape") setIsEditing(false);
          }}
          placeholder="Domain name"
          aria-label="Domain name"
          autoFocus
          className="w-32 rounded-md border border-line-strong bg-canvas px-2 py-1 text-xs text-fg placeholder-fg-subtle focus:outline-none focus:ring-1 focus:ring-info-fg"
        />
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={isSaving}
          aria-label="Save domain"
          className="rounded p-1 text-success-fg hover:bg-success-bg disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Check className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
        <button
          type="button"
          onClick={() => setIsEditing(false)}
          disabled={isSaving}
          aria-label="Cancel"
          className="rounded p-1 text-fg-muted hover:bg-surface-raised"
        >
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>
      {error && <span className="text-xs text-danger-fg">{error}</span>}
    </div>
  );
}
