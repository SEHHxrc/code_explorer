"""公共类型层次查询：声明关系、Python C3 与 C++ 基类子对象；不读取源码或新建调用边。"""

from collections import deque
from collections.abc import Iterable, Mapping
from typing import Any

from .contracts import BaseTypeFact, TypeFact


def type_facts(definitions: Mapping[str, Any], edges: Iterable[tuple[str, str, Mapping[str, Any]]]) -> dict[str, TypeFact]:
    """投影已有依赖类型/字段与解析边；未解析父类型显式保留，不按同名全仓猜测。"""
    result = {key: TypeFact(symbol_id=key, language=getattr(item, "lang", ""), kind=item.kind)
              for key, item in definitions.items() if getattr(item, "kind", "") in {"class", "struct", "interface", "trait", "type", "record"}}
    for item in definitions.values():
        if getattr(item, "kind", "") == "field" and getattr(item, "parent_fqn", "") in result:
            result[item.parent_fqn].fields[item.name] = getattr(item, "declared_type_literal", "") or getattr(item, "type_literal", "")
    for source, target, attributes in edges:
        relation = attributes.get("relation")
        if source in result and target in result and relation in {"inherits", "implements", "embeds"}:
            relation_fact = BaseTypeFact(symbol_id=target, relation=relation, virtual=bool(attributes.get("virtual")))
            if relation_fact not in result[source].bases:
                result[source].bases.append(relation_fact)
    for key, fact in result.items():
        observed = [*getattr(definitions[key], "bases", ()), *getattr(definitions[key], "implements", ())]
        observed = [name for name in observed if not (fact.language == "python" and name == "object")]
        resolved = {edge.symbol_id.split("::")[-1].split(".")[-1] for edge in fact.bases}
        fact.unresolved_bases = ([name for name in observed if name.replace("::", ".").split(".")[-1] not in resolved]
                                 if len(observed) > len(fact.bases) else [])
    return result


class TypeHierarchy:
    """有界类型查询；C3 冲突、循环与路径超限均返回未知，不用随意遍历顺序消除歧义。"""

    def __init__(self, types: Mapping[str, TypeFact], *, max_depth: int = 64, max_paths: int = 4096) -> None:
        """输入公共类型事实；缓存只属于单次分析，不修改原始事实。"""
        self.types = types
        self.max_depth, self.max_paths = max_depth, max_paths
        self._orders: dict[str, tuple[str, ...] | None] = {}

    def ancestors(self, owner: str, *, include_embeddings: bool = False) -> tuple[str, ...]:
        """返回包含自身的有界祖先集合顺序；不将此 BFS 顺序当 Python/C++ 方法解析规则。"""
        pending, seen, result = deque([owner]), set(), []
        while pending and len(seen) < self.max_paths:
            current = pending.popleft()
            if current in seen:
                continue
            seen.add(current)
            result.append(current)
            pending.extend(base.symbol_id for base in self.types.get(current, TypeFact(symbol_id=current, language="", kind="")).bases
                           if base.relation != "embeds" or include_embeddings)
        return tuple(result)

    def order(self, owner: str, visiting: tuple[str, ...] = ()) -> tuple[str, ...] | None:
        """Python 使用 C3；其他语言只返回祖先索引顺序，成员选择另由 member_owners 约束。"""
        if owner in self._orders:
            return self._orders[owner]
        if owner in visiting or len(visiting) >= self.max_depth:
            return None
        fact = self.types.get(owner)
        if fact is None:
            return (owner,)
        if fact.language != "python":
            return self.ancestors(owner, include_embeddings=fact.language == "go")
        parents = [base.symbol_id for base in fact.bases if base.relation != "embeds"]
        orders = [self.order(base, (*visiting, owner)) for base in parents]
        if fact.unresolved_bases or any(item is None for item in orders):
            self._orders[owner] = None
            return None
        sequences = [list(item) for item in orders if item is not None] + [parents.copy()]
        result = [owner]
        while any(sequences):
            sequences = [item for item in sequences if item]
            head = next((item[0] for item in sequences if not any(item[0] in other[1:] for other in sequences)), None)
            if head is None:
                self._orders[owner] = None
                return None
            result.append(head)
            for item in sequences:
                if item[0] == head:
                    item.pop(0)
        self._orders[owner] = tuple(result)
        return self._orders[owner]

    def member_owners(self, owner: str, declaring: set[str]) -> tuple[str, ...]:
        """按语言从已有声明中选择成员所属类型；不创建符号，也不把无函数体声明当实现。"""
        fact = self.types.get(owner)
        if owner in declaring:
            return (owner,)
        if fact is None or fact.unresolved_bases:
            return ()
        if fact.language == "python":
            order = self.order(owner)
            return next(((item,) for item in order or () if item in declaring), ())
        if fact.language == "go":
            level = [owner]
            seen = set()
            for _ in range(self.max_depth):
                hits = [item for item in level if item in declaring]
                if hits:
                    return tuple(hits)
                seen.update(level)
                level = [base.symbol_id for item in level for base in self.types.get(item, fact).bases
                         if base.relation == "embeds" and base.symbol_id not in seen]
                if not level or len(level) > self.max_paths:
                    return ()
            return ()
        if fact.language == "cpp":
            # 每条基类支路在首个声明处停止；不能用“类型是另一类型的祖先”抹去
            # 非虚菱形另一支路上独立的声明/子对象。虚基类路径才可规范为共享身份。
            return tuple(item for item, _ in self._cpp_paths(owner, declaring))
        candidates = [item for item in self.ancestors(owner)[1:] if item in declaring]
        if fact.language == "java":
            classes = [item for item in candidates if self.types[item].kind != "interface"]
            if classes:
                candidates = classes
        selected = [item for item in candidates if not any(item != other and item in self.ancestors(other) for other in candidates)]
        return tuple(selected)

    def subobjects(self, actual: str, declaring: str) -> tuple[tuple[str, ...], ...]:
        """C++ 非虚基类按路径区分，虚基类规范为共享身份；其他语言没有 C++ 式基类存储副本。"""
        fact = self.types.get(actual)
        if fact is None or fact.language != "cpp":
            return ((),) if declaring in self.ancestors(actual) else ()
        return tuple(path for _, path in self._cpp_paths(actual, {declaring}))

    def _cpp_paths(self, actual: str, declaring: set[str]) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """成员查找与基类子对象共用有界支路遍历；首次声明即停止，虚路径去重、非虚路径保留。"""
        pending = [(actual, (), ())]
        result: set[tuple[str, tuple[str, ...]]] = set()
        steps = 0
        while pending:
            current, path, visiting = pending.pop()
            steps += 1
            if steps > self.max_paths or len(visiting) >= self.max_depth or current in visiting:
                return ()
            if current in declaring:
                result.add((current, path))
                continue
            node = self.types.get(current)
            if node is None or node.unresolved_bases:
                return ()
            for index, base in enumerate(node.bases):
                if base.relation != "inherits":
                    continue
                child_path = ("virtual:" + base.symbol_id,) if base.virtual else (*path, str(index) + ":" + base.symbol_id)
                pending.append((base.symbol_id, child_path, (*visiting, current)))
        return tuple(sorted(result))
