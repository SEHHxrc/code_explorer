"""从到达定义 Overlay 构建带变量映射的局部值流图。"""

from __future__ import annotations

import hashlib

from ..contracts import (
    FunctionProgramGraph,
    ProgramGraphEdge,
    ProgramGraphNode,
    ProgramValueTransfer,
    ValueTransferKind,
)


class ValueFlowPass:
    """把 Def-Use 关系增强为可追踪变量改名和变换的值流边。"""

    def apply(self, graph: FunctionProgramGraph) -> FunctionProgramGraph:
        """保留 CFG/到达定义边，并追加向后兼容的 ``value_flow`` Overlay。"""
        base_edges = [edge for edge in graph.edges if edge.kind != "value_flow"]
        overlay: list[ProgramGraphEdge] = []
        seen: set[tuple[str, str, str, str, str]] = set()
        for edge in base_edges:
            if edge.kind != "reaching_def" or not edge.variable:
                continue
            target = graph.nodes.get(edge.target)
            if target is None:
                continue
            source_variable = edge.variable
            transfers = [
                transfer
                for transfer in target.value_transfers
                if source_variable in transfer.input_variables
            ]
            if not transfers:
                transfers = self._fallback_transfers(target, source_variable)
            for transfer in transfers:
                target_variable = transfer.output_variable
                transfer_kind = transfer.transfer_kind
                key = (
                    edge.source,
                    edge.target,
                    source_variable,
                    target_variable,
                    transfer_kind,
                )
                if key in seen:
                    continue
                seen.add(key)
                certainty = edge.certainty
                if transfer.certainty == "may":
                    certainty = "may"
                material = "\x1f".join(key)
                overlay.append(ProgramGraphEdge(
                    edge_id="value-flow:" + self._digest(material),
                    source=edge.source,
                    target=edge.target,
                    kind="value_flow",
                    source_variable=source_variable,
                    target_variable=target_variable,
                    transfer_kind=transfer_kind,
                    callsite_ids=list(transfer.callsite_ids),
                    certainty=certainty,
                ))
        return graph.model_copy(update={"edges": base_edges + overlay})

    @staticmethod
    def _fallback_transfers(
        node: ProgramGraphNode,
        source_variable: str,
    ) -> list[ProgramValueTransfer]:
        """在语言前端没有精确传递时生成保证完备性的保守关系。"""
        if not node.definitions:
            return [ProgramValueTransfer(
                output_variable=source_variable,
                input_variables=[source_variable],
                transfer_kind="passthrough",
                certainty="must",
                provenance="inferred",
            )]
        if node.calls:
            transfer_kind: ValueTransferKind = "call_result"
        elif len(node.uses) == 1 and len(node.definitions) == 1:
            transfer_kind = "assignment"
        else:
            transfer_kind = "expression"
        uncertain = transfer_kind == "call_result" or len(node.definitions) > 1
        return [
            ProgramValueTransfer(
                output_variable=output,
                input_variables=[source_variable],
                transfer_kind=transfer_kind,
                callsite_ids=[call.callsite_id for call in node.calls],
                certainty="may" if uncertain else "must",
                provenance="inferred",
            )
            for output in node.definitions
        ]

    @staticmethod
    def _digest(value: str) -> str:
        """生成跨运行稳定的值流边标识。"""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
