"""声明根与静态访问路径的公共值槽身份；定义版本仍由操作节点区分。"""

from typing import Protocol

from .objects import StorageValueSlot


class DeclarationBoundValues(Protocol):
    """只读消费操作的声明根，不依赖某种图、语言或编译器对象。"""

    definition_declarations: dict[str, str]
    use_declarations: dict[str, str]
    definition_value_ids: dict[str, str]
    use_value_ids: dict[str, str]
    definition_object_slots: dict[str, StorageValueSlot]
    use_object_slots: dict[str, StorageValueSlot]


def variable_root(name: str) -> str:
    """返回已有规范访问路径的标识符根；临时槽与复杂表达式不猜测声明。"""
    root = name.split(".", 1)[0].split("[", 1)[0]
    return root if root.isidentifier() else ""


def value_identity(node: DeclarationBoundValues, name: str, *, write: bool) -> str:
    """优先读取结构化对象字段，再读精确值身份/声明根；未知为词法槽，不冒充 SSA 版本。"""
    slots = node.definition_object_slots if write else node.use_object_slots
    if name in slots:
        return slots[name].value_id
    explicit = node.definition_value_ids if write else node.use_value_ids
    if name in explicit:
        return explicit[name]
    root = variable_root(name)
    declarations = node.definition_declarations if write else node.use_declarations
    declaration = declarations.get(root)
    return declaration + name[len(root):] if declaration else "lexical:" + name
