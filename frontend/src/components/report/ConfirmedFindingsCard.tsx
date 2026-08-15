import { CircleCheck } from "lucide-react";
import type { FindingsSectionVM } from "../../lib/api/reports";
import { Card } from "../Card";

/** [ Confirmed Findings ] — what the investigation actually established,
 * placed before Hypotheses so a reader meets proof before speculation.
 *
 * The backend only ever puts verified evidence here; a supported
 * hypothesis, however confident, is rendered by `HypothesesSection`
 * instead. An empty list is meaningful, not a rendering gap — it says
 * nothing was independently verified, which is exactly what a reader
 * needs before weighing the hypotheses below. */
export function ConfirmedFindingsCard({ findings }: { findings: FindingsSectionVM }) {
  const total = findings.items.length + findings.truncated_count;

  if (findings.items.length === 0) {
    return (
      <Card title="Confirmed findings">
        <p className="text-xs leading-relaxed text-fg-muted">
          {findings.availability.reason ??
            "Nothing was independently verified in this investigation."}{" "}
          Everything below this section is reasoning, not confirmation.
        </p>
      </Card>
    );
  }

  return (
    <Card
      title="Confirmed findings"
      description={`${total} verified — what the evidence actually proves`}
    >
      <ul className="flex flex-col divide-y divide-line-muted">
        {findings.items.map((finding, i) => (
          <li key={i} className="flex items-start gap-2 py-2.5 first:pt-0 last:pb-0">
            <CircleCheck
              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success-fg"
              aria-hidden="true"
            />
            {/* `break-words` throughout: a finding's statement is a real
                subject from the fact ledger, which is routinely a full
                repository-qualified path with no spaces to wrap at
                ("Uplight-Inc/ds-.../soco_ingest/src/transforms/export/
                interval_usage.py"). Without it that one token widened the
                whole report and gave the panel a horizontal scrollbar —
                caught by rendering a real report, since fixture data never
                had a path that long. Same for `source_field`. */}
            <div className="min-w-0">
              <p className="break-words text-sm leading-relaxed text-fg">{finding.statement}</p>
              {finding.evidence_summary && (
                <p className="mt-0.5 break-words text-[11px] leading-relaxed text-fg-muted">
                  {finding.evidence_summary}
                </p>
              )}
              <p className="mt-0.5 break-words text-[10px] text-fg-subtle">
                {finding.source_stage} · {finding.source_field}
              </p>
            </div>
          </li>
        ))}
      </ul>
      {findings.truncated_count > 0 && (
        <p className="mt-3 text-[11px] text-fg-subtle">
          + {findings.truncated_count} more verified finding
          {findings.truncated_count === 1 ? "" : "s"}, kept out of this view for scale.
        </p>
      )}
    </Card>
  );
}
