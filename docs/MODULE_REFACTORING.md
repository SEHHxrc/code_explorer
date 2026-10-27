# 模块重构与实施状态

本文记录当前功能边界、已完成的重构和后续扩展原则。`test/` 保留历史测试材料与当前回归测试；正常功能重构不整理其中的历史文件，只在验证对应功能时按需使用。

## 优先级概览

当前优化：安全源码适配唯一实现迁入 `security_analysis/language_adapters`，旧 frontends 仅再导出；
公共安全 IR 仍在 ir.py。`syntax_analysis.session` 在一次任务内有界复用源码视图与 Tree-sitter 树，
Tree.copy 隔离线程/消费者，命中以真实内容校验；原始文件读取、安全检查和非原生路径均保留。
单文件生产流水线从 3 次解析变为 1 次；这是解析次数结论，不表示完整任务耗时按比例下降。

本轮离线回归为 Windows 后端 677 项、WSL 针对性回归 36 项全部通过，未调用真实模型。
对 chaos-mesh 的 872 文件复核中，安全证据包与整理前逐项一致，源码及运行期间实现指纹未变化；
共 2616 次树请求中复用 714 次，实际解析 1902 次。原有 2 处恢复性解析错误仍保留，不能据此
宣称整个仓库无解析缺口。缓存占用有界，不降低扫描范围、规则或证据精度；整仓稳定提速比例尚未证明。
80 文件的关闭/开启及反向复测中，扫描和程序图完整产物哈希一致，解析次数由 160 降为 80，
但阶段耗时基本持平；不能把减少重复解析表述为显著提速，也不继续无依据地扩大缓存。

最新整理：原生 Go/TypeScript 适配、环境、传输和表解码从 semantic_index 移入 language_backends，
默认装配走公共入口；事实索引保留原有协议与 Provider。公共访问路径移入 value_binding，阶段观测移入 core。
原路径薄再导出，非原生分析不删；新增模块边界与兼容身份回归。Go 安全前端按编排、收集、API 和路由拆分，
Gin/net/http/net 规则独立于语法模块，不增加第二套污点求解器。

当前实现保留公共 CFG/DFG 与规则，通过项目级解析协议接入官方 TypeScript 声明绑定和
Go x/tools 类型/SSA 事实。依赖图写边前统一选择，成功绑定不再运行旧同名目标推测；
非原生实现仍服务独立解析和显式回退，不能整体删除。工具实现、版本和配置可审计；
原生 Go SSA 尚未替换公共污点图，Java 原生后端、完整 Vue 编译器语义仍未迁移。
不增加 MCP、微服务或另一套安全传播算法。

性能收敛已增加任务级声明索引、阶段耗时/缓存观测、Node 路径/配置/节点复用和公共有界批量
校验。仍使用完整输入与相同模型约束，不跨快照保留绑定，不靠抽样或缩小范围提速。
旧逐条位置构造与重复声明匹配入口已替换，不维护快慢两套生产算法。Go/Java 可复用公共
协议、索引和校验装配，Go 已接入、Java 原生接入仍待实现。只读性能复核见 test/profile_semantic_backend.py。

| 状态 | 功能域 | 当前结果 |
| --- | --- | --- |
| 已完成 P0 | 前端依赖图 | 图模型、布局、Sigma、控件和检查器已分离并异步加载 |
| 已完成 P0 | 项目功能域 | `projects` 统一导入、CRUD、Artifact、查询/快照与可恢复删除；纯分析已分离 |
| 已完成 P1 | 受控项目工作区 | 暂存导入、安全策略、补偿事务、Journal 和 Janitor 已建立 |
| 已完成 P1 | 项目生命周期 | 活动任务保护、文件/Artifact/数据库顺序删除已建立 |
| 已完成 P1 | ProjectInsight | 页面、组件、composable、API 和纯领域函数已分离 |
| 已完成 UX | 项目工作区导航 | 固定项目壳、分组侧栏、五个缓存工作区和内部滚动已建立 |
| 已完成 P1 | 智能体工具 | 按发现/源码/图拆分，并加入共享证据索引和搜索预算 |
| P2 | Manifest 检测器 | 框架和入口规则明显增长后再按检测器拆分 |
| 已完成 P2 第一阶段 | 隔离执行域 | 声明式协议、SQLite 队列、独立 Docker Worker、配额、取消、SSE 和审计已建立 |
| 已完成 P2 第二阶段 | Agent 持久化队列 | 原子领取、Worker 租约、重启接管、跨进程取消和过期恢复已建立 |
| 已完成 UX/构建 | 前端按需注册 | 移除 Element Plus 与全部图标的全量注册，主 JS 包降至 500 kB 以下 |
| 已完成 效率优化 | 共享事件流与查询短路 | 前后端 SSE 重复实现收敛，活动任务查询可短路，业务源码净减少 61 行 |

## 1. 前端依赖图

```text
frontend/src/features/dependency-graph/
├── DependencyGraph.vue
├── components/
│   ├── GraphControls.vue
│   └── GraphInspector.vue
├── composables/
│   ├── useGraphModel.js
│   ├── useGraphLayout.js
│   └── useSigmaRenderer.js
├── domain/
│   └── graphModel.js
└── graphStyle.js
```

旧组件保留为 props/emits 薄包装。后端 Exchange DTO 固定输出 `edges`，前端不再兼容 `links`。节点大小、过滤、布局和渲染边界互相独立。

## 2. 项目功能域与纯分析流水线

```text
backend/app/services/projects/
├── repository.py
├── artifacts.py
├── import_service.py
├── query_service.py
├── deletion_service.py
├── transaction.py
├── deletion_journal.py
└── deletion_janitor.py

backend/app/services/project_analysis/
├── pipeline.py
└── graph_exchange.py

backend/app/schemas/
├── dependency_graph.py
└── project_analysis.py
```

`ProjectImportService` 负责导入用例编排；`ProjectAnalysisPipeline` 只分析已有目录，不接触用户、
数据库、Artifact 或工作区发布。`ProjectRepository` 提供新增、按所有权查询、白名单更新和删除，
不再操作 Agent、Execution、Experiment 表。

跨文件系统、Artifact 和数据库不能使用单一 ACID 事务，因此使用逆序补偿：

```text
暂存获取 → 安全清洗 → 静态分析 → 原子发布 → 原子 Artifact → 数据库提交
```

数据库最后提交。提交前任一步骤失败都会删除本次创建的资源；数据库已提交后，即使 Journal 清理失败也不得回滚有效项目。

## 3. 受控项目工作区与崩溃恢复

```text
backend/app/services/project_workspace/
├── contracts.py
├── policy.py
├── paths.py
├── filesystem.py
├── sanitizer.py
├── journal.py
├── janitor.py
├── service.py
└── sources/
    ├── git.py
    └── zip.py
```

工作区布局：

```text
backend/storage/users/<user_id>/
├── .staging/<operation_id>/
│   ├── operation.json
│   ├── upload.part
│   └── workspace/
└── projects/<project_id>/
```

核心约束：

- Git 与 ZIP 只进入暂存区，清洗和分析成功后才原子发布。
- Git URL 拒绝凭据、非 HTTP(S)、私网 DNS 结果，并支持 `GIT_ALLOWED_HOSTS` 白名单。
- ZIP 按归档大小、文件数、压缩比和实际写入字节限制，拒绝穿越、链接和特殊路径。
- Sanitizer 采用失败关闭，统一删除敏感文件、危险二进制、链接、噪音目录和超限文件。
- 所有删除路径都由受控 ID 重新计算，不使用数据库或 Journal 中的绝对路径。
- Journal 只保存 ID 和阶段；启动 Janitor 清理超时的未提交操作。

旧 `loader.py`、Middleware 目录下的 `sanitizer.py` 和 `project_cleaner.py` 已删除。

## 4. 项目删除

旧 `project_lifecycle` 已删除。`projects.ProjectDeletionService` 先隔离文件资源，再提交数据库事务；
提交前失败恢复资源，提交后清理失败由持久化删除 Journal 和启动 Janitor 接管。

## 5. ProjectInsight

```text
frontend/src/features/project-insight/
├── ProjectInsight.vue
├── components/
│   ├── ProjectImportPanel.vue
│   ├── ProjectFileTree.vue
│   ├── SymbolOutline.vue
│   └── ProjectOverviewPanel.vue
├── composables/
│   └── useProjectAnalysis.js
└── domain/
    └── symbolTree.js

frontend/src/services/
├── httpClient.js
├── projectApi.js
├── agentApi.js
├── executionApi.js
├── experimentApi.js
└── sseClient.js
```

ProjectInsight 现在是固定高度项目壳：导入成功后隐藏大块导入表单，以紧凑头部和左侧导航切换概览、代码图谱、智能体、实验、执行五个缓存工作区。桌面端避免文档级纵向滚动，窄屏回退为横向导航和页面滚动。旧 components/ProjectInsight.vue 继续作为兼容入口。

## 6. 智能体只读工具

```text
backend/app/agents/tools/
├── base.py
├── arguments.py
├── discovery.py
├── source.py
├── graph.py
├── evidence_index.py
└── registry.py
```

工具名称和严格 JSON Schema 保持兼容。`ProjectEvidenceIndex` 每次运行只构建一次符号列表和入/出邻接表；依赖邻居查询不再扫描整张图。文本搜索增加文件数、总字节和耗时预算。注册表拒绝重复工具名，未知内部错误不再原样写入前端事件。

Docker、Shell 和安全扫描没有加入只读智能体工具目录。现已建立独立 execution 功能域，由持久化队列连接单独的受限 Docker Worker；具体安全边界和启动方式见 EXECUTION.md。

## 7. 等价效率优化与稳定性边界

本轮以 11,358 行业务源码为基线，在新增共享模块后降至 11,297 行，净减少 61 行：

- `backend/app/api/sse.py` 统一智能体与隔离执行的持久化事件轮询、心跳、终态排空和响应头；实验快照流复用同一响应封装。
- `frontend/src/services/sseClient.js` 统一三类 JSON SSE 的分片解码；原有公开服务函数和错误文案保持不变。
- Agent API 通过 `ProjectRepository` 复用项目所有权查询，删除未使用的运行存储兼容方法。
- 项目活动任务检查在发现活动智能体后直接返回，避免额外查询执行任务表。

以下代码有意保留，不以行数为目标继续压缩：

- 仓储的显式 `commit`、`rollback` 和 `close`，它们对应队列领取、补偿删除与审计事件的不同事务时点。
- dependency analyzer 的语言处理器和阶段边界，避免影响图语义及跨语言分析结果。
- 前端 feature-local 兼容导出与公开 API 函数名，避免破坏旧调用方和异步工作区加载。
- `TEMPORARY CONTROL GROUP / 临时对照组` 标记及隔离实现；实验结论确认后按协议整体移除，不与生产路径混合优化。

验证基线为后端 57 项测试、前端 6 项测试、Python 编译检查、Vite 生产构建和 `git diff --check` 全部通过。

## 8. 后端文档与类型契约

`backend/app` 当前共 102 个 Python 文件、135 个类和 454 个函数/方法，统一遵守以下维护约束：

- 每个类、函数和方法必须以三引号文档字符串说明职责；处理安全边界、事务或解析规则时还需说明关键不变量。
- 除 `self`、`cls` 外，每个位置参数、关键字参数、`*args` 和 `**kwargs` 都必须标注类型。
- 每个同步函数、异步函数和方法都必须标注返回类型；无返回值显式使用 `None`，SSE/生命周期生成器使用异步迭代器类型。
- HTTP、中间件、仓储、工作区和 Tree-sitter 处理器优先使用具体领域类型；只有 JSON 异构值、SQLAlchemy 动态更新字段和跨策略关键字参数保留 `Any`。
- `test/test_backend_annotations.py` 通过 AST 持续检查上述约束，新代码违反时测试会给出文件、行号和缺失项。

本轮只修改文档字符串、函数注解、类型 import 和签名排版；对原先无功能改动的 61 个文件执行了剥离文档、注解和 import 后的 AST 对比，函数体逻辑完全一致。

## 9. 静态安全分析的组织与效率评估

当前组织架构以 `ARCHITECTURE.md` 第 6 节为唯一总览；具体能力以 `SECURITY_COVERAGE.md` 为准。
本轮已统一单类型/多类型工厂返回，复用 `LocalInstance`、类型化字段槽位、`ValuePathQuery` 和同一方法读取/值流算法；没有保留旧单类型工厂的并行实现。以下是后续建议，尚未实施，不代表当前算法或历史结果有误。

| 优先级 | 具体问题与位置 | 建议调整 | 必须保留的不变量 |
| --- | --- | --- | --- |
| P1 | `flow_analysis/program_graph.py` 同时管理种子定位、工作列表、调用/返回、路径查询与证据拼装 | 分离纯证据装配和传播工作列表；仅保留一套算法入口，不按语言拆求解器 | Source/Sink 实参精确匹配、返回上下文、证据顺序及 may/局限 |
| P1 | `_path()` 每次重建值流邻接，状态搜索反复构建调用点索引 | 冻结私有 Overlay 后建立单次运行的查询上下文，缓存邻接/可达集合/调用点；先测量耗时 | 不按 method_id 单独缓存变动中的图；初始化/工厂编译阶段不能误用旧视图，禁止跨项目共享可变状态 |
| P1 | `service.py` 同时负责流水线、事实保留、候选排序、片段与产物组装 | 提取候选/证据构建器，应用服务保留调度与失败降级 | 已验证值流端点必须先于预算截断保留，覆盖计数与实际保留数分开 |
| 已完成公共调用查询下沉 | `scanner.py/parameter_apis.py` 不再依赖传播求解层；调用索引统一位于 semantic_index.calls | 扫描与求解共享查询，按调用点保留全部候选；旧路径仅兼容导出，扫描单次复用索引 | 不新建调用关系，不改变参数角色与数据污点的区别；旧图兼容只有一个适配入口 |
| 已完成解析复用／读取仍保留 | 依赖、语言适配器和程序图共享任务级有界源码视图及树；各消费者仍读取实际文件 | 后续只有建立可信不可变快照后才考虑合并读取；不能用路径/mtime 命中绕过检查 | Python AST 与 Tree-sitter 不强行共享；Vue 坐标不变；树独立 copy、超额不留存、退出释放 |
| P2 | `program_graph.contracts` 包含安全消费者专用 `InstanceFieldEffect/field_effects` 等字段 | 将私有 Overlay 状态集中到传播运行上下文；公共程序图只保留通用语法事实 | 原始图不变与旧契约可读，避免重构时丢失实际赋值位置与类型标签 |
| P2 | 对象能力继续增长时三个平铺文件和 `objects.py` 编排会难以浏览 | 需要时归为一个对象分析子包，保留单一公开入口，移除旧文件，不加兼容分叉 | 共享初始化/工厂/方法绑定，不按语言复制算法；仅改目录不算性能收益 |
| P2 | 扫描器分别组装前端、语义、规则表，能力覆盖散见文档 | 集中默认语言装配和能力矩阵，保留现有接口，不另建大型插件框架 | 能解析语法不代表有安全规则，不能把 Rust 等缺口自动计为已覆盖 |

不建议合并依赖图与程序图、把框架规则移入通用 CFG/DFG Pass，或把 LLM 报告验证混进静态事实提取。
也不建议仅因多个类的工具名称相同就合并 Agent 门面：普通查询与分批选择的权限/状态约束不同，它们已复用同一证据目录。
较稳妥的顺序是：运行级索引与纯证据装配 → 调用查询依赖下沉 → 候选构建服务收敛 → 有界解析复用。
每批独立做等价回归和耗时/内存对照，不与安全规则、提示词或实验预算同时修改后归因。

## 10. 下一阶段：以闭合链路为目标，不以规则数量为目标

以下为实施规划，尚未宣称已经完成原生 SSA 污点或框架全覆盖。先固定已有项目版本/依赖与
能力矩阵，再通过离线端到端正反例推进，不同时改实验提示词、预算和评分标准。

### P0：先识别真正的传播断点

已完成基础分类与分层留样：诊断 v2 按原因有界保留，少数类替换高频类，完整事件计数和省略量
不受留样上限影响。五类检查点只标记事件所属阶段，不表示所有链路已经完成检查；旧 v1 原样读取。
公共内容本轮离线验证：Windows 完整 687 项、WSL 针对性 32 项通过。chaos-mesh 872 文件复核
只有 flow_diagnostics 字段变化，安全事实、候选、值流及完整诊断计数与上一版一致；原先 200 条
定位全部为分配事件，新版 147 条覆盖 9 类原因。新诊断写入独立文件，目标源码和旧记录未修改。

为同一条候选保留输入识别、调用/返回、字段读写、敏感实参、证据交付五个检查点。
区分未使用/丢弃输入、常量敏感参数等正常无路径，与身份未映射、库副作用未建模、深度预算、
真正解析失败；诊断按类别保留有界定位和完整事件计数，不能让前 200 个分配事件挤掉全部其他断点。
不以候选数、入口数或六类 checked 代替链路完整性与准确性。

### P0：统一语义查询，减少第二套推断

已完成公共 calls 查询下沉和调用位置可选协议：同一行多个调用、未知坐标和未解析目标不猜测；
框架参数转发与求解器不再各自拥有调用索引。以下外部 API/使用点类型与 SSA 消费仍未完成。

已补齐原生声明级类型的公共持久化和查询：1.2 产物保留声明身份、位置、角色与冲突候选；
旧名字查询仅唯一时返回，形参投影不取内层同名变量类型。Go/TS 使用同一装配逻辑，不为每种
语言添加类型槽压平算法。原生引用能力继续按下面的可选契约推进，不以最近声明位置代替编译器绑定。
声明类型这批验证为 Windows 后端完整 695 项、WSL 针对性 17 项通过，包含真实 Go/TS 遮蔽声明。
chaos-mesh 872 文件复核的安全证据包与上一版逐项一致，原生 SSA 映射数仍为 3058；未调用模型、
未修改目标源码、实际配置或旧记录。此项完善公共事实保留，不代表新增污点路径或框架覆盖。

已增加 1.3 的变量引用绑定：公共声明/引用契约、精确查询/反查和声明类型查询由同一索引提供，
Go TypesInfo 与 TS TypeChecker 提供原生事实；没有给 Python/Java 等补造空白“成功”绑定。
类型未知仍可绑定，遮蔽/别名/跨文件声明分别保留；冲突、所属函数未映射或范围外变量显式记录。
引用包含写入，不能不经 CFG 就直接转为 DFG。后续公共身份第一批已通过声明投影 Pass、
到达定义和值流边的值身份消费 Go/TS 绑定；缺失/歧义仍保守回退，其他语言并未补造原生声明事实。
现有函数调用绑定继续使用，非调用的函数值引用、字段身份、完整使用点类型与 SSA 消费仍待接入。
这批 Windows 完整回归 704 项、Windows/WSL 针对性各 21 项及 Go 工具单测通过。
chaos-mesh 872 文件的安全证据包与上一版逐项一致，目标源码不变；新 Go 观察到 31668 条变量
引用、TS 观察到 9126 条（含未解析/所属未映射项，不是独立传播边或漏洞数）。Go 紧凑传输
25981660 字节、TS 1790693 字节，均未改变或触及 64 MiB 输出上限；两后端仍为 partial。
新验证使用独立诊断文件，未访问外部安全库或调用真实模型，不改旧记录/配置。

2026-10-22 公共值身份第一批已接入：ProgramGraph 1.8 使用读/写声明根与值流源/目标/读取身份，
公共到达定义按声明隔离 KILL，路径工作列表消费值身份；SemanticIndex 1.4 为形参和有依据的
直接调用接收槽追加声明/值关联。按需兼容投影只执行一次，不另建 Go/TS 求解器。
Python 安全 IR 改为作用域私有导入/遮蔽环境，但尚未产生同规格的全部声明/引用事实。
完整离线回归 723 项通过，WSL Go/TS 原生端到端和 Python/公共返回专项通过；
chaos-mesh 872 文件只读验证源码未变、机械核对无错误，仍有既有的 2 处恢复性解析缺口。
本批不扩大对象/容器、原生 SSA 消费、PDG 或外部模型范围；下一步仍按既定顺序推进。

2026-10-22 局部对象引用第二批：ProgramGraph 1.9 声明语言的对象赋值能力，
`value_binding.value_identity` 优先消费操作级精确字段身份。
`flow_analysis/object_references.py` 只计算已识别实例的有界 CFG 引用环境；
初始化和工厂返回统一接入此环境，再复用原到达定义/值流 Pass，没有平行保留旧别名拒绝算法
或新增语言私有污点求解器。`instance_common` 保留公共引用赋值判定和未知副作用拒绝，
`objects` 仍是编排入口；语言适配只提供引用/值复制能力，不承担污点传播。
Python、Java、JS/TS 支持直接局部别名共享直属字段及重绑定隔离，真实别名位置进入证据。
未覆盖的字段写回、工厂体内别名返回、容器、重复分配及未知副作用继续显式诊断，
不强行把 Go 值复制套成引用语义；保留非原生路径，不改旧实验或配置。
最终离线后端回归 733 项通过、无跳过，WSL 对象/多态工厂/返回绑定专项 26 项通过。

2026-10-22 对象公共接口收敛：`flow_analysis/object_contracts.py` 唯一拥有 LocalInstance、
ObjectBinding 和 ObjectReferenceResolver；对象编排已经通过协议消费现有 CFG 实现。
`value_binding/objects.py` 唯一拥有不可变 ObjectFieldSlot 和类型子槽渲染；ProgramGraph 1.10
分别保存结构化读/写槽，原 RD/DFG 消费统一生成的身份，新 Overlay 不重复写字符串身份表。
证据直接读取分配 ID，历史 1.9 私有 Overlay 的字符串解码集中在单一兼容入口，不回填旧记录。
后端离线完整 739 项、WSL 对象/工厂/返回专项 32 项通过；未增加原生 SSA、规则或对象写回能力。
下一批仍须按真实调用上下文实现形参字段入口、被调函数字段写入摘要、调用后字段定义与证据；
未完成前继续保留未知副作用/逃逸拒绝，不能仅靠新增接口启用旧字段摘要穿越调用。

2026-10-22 正常出口值跟踪与对象调用写回：ProgramGraph 1.11 增加直接引用实参事实及
`normal_exit_writes_modelled` 能力，公共绑定器按原参数语义逐位置过滤对象引用；普通标量传播
不受此过滤影响，变换表达式不冒充同一对象。`ObjectRootEffect` 统一对象参数与接收者字段接口，
`object_effects.py` 只缓存有界摘要，沿现有调用上下文和 `IRValueBoundary` 写回精确后置节点。
`post_calls.py` 唯一负责正常出口值观察和调用后定义，构造器与既有输出参数模型已复用，
删除重复的 CFG 后置接边实现；异常不经过正常写回，旧图缺失能力时不执行强更新。
初始化与已知单类型工厂结果共用一个权限回调，未建模的逃逸和副作用继续拒绝，不放宽成
“项目调用均无副作用”。未知根重绑定、同对象多形参、嵌套/多层对象修改、工厂体内修改后返回、
容器、Go 指针/值结构体、共享状态和并发仍是后续前沿；非原生解析及原有规则保留。
完整离线后端回归 753 项、WSL 相关专项 46 项通过；最终方法目标过滤另做 30 项定向验证通过。
未调用真实模型、未修改实际配置或旧实验；本批不混入提示词、证据排序和安全规则变更。

查询必须能够定位到源码位置/声明身份，而不只有 `(函数, 变量名)`；支持编译器已确认的外部
库 API 身份、类型/返回分量和明确失败。当前 CompilerBindingIndex 主要把有实现的项目声明映射
为调用边，不能据此声称外部 Gin/标准库 API 已统一绑定。标准库/框架模型不应为了匹配 API
伪造项目 CALLS 节点。先接入 Go/TS 已有事实，再替换适配器的对应推断；无覆盖时保留非原生回退。
`url.Values` 等编译器类型不能替代请求/本地集合的来源区别，框架来源模型仍需保留。

外部库后续分为两层：项目解析/编译器确认调用身份和实参绑定，版本化外部安全模型提供
Source/Sink、敏感参数、副作用和适用条件；不能让数据库名称匹配替代真实调用绑定。
现有框架规则作为无外部数据库时的离线模型和回归基线保留，本阶段不扩大手工规则范围。
尚未接入外部模型来源，未来查询必须保留包/版本/签名、模型来源/版本/快照和未命中原因；
没有命中不表示安全。解释性 CWE/RAG 文档不自动变为机器可执行的传播或净化模型。

### P0：Go 原生 SSA 打通公共值流消费接口

先补匿名处理器、跨文件方法与精确调用位置的身份映射，再对齐接收者、实参、返回和丢弃槽。
第一批支持标量赋值、分支 φ、普通调用/返回和明确字段投影，用 Gin/net/http 小链路验收。
内存 Store/Load、接口与未知副作用没有建模时，显式给出边界或按能力回退，不把“不支持”报告为安全。
选定函数使用一条明确的传播后端，不把两套图的边无条件并集；保留原公共 CFG/DFG 及非原生模式。
证据继续引用公共 callsite/location/slot 身份，服务器内部 SSA 不直接倾倒进模型摘要。

### P1：冻结框架范围，逐个验证真实值与参数

| 语言/运行环境 | 第一批边界 | 重点链路 |
| --- | --- | --- |
| Go | Gin、net/http | Query/Param/Body → 包装函数/Service → SQL、文件、进程或请求的实际敏感参数；内容/状态隔离 |
| Python | FastAPI/Starlette，已有 BackgroundTasks 范围 | 请求/上传、依赖注入、模型字段、普通调用/返回与已有回调边界；用 symtable 校准名字分类，不执行目标 |
| Java | Spring MVC、Servlet，优先 JDBC | 参数/RequestBody → DTO/Service → Repository；先接成熟声明/类型工具，JDK 已安装不等于已接入原生后端 |
| JS/TS 服务端 | Node http/https、已有 Express | 请求字段/正文 → 路由/中间件/普通包装 → fs、child_process、网络及已有 SQL 模型 |
| JS/TS 页面 | Vue、React 当前支持的形式 | 原生表单/props 的实际信任条件 → 已支持状态绑定 → v-html/innerHTML；普通转义输出为反例 |
| C/C++ | 当前标准库/POSIX/SQLite | 缓冲区内容、长度/状态、路径/命令/SQL 实参；不以此宣称支持任意 Web 框架 |

使用实际项目清单中锁定的版本建立 fixtures；版本缺失时只声明 API 模式支持。暂不扩展所有
框架、完整并发调度、跨进程传播或 Rust 安全规则。Vue SFC 未接官方编译器的部分仍明确非原生。
每个框架区分已支持、有界/条件支持、未支持和实际遇到的未解析项，而不是一个全绿“支持”。

### P1：端到端验收，再回到模型实验

正例必须检查入口/Source、每个实参和返回槽、真实 Sink 参数及完整证据步骤；负例包括固定安全
返回、错误/布尔状态、同名遮蔽、兄弟对象/字段、清洁覆盖、清洗错误变量/错误上下文和丢弃值。
Guard 不按名称自动截断。分页读取的静态路径必须与服务器保留步骤逐条一致，省略/未解析显式可见。
先用小项目确定预期，再用 chaos-mesh/audio2sheet/GitNexus 只读定位实际断点；真实项目候选不是
人工漏洞真值。达到这些离线要求后，再冻结新实现和协议开展 A/B，不与旧试次混算。

### 公共嵌套组合落地（2026-10-22）

在 `value_binding` 的既有 AccessPath 和直属 ObjectFieldSlot 上扩展，不新增另一套对象/污点图。
ProgramGraph 1.12 由各语言钩子输出直接引用 RHS；`flow_analysis/object_references.py` 统一
维护局部名称与字段引用，构造器纯存储摘要也复用该求解器。`object_effects.py` 仍唯一负责
已解析调用的正常出口标量写回，组合引用改变与同对象多参数继续作为前沿。
Python、Java、JS/TS 共用算法并保留非原生适配；16 段静态路径及既有状态预算有界。
继承/多继承、值结构体与指针布局留待语言事实接入，不能和本批组合能力混称全覆盖。

### 工厂快照与引用写回收敛（2026-10-22，ProgramGraph 1.14）

旧工厂按返回变量名找分配的逻辑已由 ObjectReferenceResolver.snapshot 替换。
ObjectSnapshot/ReferenceWrite 交换已知对象关系，object_snapshots 统一上下文重映射；
工厂与对象副作用共用 ObjectCallCompiler 协议，不新增语言专属污点引擎。
局部效果先接入；每轮工厂导入后再处理新可绑定调用，避免上一层工厂导出调用前的旧关系。
同一次调用不重复编译，正常出口只作观察而不被重新当成源码操作。
保持已有非原生适配；完整并发、容器、任意堆及复杂引用/深层值混合写入不在本轮扩展范围。

### 下一阶段：以链路闭合为验收，不继续横向堆规则

| 优先级 | 要衔接的功能 | 验收要求 |
| --- | --- | --- |
| P0 | 固定端到端断点用例与公共诊断 | 同一 case 核对入口/Source → 声明/实参 → 对象/返回/副作用 → 真正 Sink 参数 → 证据 ID/分页。每个中断有具体阶段，不能以规则命中数替代已建立值链。 |
| P0 | 把已有 Go SSA/TS 编译器事实接到公共值查询 | 先对齐匿名入口、接收者、实参、返回、字段 Store/Load；每个函数明确传播后端与回退原因。不要合并两套推断结果充数；Java 工具接入与公共消费者分离。 |
| P1 | 常见容器和对象边界 | 在现有静态下标之上补固定键/位置的容器别名、传参返回及取值；先设计共同存储选择器协议，避免再做平行的 dict/map/List 污点算法。验证 DTO/请求体到 Service/Repository 的真实断点。 |
| P1 | 外部 API 身份与版本化安全模型 | 编译器/项目解析先确认包、签名、参数身份；离线模型或外部模型源再提供 Source/Sink/副作用/适用条件。RAG/CWE 文本不能直接执行为传播和净化规则。 |
| P1 | 有界控制/防护证据 | 先明确正常/异常出口和 Guard 对哪个值、什么上下文、什么时序生效，再考虑控制依赖/切片。没有条件与上下文证明时仍保留候选，不按函数名截断。 |

实施时先固定 Python FastAPI、Java Spring/Servlet、Go Gin/net/http、Node 服务端和 Vue/React 的小链路
正反例；再只读用真实项目定位断点。第一批只做 P0 的断点矩阵与原生事实消费，不同时改变 LLM 提示词、
证据排序、预算或开启新 A/B。并发、MCP、全量反向切片和前端风险流可视化继续后置。

### P0 第一批落地：端到端矩阵与单函数值查询来源（2026-10-22，ProgramGraph 1.15）

`test/fixtures/chain_acceptance.json` 固定 9 个跨框架样例及各自正反例；逐段断言入口/Source、
真实调用、对象/框架值边界、敏感实参和按 ID 的全部步骤分页，不以风险数量证明链路完整。
Gin/Spring/Vue/React 未锁具体依赖版本的夹具只说明已适配 API 模式；不宣称全版本覆盖。

公共 `program_graph.ValueFlowQuery` 通过既有 SemanticSSAView 选择单函数值查询来源：
可完整映射的 Go SSA 使用原生值/φ 和精确 DebugRef 生成 Def-Use，替换而非并集原公共边；
TS 继续使用编译器声明/引用绑定与公共 CFG/DFG，不伪称 TS 原生 SSA。原安全工作列表、实参/返回
绑定和 ID 证据机制均复用。Go 匿名函数和其局部声明现在按共享源码身份映射，不归入外层函数。

每函数实际来源与回退原因保存在 flow_diagnostics.value_backends，可按诊断目录独立分页。
私有值槽装配后才进行最终选择；公共到达定义重算会失效旧标签，防止来源记录与实际边不一致。
新增源位置/调用索引与单函数预算，原图和原生事实不就地修改；失败不删除整函数、不混入半套边。

**仍是明确的后续前沿**：原生内存 SSA/Store/Load/FieldAddr、接口 invoke、闭包/协程及不能映射的
操作整函数回退，现有对象字段仍由公共路径承接。原生消费接口落地不等于完整 Go SSA 污点或框架
全覆盖。容器身份/元素边界本批未扩展，需用户确认范围后实施；外部 API 模型、Guard 有效性证明
及反向切片也没有混入。模型提示词、轮次、预算、实际 .env 和旧实验记录不改，真实 A/B 不重跑。

## 后续建议

收紧范围后的 1.17：动态键语法事实统一到 value_binding.SelectorExpression，由 program_graph 的
有界常量求值器处理唯一/有限/未知键，未知元素复用原引用环境与 RD，而不新增语言专用污点算法。
weak_update 与传播 certainty 分开；集合模型共用 CollectionEffectProvider，仅接 Python 局部原生列表
单次 append 样例。没有动态访问/候选操作的函数跳过新增计算和无用事实存储。
普通值传播预算提高到 8，结构搜索 6 和工厂转发 4 不联动；递归、完整闭包、并发及全量集合库后置。
后续优先依据真实项目关键链路断点选择新模型，不按语言特性清单自动扩展。

公共集合元素第一批（ProgramGraph 1.16）已接入：`value_binding` 拥有 CollectionAllocation、
CollectionElementSlot/StorageValueSlot 及规范路径装配；`program_graph.frontends.collections` 复用已解析 AST
提取直接字面量事实；`flow_analysis.collections` 只适配实例身份，引用、正常出口、返回快照仍走原实现。
原 reference_writes 收敛为一次 reference_interface 查询，同时给出组合写回与已核实借用返回，未保留平行版本。
Go map/slice 与定长数组角色分别处理；动态集合、Java Map/List 等 API 模型仍属后续前沿，详见覆盖清单。
旧历史段落保留对应阶段含义，当前能力看 1.16；非原生解析、原生工具和旧产物读取兼容均保留。

1. 多 Worker 或多机部署前，将原型 SQLite 队列迁移到 PostgreSQL 或专用消息队列。
2. Manifest 规则明显增长时拆分为 framework、entrypoint、package manager 和 command 检测器。
3. 为生产环境配置明确的 `GIT_ALLOWED_HOSTS`，并由网络层限制 Worker 对内网元数据地址的访问。
