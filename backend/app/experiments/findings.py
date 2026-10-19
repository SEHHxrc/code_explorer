"""两组共用的发现台账及确定性报告；只校验一致性，不证明漏洞语义。"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Annotated, Literal, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

LEDGER_VERSION = "security-review-ledger-v1"
Text = Annotated[str, Field(min_length=1, max_length=2000)]
COVERAGE_LABELS = {"input_boundaries": "输入边界", "authentication": "认证授权", "resource_limits": "资源限制",
                   "error_handling": "错误处理", "sensitive_operations": "敏感操作", "output_contexts": "输出上下文"}
STATUS_LABELS = {"source_supported_risk": "源码支持的风险", "unclosed_candidate": "未闭合候选", "excluded": "已排除"}


class LedgerRecord(BaseModel):
    """禁止未知字段和隐式类型转换，防止拼写错误改变状态或计数。"""

    model_config = ConfigDict(extra="forbid", strict=True)


class SourceReference(LedgerRecord):
    """项目相对路径及完整行范围；存在性和实际展示范围由引用审计另查。"""

    path: str = Field(min_length=1, max_length=1024)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> SourceReference:
        """拒绝反向范围及非规范路径；不把合法坐标解释为支持断言。"""
        if self.end_line < self.start_line or any(ord(char) < 32 for char in self.path) or "\\" in self.path or ":" in self.path or PurePosixPath(self.path).is_absolute() or ".." in PurePosixPath(self.path).parts:
            raise ValueError("引用必须为项目相对路径和有效行范围。")
        return self

    def citation(self) -> str:
        """使用公共引用检查器能识别的单文件/单范围语法。"""
        return f"[{self.path}:{self.start_line}-{self.end_line}]"


class ChainState(LedgerRecord):
    """输入到真实危险实现的闭合状态；不适用也须说明原因。"""

    status: Literal["closed", "open", "not_applicable"]
    description: Text


class ImpactState(LedgerRecord):
    """影响判断与条件分开，允许源码链闭合但影响仍依赖部署。"""

    status: Literal["established", "conditional", "not_established"]
    description: Text


class ProtectionCheck(LedgerRecord):
    """记录防护与实际值、危险上下文和先后顺序的对应关系。"""

    description: Text
    value_match: Literal["verified", "unverified"]
    context_match: Literal["verified", "unverified"]
    before_operation: Literal["verified", "unverified"]
    evidence: list[SourceReference] = Field(min_length=1, max_length=20)


class Finding(LedgerRecord):
    """一项独立根因；状态由模型提出，后台不自动升降级或合并语义相近条目。"""

    root_cause: str = Field(min_length=1, max_length=300)
    behavior: Text
    behavior_status: Literal["observed", "inferred", "unknown"]
    chain: ChainState
    protections: list[ProtectionCheck] = Field(default_factory=list, max_length=20)
    impact: ImpactState
    confirmed_conditions: list[Text] = Field(default_factory=list, max_length=20)
    unresolved_conditions: list[Text] = Field(default_factory=list, max_length=20)
    evidence: list[SourceReference] = Field(min_length=1, max_length=30)
    status: Literal["source_supported_risk", "unclosed_candidate", "excluded"]
    exclusion_reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_state(self) -> Finding:
        """拒绝字段状态自相矛盾，不以规则代替模型的语义判断。"""
        if not self.root_cause.strip():
            raise ValueError("独立根因不能为空白。")
        if self.status == "source_supported_risk" and (self.behavior_status != "observed" or self.chain.status == "open" or self.impact.status == "not_established"):
            raise ValueError("源码支持风险须有已观察行为、闭合或明确不适用的链路及已建立/有条件影响；否则应为未闭合候选。")
        if self.status == "excluded" and not self.exclusion_reason.strip():
            raise ValueError("已排除条目必须说明排除理由，不得由未发现输入直接推定。")
        if self.impact.status == "conditional" and not self.unresolved_conditions:
            raise ValueError("有条件影响必须列出尚未满足或未知的利用条件。")
        return self


class CoverageCheck(LedgerRecord):
    """逐安全类别声明检查状态；检查不等于发现漏洞，更不等于覆盖全部仓库。"""

    status: Literal["checked", "not_checked", "not_applicable"]
    note: Text
    evidence: list[SourceReference] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_support(self) -> CoverageCheck:
        """已检查或不适用须提供依据，未检查不能因省略字段而被默认成已检查。"""
        if self.status != "not_checked" and not self.evidence:
            raise ValueError("已检查/不适用须列出核对依据；无依据应填 not_checked。")
        return self


class ReviewCoverage(LedgerRecord):
    """两组相同的六项必填覆盖核对，不强制每类产生安全问题。"""

    input_boundaries: CoverageCheck
    authentication: CoverageCheck
    resource_limits: CoverageCheck
    error_handling: CoverageCheck
    sensitive_operations: CoverageCheck
    output_contexts: CoverageCheck


class FindingLedger(LedgerRecord):
    """模型可见的最终/批次台账；Markdown 摘要和计数只能由其状态生成。"""

    schema_version: Literal["security-review-ledger-v1"]
    findings: list[Finding] = Field(max_length=50)
    coverage: ReviewCoverage
    reviewed_ranges: list[SourceReference] = Field(max_length=100)
    unreviewed: list[Text] = Field(max_length=50)
    limitations: list[Text] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_roots(self) -> FindingLedger:
        """拒绝文字完全相同的根因重复；语义同根因是否重复仍交由评审。"""
        roots = [" ".join(item.root_cause.split()).casefold() for item in self.findings]
        if len(roots) != len(set(roots)):
            raise ValueError("同一根因文字重复，请合并影响；不同表述是否同根因仍需语义核验。")
        return self

    def render(self) -> str:
        """按状态唯一生成摘要编号、问题分区与覆盖表；原模型 JSON 由审计原样保存。"""
        from .report import REPORT_END

        def inline(value: str) -> str:
            """仅折叠展示换行，避免字段内标题改变报告结构；不改原台账。"""
            return " ".join(value.splitlines())

        def refs(values: list[SourceReference]) -> str:
            """完整输出每个独立引用，不混用文件或行范围。"""
            return " ".join(item.citation() for item in values)

        rows = [(f"F{index}", finding) for index, finding in enumerate(self.findings, 1)]
        counts = {status: sum(item.status == status for item in self.findings) for status in STATUS_LABELS}
        lines = ["## 结论", "", "台账记录：" + "；".join(f"{STATUS_LABELS[key]} {value} 项" for key, value in counts.items()) + "。",
                 "状态是模型判断，不是已确认可利用漏洞；语义正确性未由后台证明。"]
        lines.extend(f"- {key}：{inline(item.root_cause)}（{STATUS_LABELS[item.status]}）" for key, item in [row for row in rows if row[1].status == "source_supported_risk"][:5])
        for section, status in (("已核实问题", "source_supported_risk"), ("未确认风险", "unclosed_candidate"), ("范围与局限", "excluded")):
            lines.extend(["", f"## {section}", ""])
            selected = [(key, item) for key, item in rows if item.status == status]
            if not selected:
                lines.append("该状态无条目。")
            for key, item in selected:
                lines.extend([f"**{key}：{inline(item.root_cause)} — {STATUS_LABELS[status]}**",
                    f"- 代码行为（{item.behavior_status}）：{inline(item.behavior)}",
                    f"- 输入到危险实现（{item.chain.status}）：{inline(item.chain.description)}",
                    f"- 安全影响（{item.impact.status}）：{inline(item.impact.description)}",
                    "- 已核实利用前提：" + ("；".join(map(inline, item.confirmed_conditions)) or "未建立。"),
                    "- 未满足/未知条件：" + ("；".join(map(inline, item.unresolved_conditions)) or "未另列；不表示已证明全部满足。"),
                    "- 证据：" + refs(item.evidence)])
                for guard in item.protections:
                    lines.append(f"- 防护：{inline(guard.description)}；值对应={guard.value_match}，上下文={guard.context_match}，先于操作={guard.before_operation}；{refs(guard.evidence)}")
                if item.exclusion_reason:
                    lines.append("- 排除理由：" + inline(item.exclusion_reason))
            if section == "范围与局限":
                lines.extend(["", "共同覆盖核对（仅对下列依据，不代表全仓库覆盖）："])
                for name, mark in self.coverage:
                    label = {"checked": "已检查", "not_checked": "未检查", "not_applicable": "不适用"}[mark.status]
                    lines.append(f"- {COVERAGE_LABELS[name]}：{label}；{inline(mark.note)} {refs(mark.evidence)}")
                lines.append("实际核对范围（模型声明，机械审计另查）：" + (refs(self.reviewed_ranges) or "未声明。"))
                lines.extend("- 未检查范围：" + inline(item) for item in self.unreviewed)
                lines.extend("- 局限：" + inline(item) for item in self.limitations)
        return "\n".join([*lines, "", REPORT_END])


def _unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """拒绝重复 JSON 字段，不能让后写状态静默覆盖前写状态。"""
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("台账 JSON 字段重复，未自动选择后写值。")
        result[name] = value
    return result


def parse_ledger(text: str) -> FindingLedger:
    """只接受完整 JSON 对象，可有单一 JSON 围栏；拒绝尾随解释和隐式补字段。"""
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    try:
        return FindingLedger.model_validate(json.loads(text, object_pairs_hook=_unique_members))
    except ValidationError as exc:
        fields = [".".join(map(str, item["loc"])) + ": " + item["msg"] for item in exc.errors(include_input=False, include_url=False)[:6]]
        raise ValueError("发现台账字段或状态不一致：" + "；".join(fields)) from None
    except ValueError:
        raise ValueError("发现台账必须为完整 JSON 对象；未自动补字段或修正原文。") from None
