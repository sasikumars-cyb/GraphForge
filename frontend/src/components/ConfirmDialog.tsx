import { useEffect, useRef, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** What will happen, in plain language. */
  body: ReactNode;
  /** The specific things being destroyed — "4 runs", "1 approved blueprint". */
  consequences?: string[];
  confirmLabel?: string;
  cancelLabel?: string;
  /** `danger` for irreversible destruction; `default` otherwise. */
  tone?: "danger" | "default";
  isSubmitting?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Confirmation for irreversible actions.
 *
 * Replaces `window.confirm`, which could not be styled, could not show
 * *what* was about to be destroyed, blocked the main thread, and reads as a
 * browser malfunction in an otherwise polished product. Deleting a workflow
 * takes its entire run and evidence history with it — that deserves to name
 * what is being lost, which a native confirm has no way to do.
 *
 * Rendered inline by the component that owns the action (no portal, no
 * global store) — the dialog is always short-lived and scoped to one button.
 */
export function ConfirmDialog({
  open,
  title,
  body,
  consequences = [],
  confirmLabel = "Delete",
  cancelLabel = "Cancel",
  tone = "danger",
  isSubmitting = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  // Focus lands on the *safe* choice, not the destructive one — a stray
  // Enter keypress after opening must not delete anything.
  useEffect(() => {
    if (open) cancelRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onCancel]);

  if (!open) return null;

  const confirmClass =
    tone === "danger"
      ? "bg-danger-solid text-danger-on-solid hover:brightness-110"
      : "bg-accent-solid text-accent-on-solid hover:brightness-110";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-canvas/80"
        onClick={onCancel}
        aria-hidden="true"
      />
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        className="relative w-full max-w-md rounded-xl border border-line bg-surface p-5 shadow-lg"
      >
        <div className="flex items-start gap-3">
          {tone === "danger" && (
            <span className="mt-0.5 rounded-md bg-danger-bg p-1.5 text-danger-fg ring-1 ring-inset ring-danger-line/30">
              <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            </span>
          )}
          <div className="min-w-0 flex-1">
            <h2 id="confirm-dialog-title" className="text-sm font-semibold text-fg">
              {title}
            </h2>
            <div className="mt-1 text-xs leading-relaxed text-fg-muted">{body}</div>
            {consequences.length > 0 && (
              <ul className="mt-3 flex flex-col gap-1 rounded-lg bg-surface-raised px-3 py-2">
                {consequences.map((c) => (
                  <li key={c} className="text-xs text-fg-secondary">
                    · {c}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            disabled={isSubmitting}
            className="focus-ring rounded-md px-3 py-1.5 text-xs font-medium text-fg-secondary ring-1 ring-inset ring-line transition-colors hover:bg-surface-hover disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={isSubmitting}
            className={`focus-ring rounded-md px-3.5 py-1.5 text-xs font-semibold shadow-xs transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${confirmClass}`}
          >
            {isSubmitting ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
