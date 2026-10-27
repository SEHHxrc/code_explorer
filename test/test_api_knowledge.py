"""LLM API 建议只做槽位/结构检查；不调用模型，不将建议写入规则或静态证据。"""

import json
import unittest
from unittest.mock import AsyncMock, Mock

from pydantic import ValidationError

from backend.app.llm.api_knowledge import LlmApiKnowledgeProvider
from backend.app.llm.base import ModelProvider, ModelTurn, ToolCall
from backend.app.llm.response_metadata import ModelResponseMetadata, ModelUsage
from backend.app.services.security_analysis.api_contracts import ApiKnowledgeRequest, ApiModelProposal


class ApiKnowledgeTests(unittest.IsolatedAsyncioTestCase):
    """多个数据返回、状态依赖、错误参数与不完整响应均独立处理。"""

    def request(self, mode: str = "single", count: int = 1) -> ApiKnowledgeRequest:
        """构造不含源码/实参值的已知 API 身份，仅用于测试知识来源的契约。"""
        return ApiKnowledgeRequest(language="python" if mode == "single" else "go", package="example-filter",
            qualified_name="example.filter", argument_count=1, return_mode=mode, return_count=count)

    async def advise(self, text: str, *, metadata: ModelResponseMetadata | None = None,
                     calls: tuple[ToolCall, ...] = (), request: ApiKnowledgeRequest | None = None):
        """注入内存 Provider；验证没有工具定义、联网能力或自动静态规则装配。"""
        provider = Mock(spec=ModelProvider)
        provider.generate_with_tools = AsyncMock(return_value=ModelTurn(text=text, provider="mock", model="mock-api-model",
            tool_calls=calls, metadata=metadata or ModelResponseMetadata(finish_reason="stop", usage=ModelUsage(10, 20, 30))))
        result = await LlmApiKnowledgeProvider(provider).describe(request or self.request())
        kwargs = provider.generate_with_tools.call_args.kwargs
        self.assertEqual([], kwargs["tools"])
        self.assertEqual("none", kwargs["tool_choice"])
        self.assertEqual("llm_proposal", result.provenance)
        self.assertEqual("schema_and_slot_bounds_only", result.validation_scope)
        self.assertEqual(64, len(result.instructions_sha256))
        return result

    async def test_python_aggregate_data_status_and_original_are_separate(self) -> None:
        """过滤输出、布尔状态和原始备份各有投影；净化结果不使原始备份或状态自动安全。"""
        text = json.dumps({"returns": [
            {"component": 0, "member_path": [0], "role": "data", "depends_on_arguments": [0],
             "sanitized_for": ["html_text"], "validity_conditions": ["return[0][1] is True"]},
            {"component": 0, "member_path": [1], "role": "status", "depends_on_arguments": [0]},
            {"component": 0, "member_path": [2], "role": "data", "depends_on_arguments": [0]},
        ]})
        result = await self.advise(text)
        self.assertIsNone(result.error_code)
        effects = result.proposal.returns
        self.assertEqual([(0,), (1,), (2,)], [effect.member_path for effect in effects])
        self.assertEqual(("html_text",), effects[0].sanitized_for)
        self.assertEqual((), effects[1].sanitized_for)
        self.assertEqual((0,), effects[2].depends_on_arguments)

    async def test_native_multiple_return_uses_component_not_tuple_member(self) -> None:
        """Go 的内容与 error 是不同 ABI 分量；名称不决定是否依赖输入。"""
        result = await self.advise(json.dumps({"returns": [
            {"component": 0, "role": "data", "depends_on_arguments": [0]},
            {"component": 1, "role": "error", "depends_on_arguments": None},
        ]}), request=self.request("multiple", 2))
        self.assertIsNone(result.error_code)
        self.assertEqual([0, 1], [effect.component for effect in result.proposal.returns])
        self.assertIsNone(result.proposal.returns[1].depends_on_arguments)

    async def test_wrong_slots_duplicate_claims_and_fact_promotion_are_rejected(self) -> None:
        """结构校验防止越界、重复和伪装 observed；失败仍保留已发生的供应商用量。"""
        for payload in ({"returns": [{"component": 1}]},
                        {"returns": [{"component": 0, "depends_on_arguments": [1]}]},
                        {"returns": [{"component": 0}, {"component": 0}]},
                        {"returns": [{"component": 0}], "provenance": "observed"}):
            with self.subTest(payload=payload):
                result = await self.advise(json.dumps(payload))
                self.assertIsNone(result.proposal)
                self.assertEqual("invalid_api_advice_schema_or_slots", result.error_code)
                self.assertEqual(30, result.metadata.usage.total_tokens)

    async def test_length_refusal_tool_calls_and_conflicting_finish_are_not_accepted(self) -> None:
        """不修复供应商结束状态，也不执行建议响应里的工具调用。"""
        for metadata in (ModelResponseMetadata(finish_reason="length"),
                         ModelResponseMetadata(finish_reason="stop", refused=True),
                         ModelResponseMetadata(finish_reason="stop", response_status="incomplete")):
            result = await self.advise('{"returns":[]}', metadata=metadata)
            self.assertIsNone(result.proposal)
        result = await self.advise('{"returns":[]}', calls=(ToolCall("id", "execute_script", {}),))
        self.assertIsNotNone(result.error_code)

    async def test_invalid_json_and_empty_output_do_not_get_repaired(self) -> None:
        """不自动重试、拼接或补齐响应；空模型建议不能变成已知的无数据输出。"""
        for text in ("", "```json\n{}\n```", "{", "[]"):
            result = await self.advise(text)
            self.assertIsNone(result.proposal)
            self.assertIsNotNone(result.error_code)

    def test_unknown_dependency_is_not_an_empty_dependency_set(self) -> None:
        """null 表示关系未知，空集合仅为模型声称不依赖输入，二者都不是静态证明。"""
        proposal = ApiModelProposal.model_validate({"returns": [
            {"component": 0, "member_path": [0], "depends_on_arguments": None},
            {"component": 0, "member_path": [1], "depends_on_arguments": []}]})
        self.assertIsNone(proposal.returns[0].depends_on_arguments)
        self.assertEqual((), proposal.returns[1].depends_on_arguments)
        for values in ({"return_mode": "single", "return_count": 2},
                       {"return_mode": "multiple", "return_count": None}):
            with self.assertRaises(ValidationError):
                self.request().model_copy(update=values).__class__.model_validate({**self.request().model_dump(), **values})


if __name__ == "__main__":
    unittest.main()
