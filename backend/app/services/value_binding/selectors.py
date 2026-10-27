"""有界下标表达式契约；只表达常量、名称和有限选择，不执行被分析表达式。"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr


class SelectorExpression(BaseModel):
    """语言前端观察的简单键表达式；其他运算明确为 unknown，不把条件本身当元素值。"""

    model_config = ConfigDict(frozen=True)
    kind: Literal["literal", "name", "choice", "unknown"] = "unknown"
    value: StrictStr | StrictInt | None = None
    name: str = ""
    choices: tuple[SelectorExpression, ...] = ()

    def names(self) -> tuple[str, ...]:
        """返回求值需要的标量名称，供既有声明身份映射使用，不产生污点传递。"""
        return (self.name,) if self.kind == "name" else tuple(dict.fromkeys(
            name for choice in self.choices for name in choice.names()))
