"""对象字段值槽的语言无关契约；不解析 AST、求解引用或判断安全语义。"""

import hashlib

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal
from .access_paths import AccessPathSegment, canonical_path, member_path


def instance_field_name(root: str, owner: str, name: str) -> str:
    """输入展示根、已解析类型与直属字段，输出稳定类型子槽；不是运行时对象身份。"""
    if name.startswith("["):
        return member_path(root, name)
    key = hashlib.sha256(owner.encode()).hexdigest()[:16]
    return root + "." + name + '["@instance:' + key + '"]'


class ObjectFieldSlot(BaseModel):
    """一个已知分配的直属字段；可选实际类型仅隔离工厂变体，不表示字段已受污。"""

    model_config = ConfigDict(frozen=True)

    allocation_id: str = Field(min_length=1)
    field_name: str
    owner_symbol: str | None = Field(default=None, min_length=1)

    @field_validator("field_name")
    @classmethod
    def direct_field(cls, value: str) -> str:
        """仅接受一个直属标识符；嵌套路径、动态下标及容器引用不能冒充此契约。"""
        if not value.isidentifier():
            raise ValueError("ObjectFieldSlot requires a direct identifier field")
        return value

    @property
    def value_id(self) -> str:
        """输出公共 RD/DFG 消费的稳定身份，保持与 1.9 字段槽一致，不含变量展示根。"""
        suffix = instance_field_name("", self.owner_symbol, self.field_name) if self.owner_symbol else "." + self.field_name
        return "object:" + self.allocation_id + suffix


class CollectionElementSlot(BaseModel):
    """一个分配中一个固定键/下标的值槽；整数 0 与字符串 '0' 在 typed 语义下不同。"""

    model_config = ConfigDict(frozen=True)

    allocation_id: str = Field(min_length=1)
    selector: str | int

    @field_validator("selector", mode="before")
    @classmethod
    def static_selector(cls, value: object) -> str | int:
        """拒绝 bool、浮点数和隐式字符串转换，避免不相等的键共享值槽。"""
        if type(value) not in {str, int}:
            raise ValueError("Collection selector must be a static string or integer")
        return value

    @property
    def field_name(self) -> str:
        """返回规范元素后缀，使对象快照/写回装配复用同一成员接口。"""
        return AccessPathSegment("index", self.selector).render()

    @property
    def value_id(self) -> str:
        """输出按分配与键隔离的公共 RD/DFG 身份，不把集合地址当作元素内容。"""
        return "object:" + self.allocation_id + self.field_name


class CollectionSummarySlot(BaseModel):
    """未知键可能读写的摘要槽；不是字符串 '?'，不能与真实字典键混用。"""

    model_config = ConfigDict(frozen=True)
    allocation_id: str = Field(min_length=1)
    summary: Literal[True]

    @property
    def field_name(self) -> str:
        """规范未知选择器后缀，不冒充任何固定键。"""
        return "[?]"

    @property
    def value_id(self) -> str:
        """输出同一分配的摘要身份，定义必须按 may 更新。"""
        return "object:" + self.allocation_id + "[?]"


StorageValueSlot = ObjectFieldSlot | CollectionElementSlot | CollectionSummarySlot


def storage_slot(allocation_id: str, member: str, owner_symbol: str | None = None) -> StorageValueSlot:
    """将已验证的直属成员装配成字段槽或元素槽；所有消费者共用而非猜测字符串格式。"""
    if member.startswith("["):
        if member == "[?]":
            return CollectionSummarySlot(allocation_id=allocation_id, summary=True)
        path = canonical_path("collection" + member)
        if path is None or len(path.segments) != 1 or path.segments[0].kind != "index":
            raise ValueError("Not a direct collection member")
        return CollectionElementSlot(allocation_id=allocation_id, selector=path.segments[0].value)
    return ObjectFieldSlot(allocation_id=allocation_id, field_name=member, owner_symbol=owner_symbol)


def legacy_object_allocation_id(value_id: str) -> str | None:
    """只读取历史 1.9 私有 Overlay 的规范身份；新字段槽应直接读取 allocation_id。"""
    if not value_id.startswith("object:"):
        return None
    allocation, separator, _ = value_id[len("object:"):].partition(".")
    return allocation if allocation and separator else None
