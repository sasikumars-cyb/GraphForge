import type { CrossRepositoryLink, Graph, GraphEdge, GraphNode } from "../../types/graph";

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

export interface RepositoryDependencyEdge {
  source: string;
  target: string;
  topics: string[];
}

/**
 * Aggregates repository-to-repository dependency edges for the overview
 * graph from a flat list of cross-repository links (fetched in a single
 * request via `getAllCrossRepositoryLinks`) - no new backend logic, no
 * per-topic node detail. Each link's own `relationship` field is always the
 * *peer's* true PRODUCES_TO/CONSUMES_FROM edge (see the backend query), so
 * collecting every repo's own relation to every topic just means walking
 * the list once - direction then falls out as producer -> consumer.
 */
export function buildRepositoryDependencyEdges(
  links: CrossRepositoryLink[],
): RepositoryDependencyEdge[] {
  const relationByRepoTopic = new Map<string, "PRODUCES_TO" | "CONSUMES_FROM">();
  for (const link of links) {
    if (link.relationship === "PRODUCES_TO" || link.relationship === "CONSUMES_FROM") {
      relationByRepoTopic.set(`${link.repository_id}:${link.topic_name}`, link.relationship);
    }
  }

  const topicsByName = new Map<string, { repoId: string; relationship: string }[]>();
  for (const [repoTopicKey, relationship] of relationByRepoTopic) {
    const separatorIndex = repoTopicKey.indexOf(":");
    const repoId = repoTopicKey.slice(0, separatorIndex);
    const topicName = repoTopicKey.slice(separatorIndex + 1);
    const entries = topicsByName.get(topicName) ?? [];
    entries.push({ repoId, relationship });
    topicsByName.set(topicName, entries);
  }

  const edgeByPair = new Map<string, RepositoryDependencyEdge>();
  for (const [topicName, entries] of topicsByName) {
    const producers = entries.filter((e) => e.relationship === "PRODUCES_TO");
    const consumers = entries.filter((e) => e.relationship === "CONSUMES_FROM");
    for (const producer of producers) {
      for (const consumer of consumers) {
        if (producer.repoId === consumer.repoId) continue;
        const key = `${producer.repoId}->${consumer.repoId}`;
        const edge = edgeByPair.get(key) ?? {
          source: producer.repoId,
          target: consumer.repoId,
          topics: [],
        };
        if (!edge.topics.includes(topicName)) {
          edge.topics.push(topicName);
        }
        edgeByPair.set(key, edge);
      }
    }
  }

  return Array.from(edgeByPair.values());
}

/**
 * Adds a single repository's cross-repository neighbors (fetched via the
 * lightweight `/cross-repository-links` endpoint) onto that repository's
 * own graph - one small peer node + one edge per link. This never requires
 * fetching any other repository's full graph: the "own" graph already
 * contains the shared `KafkaTopic` node (matched here by `name`), so only
 * the remote component needs to be synthesized.
 */
export function mergeCrossRepositoryLinks(ownGraph: Graph, links: CrossRepositoryLink[]): Graph {
  const topicNodeIdByName = new Map(
    ownGraph.nodes
      .filter((n) => n.labels.includes("KafkaTopic"))
      .map((n) => [String(n.properties.name ?? ""), n.id]),
  );

  const peerNodes: GraphNode[] = [];
  const peerEdges: GraphEdge[] = [];
  const seenPeerIds = new Set<string>();

  for (const link of links) {
    const topicNodeId = topicNodeIdByName.get(link.topic_name);
    if (!topicNodeId) {
      continue;
    }
    if (!seenPeerIds.has(link.component_id)) {
      seenPeerIds.add(link.component_id);
      peerNodes.push({
        id: link.component_id,
        labels: ["Component"],
        properties: { name: link.component_name, repository_id: link.repository_id },
      });
    }
    peerEdges.push({
      source_id: link.component_id,
      target_id: topicNodeId,
      type: link.relationship,
      properties: {},
    });
  }

  return {
    nodes: [...ownGraph.nodes, ...peerNodes],
    edges: [...ownGraph.edges, ...peerEdges],
  };
}
