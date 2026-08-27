"""Manifest、入口点和静态符号发现工具。"""

from backend.app.agents.contracts import AgentEvidence, ToolResult
from backend.app.agents.tools.arguments import EmptyArguments, SearchArguments
from backend.app.agents.tools.base import AgentTool, ToolContext


class ManifestTool(AgentTool):
    name = "get_project_manifest"
    description = "Return the deterministic project manifest, frameworks, commands and graph summary."
    arguments_model = EmptyArguments

    async def execute(self, context: ToolContext, arguments: EmptyArguments) -> ToolResult:
        return ToolResult(content=context.artifact.get("manifest") or {})


class EntrypointsTool(AgentTool):
    name = "list_entrypoints"
    description = "List detected application, CLI, worker and script entrypoints with file evidence."
    arguments_model = EmptyArguments

    async def execute(self, context: ToolContext, arguments: EmptyArguments) -> ToolResult:
        items = (context.artifact.get("manifest") or {}).get("entrypoints") or []
        evidence = [AgentEvidence(
            path=item.get("path", ""), line=item.get("line"), symbol=item.get("name"),
            detail=f"{item.get('kind', 'entrypoint')} entrypoint",
        ) for item in items if item.get("path")]
        return ToolResult(content=items[:50], evidence=evidence[:50], truncated=len(items) > 50)


class SearchSymbolsTool(AgentTool):
    name = "search_symbols"
    description = "Search analyzed classes, functions, methods and constants by name or qualified name."
    arguments_model = SearchArguments

    async def execute(self, context: ToolContext, arguments: SearchArguments) -> ToolResult:
        query = arguments.query.casefold()
        hits = []
        evidence = []
        for row in context.evidence_index.symbols:
            symbol = row.symbol
            name = str(symbol.get("name") or "")
            fqn = str(symbol.get("fully_qualified_name") or name)
            if query not in name.casefold() and query not in fqn.casefold():
                continue
            line = (((symbol.get("extent_utf16") or {}).get("start") or {}).get("line_number"))
            hits.append({"path": row.path, "line": line, "name": name, "fqn": fqn, "kind": symbol.get("kind")})
            evidence.append(AgentEvidence(path=row.path, line=line, symbol=fqn, detail="symbol match"))
            if len(hits) >= arguments.limit:
                return ToolResult(content=hits, evidence=evidence, truncated=True)
        return ToolResult(content=hits, evidence=evidence)