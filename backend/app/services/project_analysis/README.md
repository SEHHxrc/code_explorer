# Project analysis application module

This package owns the `analyze project` use case rather than HTTP details.

- `contracts.py`: application input/output objects.
- `service.py`: import, sanitize, analyze, derive, persist, and compensate workflow.
- `graph_exchange.py`: raw analyzer graph to versioned public graph DTO.
- `repository.py`: project database transaction boundary.
- `artifact_repository.py`: analysis artifact persistence adapter.
- `exceptions.py`: stable public failures mapped by the API layer.

The stored artifact intentionally keeps the analyzer's raw dependency graph for manifest and agent evidence. Only the HTTP response uses the normalized exchange graph.