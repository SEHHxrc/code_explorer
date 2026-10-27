"""编译器共用的紧凑表协议；无损恢复公共事实，不依赖语言私有 AST。"""

from __future__ import annotations

from typing import Any, TypeVar

from backend.app.services.semantic_index.contracts import SemanticLocation, SSABlockFact, SSAFunctionFact, SSAInstructionFact, SSAValueFact
from backend.app.services.semantic_index.resolution import CompilerCallBinding, CompilerTypeBinding, CompilerVariableDeclaration, CompilerVariableReference, DeclarationBinding, ProjectSemanticResult

TableItem = TypeVar("TableItem")


def referenced(table: list[TableItem], index: int) -> TableItem:
    """按整数外键取共享项；拒绝负数、布尔值和越界，防止错误绑定。"""
    if type(index) is not int or not 0 <= index < len(table):
        raise ValueError("Invalid compiler table reference")
    return table[index]


def row(value: Any, length: int) -> list[Any]:
    """校验定长行形状；字段增删须显式升级协议，不能悄悄忽略。"""
    if not isinstance(value, list) or len(value) != length:
        raise ValueError("Invalid compiler table row")
    return value


def decode_table_result(payload: dict[str, Any]) -> ProjectSemanticResult:
    """解码 tables-v1 的调用/类型及可选 SSA 表，不原地修改输入或展开重复文本。

    输入是工作进程的有界 JSON；返回同一套公共模型，保留位置、操作数顺序、φ、零号投影
    及未知位置。共享表只节省传输，不删事实；协议校验不证明传播或安全结论正确。
    """
    raw = dict(payload)
    if raw.pop("wire_format") != "tables-v1":
        raise ValueError("Unsupported compiler wire format")
    files, literals = raw["files"], raw.pop("type_literals")
    if not isinstance(files, list) or not isinstance(literals, list) or any(
        not isinstance(value, str) for value in [*files, *literals]
    ):
        raise ValueError("Invalid compiler string table")

    def location(value: list[Any]) -> SemanticLocation:
        """位置为文件外键及四个一基 UTF-8 坐标，结束位置开区间。"""
        item = row(value, 5)
        return SemanticLocation(path=referenced(files, item[0]), line=item[1], column=item[2],
                                end_line=item[3], end_column=item[4])

    declarations = []
    for value in raw.pop("declarations"):
        item = row(value, 8)
        declarations.append(DeclarationBinding(location=location(item[:5]), name=item[5],
                                               kind=item[6], has_body=item[7]))
    calls = []
    for value in raw.pop("calls"):
        item = row(value, 8)
        calls.append(CompilerCallBinding(location=location(item[:5]),
                                        declarations=[referenced(declarations, index) for index in item[5]],
                                        unresolved_reason=item[6], dispatch=item[7]))
    types = []
    for value in raw.pop("types"):
        item = row(value, 10)
        types.append(CompilerTypeBinding(location=location(item[:5]), name=item[5],
                                         type_literal=referenced(literals, item[6]), role=item[7],
                                         owner=referenced(declarations, item[8]) if item[8] is not None else None,
                                         owner_scope=item[9]))
    locations = [location(value) for value in raw.pop("locations", [])]
    variables = []
    for value in raw.pop("variable_declarations", []):
        item = row(value, 9)
        variables.append(CompilerVariableDeclaration(location=location(item[:5]), name=item[5], role=item[6],
            owner=referenced(declarations, item[7]) if item[7] is not None else None, owner_scope=item[8]))
    variable_references = []
    for value in raw.pop("variable_references", []):
        item = row(value, 10)
        variable_references.append(CompilerVariableReference(location=location(item[:5]), name=item[5],
            declarations=[referenced(variables, index) for index in item[6]], unresolved_reason=item[7],
            owner=referenced(declarations, item[8]) if item[8] is not None else None, owner_scope=item[9]))

    def optional_location(index: int | None) -> SemanticLocation | None:
        """None 表示无源码位置；0 是有效外键，不能按真假丢弃。"""
        return referenced(locations, index) if index is not None else None

    functions = []
    for value in raw.pop("ssa_functions", []):
        item = row(value, 9)
        values = {}
        for key, value in item[5].items():
            value = row(value, 4)
            values[key] = SSAValueFact(kind=value[0], name=value[1],
                                       type_literal=referenced(literals, value[2]),
                                       location=optional_location(value[3]))
        instructions = {}
        for key, value in item[6].items():
            value = row(value, 12)
            instructions[key] = SSAInstructionFact(
                opcode=value[0], block=value[1], operands=value[2], result=value[3],
                location=optional_location(value[4]), call_location=optional_location(value[5]),
                source_name=value[6], call_arguments=value[7], receiver_value=value[8],
                invocation_kind=value[9], projection_index=value[10], operator=value[11])
        blocks = []
        for value in item[7]:
            value = row(value, 3)
            blocks.append(SSABlockFact(predecessors=value[0], successors=value[1], instructions=value[2]))
        functions.append(SSAFunctionFact(
            name=item[0], kind=item[1], location=referenced(locations, item[2]),
            parameters=item[3], receiver_parameter=item[4], values=values, instructions=instructions,
            blocks=blocks, limitations=item[8]))
    return ProjectSemanticResult(**raw, calls=calls, types=types, ssa_functions=functions,
                                 variable_declarations=variables, variable_references=variable_references)
