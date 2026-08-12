import { ProvenanceTag } from "../intelligence/ProvenanceTag";
import type { RefinementPlan, WorkItem } from "../../types/conversation";

function relatedByRelationship(
  plan: RefinementPlan,
  itemId: string,
  relationship: string,
  direction: "in" | "out",
): WorkItem[] {
  return plan.edges
    .filter(
      (e) =>
        e.relationship === relationship &&
        (direction === "out" ? e.source_id === itemId : e.target_id === itemId),
    )
    .map((e) => (direction === "out" ? e.target_id : e.source_id))
    .map((id) => plan.work_items.find((w) => w.id === id))
    .filter((w): w is WorkItem => Boolean(w));
}

function RelatedList({
  title,
  items,
  onSelect,
  tone,
}: {
  title: string;
  items: WorkItem[];
  onSelect: (id: string) => void;
  tone?: "danger" | "warning";
}) {
  return (
    <div>
      <p
        className={`text-xs font-semibold ${
          tone === "danger"
            ? "text-danger-fg"
            : tone === "warning"
              ? "text-warning-fg"
              : "text-fg-secondary"
        }`}
      >
        {title}
      </p>
      <ul className="mt-1 flex flex-col gap-1">
        {items.map((w) => (
          <li key={w.id}>
            <button
              type="button"
              onClick={() => onSelect(w.id)}
              className="text-left text-xs text-fg-muted transition-colors hover:text-fg-secondary hover:underline"
            >
              {w.id} — {w.title}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Section({ title, text }: { title: string; text: string }) {
  return (
    <div>
      <p className="text-xs font-semibold text-fg-secondary">{title}</p>
      <p className="mt-1 text-xs leading-relaxed text-fg-muted">{text}</p>
    </div>
  );
}

/**
 * Clicking a work item opens this — dependencies, blockers, downstream
 * relationships, evidence, and why, exactly the brief's own "ticket
 * intelligence panel" example. Every relationship listed here comes
 * straight off the same `plan.edges` the graph itself renders, so the
 * panel and the graph can never disagree about what blocks what.
 */
export function TicketIntelligencePanel({
  plan,
  item,
  onSelect,
}: {
  plan: RefinementPlan;
  item: WorkItem | null;
  onSelect: (id: string) => void;
}) {
  if (!item) {
    return (
      <div className="flex flex-col items-center justify-center rounded-xl border border-line-muted bg-surface p-6 text-center">
        <p className="text-sm text-fg-muted">
          Select a work item to see its dependencies, blockers, and evidence.
        </p>
      </div>
    );
  }

  const blockedBy = relatedByRelationship(plan, item.id, "blocks", "in");
  const blocks = relatedByRelationship(plan, item.id, "blocks", "out");
  const dependsOn = relatedByRelationship(plan, item.id, "depends_on", "out");
  const related = [
    ...relatedByRelationship(plan, item.id, "related", "out"),
    ...relatedByRelationship(plan, item.id, "related", "in"),
  ];
  const isCritical = plan.critical_paths.some((path) => path.includes(item.id));
  const isParallelizable = plan.parallelizable_ids.includes(item.id);

  return (
    <div className="flex flex-col gap-3 overflow-y-auto rounded-xl border border-line-muted bg-surface p-4">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-wide text-fg-muted">{item.id}</p>
        <h3 className="text-sm font-semibold text-fg">{item.title}</h3>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          <ProvenanceTag
            kind={item.status === "existing" ? "fact" : "recommendation"}
            label={item.status === "existing" ? "Existing" : "Proposed"}
          />
          {isCritical && <ProvenanceTag kind="derived" label="On critical path" />}
          {isParallelizable && <ProvenanceTag kind="derived" label="Parallelizable" />}
        </div>
      </div>

      {item.objective && <Section title="Objective" text={item.objective} />}
      {item.context && <Section title="Context" text={item.context} />}

      {item.acceptance_criteria.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-fg-secondary">Acceptance criteria</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-fg-muted">
            {item.acceptance_criteria.map((criterion, i) => (
              <li key={i}>{criterion}</li>
            ))}
          </ul>
        </div>
      )}

      {blockedBy.length > 0 && (
        <RelatedList title="Blocked by" items={blockedBy} onSelect={onSelect} tone="danger" />
      )}
      {blocks.length > 0 && <RelatedList title="Blocks" items={blocks} onSelect={onSelect} tone="warning" />}
      {dependsOn.length > 0 && <RelatedList title="Depends on" items={dependsOn} onSelect={onSelect} />}
      {related.length > 0 && <RelatedList title="Related" items={related} onSelect={onSelect} />}

      {item.related_systems.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-fg-secondary">Related systems</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {item.related_systems.map((s) => (
              <span
                key={s}
                className="rounded-md bg-neutral-bg px-1.5 py-0.5 text-[11px] text-fg-muted ring-1 ring-inset ring-line"
              >
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      {item.risks.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-fg-secondary">Risks</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-fg-muted">
            {item.risks.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      {item.evidence_note && (
        <div>
          <p className="text-xs font-semibold text-fg-secondary">Why</p>
          <p className="mt-1 text-xs leading-relaxed text-fg-muted">{item.evidence_note}</p>
          <div className="mt-1">
            <ProvenanceTag kind={item.provenance} />
          </div>
        </div>
      )}
    </div>
  );
}
