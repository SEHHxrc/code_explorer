"""模型侧工具观察窗口：按完整记录/完整源码行收敛，不切割 JSON 或证据坐标。"""

import json
import re
from typing import Any

OBSERVATION_WINDOW_VERSION = "complete-records-v2"


def encoded(value: Any) -> str:
    """把 JSON 兼容工具结果编码为紧凑完整 JSON。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def compact_observation(observation: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """缩减结果正文，保留调用参数、全部证据和读取续页坐标；原始结果不变。"""
    result = observation.get("result") or {}
    if not isinstance(result, dict):
        result = {"content": result}
    content = result.get("content")
    locator = {key: content[key] for key in (
        "path", "start_line", "end_line", "file_total_lines", "requested_end_line",
    ) if isinstance(content, dict) and key in content}
    compact = {
        "call_id": observation.get("call_id"), "name": observation.get("name"),
        "arguments": observation.get("arguments") or {},
        "result": {
            "content": locator, "evidence": result.get("evidence") or [], "truncated": True,
            "observation_window": {
                "reason": "model_context_budget", "full_result_persisted": True,
                "content_omitted": True,
                "read_hint": "缩小原请求范围重读；省略内容未审阅。",
            },
        },
    }
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        lines = content["text"].splitlines()
        selected: list[str] = []
        for line in lines:
            proposed = "\n".join([*selected, line])
            trial = {**locator, "text": proposed}
            match = re.match(r"^(\d+):", line)
            if match:
                trial["end_line"] = int(match.group(1))
                trial["next_start_line"] = int(match.group(1)) + 1
            compact["result"]["content"] = trial
            if len(encoded(compact)) > max_chars:
                break
            selected.append(line)
        actual = {**locator, "text": "\n".join(selected)}
        if selected and (match := re.match(r"^(\d+):", selected[-1])):
            actual["end_line"] = int(match.group(1))
            actual["next_start_line"] = int(match.group(1)) + 1
        else:
            actual.pop("end_line", None)
            actual["next_start_line"] = content.get("start_line")
        compact["result"]["content"] = actual
        compact["result"]["observation_window"]["content_omitted"] = len(selected) < len(lines)
    elif isinstance(content, dict):
        # 目录和搜索也必须保留可操作的完整条目，不能压缩成一个无意义的空对象。
        selected_content = dict(locator)
        for key, value in content.items():
            if key in selected_content:
                continue
            if not isinstance(value, list):
                trial = {**selected_content, key: value}
                compact["result"]["content"] = trial
                if len(encoded(compact)) <= max_chars:
                    selected_content = trial
                continue
            selected_content[key] = []
            selected_content[f"omitted_{key}"] = len(value)
            for entry in value:
                trial = {**selected_content, key: [*selected_content[key], entry],
                         f"omitted_{key}": len(value) - len(selected_content[key]) - 1}
                compact["result"]["content"] = trial
                if len(encoded(compact)) > max_chars:
                    break
                selected_content = trial
        compact["result"]["content"] = selected_content
    elif "error" in result:
        compact["result"]["content"] = {key: result[key] for key in ("error", "type") if key in result}
    return compact


def render_observation_window(history: list[dict[str, Any]], max_chars: int) -> tuple[str, dict[str, int]]:
    """按最新优先保留完整观察或结构化缩略，输出有效 JSON 与明确省略计数。"""
    envelope: dict[str, Any] = {"observations": [], "omitted_observations": len(history)}
    compacted = 0
    recent = history[-12:]
    for observation in reversed(recent):
        item = observation
        # 先保障最新有效正文，再保留预算能容纳的旧观察；不能按历史条数摊薄成全是空正文。
        overhead = len(encoded({"observations": [None, *envelope["observations"]],
                                "omitted_observations": envelope["omitted_observations"] - 1})) - 4
        remaining = max(0, max_chars - overhead)
        if len(encoded(item)) > remaining:
            item = compact_observation(observation, remaining)
        trial = {"observations": [item, *envelope["observations"]],
                 "omitted_observations": envelope["omitted_observations"] - 1}
        if len(encoded(trial)) <= max_chars:
            envelope = trial
            compacted += int(item is not observation)
    rendered = encoded(envelope)
    if len(rendered) > max_chars:
        raise ValueError("Model context budget cannot hold the tool observation envelope")
    return rendered, {
        "total_observations": len(history), "included_observations": len(envelope["observations"]),
        "compacted_observations": compacted, "omitted_observations": envelope["omitted_observations"],
    }
