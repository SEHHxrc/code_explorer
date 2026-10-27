"""绑定、返回和条件查询共同使用的纯值表达式；不依赖图或语言 AST。"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr


class ConditionExpression(BaseModel):
    """不可变的有界标量表达式，未知语义显式保留，不能执行项目代码。"""

    model_config = ConfigDict(frozen=True)
    kind: Literal["unknown", "literal", "name", "not", "and", "or", "compare", "arithmetic"] = "unknown"
    value: StrictBool | StrictInt | StrictStr | None = None
    name: str = ""
    operator: Literal["", "eq", "ne", "lt", "le", "gt", "ge", "add", "sub", "mul", "neg"] = ""
    operands: tuple[ConditionExpression, ...] = ()
    reason: str = "condition_not_modelled"

    def names(self) -> tuple[str, ...]:
        """返回全部读取名，供绑定/帧投影复用，不从表达式展示文本反解析。"""
        return (self.name,) if self.kind == "name" else tuple(dict.fromkeys(name for item in self.operands for name in item.names()))
