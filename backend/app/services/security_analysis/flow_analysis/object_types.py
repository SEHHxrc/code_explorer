"""对象消费者的类型视图：复用公共层次，只筛选已有实现，不重建调用目标或语法树。"""

from collections import defaultdict

from backend.app.services.program_graph import FunctionProgramGraph, ProgramGraphArtifact
from backend.app.services.semantic_index import SemanticIndex
from backend.app.services.semantic_index.hierarchy import TypeHierarchy


class ObjectTypes:
    """实际对象类型与声明所属类型分离；初始化复用祖先实际函数，不伪造抽象方法实现。"""

    def __init__(self, graph: ProgramGraphArtifact, index: SemanticIndex) -> None:
        """建立一次分析内的类型/函数索引；未知父类型和重复实现仍保持不可解析。"""
        self.hierarchy = TypeHierarchy(index.artifact.types)
        self.methods: dict[tuple[str, str], list[FunctionProgramGraph]] = defaultdict(list)
        self.constructors: dict[str, list[FunctionProgramGraph]] = defaultdict(list)
        for function in graph.functions.values():
            context = function.instance_context
            if context is None:
                continue
            self.methods[(context.owner_symbol, function.name)].append(function)
            if context.is_constructor and function.has_body:
                self.constructors[context.owner_symbol].append(function)

    def accepts(self, actual: str, method: FunctionProgramGraph) -> bool:
        """实际类型是否选择该已有成员实现；声明无函数体不当作无副作用实现。"""
        context = method.instance_context
        if context is None or not method.has_body:
            return False
        declaring = {owner for owner, name in self.methods if name == method.name}
        owners = self.hierarchy.member_owners(actual, declaring)
        return len(owners) == 1 and owners[0] == context.owner_symbol

    def initializers(self, actual: str) -> list[FunctionProgramGraph]:
        """Python 继承 __init__，JS 无显式构造器时转发；Java/C++ 不把父构造器当可继承方法。"""
        if actual in self.constructors:
            return self.constructors[actual]
        fact = self.hierarchy.types.get(actual)
        if fact is None or fact.language not in {"python", "javascript", "typescript"}:
            return []
        name = "__init__" if fact.language == "python" else "constructor"
        owners = self.hierarchy.member_owners(actual, {owner for owner, member in self.methods if member == name})
        return self.constructors.get(owners[0], []) if len(owners) == 1 else []

    def hidden_fields(self, actual: str) -> set[str]:
        """Java 隐藏字段属于不同声明存储；未接入声明级字段槽前，拒绝按同名合并。"""
        fact = self.hierarchy.types.get(actual)
        if fact is None or fact.language != "java":
            return set()
        seen, hidden = set(), set()
        for owner in self.hierarchy.ancestors(actual):
            fields = set(self.hierarchy.types[owner].fields) if owner in self.hierarchy.types else set()
            hidden.update(seen & fields)
            seen.update(fields)
        return hidden
