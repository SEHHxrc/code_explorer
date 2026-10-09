"""调用参数的公共声明类别；仅描述绑定，不负责调用目标或污点传播。"""

from typing import Literal

ParameterKind = Literal[
    "positional_only", "positional_or_keyword", "keyword_only",
    "variadic_positional", "variadic_keyword",
]
