"""现有语法树到公共集合分配/元素赋值事实；不求解引用，不识别第三方集合 API。"""

from dataclasses import replace
import hashlib
from typing import Any

from backend.app.services.syntax_analysis import field, semantic_children
from backend.app.services.value_binding.access_paths import AccessPath, AccessPathSegment, descendant, member_path, parse_static_index_literal
from backend.app.services.value_binding.collections import CollectionAllocation
from ..control_ir import ControlStatement, ControlValueTransfer
from .selectors import decorate_selectors, indexed_path


class CollectionSyntax:
    """各前端共用有界字面量装配；语法差异只负责提取成员，传播仍使用公共值流。"""

    def __init__(self, collector: Any) -> None:
        """复用当前语言收集器和已解析 AST，不重复解析项目。"""
        self.collector = collector
        self.javascript = collector.language in {"javascript", "typescript", "tsx"}

    def layout(self, node: Any) -> tuple[CollectionAllocation, list[tuple[str, Any]]] | None:
        """返回直接可观察的固定成员；动态键、spread、超限不伪装成完整布局。"""
        language, text = self.collector.language, self.collector._text
        if node.type == "parenthesized_expression" and len(semantic_children(node)) == 1:
            return self.layout(semantic_children(node)[0])
        body, kind, storage = node, "sequence", "reference"
        if language == "python" and node.type in {"dictionary", "list", "tuple"}:
            kind = "mapping" if node.type == "dictionary" else "sequence"
        elif self.javascript and node.type in {"object", "array"}:
            kind = "properties" if node.type == "object" else "sequence"
        elif language == "go" and node.type == "composite_literal":
            literal_type = text(field(node, "type")).replace(" ", "")
            if literal_type.startswith("map["):
                kind = "mapping"
            elif literal_type.startswith("["):
                storage = "reference" if literal_type.startswith("[]") else "value"
            else:
                return None  # 结构体仍由既有 StorageSyntax/类型布局负责。
            body = field(node, "body")
        elif language == "java" and node.type in {"array_creation_expression", "array_initializer"}:
            body = field(node, "value") if node.type == "array_creation_expression" else node
        else:
            return None
        if body is None:
            return None  # new T[n] 的默认值/长度不在本阶段猜测。
        elements, complete, index = [], True, 0
        children = semantic_children(body)
        for child in children[:256]:
            key, value = None, child
            if child.type in {"pair", "keyed_element"}:
                key_node, value = field(child, "key"), field(child, "value")
                while key_node is not None and key_node.type == "literal_element" and len(semantic_children(key_node)) == 1:
                    key_node = semantic_children(key_node)[0]
                if key_node is not None:
                    if self.javascript and key_node.type in {"property_identifier", "identifier"}:
                        key = text(key_node)
                    else:
                        if key_node.type == "computed_property_name" and len(semantic_children(key_node)) == 1:
                            key_node = semantic_children(key_node)[0]
                        key = parse_static_index_literal(text(key_node))
            elif self.javascript and child.type == "shorthand_property_identifier":
                key = text(child)
            elif child.type in {"dictionary_splat", "list_splat", "spread_element"} or kind in {"mapping", "properties"}:
                complete = False
                continue
            else:
                if self.javascript:
                    # 数组空洞占位置，不能只枚举 named_children 后把 [, value] 错写成 [0]。
                    index = sum(item.type == "," for item in body.children if item.end_byte <= child.start_byte)
                key = index
            if type(key) not in {str, int} or value is None:
                if value is not None and child.type in {"pair", "keyed_element"} and kind in {"mapping", "properties"}:
                    key_node = field(child, "key")
                    if key_node is not None and key_node.type == "computed_property_name" and len(semantic_children(key_node)) == 1:
                        key_node = semantic_children(key_node)[0]
                    symbolic = indexed_path(self.collector, AccessPath("collection"), key_node, child, None)
                    if symbolic is not None:
                        elements.append((symbolic.segments[-1].render(), value))
                complete = False
                continue
            if kind == "sequence" and (type(key) is not int or key < 0):
                complete = False
                continue
            index = key + 1 if type(key) is int else index
            if self.javascript:
                key = str(key)  # ECMAScript 属性键规范化，不套用到 Python/Go/Java。
            elements.append((AccessPathSegment("index", key).render(), value))
        # Python/JS 字面量后出现的同键值覆盖前值；不能让同一语句里的旧值继续污染最终元素。
        elements = list(dict(elements).items())
        allocation = CollectionAllocation(kind=kind, members=tuple(key for key, _ in elements if not key.startswith("[?")),
            key_semantics="javascript" if self.javascript else "typed", storage_kind=storage,
            mutable=not (language == "python" and node.type == "tuple"),
            complete=complete and len(children) <= 256)
        return allocation, elements

    def decorate(self, statement: ControlStatement, node: Any) -> ControlStatement:
        """把直接赋值字面量展开为分配及逐元素值传递，不把所有成员混到集合根。"""
        collector = self.collector
        bindings = [(target, value) for target, value, compound in collector._operation_bindings(node) if not compound]
        allocations, transfers, roots = [], [], set()
        budget = 256

        def expand(root: str, value: Any, depth: int = 0) -> bool:
            """嵌套集合共享同一分配接口；总成员预算防止递归展开放大产物。"""
            nonlocal budget
            layout = self.layout(value)
            if layout is None or depth >= 16 or budget == 0:
                return False
            allocation, elements = layout
            budget -= 1
            allocations.append((root, allocation))
            transfers.append(ControlValueTransfer(output_variable=root, input_variables=(), transfer_kind="assignment"))
            position = len(allocations) - 1
            for member, item in elements:
                if budget == 0:
                    allocations[position] = (root, allocation.model_copy(update={"complete": False}))
                    break
                output = member_path(root, member)
                if not expand(output, item, depth + 1):
                    literal = self.layout(item) is not None
                    if literal:
                        allocations[position] = (root, allocation.model_copy(update={"complete": False}))
                    names = () if literal else collector._identifiers(item)
                    budget -= 1
                    transfers.append(ControlValueTransfer(output_variable=output, input_variables=names,
                        transfer_kind="assignment", reference_source=collector._direct_reference(item)))
            return True

        for target, value in bindings:
            root = collector._direct_reference(target)
            if root and root not in roots and expand(root, value):
                roots.add(root)
        returned = ""
        expressions = collector._return_expressions(node) if statement.kind == "return" else []
        if len(expressions) == 1 and self.layout(expressions[0]) is not None:
            returned = "collection_return_" + hashlib.sha256(statement.statement_id.encode()).hexdigest()[:16]
            if not expand(returned, expressions[0]):
                returned = ""
        # Go map/slice 的赋值复制描述符并共享元素；固定数组仍为值复制，不按名称一概当引用。
        storage = getattr(collector, "_storage_context", None)
        shared = set()
        if collector.language == "go" and storage is not None:
            for target, value in bindings:
                raw = storage.type_at(collector._direct_reference(value), node).strip()
                if raw.startswith(("map[", "[]")):
                    shared.add(collector._direct_reference(target))
                elif raw.startswith("[") and "]" in raw and self.layout(value) is None:
                    size = parse_static_index_literal(raw[1:raw.index("]")])
                    root = collector._direct_reference(target)
                    if root and type(size) is int and 0 <= size <= 256:
                        allocations.append((root, CollectionAllocation(kind="sequence", storage_kind="value",
                            members=tuple(AccessPathSegment("index", index).render() for index in range(size)))))
        if not allocations and not shared:
            return decorate_selectors(collector, statement, bindings)
        if any(not allocation.complete for _, allocation in allocations):
            collector._limitations.append("集合字面量含动态键、展开或超限成员；只保留已观察固定元素，不证明完整布局或安全。")
        retained = [replace(item, storage_mode="reference") if item.output_variable in shared and item.reference_source else item
                    for item in statement.value_transfers
                    if not any(item.output_variable == root or descendant(item.output_variable, root) for root in roots)]
        all_transfers = (*retained, *transfers)
        statement = replace(statement, collection_allocations=tuple(allocations), value_transfers=all_transfers,
            return_values=((returned,),) if returned else statement.return_values,
            return_value_forms=("reference",) if returned else statement.return_value_forms,
            definitions=collector._unique((*statement.definitions, *(item.output_variable for item in transfers))),
            uses=collector._unique((*statement.uses, *((returned,) if returned else ()), *(name for item in transfers for name in item.input_variables))))
        return decorate_selectors(collector, statement, bindings)
