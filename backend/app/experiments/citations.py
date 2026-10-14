"""引用的路径、行号与实际展示范围检查；只产生机械警告，不判定语义正确。"""

import json
import re
from typing import Any
from pathlib import PurePosixPath

_BRACKET = re.compile(r"\[([^\]\n]+)\]")
_CITATION = re.compile(r"([^:\]\n]+):(?:L)?(\d+)(?:\s*-\s*(?:L)?(\d+))?")
_REFERENCE_LIKE = re.compile(r":\s*(?:L)?\d|\bL\d|同文件|同上")
_NUMBERED = re.compile(r"^(\d+):([^\n]*)", re.MULTILINE)


def visible_lines(payload: dict[str, Any]) -> dict[str, set[int]]:
    """从可见请求提取实际源码行，忽略只有路径/坐标却没有正文的对象。"""
    result: dict[str, set[int]] = {}

    def visit(value: Any) -> None:
        """解析受控工具 JSON 和安全证据 JSON，不运行其中的任何代码。"""
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            path, text = value.get("path"), value.get("text")
            if isinstance(path, str) and isinstance(text, str) and text:
                path = PurePosixPath(path.replace("\\", "/")).as_posix()
                numbered = list(_NUMBERED.finditer(text))
                numbers = {int(match.group(1)) for match in numbered
                           if "[整行超过片段预算" not in match.group(2) and match.group(2).strip() != "[REDACTED]"}
                if not numbered and isinstance(value.get("line"), int):
                    numbers = {value["line"]}  # 搜索命中预览，存在展示不等同于完整上下文。
                if not numbered and not numbers and isinstance(value.get("start_line"), int):
                    start = value["start_line"]
                    end = min(value.get("end_line", start), start + len(text.splitlines()) - 1)
                    numbers = set(range(start, end + 1))
                result.setdefault(path, set()).update(numbers)
            for item in value.values():
                # 源码/搜索预览里的 JSON 是项目内容，不能冒充工具出具的另一份源码展示证明。
                if isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, str):
            candidates = [value]
            if "STATIC_SECURITY_EVIDENCE\n" in value:
                candidates.append(value.split("STATIC_SECURITY_EVIDENCE\n", 1)[1])
            for candidate in candidates:
                try:
                    decoded, _ = json.JSONDecoder().raw_decode(candidate.lstrip())
                except (ValueError, RecursionError):
                    continue
                if isinstance(decoded, (dict, list)):
                    visit(decoded)

    for message in payload.get("messages", payload.get("input", [])):
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool":
            visit(message.get("content", ""))
        elif message.get("type") == "function_call_output":
            visit(message.get("output", ""))
        elif message.get("role") == "user":
            content = message.get("content") or ""
            if isinstance(content, str) and "\n\nTRUSTED_STATIC_CONTEXT\n" in content:
                context = content.rsplit("\n\nTRUSTED_STATIC_CONTEXT\n", 1)[1]
                if context.startswith("STATIC_SECURITY_EVIDENCE\n"):
                    visit(context)
    return result


def check_citations(answer: str, manifest: dict[str, Any], seen: dict[str, set[int]], final_seen: dict[str, set[int]]) -> dict[str, Any]:
    """区分语法、简称/歧义、真实坐标错误和展示缺口；仅给提示，不修改报告或验证语义。"""
    warnings = []
    checked = set()
    malformed = set()
    for bracket in _BRACKET.finditer(answer):
        raw = bracket.group(1)
        match = _CITATION.fullmatch(raw.strip())
        if match is None or match.group(1).strip() in {"同文件", "同上"}:
            if _REFERENCE_LIKE.search(raw) and raw not in malformed:
                malformed.add(raw)
                warnings.append({"path": raw, "start_line": None, "end_line": None,
                                 "reason": "citation_syntax_invalid"})
            continue
        path = PurePosixPath(match.group(1).replace("\\", "/").strip()).as_posix()
        start, end = int(match.group(2)), int(match.group(3) or match.group(2))
        key = (path, start, end)
        if key in checked:
            continue
        checked.add(key)
        reason = None
        if PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts or ":" in path:
            reason = "path_outside_snapshot"
        else:
            if path not in manifest:
                matches = [full for full in manifest if full.endswith("/" + path)]
                if len(matches) == 1:
                    warnings.append({"path": path, "start_line": start, "end_line": end,
                                     "reason": "path_abbreviation_resolved", "resolved_path": matches[0]})
                    path = matches[0]  # 仅用于核对原始引用，不改报告，不把简称视为规范引用。
                else:
                    reason = "path_ambiguous" if matches else "path_not_in_snapshot"
        if reason is None and (start < 1 or end < start):
            reason = "invalid_line_range"
        elif reason is None and manifest[path].get("lines") is None:
            reason = "line_count_unavailable"
        elif reason is None and end > manifest[path]["lines"]:
            reason = "line_out_of_range"
        elif reason is None and not all(line in seen.get(path, set()) for line in range(start, end + 1)):
            reason = "source_not_shown"
        elif reason is None and not all(line in final_seen.get(path, set()) for line in range(start, end + 1)):
            reason = "seen_earlier_not_in_final_request"
        if reason:
            warnings.append({"path": path, "start_line": start, "end_line": end, "reason": reason})
    return {"version": "mechanical-citations-v2", "checked": len(checked), "malformed": len(malformed), "warnings": warnings,
            "semantic_validation": "not_performed", "semantic_coverage": "not_proven"}
