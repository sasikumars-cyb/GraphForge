/**
 * Rolls an indexing job's `result_summary` up into the three headline
 * counts the Architecture overview shows per repository.
 *
 * Why this is keyed by shape rather than by name
 * ----------------------------------------------
 * `result_summary` is an open `Record<string, number>` written by whichever
 * language parser ran (see backend `app/indexer/services/indexing_service.py`
 * `_summarize`). The Java/Spring parser emits `controllers`, `services`,
 * `feign_clients`, `maven_dependencies`, `kafka_*`; the Python parser emits
 * `python_modules`, `python_classes`, `python_functions`,
 * `python_dependencies`.
 *
 * This page previously summed a hardcoded Java list, so every Python
 * repository reported `Components: 0 · External dependencies: 0 ·
 * Messaging touchpoints: 0` while its graph held hundreds of nodes — the
 * overview flatly contradicted the graph one click away.
 *
 * Classifying by key *shape* instead means a parser added later (Go, C#,
 * TypeScript) is counted correctly with no change here, as long as it keeps
 * the existing naming convention.
 */

/** Third-party/package dependencies: `maven_dependencies`, `python_dependencies`, … */
function isDependencyKey(key: string): boolean {
  return key === "dependencies" || key.endsWith("_dependencies");
}

/** Messaging touchpoints: `kafka_producers`, `kafka_consumers`, topics, … */
function isMessagingKey(key: string): boolean {
  return /(^kafka_)|topic|producer|consumer/.test(key);
}

/**
 * Counts that describe something *already counted* by another key, and so
 * must not be added again. `endpoints` is the sum of endpoints across all
 * controllers — a controller and its endpoints are one code unit for the
 * purposes of a headline "components" number, and including both was
 * excluded from the original Java calculation too.
 */
const NESTED_COUNT_KEYS = new Set(["endpoints"]);

export interface RepositoryCounts {
  components: number | undefined;
  externalDependencies: number | undefined;
  messagingTouchpoints: number | undefined;
}

/**
 * `undefined` (rather than 0) is returned for a category when the summary
 * carries no key for it at all — the card renders that as "—", which is
 * honestly "not measured", not the false claim "measured, found none".
 * A repository that genuinely has zero dependencies still reports 0,
 * because its parser emitted the key.
 */
export function summarizeRepositoryCounts(
  summary: Record<string, number> | null | undefined,
): RepositoryCounts {
  if (!summary) {
    return { components: undefined, externalDependencies: undefined, messagingTouchpoints: undefined };
  }

  let components: number | undefined;
  let externalDependencies: number | undefined;
  let messagingTouchpoints: number | undefined;

  const add = (current: number | undefined, value: number) => (current ?? 0) + value;

  for (const [key, rawValue] of Object.entries(summary)) {
    if (typeof rawValue !== "number" || !Number.isFinite(rawValue)) continue;
    if (NESTED_COUNT_KEYS.has(key)) continue;

    if (isDependencyKey(key)) {
      externalDependencies = add(externalDependencies, rawValue);
    } else if (isMessagingKey(key)) {
      messagingTouchpoints = add(messagingTouchpoints, rawValue);
    } else {
      components = add(components, rawValue);
    }
  }

  return { components, externalDependencies, messagingTouchpoints };
}
