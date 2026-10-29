"""从公共结构化控制 IR 构建语言无关 CFG。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from backend.app.services.value_binding import BindingValue, StructuredBindingResolver

from ..contracts import (
    EdgeCertainty,
    FunctionProgramGraph,
    ProgramCallSite,
    ProgramEdgeBranch,
    ProgramGraphEdge,
    ProgramGraphNode,
    ProgramValueTransfer,
    ProgramNodeKind,
)
from ..control_ir import ControlFunction, ControlStatement
from .return_capture import capture_return

Exit = tuple[str, ProgramEdgeBranch]


@dataclass
class _Fragment:
    """CFG 片段及尚未接入外层结构的出口。"""

    entry: str | None = None
    normal_exits: list[Exit] = field(default_factory=list)
    break_exits: list[str] = field(default_factory=list)
    continue_exits: list[str] = field(default_factory=list)
    return_exits: list[str] = field(default_factory=list)
    throw_exits: list[str] = field(default_factory=list)


class ControlFlowGraphBuilder:
    """组合结构化片段，统一处理顺序、分支、循环和突然退出。"""

    def __init__(self) -> None:
        """初始化单函数构建状态。"""
        self._function: ControlFunction | None = None
        self._nodes: dict[str, ProgramGraphNode] = {}
        self._edges: list[ProgramGraphEdge] = []
        self._edge_keys: set[tuple[str, str, str, str]] = set()
        self._exception_depth = 0

    def build(self, function: ControlFunction) -> FunctionProgramGraph:
        """输入公共函数 IR，输出带显式入口和出口的函数级 CFG。"""
        self._function = function
        self._nodes = {}
        self._edges = []
        self._edge_keys = set()
        self._exception_depth = 0
        entry_id = f"method-entry:{self._digest(function.method_id)}"
        exit_id = f"method-exit:{self._digest(function.method_id)}"
        self._nodes[entry_id] = ProgramGraphNode(
            node_id=entry_id,
            kind="method_entry",
            language=function.language,
            method_id=function.method_id,
            location=function.location,
            code=function.name,
            definitions=list(dict.fromkeys((*function.parameters, *(
                projection.output
                for index, pattern in enumerate(function.parameter_patterns)
                if index < len(function.parameters)
                for projection in StructuredBindingResolver().resolve(
                    pattern, BindingValue(variables=(function.parameters[index],))
                ).projections
            )))),
            provenance="generated",
        )
        self._nodes[exit_id] = ProgramGraphNode(
            node_id=exit_id,
            kind="method_exit",
            language=function.language,
            method_id=function.method_id,
            location=function.location,
            code=function.name,
            provenance="generated",
        )
        fragment = self._sequence(function.body)
        if fragment.entry is None:
            self._edge(entry_id, exit_id, "normal")
        else:
            self._edge(entry_id, fragment.entry, "normal")
            self._connect_exits(fragment.normal_exits, exit_id)
        for node_id in fragment.return_exits:
            self._edge(node_id, exit_id, "return")
        for node_id in fragment.throw_exits:
            self._edge(node_id, exit_id, "exception", certainty="may", source_before_write=self._nodes[node_id].kind != "throw")
        for node_id in fragment.break_exits:
            self._edge(node_id, exit_id, "break", certainty="may")
        for node_id in fragment.continue_exits:
            self._edge(node_id, exit_id, "continue", certainty="may")
        return FunctionProgramGraph(
            method_id=function.method_id,
            symbol_id=function.symbol_id,
            language=function.language,
            name=function.name,
            location=function.location,
            parameters=list(function.parameters),
            parameter_patterns=list(function.parameter_patterns),
            parameter_kinds=dict(function.parameter_kinds),
            parameter_defaults=dict(function.parameter_defaults),
            instance_context=function.instance_context,
            deferred_return=function.deferred_return,
            object_assignment=function.object_assignment,
            normal_exit_writes_modelled=function.normal_exit_writes_modelled,
            has_body=function.has_body,
            storage_parameters=dict(function.storage_parameters),
            entry_node_id=entry_id,
            exit_node_id=exit_id,
            nodes=self._nodes,
            edges=self._edges,
            limitations=list(function.limitations),
        )

    def _sequence(self, statements: tuple[ControlStatement, ...]) -> _Fragment:
        """按源码顺序组合语句，并保留不可达语句节点供诊断和展示。"""
        combined = _Fragment()
        reachable = True
        for statement in statements:
            current = self._statement(statement)
            current_reachable = combined.entry is None or reachable
            if combined.entry is None:
                combined.entry = current.entry
                combined.normal_exits = current.normal_exits
            elif reachable and current.entry is not None:
                self._connect_exits(combined.normal_exits, current.entry)
                combined.normal_exits = current.normal_exits
            if current_reachable:
                combined.break_exits.extend(current.break_exits)
                combined.continue_exits.extend(current.continue_exits)
                combined.return_exits.extend(current.return_exits)
                combined.throw_exits.extend(current.throw_exits)
            reachable = reachable and bool(current.normal_exits)
        return combined

    def _statement(self, statement: ControlStatement) -> _Fragment:
        """按公共语句种类构造一个 CFG 片段。"""
        if statement.kind == "if":
            return self._if(statement)
        if statement.kind == "loop":
            return self._loop(statement)
        if statement.kind == "try":
            return self._try(statement)
        node_id = self._add_node(statement)
        implicit = [node_id] if self._exception_depth and statement.calls else []
        if statement.kind == "return":
            return _Fragment(entry=node_id, return_exits=[node_id], throw_exits=implicit)
        if statement.kind == "throw":
            return _Fragment(entry=node_id, throw_exits=[node_id])
        if statement.kind == "break":
            return _Fragment(entry=node_id, break_exits=[node_id])
        if statement.kind == "continue":
            return _Fragment(entry=node_id, continue_exits=[node_id])
        return _Fragment(entry=node_id, normal_exits=[(node_id, "normal")], throw_exits=implicit)

    def _if(self, statement: ControlStatement) -> _Fragment:
        """连接条件的 true/false 分支并汇总两侧出口。"""
        condition = self._add_node(statement, override_kind="condition")
        consequence = self._sequence(statement.body)
        alternative = self._sequence(statement.alternative)
        normal: list[Exit] = []
        if consequence.entry is None:
            normal.append((condition, "true"))
        else:
            self._edge(condition, consequence.entry, "true")
            normal.extend(consequence.normal_exits)
        if alternative.entry is None:
            normal.append((condition, "false"))
        else:
            self._edge(condition, alternative.entry, "false")
            normal.extend(alternative.normal_exits)
        return _Fragment(
            entry=condition,
            normal_exits=normal,
            break_exits=consequence.break_exits + alternative.break_exits,
            continue_exits=consequence.continue_exits + alternative.continue_exits,
            return_exits=consequence.return_exits + alternative.return_exits,
            throw_exits=consequence.throw_exits + alternative.throw_exits + ([condition] if self._exception_depth and statement.calls else []),
        )

    def _loop(self, statement: ControlStatement) -> _Fragment:
        """连接循环条件、回边、continue 和待外层消费的 break。"""
        condition = self._add_node(statement, override_kind="condition")
        body = self._sequence(statement.body)
        alternative = self._sequence(statement.alternative)
        if body.entry is None:
            self._edge(condition, condition, "true", certainty="may", loop_back=True)
        else:
            self._edge(condition, body.entry, "true")
            for node_id, branch in body.normal_exits:
                self._edge(node_id, condition, branch if branch in {"true", "false"} else "back", certainty="may", loop_back=True)
            for node_id in body.continue_exits:
                self._edge(node_id, condition, "continue", certainty="may", loop_back=True)
        if alternative.entry is not None:
            self._edge(condition, alternative.entry, "false")
            normal_exits: list[Exit] = alternative.normal_exits
        else:
            normal_exits = [(condition, "false")]
        return _Fragment(
            entry=condition,
            normal_exits=normal_exits + [
                (node_id, "break") for node_id in body.break_exits
            ],
            break_exits=alternative.break_exits,
            continue_exits=alternative.continue_exits,
            return_exits=body.return_exits + alternative.return_exits,
            throw_exits=body.throw_exits + alternative.throw_exits + ([condition] if self._exception_depth and statement.calls else []),
        )

    def _try(self, statement: ControlStatement) -> _Fragment:
        """处理正常/突然退出与共享 finally；待完成原因通过显式边栈隔离，不复制 finally 调用。"""
        try_node = self._add_node(statement, override_kind="try")
        self._exception_depth += 1
        body = self._sequence(statement.body)
        if body.entry is not None:
            self._edge(try_node, body.entry, "normal")
        handlers = [self._sequence(items) for items in statement.handlers]
        for index, handler in enumerate(handlers):
            if handler.entry is None:
                identity = statement.statement_id + ":empty-handler:" + str(index)
                self._nodes[identity] = self._nodes[try_node].model_copy(update={"node_id": identity, "kind": "operation", "calls": [], "code": "empty_handler", "provenance": "generated"})
                handler.entry, handler.normal_exits = identity, [(identity, "normal")]
        unhandled_throws = list(body.throw_exits)
        if handlers:
            for thrown in body.throw_exits:
                for handler in handlers:
                    if handler.entry is not None:
                        self._edge(thrown, handler.entry, "exception", certainty="may", source_before_write=self._nodes[thrown].kind != "throw")
            if any(statement.handler_catch_all):
                unhandled_throws = []
        normal: list[Exit] = (
            list(body.normal_exits) if body.entry is not None else [(try_node, "normal")]
        )
        alternative = self._sequence(statement.alternative)
        if alternative.entry is not None:
            self._connect_exits(normal, alternative.entry)
            normal = alternative.normal_exits
        for handler in handlers:
            normal.extend(handler.normal_exits)
        finalizer = self._sequence(statement.finalizer)
        self._exception_depth -= 1
        combined = _Fragment(
            entry=try_node,
            normal_exits=normal,
            break_exits=body.break_exits + self._flatten(handlers, "break_exits") + alternative.break_exits,
            continue_exits=body.continue_exits + self._flatten(handlers, "continue_exits") + alternative.continue_exits,
            return_exits=body.return_exits + self._flatten(handlers, "return_exits") + alternative.return_exits,
            throw_exits=unhandled_throws + self._flatten(handlers, "throw_exits") + alternative.throw_exits,
        )
        return self._finally(statement, combined, finalizer) if finalizer.entry is not None else combined

    def _finally(self, statement: ControlStatement, pending: _Fragment, finalizer: _Fragment) -> _Fragment:
        """共享清理块后按待完成 token 恢复；清理中的突然退出覆盖原退出，嵌套栈自然组合。"""
        result = _Fragment(entry=pending.entry)
        tokens = []
        groups = [("normal", pending.normal_exits), *( (kind, [(key, "exception" if kind == "throw" else "normal") for key in getattr(pending, kind + "_exits")])
            for kind in ("return", "throw", "break", "continue"))]
        for kind, exits in groups:
            for source, branch in exits:
                token = statement.statement_id + ":pending:" + kind + ":" + source
                tokens.append(token)
                resume = self._completion_node(source, kind, self._digest(token))
                self._edge(source, finalizer.entry, branch, completion_push=token,
                           source_before_write=kind == "throw" and self._nodes[source].kind != "throw")
                for last, final_branch in finalizer.normal_exits:
                    self._edge(last, resume, final_branch, completion_pop=[token])
                if kind == "normal":
                    result.normal_exits.append((resume, "normal"))
                else:
                    getattr(result, kind + "_exits").append(resume)
        for kind in ("return", "throw", "break", "continue"):
            for source in getattr(finalizer, kind + "_exits"):
                resume = self._completion_node(source, kind, self._digest(statement.statement_id + source + kind))
                self._edge(source, resume, "exception" if kind == "throw" else "normal", completion_pop=tokens,
                           source_before_write=kind == "throw" and self._nodes[source].kind != "throw")
                getattr(result, kind + "_exits").append(resume)
        return result

    def _completion_node(self, source: str, kind: str, suffix: str) -> str:
        """创建延后完成点；return 在进入 finally 前冻结，其他完成点不重新执行调用。"""
        node = self._nodes[source]
        if kind == "return":
            capture, resume = capture_return(node, suffix)
            self._nodes[source] = capture
        else:
            resume = node.model_copy(update={"node_id": source + ":resume:" + suffix, "kind": "operation" if kind == "normal" else kind,
                "calls": [], "definitions": [], "value_transfers": [], "scalar_values": {}, "provenance": "inferred"})
        self._nodes[resume.node_id] = resume
        return resume.node_id

    def _add_node(
        self,
        statement: ControlStatement,
        *,
        override_kind: ProgramNodeKind | None = None,
    ) -> str:
        """把公共语句转换为程序图节点。"""
        function = self._required_function()
        kind_by_statement: dict[str, ProgramNodeKind] = {
            "operation": "operation",
            "assignment": "assignment",
            "call": "call",
            "if": "condition",
            "loop": "condition",
            "return": "return",
            "throw": "throw",
            "break": "break",
            "continue": "continue",
            "try": "try",
        }
        kind = override_kind or kind_by_statement[statement.kind]
        self._nodes[statement.statement_id] = ProgramGraphNode(
            node_id=statement.statement_id,
            kind=kind,
            language=function.language,
            method_id=function.method_id,
            location=statement.location,
            code=statement.code[:1000],
            definitions=list(statement.definitions),
            uses=list(statement.uses),
            return_values=[list(value) for value in statement.return_values],
            return_value_locations=list(statement.return_value_locations),
            return_value_forms=list(statement.return_value_forms),
            return_binding=statement.return_binding,
            certainty=statement.certainty,
            provenance=statement.provenance,
            condition=statement.condition,
            scalar_values=dict(statement.scalar_values),
            calls=[ProgramCallSite(
                callsite_id=call.callsite_id,
                name=call.name,
                receiver=call.receiver,
                location=call.location,
                positional_arguments=[list(item) for item in call.positional_arguments],
                keyword_arguments={key: list(value) for key, value in call.keyword_arguments},
                receiver_identifiers=list(call.receiver_identifiers),
                argument_values=list(call.argument_values),
                positional_conditions=list(call.positional_conditions),
                keyword_conditions=dict(call.keyword_conditions),
                positional_spread_positions=list(call.positional_spread_positions),
                result_targets=list(call.result_targets),
                result_pattern=call.result_pattern,
                positional_argument_locations=list(call.positional_argument_locations),
                keyword_argument_locations=dict(call.keyword_argument_locations),
                positional_references=list(call.positional_references),
            addressed_argument_positions=list(call.addressed_argument_positions),
                keyword_references=dict(call.keyword_references),
                initializer_kind=call.initializer_kind,
                callable_access=call.callable_access,
                argument_binding_error=call.argument_binding_error,
            ) for call in statement.calls],
            value_transfers=[ProgramValueTransfer(
                output_variable=transfer.output_variable,
                input_variables=list(transfer.input_variables),
                transfer_kind=transfer.transfer_kind,
                callsite_ids=list(transfer.callsite_ids),
                certainty=transfer.certainty,
                provenance=transfer.provenance,
                reference_source=transfer.reference_source,
                storage_mode=transfer.storage_mode,
            ) for transfer in statement.value_transfers],
            storage_allocations=dict(statement.storage_allocations),
            collection_allocations=dict(statement.collection_allocations),
            selector_definitions=dict(statement.selector_definitions),
            access_selectors=dict(statement.access_selectors),
        )
        return statement.statement_id

    def _connect_exits(self, exits: list[Exit], target: str) -> None:
        """把一组带分支标签的正常出口接到后继节点。"""
        for source, branch in exits:
            self._edge(source, target, branch)

    def _edge(
        self,
        source: str,
        target: str,
        branch: ProgramEdgeBranch,
        *,
        certainty: EdgeCertainty = "must",
        completion_push: str = "", completion_pop: list[str] | None = None, source_before_write: bool = False,
        loop_back: bool = False,
    ) -> None:
        """去重添加一条 CFG 边。"""
        key = (source, target, "cfg", branch)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        material = "\x1f".join(key)
        self._edges.append(ProgramGraphEdge(
            edge_id="cfg:" + self._digest(material),
            source=source,
            target=target,
            kind="cfg",
            branch=branch,
            completion_push=completion_push, completion_pop=completion_pop or [], source_before_write=source_before_write,
            loop_back=loop_back,
            certainty="may" if certainty == "may" or self._nodes[source].certainty == "may" or self._nodes[target].certainty == "may" else "must",
        ))

    def _required_function(self) -> ControlFunction:
        """返回当前函数；未初始化代表构建器使用错误。"""
        if self._function is None:
            raise RuntimeError("CFG builder has no active function")
        return self._function

    @staticmethod
    def _flatten(fragments: list[_Fragment], name: str) -> list[str]:
        """合并多个处理分支的同类突然退出。"""
        return [item for fragment in fragments for item in getattr(fragment, name)]

    @staticmethod
    def _digest(value: str) -> str:
        """生成稳定、紧凑的图实体标识后缀。"""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
