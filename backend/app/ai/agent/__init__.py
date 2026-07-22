"""The Change Investigation Agent.

Given a pull request, autonomously decides which deterministic evidence
(graph traversal, cross-repository metadata, indexing summary, the raw
diff, git history) is actually worth gathering before generating the
final AI-enriched impact analysis - instead of always gathering
everything upfront, the way `AIAnalysisService` does.

The deterministic graph remains the sole source of truth throughout: this
package never invents a dependency, repository, or relationship - it only
decides *whether* to look at evidence that already exists. See
`investigation_agent.InvestigationAgent` for the entry point.
"""

from app.ai.agent.investigation_agent import InvestigationAgent, InvestigationResult

__all__ = ["InvestigationAgent", "InvestigationResult"]
