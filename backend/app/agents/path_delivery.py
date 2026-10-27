"""路径交付的传输观察；只存 ID/分页/哈希，不推断模型实际使用或安全语义。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from backend.app.llm.base import ModelTurn
from backend.app.llm.observation import ModelWireObserver

PATH_DELIVERY_VERSION = "path-delivery-v2-report-projection"


def path_references(content: dict[str, Any], *, compacted: bool = False) -> list[dict[str, Any]]:
    """提取服务器证据页中的定位元数据；目录、分段与完整传播路径不能混为一谈。"""
    view = content.get("view")
    if content.get("kind") != "static_security_evidence" and not str(content.get("schema_version", "")).startswith("security-path-summary-"):
        return []
    rows = content.get("items", [])
    rows = rows if isinstance(rows, list) else []
    common = {"snapshot": content.get("snapshot"), "view": view, "content_omitted": compacted,
              "body_sha256": hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    if view in {"detail", "coverage"} and content.get("id"):
        return [{**common, "id": content["id"], "representation": "section_page", "section": content.get("section", "overview"),
                 "offset": content.get("offset"), "next_offset": content.get("next_offset"),
                 "total_items": content.get("total_items"), "has_more": content.get("has_more"), "item_count": len(rows),
                 "item_ids": [row[key] for row in rows if isinstance(row, dict)
                              for key in ("step_id", "edge_id", "snippet_id") if key in row],
                 "item_orders": [row["order"] for row in rows if isinstance(row, dict) and "order" in row]}]
    return [{**common, "id": row["id"], "representation": "family_summary" if row.get("kind") == "path_group" else "route_navigation",
             "sequence_delivery": row.get("sequence_delivery"), "path_group_id": row.get("path_group_id"),
             "has_more": content.get("has_more")}
            for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)]


def _wire_references(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """只检查实际 Chat/Responses 正文中的应用摘要、工具结果及显式证据归档。"""
    references: list[dict[str, Any]] = []
    for message in payload.get("messages", payload.get("input", [])):
        if not isinstance(message, dict):
            continue
        is_tool = message.get("role") == "tool" or message.get("type") == "function_call_output"
        text = message.get("output") if is_tool and "output" in message else message.get("content")
        if not isinstance(text, str):
            continue
        if is_tool:
            try:
                result = json.loads(text)
            except ValueError:
                continue
            if isinstance(result, dict) and isinstance(result.get("content"), dict):
                references.extend({**item, "tool_call_id": message.get("tool_call_id", message.get("call_id"))}
                                  for item in path_references(result["content"], compacted=bool(result.get("observation_window", {}).get("content_omitted"))))
        elif message.get("role") == "user":
            for marker in ("STATIC_SECURITY_EVIDENCE\n", "APPLICATION_BATCH_EVIDENCE\n"):
                if marker not in text:
                    continue
                try:
                    value, _ = json.JSONDecoder().raw_decode(text.split(marker, 1)[1])
                except ValueError:
                    continue
                if not isinstance(value, dict):
                    continue
                references.extend(path_references(value))
                if marker.startswith("APPLICATION_BATCH"):
                    for row in value.get("observations", []):
                        result = row.get("result", {}) if isinstance(row, dict) else {}
                        content = result.get("content") if isinstance(result, dict) else None
                        if isinstance(content, dict):
                            references.extend({**item, "archive_batch_id": row.get("batch_id"), "observation_id": row.get("observation_id")}
                                              for item in path_references(content, compacted=bool(result.get("observation_window", {}).get("content_omitted"))))
    return references


class PathDeliveryObserver:
    """每运行私有的旁路记录器；复用审计/退避观察接口，不修改模型请求或工具结果。"""

    def __init__(self, emit: Callable[[str, dict[str, Any]], None]) -> None:
        """接收当前运行事件写入函数；不持有认证头、端点地址或源码正文。"""
        self.emit = emit
        self.delegate: ModelWireObserver | None = None
        self.step = 0
        self.sequence = 0
        self.known_ids: set[str] = set()
        self.pending: dict[int, list[dict[str, Any]]] = {}

    def returned(self, call_id: str, result: dict[str, Any]) -> None:
        """记录工具已生成的页；此时不能标记已发送或已被模型使用。"""
        references = path_references(result)
        self.known_ids.update(item["id"] for item in references)
        if references:
            self.emit("paths.returned", {"step": self.step, "call_id": call_id, "references": references})

    def sent(self, payload: dict[str, Any], attempt: int, *, wire_sha256: str = "", wire_bytes: int = 0) -> int:
        """HTTP 发送边界按真实序列化正文记录；发送尝试仍不证明平台接受或模型使用。"""
        self.sequence += 1
        identifier = self.delegate.sent(payload, attempt, wire_sha256=wire_sha256, wire_bytes=wire_bytes) if self.delegate else self.sequence
        references = _wire_references(payload)
        self.pending[identifier] = references
        self.known_ids.update(item["id"] for item in references)
        self.emit("paths.send_attempt", {"step": self.step, "attempt": attempt, "request_id": identifier,
            "audit_request_id": identifier if self.delegate else None, "wire_sha256": wire_sha256,
            "references": references, "semantics": "outgoing_request_not_model_use_or_acceptance"})
        return identifier

    def received(self, request_id: int, payload: dict[str, Any], *, diagnostics: dict[str, Any] | None = None) -> None:
        """记录同一尝试收到响应，不把它升级为路径已阅读、已核实或已使用。"""
        if self.delegate:
            self.delegate.received(request_id, payload, diagnostics=diagnostics)
        references = self.pending.pop(request_id, [])
        self.emit("paths.request_resolved", {"step": self.step, "request_id": request_id,
            "status": "response_received", "reference_ids": sorted({item["id"] for item in references}),
            "model_use": "unknown"})

    def failed(self, request_id: int, error_type: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        """保留失败尝试状态；失败既不证明模型收到正文，也不证明零消耗。"""
        if self.delegate:
            self.delegate.failed(request_id, error_type, diagnostics=diagnostics)
        references = self.pending.pop(request_id, [])
        self.emit("paths.request_resolved", {"step": self.step, "request_id": request_id,
            "status": "failed", "error_type": error_type, "status_code": (diagnostics or {}).get("status_code"),
            "reference_ids": sorted({item["id"] for item in references}), "model_use": "unknown"})

    def retry_wait(self, attempt: int, requested_seconds: float, elapsed_seconds: float, *, status_code: int | None, completed: bool) -> None:
        """原样转交现有退避计量，不额外重试或重复累计 Token。"""
        if self.delegate:
            self.delegate.retry_wait(attempt, requested_seconds, elapsed_seconds, status_code=status_code, completed=completed)

    def mentions(self, text: str) -> list[str]:
        """只匹配显式 ID；文件引用不自动映射到某条路径，也不做语义使用判断。"""
        return sorted(key for key in self.known_ids if re.search(r"(?<![\w:.-])" + re.escape(key) + r"(?![\w:.-])", text))

    def model_observed(self, turn: ModelTurn) -> None:
        """分别记录模型请求的证据与可见文本中的 ID；违规/未执行调用也保留其请求事实。"""
        requested = [{"call_id": call.id, "name": call.name, **{key: call.arguments[key] for key in
                     ("evidence_id", "path_group_id", "section", "offset", "limit") if key in call.arguments}}
                     for call in turn.tool_calls if call.name in {"get_static_security_evidence", "search_static_security_evidence"}]
        declared = [key for call in turn.tool_calls if call.name == "submit_analysis_batch"
                    and isinstance(call.arguments.get("evidence_ids"), list)
                    for key in call.arguments["evidence_ids"] if isinstance(key, str)]
        ledger_mentions = {key for call in turn.tool_calls if call.name == "submit_analysis_batch"
                           and isinstance(call.arguments.get("ledger_json"), str) for key in self.mentions(call.arguments["ledger_json"])}
        self.emit("paths.model_observed", {"step": self.step, "requested": requested, "declared_report_ids": declared,
            "explicit_id_mentions": sorted(set(self.mentions(turn.text)) | ledger_mentions),
            "semantics": "requests_and_literal_mentions_only_not_verified_use;file_citations_not_mapped_to_routes"})
