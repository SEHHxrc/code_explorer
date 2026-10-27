"""将已有声明/引用事实投影为操作的值槽根；不重新解析源码或猜测最近声明。"""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Sequence
from typing import TypeVar

from backend.app.services.semantic_index import SemanticIndex, SemanticIndexArtifact
from backend.app.services.semantic_index.contracts import VariableDeclarationFact, VariableReferenceFact

from ..contracts import FunctionProgramGraph, ProgramGraphLocation, ProgramGraphArtifact
from backend.app.services.value_binding.identities import variable_root
from .reaching_definitions import ReachingDefinitionsPass
from .value_flow import ValueFlowPass

_Fact = TypeVar("_Fact", VariableDeclarationFact, VariableReferenceFact)


class DeclarationBindingPass:
    """任务内范围索引；同一操作中不能唯一绑定的名字保留缺口，不选第一项。"""

    def __init__(self, index: SemanticIndex | SemanticIndexArtifact) -> None:
        """输入已保存的公共事实，按文件/所属函数建立只读查询表。"""
        self.artifact = index.artifact if isinstance(index, SemanticIndex) else index
        self._declarations: dict[tuple[str, str], list[VariableDeclarationFact]] = defaultdict(list)
        self._references: dict[tuple[str, str], list[VariableReferenceFact]] = defaultdict(list)
        self._parameters: dict[str, list[VariableDeclarationFact]] = defaultdict(list)
        for item in self.artifact.variable_declarations.values():
            self._declarations[(item.location.path, item.owner_symbol)].append(item)
            if item.role == "parameter":
                self._parameters[item.owner_symbol].append(item)
        for item in self.artifact.variable_references.values():
            self._references[(item.location.path, item.owner_symbol)].append(item)
        self._lines: dict[tuple[str, str, str], list[int]] = {}
        for kind, table in (("declaration", self._declarations), ("reference", self._references)):
            for key, items in table.items():
                items.sort(key=lambda item: item.location.line)
                self._lines[(kind, *key)] = [item.location.line for item in items]

    def apply(self, graph: FunctionProgramGraph) -> FunctionProgramGraph:
        """输入 CFG，返回附带声明根映射的新图；无事实时保留非原生行为。"""
        key = (graph.location.path, graph.symbol_id)
        if key not in self._declarations and key not in self._references:
            return graph
        nodes = {}
        for node in graph.nodes.values():
            if node.kind == "method_exit" or node.location is None:
                nodes[node.node_id] = node
                continue
            declared = self._within(self._declarations.get(key, ()), self._lines.get(("declaration", *key), ()), node.location)
            referenced = self._within(self._references.get(key, ()), self._lines.get(("reference", *key), ()), node.location)
            if node.kind == "method_entry":
                declared, referenced = self._parameters[graph.symbol_id], []
            writes, reads, gaps = {}, {}, {}
            for role, names, output in (("write", node.definitions, writes), ("read", node.uses, reads)):
                for root in sorted({variable_root(name) for name in names} - {""}):
                    declarations = [item for item in declared if item.name == root] if role == "write" else []
                    references = [item for item in referenced if item.name == root]
                    # 新声明位置决定写入身份，RHS 同名引用仍绑定外层实体。
                    identities = {item.declaration_id for item in declarations}
                    invalid = False
                    if not identities:
                        identities = {identity for item in references for identity in item.declaration_ids}
                        invalid = any(item.unresolved_reason or item.owner_unresolved_reason for item in references)
                    known = {identity for identity in identities if identity in self.artifact.variable_declarations}
                    invalid |= any(self.artifact.variable_declarations[identity].owner_unresolved_reason for identity in known)
                    if len(known) == 1 and known == identities and not invalid:
                        output[root] = next(iter(known))
                    else:
                        gaps[role + ":" + root] = ("ambiguous_declaration_binding" if len(identities) > 1 else
                            "unresolved_declaration_binding" if invalid or identities else "declaration_binding_not_available")
            nodes[node.node_id] = node.model_copy(update={"definition_declarations": writes,
                "use_declarations": reads, "binding_gaps": gaps})
        return graph.model_copy(update={"nodes": nodes})

    def project(self, graph: ProgramGraphArtifact) -> ProgramGraphArtifact:
        """旧调用方只提供语义索引时在私有图补充；服务已应用则不重复投影/计算。"""
        if "declaration_bindings" in graph.overlays:
            return graph
        functions = {}
        for key, function in graph.functions.items():
            bound = self.apply(function)
            functions[key] = ValueFlowPass().apply(ReachingDefinitionsPass().apply(bound)) if bound != function else function
        return graph.model_copy(update={"functions": functions, "overlays": [*graph.overlays, "declaration_bindings"]})

    @staticmethod
    def _within(items: Sequence[_Fact], lines: Sequence[int], location: ProgramGraphLocation) -> list[_Fact]:
        """精确源码范围筛选，列坐标缺失不作唯一绑定；不扫描整个语义索引。"""
        start = (location.line, location.column)
        end = (location.end_line, location.end_column)
        return [item for item in items[bisect_left(lines, location.line):bisect_right(lines, location.end_line)]
                if item.location.column and item.location.end_column
                and start <= (item.location.line, item.location.column)
                and (item.location.end_line, item.location.end_column) <= end]
