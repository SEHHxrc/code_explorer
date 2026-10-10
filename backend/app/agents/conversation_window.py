"""按完整工具轮次压缩原生消息；调用和结果不能被分别裁剪。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.agents.observations import compact_observation, encoded
from backend.app.llm.base import ModelTurn
from backend.app.llm.budget import BudgetPlanner, ModelBudgetError, request_payload
from backend.app.llm.conversation import ModelMessage

CONVERSATION_VERSION = "native-tool-rounds-v1"


@dataclass(frozen=True)
class ToolRound:
    """一轮模型工具调用及全部执行观察；完整结果由运行存储另行持久化。"""

    assistant: ModelMessage
    observations: list[dict[str, Any]]


def assistant_message(turn: ModelTurn) -> ModelMessage:
    """优先使用适配器保留的原生续接状态；测试/简单供应商可使用规范化调用。"""
    if turn.continuation is not None:
        return turn.continuation
    return ModelMessage(role="assistant", content=turn.text or None, tool_calls=[
        {"id": call.id, "type": "function", "function": {
            "name": call.name, "arguments": encoded(call.arguments),
        }} for call in turn.tool_calls
    ])


def round_messages(round_: ToolRound, *, max_chars: int | None = None) -> list[ModelMessage]:
    """序列化一整轮；只收缩结果正文，保留所有调用及对应结果、证据和续读位置。"""
    messages = [round_.assistant]
    for observation in round_.observations:
        item = observation
        if max_chars is not None and len(encoded(item)) > max_chars:
            item = compact_observation(item, max_chars)
        messages.append(ModelMessage(role="tool", tool_call_id=observation["call_id"],
                                     content=encoded(item["result"])))
    return messages


def render_conversation_window(
    rounds: list[ToolRound], *, initial: ModelMessage, instructions: str, tools: list[dict[str, Any]],
    planner: BudgetPlanner, provider: Any,
) -> tuple[list[ModelMessage], dict[str, Any]]:
    """最新轮次优先；过旧轮次整体省略，最新轮次无法配对容纳时安全失败。"""
    kept: list[list[ModelMessage]] = []
    included = compacted = 0

    def initial_with_notice(retained_rounds: int, compacted_results: int) -> ModelMessage:
        """向模型明确当前窗口省略，不以定位提示冒充已经展示的源码。"""
        notice = {"total_rounds": len(rounds), "omitted_rounds": len(rounds) - retained_rounds,
                  "compacted_results": compacted_results,
                  "limitation": "当前请求未包含的正文无法直接核验；必要时缩小范围续读，不能把定位信息当作完整源码。"}
        return ModelMessage(role="user", content=(initial.get("content") or "") + "\n\nCONVERSATION_WINDOW\n" + encoded(notice))

    def fits(group: list[ModelMessage]) -> bool:
        """按将要实际发送的协议，检查加入候选整轮后的预算。"""
        messages = [initial_with_notice(len(kept) + 1, sum(len(round_.observations) for round_ in rounds)),
                    *group, *(message for batch in kept for message in batch)]
        return planner.fits(request_payload(provider, instructions=instructions, messages=messages, tools=tools,
                                            model=provider.model, max_output_tokens=planner.limits.max_output_tokens))

    for index, round_ in enumerate(reversed(rounds)):
        group = round_messages(round_)
        was_compacted = False
        if not fits(group):
            low, high = 0, max((len(encoded(item)) for item in round_.observations), default=0)
            found: list[ModelMessage] | None = None
            while low <= high:
                middle = (low + high) // 2
                trial = round_messages(round_, max_chars=middle)
                if fits(trial):
                    found = trial
                    low = middle + 1
                else:
                    high = middle - 1
            if found is None:
                if index == 0:
                    raise ModelBudgetError("Input budget cannot hold the latest complete tool-call/result round")
                continue
            group, was_compacted = found, True
        kept.insert(0, group)
        included += len(round_.observations)
        compacted += len(round_.observations) if was_compacted else 0
    total = sum(len(round_.observations) for round_ in rounds)
    return [initial_with_notice(len(kept), compacted), *(message for group in kept for message in group)], {
        "total_observations": total, "included_observations": included,
        "compacted_observations": compacted, "omitted_observations": total - included,
        "conversation_version": CONVERSATION_VERSION,
    }
