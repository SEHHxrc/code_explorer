"""公共变量引用投影和查询；不解析名字，不推断动态对象或建立数据流边。"""

from collections import defaultdict
import hashlib

from backend.app.services.program_index import ProgramIdentity
from .contracts import SemanticIndexArtifact, SemanticLocation, VariableDeclarationFact, VariableReferenceFact, VariableTypeFact
from .locations import matches_location, normalized_location
from .resolution import CompilerVariableDeclaration, DeclarationBinding, DeclarationSymbolIndex, ProjectSemanticResult


def project_variable_references(artifact: SemanticIndexArtifact, results: list[ProjectSemanticResult],
                               symbols: DeclarationSymbolIndex) -> SemanticIndexArtifact:
    """把编译器绑定投影为稳定声明/引用；保留未知与冲突，不更改输入或创建 CALLS/DFG。"""
    declarations = dict(artifact.variable_declarations)
    references = dict(artifact.variable_references)
    registered: dict[tuple[str, int], str] = {}

    def owner_for(owner: DeclarationBinding | None, scope: str, path: str) -> tuple[str, str | None]:
        """仅将已映射函数声明或模块转成所属身份，匿名/未映射函数不归入外层。"""
        if scope == "module":
            return ProgramIdentity.file_id(path), None
        symbol = symbols.symbol_for(owner) if owner is not None else None
        return (symbol, None) if symbol is not None else ("", "reference_owner_not_mapped")

    def register(item: CompilerVariableDeclaration, backend: str) -> str:
        """登记源码实体；类型 unknown 不影响绑定，坐标不足则不伪造声明身份。"""
        key = (backend, id(item))
        if key in registered:
            return registered[key]
        location = normalized_location(item.location)
        owner, reason = owner_for(item.owner, item.owner_scope, location.path)
        identity = ProgramIdentity.declaration_id(owner, item.name, item.role, location.path, location.line,
                                                  location.column, location.end_line, location.end_column)
        if identity:
            declarations[identity] = VariableDeclarationFact(declaration_id=identity, owner_symbol=owner,
                name=item.name, role=item.role, language=_language(backend, location.path), location=location,
                owner_unresolved_reason=reason)
        registered[key] = identity
        return identity

    for result in results:
        for item in result.variable_declarations:
            register(item, result.backend)
        for item in result.variable_references:
            location = normalized_location(item.location)
            owner, owner_reason = owner_for(item.owner, item.owner_scope, location.path)
            targets = [register(target, result.backend) for target in item.declarations]
            reason = item.unresolved_reason or ("variable_declaration_location_incomplete" if any(not target for target in targets)
                                                else None if targets else "compiler_variable_reference_unresolved")
            if not all((location.line, location.column, location.end_line, location.end_column)):
                reason = "variable_reference_location_incomplete"
            identity = "varref:" + hashlib.sha256("\x1f".join((result.backend, location.location_id, item.name)).encode()).hexdigest()[:24]
            fact = VariableReferenceFact(reference_id=identity, owner_symbol=owner, name=item.name,
                language=_language(result.backend, location.path), location=location,
                declaration_ids=sorted(set(filter(None, targets))), unresolved_reason=reason,
                owner_unresolved_reason=owner_reason, resolution_method=f"{result.backend}_identifier_binding")
            previous = references.get(identity)
            if previous is not None and previous != fact:
                fact = fact.model_copy(update={"declaration_ids": sorted(set(previous.declaration_ids + fact.declaration_ids)),
                                              "unresolved_reason": "compiler_variable_reference_conflict",
                                              **({"owner_symbol": "", "owner_unresolved_reason": "reference_owner_conflict"}
                                                 if previous.owner_symbol != fact.owner_symbol else {})})
            references[identity] = fact
    if not any(result.variable_declarations or result.variable_references for result in results):
        return artifact
    return artifact.model_copy(update={"schema_version": artifact.schema_version if artifact.schema_version in {"1.4", "1.5"} else "1.3",
                                       "variable_declarations": declarations, "variable_references": references})


def _language(backend: str, path: str) -> str:
    """共用后端名到现有语言身份的映射，不从名字推断安全角色。"""
    return ("typescript" if path.endswith((".ts", ".tsx", ".mts", ".cts")) else "javascript") if backend == "typescript" else backend


class VariableReferenceIndex:
    """单任务变量引用索引；按位置/声明反查，不在查询时遍历完整仓库。"""

    def __init__(self, artifact: SemanticIndexArtifact) -> None:
        """预建引用位置、声明入引用与声明类型表；不把旧名字槽补成绑定。"""
        self.artifact = artifact
        by_line: dict[tuple[str, int], list[VariableReferenceFact]] = defaultdict(list)
        by_declaration: dict[str, list[VariableReferenceFact]] = defaultdict(list)
        types: dict[str, list[VariableTypeFact]] = defaultdict(list)
        for item in artifact.variable_references.values():
            by_line[(ProgramIdentity.file_id(item.location.path), item.location.line)].append(item)
            for identity in item.declaration_ids:
                by_declaration[identity].append(item)
        self._by_line = {key: tuple(sorted(items, key=lambda item: (item.location.column, item.reference_id))) for key, items in by_line.items()}
        self._by_declaration = {key: tuple(sorted(items, key=lambda item: (item.location.path, item.location.line, item.location.column, item.reference_id)))
                                for key, items in by_declaration.items()}
        for item in artifact.variable_types.values():
            if item.declaration_id:
                types[item.declaration_id].append(item)
        self._types = {key: tuple(sorted(items, key=lambda item: item.fact_id)) for key, items in types.items()}

    def at(self, location: SemanticLocation, owner_symbol: str | None) -> tuple[VariableReferenceFact, ...]:
        """返回位置上的全部引用，未知列不猜测，匿名函数可显式保留空所属身份。"""
        return tuple(item for item in self._by_line.get((ProgramIdentity.file_id(location.path), location.line), ())
                     if (owner_symbol is None or item.owner_symbol == owner_symbol) and matches_location(location, item.location))

    def to(self, declaration_id: str) -> tuple[VariableReferenceFact, ...]:
        """返回指向源码声明的引用，包括写引用；不是运行时调用者或到达定义集合。"""
        return self._by_declaration.get(declaration_id, ())

    def declaration_types(self, reference_id: str) -> tuple[VariableTypeFact, ...]:
        """返回已绑定声明的类型候选，不是使用点流敏感类型；冲突/悬空引用不补造结果。"""
        reference = self.artifact.variable_references.get(reference_id)
        if reference is None or reference.unresolved_reason or any(key not in self.artifact.variable_declarations for key in reference.declaration_ids):
            return ()
        return tuple(item for identity in reference.declaration_ids for item in self._types.get(identity, ()))
