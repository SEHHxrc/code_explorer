"""跨图值传播使用的稳定抽象槽位身份。"""

from __future__ import annotations

import hashlib

PARAMETER_SLOT = "parameter"
RETURN_SLOT = "return"
CALL_RESULT_SLOT = "call_result"


def parameter_slot_id(method_id: str, position: int) -> str:
    """返回一个方法形参位置的稳定槽位身份。"""
    return _slot_id(PARAMETER_SLOT, method_id, str(position))


def return_slot_id(method_id: str) -> str:
    """返回一个方法所有 return 分支汇入的抽象槽位身份。"""
    return _slot_id(RETURN_SLOT, method_id)


def call_result_slot_id(callsite_id: str) -> str:
    """返回调用表达式结果在调用者中的稳定槽位身份。"""
    return _slot_id(CALL_RESULT_SLOT, callsite_id)


def _slot_id(kind: str, *parts: str) -> str:
    """根据槽位种类和稳定实体身份生成短摘要。"""
    material = "\x1f".join((kind, *parts))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"slot:{kind}:{digest}"

