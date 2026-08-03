"""The Frontier Agent Framework — the shared base every Engineering
Intelligence Agent inherits. See `base_frontier_agent.BaseFrontierAgent`
for the run loop and the three hooks a concrete agent implements.

Reuses the frozen `app.agents._contract` types (`AgentContext`,
`AgentOutput`, `IAgent`) and the Engineering Intelligence Service Layer
(`app.services.engineering_intelligence`) unmodified — see each module's
docstring for what it owns and why it doesn't duplicate existing logic.
"""
