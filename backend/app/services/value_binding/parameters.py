"""调用参数的公共声明类别；仅描述绑定，不负责调用目标或污点传播。"""

from typing import Literal
from collections.abc import Iterable, Mapping
import json

from .access_paths import canonical_path

ParameterKind = Literal[
    "positional_only", "positional_or_keyword", "keyword_only",
    "variadic_positional", "variadic_keyword",
]


def mapping_member_bindings(parameters: Mapping[str, ParameterKind], roots: Iterable[str],
                            provided: set[str], *, max_members: int = 128) -> list[tuple[str, str]]:
    """将未知映射投影到有界关键字槽；只表达 may 关系，不证明键存在或调用成功。

    位置专用参数不能从映射接收值，显式已提供参数不被另一个展开覆盖；额外关键字
    参数保留映射根，其后续成员读取仍由同一集合值流求解。
    """
    result = []
    for root in dict.fromkeys(roots):
        if canonical_path(root) is None:
            continue
        for name, kind in list(parameters.items())[:max_members]:
            if name in provided or kind in {"positional_only", "variadic_positional"}:
                continue
            result.append((name, root if kind == "variadic_keyword" else root + "[" + json.dumps(name, ensure_ascii=False) + "]"))
    return result
