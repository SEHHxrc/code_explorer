# 智能体只读工具模块

本目录定义大模型唯一可调用的项目工具。入口是 `registry.create_project_tool_registry()`，它创建 `ToolRegistry` 并注册六个工具。`AgentTool.schema()` 把 Pydantic 参数模型转换为严格 JSON Schema，`ToolRegistry.execute(name, context, arguments)` 校验工具名和参数后才执行。

## 模型可见工具

| 工具名 | 实现类 | 参数 | 返回内容 |
| --- | --- | --- | --- |
| `get_project_manifest` | `ManifestTool` | 无 | 语言、框架、命令、入口点和图摘要。 |
| `list_entrypoints` | `EntrypointsTool` | 无 | 最多 50 个应用、CLI、Worker 或脚本入口及证据。 |
| `search_symbols` | `SearchSymbolsTool` | `query: str`；`limit: 1..50=20` | 匹配的类、函数、方法、常量及路径/行号。 |
| `read_file_range` | `ReadFileTool` | `path: str`；`start_line>=1=1`；`end_line>=1=120` | 项目内有限行源码，带行号并脱敏。 |
| `get_dependency_neighbors` | `DependencyNeighborsTool` | `node_id: str`；`direction: both|incoming|outgoing=both`；`limit: 1..100=30` | 指定图节点的入边/出边邻居。 |
| `search_project_text` | `SearchProjectTextTool` | `query: str`；`limit: 1..50=20` | 项目文本中的字面量匹配及路径/行号。 |

## 文件与类

| 文件 | 作用 |
| --- | --- |
| `arguments.py` | `StrictArguments` 及各工具参数模型；禁止额外字段。 |
| `base.py` | `ToolContext`、抽象 `AgentTool` 和 `ToolRegistry`。 |
| `registry.py` | 默认工具集合的唯一装配入口。 |
| `discovery.py` | Manifest、入口点和符号搜索工具。 |
| `source.py` | 受限源码读取与全文搜索工具。 |
| `graph.py` | 依赖邻居查询工具。 |
| `evidence_index.py` | `ProjectEvidenceIndex`：为符号、节点和入/出边建立一次性索引，避免每轮重复扫描。 |
| `security.py` | `get_static_security_evidence`：按问题和分页参数读取自包含安全候选及省略信息。 |

## 工具调用约束

工具拿到的 `ToolContext` 包含项目 ID、用户 ID、解析后的项目根目录、静态分析产物和证据索引。`read_file_range` 会拒绝越界路径、过大文件和二进制文件；全文搜索会跳过噪声目录，并限制扫描文件数、字节数、时间和结果数。所有文本结果在返回模型前经过敏感信息脱敏。

新增工具需继承 `AgentTool`，声明稳定的 `name`、`description` 和 `arguments_model`，实现异步 `execute()`，再显式加入 `create_project_tool_registry()`。禁止把 Shell、Docker Socket 或任意写文件能力注册为模型工具。
