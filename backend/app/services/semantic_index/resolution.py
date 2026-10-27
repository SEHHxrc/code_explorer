"""语言工具的项目级解析契约；不依赖图实现或语言私有 AST。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .contracts import SemanticLocation, SSAFunctionFact


class DeclarationBinding(BaseModel):
    """编译器定位到的声明；声明绑定不等于唯一运行时调用目标。"""

    location: SemanticLocation
    name: str
    kind: Literal["function", "method", "constructor", "class", "signature"]
    has_body: bool = False


DeclarationKey = tuple[str, str, str, int, int]


class DeclarationSymbolIndex:
    """任务级声明身份索引；所有语言共用唯一匹配规则，不按同名猜测。"""

    def __init__(self, definitions: Iterable[tuple[str, DeclarationKey]]) -> None:
        """输入 (符号身份, 路径/名称/种类/起止行)；歧义永久保留为 None。"""
        self._symbols: dict[DeclarationKey, str | None] = {}
        for symbol, key in definitions:
            if key in self._symbols and self._symbols[key] != symbol:
                self._symbols[key] = None
            else:
                self._symbols[key] = symbol

    def symbol_for(self, declaration: DeclarationBinding) -> str | None:
        """常量时间查唯一符号；同位置同名不同作用域不静默选择第一项。"""
        location = declaration.location
        return self._symbols.get((location.path, declaration.name, declaration.kind,
                                  location.line, location.end_line))


class CompilerCallBinding(BaseModel):
    """调用点的声明绑定或明确失败；只有消费者可映射到既有项目符号。"""

    location: SemanticLocation
    declarations: list[DeclarationBinding] = Field(default_factory=list)
    unresolved_reason: str | None = None
    dispatch: Literal["direct", "virtual", "unknown"] = "unknown"


class CompilerTypeBinding(BaseModel):
    """变量、形参或返回值的编译器类型文本，保留源码位置与推断来源。"""

    location: SemanticLocation
    name: str
    type_literal: str
    role: Literal["variable", "parameter", "return"]
    owner: DeclarationBinding | None = None
    owner_scope: Literal["module", "callable"] = "module"


class CompilerVariableDeclaration(BaseModel):
    """编译器变量实体的源码声明；不依赖类型是否已知，也不包含安全角色。"""

    location: SemanticLocation
    name: str
    role: Literal["variable", "parameter"]
    owner: DeclarationBinding | None = None
    owner_scope: Literal["module", "callable"] = "module"


class CompilerVariableReference(BaseModel):
    """变量标识符引用及声明候选；编译器符号对象不跨进程持久化。"""

    location: SemanticLocation
    name: str
    declarations: list[CompilerVariableDeclaration] = Field(default_factory=list)
    unresolved_reason: str | None = None
    owner: DeclarationBinding | None = None
    owner_scope: Literal["module", "callable"] = "module"


class ProjectSemanticResult(BaseModel):
    """单次解析的事实和能力边界；调用方持久化摘要，不持久化 AST。"""

    protocol_version: Literal["1.0"] = "1.0"
    backend: str
    version: str = ""
    status: Literal["completed", "partial", "unavailable", "failed"]
    files: list[str] = Field(default_factory=list)
    calls: list[CompilerCallBinding] = Field(default_factory=list)
    types: list[CompilerTypeBinding] = Field(default_factory=list)
    variable_declarations: list[CompilerVariableDeclaration] = Field(default_factory=list)
    variable_references: list[CompilerVariableReference] = Field(default_factory=list)
    diagnostics: list[dict[str, str | int]] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    implementation_sha256: str = ""
    diagnostic_counts: dict[str, int] = Field(default_factory=dict)
    diagnostics_truncated: bool = False
    program_count: int = 0
    transport_bytes: int = 0
    timings_ms: dict[str, float] = Field(default_factory=dict)
    cache_stats: dict[str, int] = Field(default_factory=dict)
    ssa_functions: list[SSAFunctionFact] = Field(default_factory=list)
    analysis_configuration: dict[str, object] = Field(default_factory=dict)

    def summary(self) -> dict[str, object]:
        """返回可审计的运行摘要；调用与类型正文由公共索引保存。"""
        return {
            "backend": self.backend, "version": self.version, "status": self.status,
            "protocol_version": self.protocol_version,
            "files_considered": len(self.files), "calls_observed": len(self.calls),
            "types_observed": len(self.types), "capabilities": self.capabilities,
            "variable_declarations_observed": len(self.variable_declarations),
            "variable_references_observed": len(self.variable_references),
            "diagnostics": self.diagnostics,
            "implementation_sha256": self.implementation_sha256,
            "diagnostic_counts": self.diagnostic_counts,
            "diagnostics_truncated": self.diagnostics_truncated,
            "program_count": self.program_count, "transport_bytes": self.transport_bytes,
            "timings_ms": self.timings_ms, "cache_stats": self.cache_stats,
            "ssa_functions": len(self.ssa_functions),
            "ssa_blocks": sum(len(item.blocks) for item in self.ssa_functions),
            "ssa_instructions": sum(len(item.instructions) for item in self.ssa_functions),
            "analysis_configuration": self.analysis_configuration,
        }


class ProjectSemanticResolver(Protocol):
    """项目级、只读的语言语义入口；不接收依赖分析器内部可变状态。"""

    def resolve(
        self, project_root: str, *, file_paths: list[str], max_file_bytes: int,
    ) -> ProjectSemanticResult:
        """输入明确文件范围及单文件预算，返回规范事实与未完成原因。"""
        ...
