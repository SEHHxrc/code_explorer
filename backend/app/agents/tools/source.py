"""受路径、文件、字节和时间预算约束的源码读取工具。"""

from __future__ import annotations

import asyncio
import os
import time

from backend.app.agents.contracts import AgentEvidence, ToolResult
from backend.app.agents.policy import (
    IGNORED_DIRECTORIES,
    MAX_READ_LINES,
    MAX_SOURCE_BYTES,
    redact_secrets,
    resolve_project_path,
)
from backend.app.agents.tools.arguments import ReadFileArguments, SearchArguments, SearchManyArguments
from backend.app.agents.tools.base import AgentTool, ToolContext

MAX_SEARCH_FILES = 5_000
MAX_SEARCH_BYTES = 64 * 1024 * 1024
MAX_SEARCH_SECONDS = 3.0


class ReadFileTool(AgentTool):
    """封装 `ReadFileTool` 对应的领域状态与行为。"""
    name = "read_file_range"
    description = "Read a bounded line range from a text file inside the project. Secrets are redacted."
    arguments_model = ReadFileArguments

    async def execute(self, context: ToolContext, arguments: ReadFileArguments) -> ToolResult:
        """执行工具请求并返回结构化结果和证据。"""
        target = resolve_project_path(context.project_root, arguments.path)
        if not target.is_file():
            raise ValueError("Requested project file does not exist")
        if target.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError("Requested file exceeds the read limit")
        raw = await asyncio.to_thread(target.read_bytes)
        if b"\x00" in raw[:4096]:
            raise ValueError("Binary files cannot be read by this tool")
        start = arguments.start_line
        end = min(arguments.end_line, start + MAX_READ_LINES - 1)
        if end < start:
            raise ValueError("end_line must not be smaller than start_line")
        lines = raw.decode("utf-8", errors="replace").splitlines()
        selected = lines[start - 1:end]
        numbered = "\n".join(f"{index}: {line}" for index, line in enumerate(selected, start=start))
        return ToolResult(
            content={
                "path": arguments.path,
                "start_line": start,
                "end_line": start + max(len(selected) - 1, 0),
                "text": redact_secrets(numbered),
                "file_total_lines": len(lines),
                "requested_end_line": arguments.end_line,
                "has_more_lines": end < len(lines),
                "next_start_line": end + 1 if end < len(lines) else None,
            },
            evidence=[AgentEvidence(path=arguments.path, line=start, detail="source excerpt")],
            truncated=end < min(arguments.end_line, len(lines)),
        )


class SearchProjectTextTool(AgentTool):
    """封装 `SearchProjectTextTool` 对应的领域状态与行为。"""
    name = "search_project_text"
    description = "Search bounded UTF-8 project text files for a literal string and return matching lines."
    arguments_model = SearchArguments

    async def execute(self, context: ToolContext, arguments: SearchArguments) -> ToolResult:
        """执行工具请求并返回结构化结果和证据。"""
        return await asyncio.to_thread(self._search, context, arguments)

    @staticmethod
    def _search(context: ToolContext, arguments: SearchArguments) -> ToolResult:
        """在受控预算内搜索项目文本并返回命中证据。"""
        return search_literals(context, [arguments.query], arguments.limit)


class SearchProjectTextManyTool(AgentTool):
    """批量字面搜索；与单词搜索复用同一引擎，不查询静态索引。"""
    name = "search_project_text_many"
    description = "Search 1-8 literal queries in one raw-file traversal, with shared budgets and explicit coverage."
    arguments_model = SearchManyArguments

    async def execute(self, context: ToolContext, arguments: SearchManyArguments) -> ToolResult:
        """校验词长后执行共享扫描；不按关键词数放大文件、字节或时间预算。"""
        if any(not query or len(query) > 200 for query in arguments.queries):
            raise ValueError("Queries must contain 1-200 characters each")
        return await asyncio.to_thread(search_literals, context, arguments.queries, arguments.limit)


def search_literals(context: ToolContext, queries: list[str], limit: int) -> ToolResult:
    """遍历项目文本，返回去重命中及范围说明；跳过不等同于已成功搜索。"""
    folded = [query.casefold() for query in queries]
    hits = []
    evidence = []
    visited_files = 0
    scanned_files = 0
    read_text_files = 0
    partial_file = None
    scanned_bytes = 0
    started_at = time.monotonic()
    skipped: dict[str, int] = {}
    directory_failures = 0
    ignored_directories = 0
    hit_counts = [0] * len(queries)

    def skip(reason: str) -> None:
        """累计跳过原因，不泄露不属于项目的路径。"""
        skipped[reason] = skipped.get(reason, 0) + 1

    def walk_error(error: OSError) -> None:
        """记录不可遍历目录；不能声称其内部文件已被搜索。"""
        nonlocal directory_failures
        directory_failures += 1

    def result(termination: str | None = None) -> ToolResult:
        """范围与命中分别标记完整性，空命中不代表全仓库无匹配。"""
        coverage_complete = termination is None and not skipped and not directory_failures
        return ToolResult(content={
            "queries": queries, "matching": "casefold_literal", "scope": {
                "visited_files": visited_files, "searched_text_files": scanned_files,
                "read_text_files": read_text_files, "partial_file": partial_file,
                "read_bytes": scanned_bytes, "skipped_files": sum(skipped.values()),
                "skip_reasons": skipped, "ignored_directories": ignored_directories,
                "directory_failures": directory_failures, "termination_reason": termination,
                "traversal_complete": termination is None and not directory_failures,
                "results_complete": coverage_complete, "query_hit_counts": hit_counts,
                "all_project_files_searched": coverage_complete and not ignored_directories,
                "limits": {"files": MAX_SEARCH_FILES, "bytes": MAX_SEARCH_BYTES, "seconds": MAX_SEARCH_SECONDS, "hits": limit},
                "coverage": "eligible UTF-8 text files, excluding ignored directories",
            }, "matches": hits,
        }, evidence=evidence, truncated=not coverage_complete)

    for current, directories, files in os.walk(context.project_root, onerror=walk_error):
        retained = [name for name in directories if name not in IGNORED_DIRECTORIES and not os.path.islink(os.path.join(current, name))]
        ignored_directories += len(directories) - len(retained)
        directories[:] = sorted(retained)
        for filename in sorted(files):
            if (
                visited_files >= MAX_SEARCH_FILES
                or scanned_bytes >= MAX_SEARCH_BYTES
                or time.monotonic() - started_at >= MAX_SEARCH_SECONDS
            ):
                reason = "file_budget" if visited_files >= MAX_SEARCH_FILES else "byte_budget" if scanned_bytes >= MAX_SEARCH_BYTES else "time_budget"
                return result(reason)
            visited_files += 1
            try:
                path = resolve_project_path(context.project_root, os.path.relpath(os.path.join(current, filename), context.project_root))
                size = path.stat().st_size
                if size > MAX_SOURCE_BYTES:
                    skip("file_too_large")
                    continue
                if scanned_bytes + size > MAX_SEARCH_BYTES:
                    skip("byte_budget")
                    return result("byte_budget")
                raw = path.read_bytes()
                scanned_bytes += len(raw)
            except OSError:
                skip("read_failed")
                continue
            except ValueError:
                skip("path_policy")
                continue
            if b"\x00" in raw[:4096]:
                skip("binary")
                continue
            try:
                lines = raw.decode("utf-8").splitlines()
            except UnicodeDecodeError:
                skip("encoding")
                continue
            read_text_files += 1
            relative = path.relative_to(context.project_root).as_posix()
            partial_file = {"path": relative, "last_searched_line": 0}
            for line_number, line in enumerate(lines, 1):
                if time.monotonic() - started_at >= MAX_SEARCH_SECONDS:
                    return result("time_budget")
                partial_file["last_searched_line"] = line_number
                folded_line = line.casefold()
                matching = [index for index, query in enumerate(folded) if query in folded_line]
                if not matching:
                    continue
                for index in matching:
                    hit_counts[index] += 1
                redacted = redact_secrets(line.strip())
                preview = redacted[:500]
                hits.append({"path": relative, "line": line_number, "text": preview,
                             "text_truncated": len(redacted) > 500, "query_indices": matching})
                evidence.append(AgentEvidence(path=relative, line=line_number, detail="text match"))
                if len(hits) >= limit:
                    return result("hit_budget")
            scanned_files += 1
            partial_file = None
    return result()
