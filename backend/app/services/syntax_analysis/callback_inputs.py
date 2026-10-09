"""回调输入绑定契约：只描述注册证据，不包含漏洞规则或传播算法。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CallbackParameterInput:
    """注册点观察到的参数类型；回调由原文件字节范围标识，避免同名串联。

    callback_span: 回调函数的原始字节范围，不能仅使用函数名。
    parameter_position: 外部输入对应的零起始形参位置。
    type_name: 适配器规范的接收者/输入类型，由安全规则决定其信任类别。
    registration_span: 注册输入回调的原始字节范围，供维护及后续证据使用。
    limitations: 本次注册关系的有限语义说明，不用于匹配安全规则。
    receiver_key: 原始词法接收者身份，用于同一作用域内匹配请求 data/end；不证明堆别名。
    """

    callback_span: tuple[int, int]
    parameter_position: int
    type_name: str
    registration_span: tuple[int, int]
    limitations: tuple[str, ...] = ()
    receiver_key: str = ""
