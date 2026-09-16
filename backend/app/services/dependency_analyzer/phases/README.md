# 依赖分析阶段模块

本目录把跨文件分析拆成严格有序的五个阶段。各阶段以 Mixin 形式组合进 `UnifiedCodeAnalyzer`，共享索引但不单独作为公共 API。

| 文件/类 | 输入状态 | 主要输出 |
| --- | --- | --- |
| `collection.py` / `CollectionPhase` | 项目根目录、语言 Handler | 收集源码、并行解析 Tree-sitter、合并 `FileContext`。 |
| `indexing.py` / `IndexingPhase` | 定义、引用与导入记录 | 文件、模块、符号、短名和类成员索引。 |
| `imports.py` / `ImportResolutionPhase` | 模块索引与导入记录 | Python/JS/TS/Java/Go/Rust/C/C++ 的本地或外部导入目标。 |
| `types.py` / `TypeResolutionPhase` | 定义、继承、导入和类型文字 | 继承、MRO、成员查找与类型/可调用对象解析缓存。 |
| `graph.py` / `GraphResolutionPhase` | 所有索引和解析结果 | 节点、contains/declares/imports/calls/inherits/overrides 等最终边。 |

`phases/__init__.py` 统一导出五个阶段类。阶段之间不可逆向调用：例如采集阶段不能依赖图阶段，Handler 也不能访问全局解析器状态。这一约束避免循环依赖并允许分别测试各阶段。
