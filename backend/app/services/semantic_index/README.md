# semantic_index 模块

## 安全主流程的公共事实（1.6）

`files` 保存项目相对文件到语言的范围；`None` 表示旧记录缺失，`{}` 表示明确空范围。
`projection.ProgramFacts` 统一提供文件、声明节点和调用边投影；有新调用事实时不混入旧图的另一套目标。
安全扫描、ProgramGraph 文件发现、结构搜索和前端路径投影均通过此入口读取，不再要求完整 node-link 图。

`dynamic.BoundedCallableResolver.enrich(artifact, program_graph)` 消费前端的 `CallableAccess` 和 CFG 到达定义，
返回新的事实索引及私有程序图。当前支持已知实例上的成员方法值、Python getattr、有限字符串成员名、
模块函数别名和局部别名回溯；共用 TypeHierarchy 处理继承，候选始终为 inferred/may。
每调用点最多 128 个目标、别名回溯最多 8 层；未知接收者、运行时成员名、遮蔽、覆盖和截断均保留边界。
绑定后接收者变量被覆盖时，不借用新对象的 self 字段。动态导入、数据库选择的插件及任意反射仍可能未知。
`CallSiteFact.binding_diagnostics` 保存展开与调用绑定局限，不表示代码有漏洞，也不用于证明净化有效。

原生工具的进程、环境和协议实现已归入 `services/language_backends/`；本目录保留事实契约、
索引、查询协议及图到事实的 Provider。原 go/typescript/go_environment/worker/wire 文件仅转出同一实现，
不在事实层启动进程。默认后端装配通过 `language_backends.project_resolvers()`，调用协议仍定义于 resolution。

本模块保存依赖图与 ProgramGraph 之间真正可复用的“语义事实”，而不是再定义一种图。
它通过 `SemanticIndexView` 接口提供只读查询，内部当前使用 Pydantic 产物和轻量内存索引。

## 公共查询入口

- `SemanticIndex.callsite(callsite_id)` 返回原调用事实，未解析原因不会因为没有目标而丢失。
- 可选协议 `SemanticCallSiteView` 的 `calls_at(SemanticLocation, caller_symbol=...)` 按起始行及已知
  列/结束范围/位置身份筛选。坐标零值表示未知，同一行多个调用返回全部匹配，不选择第一个；
  起始行未知时不猜测。位置查询不执行目标解析，也不能查询尚未持久化的外部 API 身份。
- `calls.py` 唯一拥有 `CallTransition/CallTransitionIndex`，通过 `CallTransitionView` 提供
  `for_source(symbol)`、`for_target(symbol)` 和 `for_callsite(caller_symbol, callsite_id)`。
  仅投影已有 calls，不包含实例化边，不创建新目标，不绑定语言参数或运行污点传播。
- `from_sources(semantic_index=..., dependency_graph=...)` 优先读取非空语义调用事实；旧产物没有
  调用事实时使用唯一依赖图适配入口。非空索引中的未解析调用不与旧图目标无条件合并。
- 规则扫描的框架参数角色转发和污点传播共用这个查询；扫描按运行最多建立一次调用索引。
  `security_analysis/flow_analysis/call_graph.py` 仅保留语言参数绑定及旧查询名的兼容导出，
  不保留另一套调用边索引。旧基础 `SemanticIndexView` 不被强迫实现新位置/SSA 可选协议。

### 声明级类型与兼容查询

原生编译器已有的声明类型现在按事实身份完整保存在 `variable_types`，不再压成唯一的
`(owner_symbol, name)` 槽。`VariableTypeFact` 新增 `role`（parameter/variable/unknown）和
`declaration_id`；完整声明坐标才能生成精确声明身份，缺失时保持为空。同一声明的冲突类型也
分别保留；不能因为类型文本相同就合并两个不同位置的声明。重复同一事实则只保留一次。

- 可选协议 `SemanticTypeView.variable_types_for(owner_symbol, name, role=...)` 返回全部候选。
- `variable_types_at(declaration_location, owner_symbol=..., name=...)` 按声明位置查询，和调用查询
  共用 `locations.matches_location`；同一行的歧义不选第一个，位置未知不猜测。
- 旧 `variable_type(owner_symbol, name)` 仅在恰有一条事实时返回，否则返回 None。名字只能
  找到候选，不等于证明使用点绑定。不要直接以名字槽键读取新产物的 variable_types 字典。
- ProgramGraph 的形参投影优先读取明确 parameter 角色，不把内层同名局部变量的类型写到形参上；
  角色未知的旧产物仍可在唯一候选时兼容，冲突则不填类型。

新原生声明类型产物版本为 1.2，继续读取 1.0/1.1。ProgramGraph 追加值槽时不会降级版本。
类型数量统计的是保留的事实数，不是不同变量名数；旧记录不重建。不按最近行号推断可见性。

### 变量引用→源码声明绑定（1.3）

`VariableDeclarationFact` 表示源码中的变量/形参声明，`VariableReferenceFact` 表示一个标识符
引用及其声明候选。它们没有运行时内存地址，也不代表 SSA 赋值版本。一个符号可能有多处合法
声明，保留候选列表，不强行选一个位置。`ProgramIdentity.declaration_id()` 是类型与引用共用
的稳定身份入口，缺少精确声明范围时保持未知。

可选协议 `SemanticReferenceView` 提供：

- `variable_declaration(declaration_id)` / `variable_reference(reference_id)`：查询保存的实体。
- `variable_references_at(location, owner_symbol=...)`：按引用位置查询全部匹配。
- `variable_references_to(declaration_id)`：反查已保存引用，包括写引用，不是数据流读边。
- `declaration_types_for_reference(reference_id)`：沿编译器绑定查询声明类型候选；不是使用点的
  流敏感类型，绑定冲突/悬空时不按同名或最近位置补结果。

Go 使用 `go/types.Info.Defs/Uses` 的符号实体；TS 使用检查器符号及项目内变量声明，并处理
导入别名、块级遮蔽、解构声明和简写属性。类型 any/unknown 不使声明绑定丢失。外部/范围外
变量保留明确原因；匿名函数所属身份无法映射时保留 owner_unresolved_reason，不错误归入
外层函数，也不抹掉已知的外层声明绑定。绑定冲突不使用最后写入者。

引用能力由 backend_runs.capabilities 的 variable_reference_binding 声明；旧产物及其他后端
缺省为空，不能因查询对象实现了协议便声称已产生绑定。1.3 通过共同 Provider 持久化，追加值槽
不降级版本。主传输仍为 tables-v1，通过可选的变量声明/引用共享表扩展，完整校验行长与外键；
Python 复用表对象和已登记声明，不按每次引用重复展开声明。

**已有 Go/TS 声明/引用现已通过公共 DeclarationBindingPass 进入到达定义和值流求解。**
例如 x=输入、随后 x=安全常量、最后 sink(x)：赋值和读取引用仍绑定同一源码变量，
CFG/到达定义区分赋值版本；同名不同声明不再互相 KILL 或直接串联。函数调用使用既有调用
声明绑定，函数值的一般引用、字段/堆对象、动态属性和完整使用点类型未在本批实现。
Python/Java 等后端尚未输出这份可选原生引用事实，非原生分析继续保留。

1.4 追加 `ParameterFact.declaration_id` 和 `ValueSlotFact.value_ids`，将有依据的形参/直接调用
接收槽关联到声明根派生值身份。多调用语句不把整节点定义伪装为每个调用的精确接收关系。
缺失绑定时字段为空，不伪造 Python/Java 编译器事实。旧 1.0～1.3 继续读取；各增量投影不降级版本。
这不意味着原生 SSA、堆别名、外部 API 身份或所有使用点均已覆盖。

## 编译器语义接入

`resolution.ProjectSemanticResolver.resolve(project_root, file_paths=..., max_file_bytes=...)`
是项目级语言工具接口，不接收依赖分析器内部的 Definition、Reference 或 NetworkX 状态。
返回 `ProjectSemanticResult`：声明绑定、变量/参数/返回类型、能力、工具版本及诊断。
Go 还可返回语言无关的原生 SSA 交换事实；映射后存入索引 `native_ssa`，
可用 `SemanticIndex.native_ssa_for(symbol_id)` 读取，不把完整 SSA 加入 LLM 初始摘要。
查询能力采用可选的 `SemanticSSAView` 协议，不强迫没有原生 SSA 的语言提供占位实现。
`ssa.validate_ssa_function` 唯一负责交换事实的块/指令/值外键与 φ 结构校验，工具接入和消费/恢复共用，
不各维护一套校验器，也不把结构合法解释为安全语义正确。
`SemanticIndexProvider.build(...)` 仍只转换已有依赖分析状态，不负责启动编译器。

`language_backends.TypeScriptSemanticResolver` 使用锁定 TypeScript 6.0.3 的 Node 工作进程。
每次项目分析启动一次进程，按最近的 tsconfig/jsconfig 建立必要 Program，会话内复用读取和
配置结果；不启动监听器、安装目标依赖、emit、执行构建或语言服务插件。安装入口见
`backend/tools/typescript_semantics/README.md`。缺少工具、超时或协议失败均显式回退。

`language_backends.GoSemanticResolver` 使用固定 x/tools v0.52.0 的可信 Go 工具，按项目模块加载类型，
只为明确的、类型正确的包构建 SSA；缺依赖、构建标签和当前主机平台导致的缺口明确记录。
准备入口见 `backend/tools/go_semantics/README.md`。`language_backends.worker.run_worker` 是 Go/TS 唯一共用的
进程 JSON 传输入口，统一超时、输出预算、启动被拒绝和协议错误分类，不维护两套重试/传输实现。
`language_backends.wire.decode_table_result` 是两种工具唯一的紧凑表解码入口：共享文件、声明、类型文本；
原生 SSA 另以位置外键和定长值/指令/基本块行交换。公共事实模型不变，不通过删指令、
截类型或降低扫描范围缩小输出。负数/布尔/越界引用和错误行长均失败，不原地修改输入。
Go 类型格式化缓存仅属于本次任务；完整类型字符串仍可从公共事实读取。协议与准备命令见 Go 工具 README。

`language_backends.GoAnalysisConfig` 是 Go 专用的环境白名单策略：只读探测宿主有效缓存并验证
位置，正式分析关闭 GOENV 和外部钩子；可显式配置目标 OS/架构、构建标签及缓存目录。
实际条件存于 backend_runs.analysis_configuration，缓存路径只存摘要，不泄漏完整宿主环境。
默认不变更构建平台，不启用 CGO/go.work，不下载目标依赖。详见工具 README 和 .env.example。
显式运行 `backend/tools/go_semantics/prepare.ps1` 后，工作区 `.goenv/ready` 标识本地依赖环境
已准备；未显式覆盖时优先复用其中的 modules/build 缓存，不修改全局 Go 配置。
分析器仍严格离线；本地环境位于待分析目标内部时不用于缓存写入，退回宿主有效缓存。

`dependency_analyzer.compiler.CompilerBindingIndex` 按路径、名称和声明范围唯一映射既有符号。
调用图生成前选择结果，语义索引和安全分析复用同一组边；成功绑定跳过旧名称推测，不无条件
合并新旧目标。虚调用可以引用既有继承关系追加 may 候选，并注明独立解析来源。无实现签名、
动态类型、未对齐身份和 Vue 会明确回退，不等于完成编译器语义覆盖。

兼容性可选字段：`backend_runs` 保存工具/协议版本、实现哈希、配置摘要、诊断完整计数及
有界样本、Program 数和传输体积；调用点/目标用 `fallback_reason` 保留回退原因；变量类型
记录解析方法和源码位置。旧产物缺省为空，不按新规则重建或改写历史记录。
`typescript_declaration_binding` 一律为 inferred/may，不证明唯一动态分派、完整堆或路径可行性。
`go_declaration_binding` 遵循相同规则。SSA 函数、块、指令、参数和操作数有独立身份；
φ、接收者、实参序号、源码 DebugRef 及投影信息不会靠名字或行号替代。当前安全传播
仍消费公共 ProgramGraph 接口。当前安全求解完成私有槽位装配后，由 `program_graph.ValueFlowQuery`
选择整函数的值查询来源：可完整映射的 Go 标量/φ/直接调用使用 SSA 定义—使用事实，
其余函数保留公共 CFG/DFG 并记录回退原因；不是完整原生内存 SSA/污点覆盖。

## 任务级性能机制

- `DeclarationSymbolIndex` 是与语言无关的声明唯一匹配表，输入符号及路径/名称/种类/起止行，
  一次建立、常量时间查询。冲突保留为 None，不退回按名称猜测，供未来 Go/Java 同样复用。
- `ProjectSemanticResult.timings_ms/cache_stats` 是可选的后端观测元数据，不参与安全事实、
  置信度或候选排序。当前记录 Program/提取、工作进程、JSON 读取、协议解码与总体时间；
  缓存命中数不是准确率、已覆盖数量或漏洞数量。
- 公共 Provider 以最多 4096 条为一批，使用相同 CallSiteFact/SemanticLocation 模型进行
  Pydantic 校验，减少逐条 Python 构造开销。批次不是保留上限，不抽样，不使用 model_construct，
  不跳过约束；无论输入来自何种语言都复用此装配路径。
- 所有源码/声明查询缓存只属于一次分析，不跨快照存留。跨任务持久缓存和增量编译本批未实现，
  避免依赖、配置或工具版本变化时复用旧绑定。

只读性能验证入口：

```powershell
python -X utf8 -m test.profile_semantic_backend --project-root "D:/samples/project" --output "backend/storage/runtime/semantic-profile-new" --profile
```

去掉 `--profile` 可测非采样运行；输出必须是尚不存在且位于目标源码之外的目录。
该工具保留独立 CPU profile、传输产物及完整语义指纹；
计时不含后续源码指纹核对和诊断写盘。比较时保持项目、实现范围、线程数和采样方式一致。

## 当前事实

- `CallableFact`：函数、方法和构造器定义；
- `TypeFact/BaseTypeFact`（1.5）：类型、直属字段类型、按声明顺序排列的父类/接口/嵌入、虚基类及未解析父类型；
- `CallSiteFact`：调用位置、调用者、名称、接收者及未解析原因；
- `ResolvedTargetFact`：调用候选目标、解析方法、置信度、may/must 和截断状态；
- `VariableTypeFact`：作用域内变量的声明或保守推断类型。
- `VariableDeclarationFact/VariableReferenceFact`：变量源码实体与编译器引用绑定，不是值流。
- `ParameterFact`：ProgramGraph 中的形参、位置、类型和参数槽位；
- `ReturnFact`：每处 `return` 的值名称、源码位置和统一返回槽位；
- `ValueSlotFact`：`PARAMETER_SLOT`、`RETURN_SLOT`、`CALL_RESULT_SLOT` 三类跨图值接口。

`DependencyAnalysisSemanticProvider` 生成基础事实；含类型层次时格式为 1.5。它只读取依赖分析器已有的定义、引用、
变量类型表和 MultiDiGraph，不重新解析源码。`ProgramGraphSemanticProvider` 再通过
`SemanticIndexEnricher` 接口追加 1.1 值传播事实，同样不重新读取源码。项目分析会持久化
增强后的索引；类型事实和后续值/编译器事实共存，增强器不降级 1.5。安全分析优先从该索引取得跨过程调用目标和返回接口，旧调用方仍可回退到
依赖图边与 ProgramGraph 节点。

`hierarchy.py` 的 `TypeHierarchy` 供依赖成员解析和对象消费者共用，不重新扫描 AST。
字段类型优先消费 `Definition.declared_type_literal`，保留 `*`/`&` 等存储修饰；
依赖调用查找仍使用原 `type_literal` 规范化类型，不能为了找类名丢掉值/引用语义。
`order` 为 Python 计算 C3；其他语言的 `ancestors` 仅为祖先索引，成员选择用 `member_owners`。
C++ `subobjects` 保留非虚基类路径、规范共享虚基类，成员查找复用同一支路遍历。
直接声明与实际实例类型分开；循环、C3 冲突、不完整父类型和路径预算不能用随意顺序消除歧义。

## 边界

`semantic_index` 不拥有语言私有 AST、依赖边或安全结论，也不要求生产者继承公共基类。
原生 SSA 是只读交换事实，保留工具的基本块/值身份供后续 ProgramGraph 投影，不另建调用图。
未来语言
前端可以实现项目级解析协议，再适配为既有事实；产物生产者也可实现
`SemanticIndexProvider`/`SemanticIndexEnricher`。有限字段/工厂传播属于安全分析的私有
Overlay，不归本模块拥有；完整堆、容器别名和动态目标仍未解决，语言私有 AST 不进入公共层。
