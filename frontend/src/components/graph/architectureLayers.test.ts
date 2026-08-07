import { describe, expect, it } from "vitest";
import { layerForLabel, LAYER_ORDER, LAYER_LABELS } from "./architectureLayers";

describe("layerForLabel", () => {
  it.each([
    ["Controller", "service"],
    ["Service", "service"],
    ["Endpoint", "service"],
    ["FeignClient", "service"],
    ["Repository", "service"],
  ])("classifies %s as service", (label, expected) => {
    expect(layerForLabel(label)).toBe(expected);
  });

  it.each([
    ["DataTable", "data"],
    ["KafkaTopic", "data"],
  ])("classifies %s as data", (label, expected) => {
    expect(layerForLabel(label)).toBe(expected);
  });

  it.each([
    ["Module", "code"],
    ["Class", "code"],
    ["Function", "code"],
    ["Component", "code"],
    ["MavenDependency", "code"],
    ["PythonDependency", "code"],
  ])("classifies %s as code", (label, expected) => {
    expect(layerForLabel(label)).toBe(expected);
  });

  it("defaults an unknown/future label to code, never an unclassified bucket", () => {
    expect(layerForLabel("SomeFutureLanguageConstruct")).toBe("code");
  });

  it("LAYER_ORDER and LAYER_LABELS agree on exactly the three tiers", () => {
    expect(LAYER_ORDER).toEqual(["service", "code", "data"]);
    expect(Object.keys(LAYER_LABELS).sort()).toEqual(["code", "data", "service"]);
  });
});
