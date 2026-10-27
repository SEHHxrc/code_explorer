"""集合分配的公共语法事实；不判断 Source/Sink，也不包含第二套传播求解器。"""

from typing import Literal, Protocol
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .access_paths import canonical_path


class CollectionAllocation(BaseModel):
    """一次直接字面量分配及有界布局；动态元素明确记为不完整而非安全。"""

    model_config = ConfigDict(frozen=True)

    kind: Literal["mapping", "sequence", "properties"]
    members: tuple[str, ...] = ()
    key_semantics: Literal["typed", "javascript"] = "typed"
    storage_kind: Literal["reference", "value"] = "reference"
    complete: bool = True
    mutable: bool = True
    limit: int = Field(default=256, ge=1, le=256)

    @field_validator("members")
    @classmethod
    def direct_members(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """布局只接受一个直属规范元素；类型化键不降级成普通字段名。"""
        if len(values) > 256 or any(not (path := canonical_path("collection" + value))
                                    or len(path.segments) != 1 or path.segments[0].kind != "index" for value in values):
            raise ValueError("Collection layout requires bounded direct static indices")
        return tuple(dict.fromkeys(values))


class CollectionCallView(Protocol):
    """集合模型只消费调用身份/值槽，不依赖 AST、某张图或安全规则。"""

    callsite_id: str
    name: str
    receiver: str
    positional_arguments: list[list[str]]
    keyword_arguments: dict[str, list[str]]


@dataclass(frozen=True)
class CollectionEffect:
    """已核实操作的共同效果；各语言适配语义，公共消费者负责存储/时序而非漏洞判定。"""

    operation: Literal["append", "remove", "slice", "reorder", "iterate"]
    receiver: str
    value_names: tuple[str, ...] = ()
    model_id: str = ""


class CollectionEffectProvider(Protocol):
    """按语言、已确认集合种类和调用选择效果；None 明确表示未建模，不表示只读或安全。"""

    def effect(self, language: str, call: CollectionCallView, allocation: CollectionAllocation) -> CollectionEffect | None:
        """返回一项已支持效果；复杂 API 应另接模型，不能按同名方法一概套用。"""
        ...
