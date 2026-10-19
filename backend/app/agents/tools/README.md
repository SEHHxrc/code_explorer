# 智能体只读工具模块

本目录定义大模型唯一可调用的项目工具。普通入口是 `registry.create_project_tool_registry()`，注册七个工具。`AgentTool.schema()` 把 Pydantic 参数模型转换为严格 JSON Schema，`ToolRegistry.execute(name, context, arguments)` 校验工具名和参数后才执行。新实验通过 `experiments.tools.create_experiment_tool_registry(with_evidence=...)` 注册共同原始文件工具和报告工具，仅证据组追加静态查询。

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

新实验有 `search_static_security_evidence(query, category, path, trust_class, cursor, limit)` 与 `get_static_security_evidence(evidence_id, section, offset, limit)`；前者给统一排序的路径目录，后者分页提供 overview/flow/snippets/protections/unresolved，复用 `SecurityEvidenceCatalog`。普通 Agent 的同名工具也支持 evidence_id/section（无批次状态限制）；空 ID 用 query/cursor/limit 查询同一目录，offset 仅用于详情，不再回到旧事实包。每个运行首次使用才验证目录，后续复用；目录不算源码已读，不执行源码反向切片。

两组共同的 `submit_analysis_batch(ledger_json, evidence_ids, scopes)` 保存本批发现台账并由后台生成四段报告。ledger_json 是共同契约 JSON 字符串，不另交 Markdown；scopes 使用阶段台账中的 `path:start-end`，证据 ID 必须已交付且覆盖本批选择。长度/状态/展示错误不提交，也不消耗 ID；reported 只表示已生成报告，非漏洞确认。事件保留台账及报告，下一批仅携台账避免重复正文，已报告 ID 自动排除。工具不写项目文件。

| 文件 | 作用 |
| --- | --- |
| `arguments.py` | `StrictArguments` 及各工具参数模型；禁止额外字段。 |
| `base.py` | `ToolContext`、抽象 `AgentTool` 和 `ToolRegistry`。 |
| `registry.py` | 默认工具集合的唯一装配入口。 |
| `discovery.py` | Manifest、入口点和符号搜索工具。 |
| `source.py` | 受限源码读取与全文搜索工具。 |
| `graph.py` | 依赖邻居查询工具。 |
| `evidence_index.py` | `ProjectEvidenceIndex`：为符号、节点和入/出边建立一次性索引，避免每轮重复扫描。 |
| `security.py` | 共享静态目录/ID 详情的普通工具与分批约束门面；目录不计为源码已读。 |

## 工具调用约束

工具拿到的 `ToolContext` 包含项目 ID、用户 ID、解析后的项目根目录、静态分析产物和证据索引。`read_file_range` 会拒绝越界路径、过大文件和二进制文件；全文搜索会跳过噪声目录，并限制扫描文件数、字节数、时间和结果数。所有文本结果在返回模型前经过敏感信息脱敏。

`search_project_text` 返回查询词、命中行和 `scope`：完整扫描文本文件数、已读取文件数、部分文件扫描位置、跳过数量/原因、忽略目录、遍历错误以及预算终止原因。无匹配只针对该范围，不能推断全项目无问题。实验两组另共同注册 `search_project_text_many(queries: 1..8 个字面关键词, limit: 1..50)`，一次遍历共享同样的扫描预算，按文件/行去重，并返回匹配的查询索引；不额外提供 AST 或静态规则信息。

`read_file_range` 返回真实 `start_line/end_line`、请求的 `requested_end_line`、`file_total_lines`、`has_more_lines` 和可继续读取的 `next_start_line`。`truncated` 表示请求范围被限制，不因为文件后面还有行就误报为截断。全文搜索的单行预览长度受限时标明 `text_truncated`，精确路径/行号仍可用于重读。编排器完整持久化工具结果，模型观察窗口可能另外压缩或省略，并明确记录；工具分页限制与模型预算限制是两个不同层次。

新增工具需继承 `AgentTool`，声明稳定的 `name`、`description` 和 `arguments_model`，实现异步 `execute()`，再显式加入 `create_project_tool_registry()`。禁止把 Shell、Docker Socket 或任意写文件能力注册为模型工具。
