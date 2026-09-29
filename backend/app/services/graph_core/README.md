# 公共图契约

本模块通过 `GraphArtifactView` 接口描述静态分析图的稳定共性，不规定图的内部存储，也不共享依赖解析、CFG 或数据流算法。

当前职责：

- `contracts.py`：图能力、最小节点/边投影和校验问题；
- `protocols.py`：基于 `typing.Protocol` 的只读图接口；
- `validation.py`：节点/边身份唯一性和悬空边校验；
- `adapters/dependency.py`：现有 NetworkX node-link 依赖图适配；
- `adapters/program.py`：现有函数分区 ProgramGraph 适配。

`DependencyGraphView` 和 `ProgramGraphView` 只提供公共工具所需的扁平投影，原图仍保留各自语义：前者负责跨文件结构和调用目标，后者负责函数内 CFG 与到达定义。源码解析和事实复用属于后续 `semantic_index`，不放入本模块。
