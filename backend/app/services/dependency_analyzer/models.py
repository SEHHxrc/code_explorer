# -*- coding: utf-8 -*-
from __future__ import annotations

"""分析流水线使用的不可跨线程共享数据载体。"""
from dataclasses import dataclass, field

@dataclass
class Definition:
    """一处代码定义（模块 / 类 / 结构体 / 函数 / 方法 / 字段 / 全局变量）。"""
    fqn: str
    name: str
    kind: str
    file: str
    lang: str
    line: int = 0
    end_line: int = 0
    parent_fqn: str = ""
    owner_literal: str = ""      # 成员所属类型字面量（Go 接收者 / Rust impl / C++ 类外定义）
    bases: list = field(default_factory=list)        # 继承 / 嵌入
    implements: list = field(default_factory=list)   # 接口实现
    type_literal: str = ""       # 字段或变量的声明类型，用于属性类型推断
    return_type: str = ""        # 函数/方法返回值类型，用于跨调用的类型推断
    is_declaration: bool = False  # C/C++ 的原型声明（无函数体）

@dataclass
class Reference:
    """一处尚未解析的引用（调用 / 实例化 / 类型使用）。"""
    file: str
    from_fqn: str
    kind: str            # call | new | typeref
    name: str
    receiver: str = ""   # 接收者原文：'' / self / 局部变量 / 模块别名 / 类名
    line: int = 0
    class_fqn: str = ""  # 引用发生处所在的 class-like 作用域
    lang: str = ""

@dataclass
class ImportRec:
    """一条待解析导入；输入模块、别名、具体符号、导入类型和源码位置。"""
    file: str
    module: str          # 模块字面量：os.path / ./model/animal / fmt / util.h / com.demo.Animal
    alias: str = ""      # 本地绑定名
    symbol: str = ""     # 具体导入的符号名（'' 表示整模块，'*' 表示通配）
    kind: str = "module"  # module | symbol | wildcard | system | submodule
    line: int = 0

@dataclass
class Frame:
    """作用域帧。"""
    fqn: str
    kind: str            # module | class | function | method | namespace | impl
    name: str = ""
    definition: Definition | None = None
    owner_literal: str = ""
    var_types: dict = field(default_factory=dict)
