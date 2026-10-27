"""外部 API 模型建议的公共契约；与源码事实、可执行规则和解释性 CWE 文档隔离。"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator


class ApiKnowledgeRequest(BaseModel):
    """只提交 API 身份和已知形状，不提交项目源码、实参值、密钥或项目事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    language: str = Field(min_length=1, max_length=32)
    package: str = Field(min_length=1, max_length=300)
    qualified_name: str = Field(min_length=1, max_length=500)
    library_version: str = Field(default="", max_length=100)
    argument_count: StrictInt = Field(ge=0, le=128, description="已知签名中的规范化形参槽位数，不含 receiver；不是本次调用的语法实参数量")
    return_mode: Literal["single", "multiple", "unknown"] = "unknown"
    return_count: StrictInt | None = Field(default=None, ge=0, le=128)

    @model_validator(mode="after")
    def validate_shape(self) -> ApiKnowledgeRequest:
        """已知 single 仅有一个 ABI 分量；Python 元组成员通过 member_path 区分。"""
        if self.return_mode == "single" and self.return_count != 1:
            raise ValueError("single 返回必须声明一个 ABI 分量")
        if self.return_mode == "multiple" and self.return_count is None:
            raise ValueError("multiple 返回需要已知分量数")
        return self


class ApiReturnEffect(BaseModel):
    """单个返回分量/成员的依赖和过滤建议；依赖未知与不依赖输入是不同状态。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    component: StrictInt = Field(ge=0, le=127)
    member_path: tuple[StrictInt | StrictStr, ...] = Field(default=(), max_length=16)
    role: Literal["data", "status", "error", "metadata", "unknown"] = "unknown"
    depends_on_arguments: tuple[StrictInt, ...] | None = Field(default=None, max_length=128)
    depends_on_receiver: bool | None = None
    sanitized_for: tuple[str, ...] = Field(default=(), max_length=16)
    validity_conditions: tuple[str, ...] = Field(default=(), max_length=16)
    required_conditions: tuple[ApiCondition, ...] = Field(default=(), max_length=16)
    propagation_kind: Literal["value_copy", "derived", "unknown"] = "unknown"


class ApiValueSlot(BaseModel):
    """知识建议中的逻辑槽位；不是项目变量 ID，也不能证明调用身份已绑定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["argument", "receiver", "return"]
    index: StrictInt = Field(default=0, ge=0, le=127)
    member_path: tuple[StrictInt | StrictStr, ...] = Field(default=(), max_length=16)


class ApiCondition(BaseModel):
    """有限的类型化条件；只允许比较和空值测试，不执行模型生成表达式。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    slot: ApiValueSlot
    operator: Literal["eq", "not_eq", "is_null", "is_not_null"]
    value: StrictStr | StrictInt | StrictBool | None = None

    @model_validator(mode="after")
    def validate_null_test(self) -> ApiCondition:
        """空值测试不允许携带另一个比较常量，避免消费者对矛盾字段作不同解释。"""
        if self.operator in {"is_null", "is_not_null"} and self.value is not None:
            raise ValueError("空值条件不能携带比较常量")
        return self


class ApiModelProposal(BaseModel):
    """模型提出的分量模型；通过结构校验也不能当作已验证净化或静态传播规则。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    returns: tuple[ApiReturnEffect, ...] = Field(default=(), max_length=128)
    limitations: tuple[str, ...] = Field(default=(), max_length=32)
    claimed_sources: tuple[str, ...] = Field(default=(), max_length=16)

    def check_request(self, request: ApiKnowledgeRequest) -> None:
        """按已知签名检查槽位和实参范围；不根据角色名称认定数据无污点或过滤有效。"""
        slots: set[tuple[int, tuple[int | str, ...]]] = set()
        for effect in self.returns:
            slot = (effect.component, effect.member_path)
            if slot in slots:
                raise ValueError("同一返回槽位存在重复或矛盾建议")
            slots.add(slot)
            if request.return_count is not None and effect.component >= request.return_count:
                raise ValueError("建议超出已知返回分量范围")
            if any(index < 0 or index >= request.argument_count for index in effect.depends_on_arguments or ()):
                raise ValueError("建议引用不存在的实参")
            for condition in effect.required_conditions:
                condition_slot = condition.slot
                if (condition_slot.kind == "argument" and condition_slot.index >= request.argument_count or
                    condition_slot.kind == "return" and request.return_count is not None and condition_slot.index >= request.return_count or
                    condition_slot.kind == "receiver" and condition_slot.index != 0):
                    raise ValueError("生效条件引用不存在的槽位")


ApiReturnEffect.model_rebuild()
