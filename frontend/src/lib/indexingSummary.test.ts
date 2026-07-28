import { describe, expect, it } from "vitest";
import { summarizeRepositoryCounts } from "./indexingSummary";

describe("summarizeRepositoryCounts", () => {
  it("counts a Python repository's modules, classes and functions as components", () => {
    // Regression test: this page summed a hardcoded Java field list
    // (controllers/services/feign_clients, maven_dependencies, kafka_*), so
    // every Python repository rendered "Components: 0 · External
    // dependencies: 0" while its graph held hundreds of nodes. These are the
    // real counts from ds-databricks-soco-apc-c2m-rcs-dataingest.
    expect(
      summarizeRepositoryCounts({
        controllers: 0,
        endpoints: 0,
        services: 0,
        feign_clients: 0,
        kafka_producers: 0,
        kafka_consumers: 0,
        maven_dependencies: 0,
        python_modules: 85,
        python_classes: 58,
        python_functions: 433,
        python_dependencies: 18,
      }),
    ).toEqual({
      components: 576, // 85 + 58 + 433 — matches the graph's 576 Component nodes
      externalDependencies: 18, // maven 0 + python 18
      messagingTouchpoints: 0,
    });
  });

  it("still counts a Java/Spring repository the way it always did", () => {
    expect(
      summarizeRepositoryCounts({
        controllers: 3,
        endpoints: 12,
        services: 5,
        feign_clients: 2,
        kafka_producers: 1,
        kafka_consumers: 4,
        maven_dependencies: 30,
      }),
    ).toEqual({
      components: 10, // 3 + 5 + 2 — endpoints excluded, they belong to controllers
      externalDependencies: 30,
      messagingTouchpoints: 5,
    });
  });

  it("counts an unseen language's keys without needing to know about it", () => {
    // The whole point of classifying by key shape: a parser added later is
    // counted correctly with no change to this module.
    expect(
      summarizeRepositoryCounts({ go_packages: 7, go_functions: 40, go_dependencies: 12 }),
    ).toEqual({ components: 47, externalDependencies: 12, messagingTouchpoints: undefined });
  });

  it("reports undefined, not zero, for categories the parser never measured", () => {
    // "—" ("not measured") and "0" ("measured, found none") are different
    // claims; only the second one is safe to assert.
    expect(summarizeRepositoryCounts({ python_modules: 4 })).toEqual({
      components: 4,
      externalDependencies: undefined,
      messagingTouchpoints: undefined,
    });
  });

  it("handles a missing or empty summary", () => {
    const empty = {
      components: undefined,
      externalDependencies: undefined,
      messagingTouchpoints: undefined,
    };
    expect(summarizeRepositoryCounts(null)).toEqual(empty);
    expect(summarizeRepositoryCounts(undefined)).toEqual(empty);
    expect(summarizeRepositoryCounts({})).toEqual(empty);
  });
});
