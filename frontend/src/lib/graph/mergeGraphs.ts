import type { Graph, GraphEdge, GraphNode } from "../../types/graph";

/**
 * Merges one graph per repository into a single graph, deduplicating
 * `KafkaTopic` nodes by their `name` property.
 *
 * This mirrors the backend's own cross-repository semantics exactly: two
 * repositories that each independently indexed a producer/consumer for the
 * same topic name get two separate `KafkaTopic` nodes (namespaced by
 * repository id) with no edge between them - there's no merged-graph
 * endpoint, so this dedup has to happen client-side to make the shared
 * topic visually become the single connection point between services.
 */
export function mergeGraphs(graphs: Graph[]): Graph {
  const nodesById = new Map<string, GraphNode>();
  const canonicalIdByTopicName = new Map<string, string>();
  const idRemap = new Map<string, string>();

  for (const graph of graphs) {
    for (const node of graph.nodes) {
      if (node.labels.includes("KafkaTopic")) {
        const topicName = String(node.properties.name ?? node.id);
        const canonicalId = canonicalIdByTopicName.get(topicName);
        if (canonicalId !== undefined) {
          idRemap.set(node.id, canonicalId);
          continue;
        }
        canonicalIdByTopicName.set(topicName, node.id);
      }
      idRemap.set(node.id, node.id);
      nodesById.set(node.id, node);
    }
  }

  const edges: GraphEdge[] = [];
  const seenEdgeKeys = new Set<string>();

  for (const graph of graphs) {
    for (const edge of graph.edges) {
      const sourceId = idRemap.get(edge.source_id) ?? edge.source_id;
      const targetId = idRemap.get(edge.target_id) ?? edge.target_id;
      const key = `${sourceId}->${targetId}:${edge.type}`;
      if (seenEdgeKeys.has(key)) {
        continue;
      }
      seenEdgeKeys.add(key);
      edges.push({ ...edge, source_id: sourceId, target_id: targetId });
    }
  }

  return { nodes: Array.from(nodesById.values()), edges };
}
