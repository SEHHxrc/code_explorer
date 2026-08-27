# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from backend.app.schemas.manifest import ProjectManifest


def build_repo_map(
    manifest: ProjectManifest,
    file_symbols: dict[str, list[dict[str, Any]]],
    max_symbols: int = 160,
) -> str:
    """Create a compact, deterministic LLM context from the analyzer output."""
    central_ids = {
        item["id"] for item in manifest.graph_summary.get("central_nodes", [])
        if isinstance(item, dict) and item.get("id")
    }
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for path, symbols in file_symbols.items():
        for symbol in symbols:
            fqn = symbol.get("fully_qualified_name") or symbol.get("name") or "unknown"
            score = 10 if fqn in central_ids else 0
            score += 3 if symbol.get("kind") in {"class", "function", "method", "interface"} else 0
            ranked.append((score, path, symbol))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2].get("name", "")))

    lines = [
        f"PROJECT {manifest.project_name}",
        f"LANGUAGES: {', '.join(manifest.languages) or 'unknown'}",
        f"FRAMEWORKS: {', '.join(manifest.frameworks) or 'unknown'}",
        "ENTRYPOINTS:",
    ]
    for entry in manifest.entrypoints:
        location = f"{entry.path}:{entry.line}" if entry.line else entry.path
        lines.append(f"- {entry.kind} {entry.name} @ {location}")
    lines.append("IMPORTANT SYMBOLS:")
    for _, path, symbol in ranked[:max_symbols]:
        extent = (symbol.get("extent_utf16") or {}).get("start", {}) or {}
        line = extent.get("line_number")
        location = f"{path}:{line}" if line else path
        fqn = symbol.get("fully_qualified_name") or symbol.get("name") or "unknown"
        lines.append(f"- {symbol.get('kind', 'symbol')} {fqn} @ {location}")
    return "\n".join(lines)
