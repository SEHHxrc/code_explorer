"""受路径、文件、字节和时间预算约束的源码读取工具。"""

from __future__ import annotations

import asyncio
import os
import time

from backend.app.agents.contracts import AgentEvidence, ToolResult
from backend.app.agents.policy import IGNORED_DIRECTORIES, MAX_READ_LINES, MAX_SOURCE_BYTES, redact_secrets, resolve_project_path
from backend.app.agents.tools.arguments import ReadFileArguments, SearchArguments
from backend.app.agents.tools.base import AgentTool, ToolContext

MAX_SEARCH_FILES = 5_000
MAX_SEARCH_BYTES = 64 * 1024 * 1024
MAX_SEARCH_SECONDS = 3.0


class ReadFileTool(AgentTool):
    name = "read_file_range"
    description = "Read a bounded line range from a text file inside the project. Secrets are redacted."
    arguments_model = ReadFileArguments

    async def execute(self, context: ToolContext, arguments: ReadFileArguments) -> ToolResult:
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
            },
            evidence=[AgentEvidence(path=arguments.path, line=start, detail="source excerpt")],
            truncated=end < len(lines),
        )


class SearchProjectTextTool(AgentTool):
    name = "search_project_text"
    description = "Search bounded UTF-8 project text files for a literal string and return matching lines."
    arguments_model = SearchArguments

    async def execute(self, context: ToolContext, arguments: SearchArguments) -> ToolResult:
        return await asyncio.to_thread(self._search, context, arguments)

    @staticmethod
    def _search(context: ToolContext, arguments: SearchArguments) -> ToolResult:
        query = arguments.query.casefold()
        hits = []
        evidence = []
        scanned_files = 0
        scanned_bytes = 0
        started_at = time.monotonic()
        for current, directories, files in os.walk(context.project_root):
            directories[:] = [name for name in directories if name not in IGNORED_DIRECTORIES]
            for filename in files:
                if (
                    scanned_files >= MAX_SEARCH_FILES
                    or scanned_bytes >= MAX_SEARCH_BYTES
                    or time.monotonic() - started_at >= MAX_SEARCH_SECONDS
                ):
                    return ToolResult(content=hits, evidence=evidence, truncated=True)
                path = resolve_project_path(
                    context.project_root,
                    os.path.relpath(os.path.join(current, filename), context.project_root),
                )
                try:
                    size = path.stat().st_size
                    scanned_files += 1
                    if size > MAX_SOURCE_BYTES or scanned_bytes + size > MAX_SEARCH_BYTES:
                        continue
                    raw = path.read_bytes()
                    scanned_bytes += len(raw)
                except OSError:
                    continue
                if b"\x00" in raw[:4096]:
                    continue
                relative = path.relative_to(context.project_root).as_posix()
                for line_number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                    if query not in line.casefold():
                        continue
                    preview = redact_secrets(line.strip())[:500]
                    hits.append({"path": relative, "line": line_number, "text": preview})
                    evidence.append(AgentEvidence(path=relative, line=line_number, detail="text match"))
                    if len(hits) >= arguments.limit:
                        return ToolResult(content=hits, evidence=evidence, truncated=True)
        return ToolResult(content=hits, evidence=evidence)