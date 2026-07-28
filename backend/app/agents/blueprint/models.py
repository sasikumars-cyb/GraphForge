"""Blueprint data models — the wire contract between agents and the frontend renderer.

Agents produce BlueprintArtifacts containing Diagrams. The frontend renders
each Diagram using the appropriate renderer for its DiagramType.

Design rules:
- No UI concerns here. Nodes have `type` strings; the frontend maps those to
  colors and shapes.
- All fields serialize cleanly to JSON (model_dump()). The result is stored
  inside AgentStep.result["blueprint"] in the existing JSONB column — no
  migration needed.
- Adding a new DiagramType requires: register the enum value here, add a
  factory method in factory.py, add a renderer case in the frontend
  BlueprintRenderer. Nothing else changes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class DiagramType(StrEnum):
    FLOW = "flow"  # ordered step-by-step flow
    ARCHITECTURE = "architecture"  # component relationships
    SEQUENCE = "sequence"  # time-ordered interactions
    DEPENDENCY = "dependency"  # dependency graph (repo/component/topic)
    TIMELINE = "timeline"  # phase-based horizontal timeline
    ER = "er"  # entity-relationship
    RISK_HEATMAP = "risk_heatmap"  # risk severity grid


class DiagramNode(BaseModel):
    """A node in a diagram.

    `type` is a semantic tag the frontend uses to pick styling:
      default | input | output | component | topic | risk | phase | entity
    """

    id: str
    label: str
    type: str = "default"
    properties: dict[str, Any] = Field(default_factory=dict[str, Any])
    metadata: dict[str, Any] = Field(default_factory=dict[str, Any])


class DiagramEdge(BaseModel):
    """A directed edge between two nodes."""

    id: str
    source: str
    target: str
    label: str = ""
    type: str = "default"  # default | dependency | data_flow | call | risk
    metadata: dict[str, Any] = Field(default_factory=dict[str, Any])


class DiagramLayout(BaseModel):
    direction: str = "LR"  # LR | TB | RL | BT
    algorithm: str = "dagre"  # dagre | manual | grid


class Diagram(BaseModel):
    id: str
    title: str
    description: str = ""
    type: DiagramType
    nodes: list[DiagramNode] = Field(default_factory=list)
    edges: list[DiagramEdge] = Field(default_factory=list)
    layout: DiagramLayout = Field(default_factory=DiagramLayout)
    metadata: dict[str, Any] = Field(default_factory=dict[str, Any])
    evidence: list[str] = Field(default_factory=list)
    confidence: float | None = None


class BlueprintArtifact(BaseModel):
    """Collection of Diagrams produced by one agent stage.

    Stored as `result["blueprint"]` inside AgentStep.result. Agents that
    don't generate a blueprint yet leave result["blueprint"] absent or null.
    """

    agent_id: str
    stage: str
    diagrams: list[Diagram] = Field(default_factory=list)
    version: str = "1.0"
