"""The architecture graph domain: nodes and edges discovered by
`app.indexer`, persisted in and queried from Neo4j.

`interfaces.IGraphRepository` is the contract; `neo4j_repository.py` is the
(real, working) implementation. `models.py` defines the generic
node/edge/payload shapes both the indexer and the API routers speak.
"""
