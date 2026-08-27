"""图增强实验运行的上下文；该路径代表预期保留的正式能力。"""

from __future__ import annotations

import json

from backend.app.agents.context_builder import MAX_CONTEXT_CHARS, ProjectContextBuilder
from backend.app.agents.contracts import AgentEvidence, ContextPacket
from backend.app.schemas.manifest import ProjectManifest
from backend.app.experiments.context import neutral_repo_map


class GraphAugmentedContextBuilder:
    """在中性 Repo Map 之上增加图摘要和有界依赖关系样本。"""

    def build(self, *, project_id: str, question: str, artifact: dict) -> ContextPacket:
        manifest = ProjectManifest.model_validate(artifact.get("manifest") or {})
        repo_map = ProjectContextBuilder._select_repo_map(neutral_repo_map(artifact), question)
        graph_context = self._compact_graph(artifact.get("dependency_graph") or {})
        evidence = [AgentEvidence(
            path=item.path,
            line=item.line,
            symbol=item.name,
            detail=f"{item.kind} entrypoint",
        ) for item in manifest.entrypoints[:20]]
        prompt_context = (
            "PROJECT_MANIFEST\n"
            + json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2)
            + "\n\nNEUTRAL_REPO_MAP\n"
            + repo_map
            + "\n\nDEPENDENCY_GRAPH_CONTEXT\n"
            + json.dumps(graph_context, ensure_ascii=False)
        )[:MAX_CONTEXT_CHARS]
        return ContextPacket(
            project_id=project_id,
            project_name=manifest.project_name,
            prompt_context=prompt_context,
            manifest=manifest.model_dump(),
            repo_map=repo_map,
            evidence=evidence,
        )

    @staticmethod
    def _compact_graph(graph: dict, max_nodes: int = 120, max_edges: int = 240) -> dict:
        """优先选取高连接节点及其关系，避免把完整图直接塞入上下文。"""
        nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
        edges = [edge for edge in graph.get("links", graph.get("edges", [])) if isinstance(edge, dict)]
        degree: dict[str, int] = {}
        for edge in edges:
            for endpoint in (edge.get("source"), edge.get("target")):
                endpoint = endpoint.get("id") if isinstance(endpoint, dict) else endpoint
                if endpoint is not None:
                    key = str(endpoint)
                    degree[key] = degree.get(key, 0) + 1
        selected_ids = {
            str(node.get("id"))
            for node in sorted(nodes, key=lambda item: -degree.get(str(item.get("id")), 0))[:max_nodes]
            if node.get("id") is not None
        }
        selected_edges = []
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            source = source.get("id") if isinstance(source, dict) else source
            target = target.get("id") if isinstance(target, dict) else target
            if str(source) in selected_ids and str(target) in selected_ids:
                selected_edges.append({
                    "source": source,
                    "target": target,
                    "relation": edge.get("relation") or edge.get("type"),
                })
                if len(selected_edges) >= max_edges:
                    break
        return {
            "nodes": [
                {"id": node.get("id"), "name": node.get("name"), "kind": node.get("kind"), "file": node.get("file")}
                for node in nodes if str(node.get("id")) in selected_ids
            ],
            "edges": selected_edges,
            "truncated": len(nodes) > max_nodes or len(edges) > max_edges,
        }