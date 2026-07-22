import { describe, expect, it } from "vitest";
import { mergeGraphs } from "./mergeGraphs";
import type { Graph } from "../../types/graph";

const orderServiceGraph: Graph = {
  nodes: [
    {
      id: "order:svc:OrderService",
      labels: ["GraphNode", "Component", "Service"],
      properties: { name: "OrderService" },
    },
    {
      id: "order:kafka-topic:order.created",
      labels: ["GraphNode", "KafkaTopic"],
      properties: { name: "order.created" },
    },
  ],
  edges: [
    {
      source_id: "order:svc:OrderService",
      target_id: "order:kafka-topic:order.created",
      type: "PRODUCES_TO",
      properties: {},
    },
  ],
};

const inventoryServiceGraph: Graph = {
  nodes: [
    {
      id: "inv:svc:InventoryService",
      labels: ["GraphNode", "Component", "Service"],
      properties: { name: "InventoryService" },
    },
    {
      id: "inv:kafka-topic:order.created",
      labels: ["GraphNode", "KafkaTopic"],
      properties: { name: "order.created" },
    },
  ],
  edges: [
    {
      source_id: "inv:svc:InventoryService",
      target_id: "inv:kafka-topic:order.created",
      type: "CONSUMES_FROM",
      properties: {},
    },
  ],
};

describe("mergeGraphs", () => {
  it("keeps all nodes when no topics are shared", () => {
    const paymentGraph: Graph = {
      nodes: [
        {
          id: "pay:svc:PaymentService",
          labels: ["GraphNode", "Component", "Service"],
          properties: { name: "PaymentService" },
        },
      ],
      edges: [],
    };

    const merged = mergeGraphs([orderServiceGraph, paymentGraph]);

    expect(merged.nodes).toHaveLength(3);
    expect(merged.edges).toHaveLength(1);
  });

  it("deduplicates KafkaTopic nodes that share the same name property across repositories", () => {
    const merged = mergeGraphs([orderServiceGraph, inventoryServiceGraph]);

    const topicNodes = merged.nodes.filter((n) => n.labels.includes("KafkaTopic"));
    expect(topicNodes).toHaveLength(1);
    expect(topicNodes[0].id).toBe("order:kafka-topic:order.created");
  });

  it("remaps edges from the deduplicated repo's topic node onto the canonical node", () => {
    const merged = mergeGraphs([orderServiceGraph, inventoryServiceGraph]);

    const consumeEdge = merged.edges.find((e) => e.type === "CONSUMES_FROM");
    expect(consumeEdge).toBeDefined();
    expect(consumeEdge!.target_id).toBe("order:kafka-topic:order.created");

    const produceEdge = merged.edges.find((e) => e.type === "PRODUCES_TO");
    expect(produceEdge!.target_id).toBe("order:kafka-topic:order.created");
  });

  it("does not duplicate an edge that appears identically in more than one input graph", () => {
    const merged = mergeGraphs([orderServiceGraph, orderServiceGraph]);

    expect(merged.edges).toHaveLength(1);
  });

  it("returns an empty graph for an empty input list", () => {
    expect(mergeGraphs([])).toEqual({ nodes: [], edges: [] });
  });
});
