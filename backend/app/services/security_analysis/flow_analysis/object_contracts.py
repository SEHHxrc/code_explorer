"""对象引用查询公共接口；属于传播域，不包含语言 AST、规则或具体 CFG 求解实现。"""

from dataclasses import dataclass
from typing import Protocol

from backend.app.services.program_graph import FunctionProgramGraph, ProgramGraphArtifact, ProgramGraphNode
from backend.app.services.program_graph.contracts import FieldValueOrigin
from backend.app.services.value_binding.objects import StorageValueSlot
from backend.app.services.value_binding.collections import CollectionAllocation
from ..ir import IRValueBoundary


@dataclass(frozen=True)
class LocalInstance:
    """已有静态分配/工厂调用结果；不是运行时地址，也不是整个对象的污点标签。"""

    node_id: str
    callsite_id: str
    allocation_id: str
    owner_symbol: str
    result_name: str
    fields: tuple[str, ...]
    factory_depth: int = 0
    storage_kind: str = "reference"
    embedded_fields: tuple[str, ...] = ()
    collection: CollectionAllocation | None = None


@dataclass(frozen=True)
class ObjectBinding:
    """某操作的候选分配与别名赋值；空字符串显式保留实际未知分支。"""

    allocations: frozenset[str] = frozenset({""})
    alias_nodes: frozenset[str] = frozenset()

    @property
    def unique_allocation(self) -> str | None:
        """仅唯一且非未知时返回分配身份；空集、分支歧义及未知均不能强更新字段。"""
        return next(iter(self.allocations)) if len(self.allocations) == 1 and "" not in self.allocations else None


@dataclass(frozen=True)
class SnapshotObject:
    """快照中一个已知分配及从根可达的规范路径；多个引用共享一个分配，不递归复制环。"""

    allocation_id: str
    path: str
    variants: tuple[LocalInstance, ...]


@dataclass(frozen=True)
class SnapshotReference:
    """直属字段引用；仅包含已有引用环境能确认的对象，不把标量依赖提升为对象身份。"""

    allocation_id: str
    field_name: str
    target_id: str


@dataclass(frozen=True)
class ObjectSnapshot:
    """返回/副作用共用的有界可达引用快照；路径用于导航，身份用于保留共享和环。"""

    root_id: str
    objects: tuple[SnapshotObject, ...]
    references: tuple[SnapshotReference, ...]
    external_paths: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ObjectReferenceWrite:
    """正常出口的一项组合引用结果；来源为形参路径、局部新对象快照或明确未解析。"""

    target_name: str
    source_name: str = ""
    snapshot: ObjectSnapshot | None = None
    origins: tuple[FieldValueOrigin, ...] = ()
    unresolved: bool = False


class ObjectReferenceResolver(Protocol):
    """输入公共操作与展示名称，查询已有对象/字段或生成独立 Overlay；不新增调用目标。

    write=False 查询操作前的读取身份，write=True 查询操作后的写入身份。
    未解析/超限必须返回未知，不得把无结果解释为安全；实现可来自公共 CFG 或后续原生后端。
    """

    def at(self, node: ProgramGraphNode, name: str, *, write: bool = False) -> ObjectBinding:
        """返回操作前/后的候选引用状态，包含明确未知分支及真实别名来源节点。"""
        ...

    def possible(self, node: ProgramGraphNode, name: str, *, address: bool = False) -> list[LocalInstance]:
        """返回唯一分配的类型变体；address 查询变量单元而非其中存的指针，歧义保持未知。"""
        ...

    def alias_origins(self, node: ProgramGraphNode, name: str) -> list[FieldValueOrigin]:
        """返回原始赋值位置；分支合并为 may 来源，不证明唯一执行顺序。"""
        ...

    def field_binding(self, node: ProgramGraphNode, name: str, *, write: bool) -> StorageValueSlot | None:
        """逐跳解析静态嵌套路径，返回末级直属字段身份；未知中间引用或超限返回 None。"""
        ...

    def snapshot(self, node: ProgramGraphNode, name: str, *, allocation_id: str | None = None,
                 write: bool = False, max_objects: int = 256) -> ObjectSnapshot | None:
        """导出某可达对象的已知内部引用；歧义引用/预算超限不返回截断的精确快照。"""
        ...

    def bind_fields(self, function: FunctionProgramGraph) -> FunctionProgramGraph:
        """返回绑定字段身份并重算原公共值流的新函数图，不就地修改输入。"""
        ...


@dataclass(frozen=True)
class ObjectRootEffect:
    """一个接收者或对象形参的字段路径接口；只描述值读写，不判断净化或漏洞。

    fields 是需绑定的入口/出口字段，written_fields 是实际观察写入，must_write 只表示
    当前 CFG 的所有正常出口均写入。异常、未知副作用及形参重绑定不能冒充确定写回。
    """

    root: str
    fields: frozenset[str]
    written_fields: frozenset[str]
    must_write: frozenset[str]
    normal_exit_id: str
    reference_writes: tuple[ObjectReferenceWrite, ...] = ()


class ObjectCallCompiler(Protocol):
    """工厂与普通分配共用的调用接口；权限证明与后置编译来自同一实现，避免前后阶段不一致。"""

    def permissions_for(self, caller: FunctionProgramGraph, instances: list[LocalInstance], *,
                        references: ObjectReferenceResolver | None = None) -> tuple[frozenset[tuple[str, str]], frozenset[str]]:
        """返回已核实的对象实参/接收者操作权限，未知调用不因存在方法名而放行。"""
        ...

    def apply(self, graph: ProgramGraphArtifact, instances: dict[str, list[LocalInstance]]) -> tuple[ProgramGraphArtifact, list[IRValueBoundary]]:
        """在私有图编译尚未接入的正常出口效果，可追加局部新对象；重复调用不能复制后置节点。"""
        ...
