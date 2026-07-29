/**
 * VerificationWarnings — surfaces the claims an agent could not back up.
 *
 * Every planning-family agent (planning, development, testing, and the
 * engineering review that aggregates them) already computes a deterministic
 * `verification_warnings` list: claims — file paths, component names,
 * repository names — that appear in its output but not anywhere in the
 * evidence its own tool calls returned. That check was doing real work and
 * catching real fabrications, and then nothing rendered it. The Evidence tab
 * even told the reader to "see verification_warnings", a field the UI did
 * not have. A plan could show a green `verified` badge next to a file that
 * exists in no indexed repository, and nothing on screen contradicted it.
 *
 * So this renders expanded by default and sits above the result body. A
 * caveat placed below the content it qualifies, or behind a collapsed
 * toggle, is one the reader reaches only after already believing the plan.
 */

interface VerificationWarningsProps {
  warnings?: string[];
  /** What produced these — "plan", "implementation plan", "test plan". */
  subject?: string;
}

export function VerificationWarnings({
  warnings,
  subject = "plan",
}: VerificationWarningsProps) {
  if (!warnings || warnings.length === 0) return null;

  const count = warnings.length;

  return (
    <section
      className="rounded-lg border border-warning-line/40 bg-warning-bg p-4"
      aria-labelledby="verification-warnings-heading"
    >
      <div className="flex items-start gap-3">
        <span aria-hidden="true" className="mt-0.5 text-lg leading-none text-warning-fg">
          ⚠
        </span>
        <div className="min-w-0 flex-1">
          <h3
            id="verification-warnings-heading"
            className="text-sm font-semibold text-warning-fg"
          >
            {count} unverified {count === 1 ? "claim" : "claims"} in this {subject}
          </h3>
          <p className="mt-1 text-xs text-warning-fg">
            These were checked against the indexed code this run actually
            retrieved, and could not be matched. Treat them as unconfirmed —
            the rest of the {subject} is unaffected.
          </p>

          <ul className="mt-3 flex flex-col gap-1.5">
            {warnings.map((warning) => (
              <li
                key={warning}
                className="flex gap-2 text-xs leading-relaxed text-warning-fg"
              >
                <span aria-hidden="true" className="select-none text-warning-fg">
                  •
                </span>
                <span className="min-w-0 break-words">{warning}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
