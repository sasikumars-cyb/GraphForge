import { describe, expect, it } from "vitest";
import { legendLabelsFor, primaryLabel, resolveLabelColors } from "./graphLabels";

describe("primaryLabel", () => {
  it("prefers the specific label over the structural ones", () => {
    // Regression test: labels were matched against a Java-only colour map,
    // so a Python function — which arrives carrying "Component" too —
    // resolved to "Component" and drew in the same generic grey as every
    // other Python node. These are the real label sets from the indexer.
    expect(primaryLabel(["Component", "Function", "GraphNode"])).toBe("Function");
    expect(primaryLabel(["Component", "GraphNode", "Module"])).toBe("Module");
    expect(primaryLabel(["Class", "Component", "GraphNode"])).toBe("Class");
    expect(primaryLabel(["GraphNode", "PythonDependency"])).toBe("PythonDependency");
    expect(primaryLabel(["GraphNode", "Repository"])).toBe("Repository");
  });

  it("still resolves Java/Spring labels", () => {
    expect(primaryLabel(["Component", "Controller", "GraphNode"])).toBe("Controller");
    expect(primaryLabel(["GraphNode", "KafkaTopic"])).toBe("KafkaTopic");
  });

  it("surfaces a label it has no colour for rather than hiding it as Component", () => {
    // A language added later should read under its own name; it just gets
    // the fallback colour.
    expect(primaryLabel(["Component", "GoPackage", "GraphNode"])).toBe("GoPackage");
    expect(resolveLabelColors("GoPackage")).toEqual(resolveLabelColors("Component"));
  });

  it("falls back to Component only when nothing else is available", () => {
    expect(primaryLabel(["Component", "GraphNode"])).toBe("Component");
    expect(primaryLabel([])).toBe("Component");
  });
});

describe("legendLabelsFor", () => {
  it("lists only labels present in the graph, most frequent first", () => {
    // The legend previously advertised six Java/Spring types regardless of
    // what was indexed, and omitted the types actually present.
    const nodes = [
      { labels: ["Component", "Function", "GraphNode"] },
      { labels: ["Component", "Function", "GraphNode"] },
      { labels: ["Component", "Function", "GraphNode"] },
      { labels: ["Component", "GraphNode", "Module"] },
      { labels: ["Component", "GraphNode", "Module"] },
      { labels: ["Class", "Component", "GraphNode"] },
    ];

    expect(legendLabelsFor(nodes)).toEqual(["Function", "Module", "Class"]);
    expect(legendLabelsFor(nodes)).not.toContain("Controller");
    expect(legendLabelsFor(nodes)).not.toContain("MavenDependency");
  });

  it("returns nothing for an empty graph", () => {
    expect(legendLabelsFor([])).toEqual([]);
  });
});
