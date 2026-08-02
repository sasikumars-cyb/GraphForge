import { describe, expect, it } from "vitest";
import {
  buildStructuralDependencyEdges,
  mergeGraphs,
  mergeRepositoryDependencyEdges,
} from "./mergeGraphs";
import type { Graph, GraphEdge } from "../../types/graph";

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

describe("buildStructuralDependencyEdges", () => {
  it("converts repository-node edges into overview edges, stripping the ':repository' suffix", () => {
    const edges: GraphEdge[] = [
      {
        source_id: "engine-id:repository",
        target_id: "notes-id:repository",
        type: "CALLS_SERVICE",
        properties: {},
      },
    ];

    const result = buildStructuralDependencyEdges(edges);

    expect(result).toEqual([{ source: "engine-id", target: "notes-id", topics: ["CALLS_SERVICE"] }]);
  });

  it("merges multiple relationship types between the same repository pair into one edge", () => {
    const edges: GraphEdge[] = [
      {
        source_id: "engine-id:repository",
        target_id: "notes-id:repository",
        type: "CALLS_SERVICE",
        properties: {},
      },
      {
        source_id: "engine-id:repository",
        target_id: "notes-id:repository",
        type: "SHARES_TOPIC",
        properties: {},
      },
    ];

    const result = buildStructuralDependencyEdges(edges);

    expect(result).toHaveLength(1);
    expect(result[0].topics).toEqual(["CALLS_SERVICE", "SHARES_TOPIC"]);
  });

  it("ignores an edge whose endpoints aren't repository nodes", () => {
    const edges: GraphEdge[] = [
      { source_id: "engine-id:svc:Foo", target_id: "notes-id:repository", type: "CALLS", properties: {} },
    ];

    expect(buildStructuralDependencyEdges(edges)).toEqual([]);
  });
});

describe("mergeRepositoryDependencyEdges", () => {
  it("combines edge lists, merging labels for the same repository pair", () => {
    const kafkaEdges = [{ source: "a", target: "b", topics: ["order.created"] }];
    const structuralEdges = [{ source: "a", target: "b", topics: ["CALLS_SERVICE"] }];

    const result = mergeRepositoryDependencyEdges(kafkaEdges, structuralEdges);

    expect(result).toEqual([{ source: "a", target: "b", topics: ["order.created", "CALLS_SERVICE"] }]);
  });

  it("keeps distinct repository pairs as separate edges", () => {
    const kafkaEdges = [{ source: "a", target: "b", topics: ["order.created"] }];
    const structuralEdges = [{ source: "c", target: "d", topics: ["CALLS_SERVICE"] }];

    const result = mergeRepositoryDependencyEdges(kafkaEdges, structuralEdges);

    expect(result).toHaveLength(2);
  });
});
