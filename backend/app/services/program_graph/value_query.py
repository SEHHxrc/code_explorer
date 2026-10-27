"""公共值查询的后端选择：一函数只使用一份 Def-Use，不并集原生与词法传播结果。"""

from backend.app.services.semantic_index.protocols import SemanticSSAView

from .contracts import FunctionProgramGraph, ProgramGraphArtifact, ValueBackendSelection
from .passes.ssa_values import SSAProjectionUnavailable, SSAValueProjection
from .passes.reaching_definitions import ReachingDefinitionsPass
from .passes.value_flow import ValueFlowPass
from .passes.control_dependence import ControlDependencePass


class ValueFlowQuery:
    """将已保存的编译器事实接入同一 ProgramGraph 值查询界面，不启动语言工具。"""

    def __init__(self, index: SemanticSSAView | None) -> None:
        """接收本次分析的只读索引；工具不可用时沿用现有公共 CFG/DFG。"""
        self.index = index

    def project(self, graph: ProgramGraphArtifact) -> ProgramGraphArtifact:
        """为所有函数选择值流来源；保留每个选择/回退原因，输入图与语义索引不变。"""
        return graph.model_copy(update={"functions": {key: ControlDependencePass().apply(self.function(function)) for key, function in graph.functions.items()}})

    def function(self, function: FunctionProgramGraph) -> FunctionProgramGraph:
        """返回拥有唯一值流来源的函数；TS 编译器绑定不冒充不存在的原生 SSA。"""
        bound = any(node.definition_declarations or node.use_declarations for node in function.nodes.values())
        backend = "compiler_bound_cfg" if bound else "common_cfg"
        reason = "native_ssa_not_available" if function.language == "go" else "native_ssa_not_provided"
        native = self.index.native_ssa_for(function.symbol_id) if self.index else None
        if native is not None:
            try:
                view = SSAValueProjection(function, native).apply()
                return view.model_copy(update={"value_backend": ValueBackendSelection(
                    backend="native_ssa_def_use", identity_backend="compiler" if bound else "source_location",
                    native_symbol=function.symbol_id)})
            except SSAProjectionUnavailable as exc:
                reason = str(exc)
        gaps = sorted({reason for node in function.nodes.values() for reason in node.binding_gaps.values()})
        if function.value_backend is not None and function.value_backend.backend == "native_ssa_def_use":
            # 调用方恢复/重用已投影函数但不再提供原生事实时，标签与实际边也必须一起回退。
            function = ValueFlowPass().apply(ReachingDefinitionsPass().apply(function))
        return function.model_copy(update={"value_backend": ValueBackendSelection(backend=backend,
            identity_backend="partial_compiler" if bound and gaps else "compiler" if bound else "lexical",
            fallback_reason=reason, binding_gaps=gaps)})
