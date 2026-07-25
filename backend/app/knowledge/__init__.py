"""Knowledge Sources — multi-connection architecture.

A Knowledge Source is a *type* of system (GitHub, Jira, Confluence, Neo4j).
A Knowledge Connection is a configured *instance* connecting to a specific
deployment of that source (e.g. "Production GitHub", "Open Source GitHub").

Each connection specifies:
- A transport (REST, GraphQL, MCP, SDK, filesystem, database driver)
- Authentication appropriate to that transport
- Health tracked independently
- Capabilities it provides to the Tool Registry
"""
