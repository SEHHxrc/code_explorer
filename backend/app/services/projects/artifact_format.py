"""语义实体的无损落盘表示；共享重复文本与字段布局，不改变公开领域契约。"""

from __future__ import annotations

from collections import Counter
from typing import Any

STORAGE_FORMAT = "semantic-entities-v1"


def _referenced(table: list[Any], index: int) -> Any:
    """只接受有效整数外键；布尔值、负数和越界不隐式纠正。"""
    if type(index) is not int or not 0 <= index < len(table):
        raise ValueError("Invalid semantic entity table reference")
    return table[index]


def pack_semantic_index(index: dict[str, Any]) -> dict[str, Any]:
    """输入普通语义索引，输出版本化共享表；不修改原对象、不删默认值或未知字段。"""
    counts: Counter[str] = Counter()
    pending = [index]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise TypeError("Semantic entity keys must be strings")
            counts.update(key for key in value if len(key) >= 16)
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
        elif isinstance(value, str) and len(value) >= 16:
            counts[value] += 1
    strings = [value for value, count in counts.items() if count > 1]
    del counts, pending
    string_ids = {value: number for number, value in enumerate(strings)}
    shapes: list[list[str | int]] = []
    shape_ids: dict[tuple[str, ...], int] = {}

    def string(value: str) -> str | int:
        """共享字符串用负整数引用；非重复短文本原样保存。"""
        number = string_ids.get(value)
        return -number - 1 if number is not None else value

    def encode(value: Any) -> Any:
        """对象存字段布局与值行；列表和原始负数显式标记，避免与引用冲突。"""
        if isinstance(value, dict):
            if not value:
                return {}
            keys = tuple(value)
            number = shape_ids.get(keys)
            if number is None:
                number = len(shapes)
                shape_ids[keys] = number
                shapes.append([string(key) for key in keys])
            return [number, [encode(item) for item in value.values()]]
        if isinstance(value, (list, tuple)):
            return [-1, [encode(item) for item in value]] if value else []
        if isinstance(value, str):
            return string(value)
        if type(value) is int and value < 0:
            return [-2, value]
        if value is None or type(value) in (bool, int, float):
            return value
        raise TypeError("Semantic entity value is not JSON serializable")

    root = encode(index)
    return {"storage_format": STORAGE_FORMAT, "strings": strings, "shapes": shapes, "root": root}


def unpack_semantic_index(payload: dict[str, Any]) -> dict[str, Any]:
    """还原与保存前相等的普通字典；共享不可变文本，分别创建每个可变容器。"""
    if payload.get("storage_format") != STORAGE_FORMAT or set(payload) != {"storage_format", "strings", "shapes", "root"}:
        raise ValueError("Unsupported semantic entity storage format")
    strings, raw_shapes = payload["strings"], payload["shapes"]
    if not isinstance(strings, list) or any(not isinstance(item, str) for item in strings) or not isinstance(raw_shapes, list):
        raise ValueError("Invalid semantic entity tables")

    def string(value: Any) -> str:
        """解码字段名；不把布尔值、正数或错误类型当成外键。"""
        if isinstance(value, str):
            return value
        if type(value) is int and value < 0:
            return _referenced(strings, -value - 1)
        raise ValueError("Invalid semantic entity key")

    shapes = []
    for raw in raw_shapes:
        if not isinstance(raw, list):
            raise ValueError("Invalid semantic entity shape")
        keys = [string(value) for value in raw]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate semantic entity field")
        shapes.append(keys)

    def decode(value: Any) -> Any:
        """保留零值、空值、列表顺序和原始负数；损坏的引用或行明确报错。"""
        if isinstance(value, list):
            if not value:
                return []
            if len(value) != 2 or type(value[0]) is not int:
                raise ValueError("Invalid semantic entity row")
            tag, content = value
            if tag == -2 and type(content) is int and content < 0:
                return content
            if not isinstance(content, list):
                raise ValueError("Invalid semantic entity values")
            if tag == -1:
                return [decode(item) for item in content]
            keys = _referenced(shapes, tag)
            if len(keys) != len(content):
                raise ValueError("Invalid semantic entity field count")
            return {key: decode(item) for key, item in zip(keys, content)}
        if isinstance(value, dict):
            if value:
                raise ValueError("Unencoded semantic entity object")
            return {}
        if type(value) is int and value < 0:
            return _referenced(strings, -value - 1)
        return value

    result = decode(payload["root"])
    if not isinstance(result, dict):
        raise ValueError("Semantic entity root must be an object")
    return result
