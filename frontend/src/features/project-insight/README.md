# 项目洞察工作台

这是导入项目后的前端总编排模块。入口 `ProjectInsight.vue` 管理导入态与工作台态，并按导航项懒加载概览、代码与图、Agent、图实验、执行与安全页面。`KeepAlive` 保留切换后的会话和任务输出。

## 文件与职责

| 文件或目录 | 作用 |
| --- | --- |
| `ProjectInsight.vue` | 顶层状态、工作区切换、Git/ZIP 导入、后端快照恢复、概览生成和项目删除。 |
| `composables/useProjectAnalysis.js` | 统一项目数据、加载/删除状态、AbortController 生命周期和 API 调用。 |
| `views/ProjectExploreWorkspace.vue` | 组合文件树、静态安全路径图和符号包含关系；默认显示所有已保存候选并集，可单独选路。 |
| `composables/useSecurityPaths.js` / `domain/securityPaths.js` | 读取已有路径并集、按 ID 获取详情、精确成员筛选和取消迟到请求；不重扫、不调用模型。 |
| `components/SecurityPathDetails.vue` | 按需展示 Source/Sink、完整传播步骤的定位和值名，以及 Guard/Sanitizer 观察；不把观察等同防护证明。 |
| `components/ProjectImportPanel.vue` | Git URL 与 ZIP 文件输入，以及上传/静态分析阶段、文件计数、耗时与失败提示。 |
| `utils/importProgress.js` | 串行查询服务端导入进度、终态停止、错误重试和独立取消，以及文件阶段百分比计算。 |
| `components/ProjectWorkspaceHeader.vue` | 项目标识、语言、图规模和模型状态。 |
| `components/ProjectWorkspaceNav.vue` | 分组工作区导航。 |
| `components/ProjectOverviewPanel.vue` | Manifest 摘要以及静态/模型增强概览；明确区分配置中观察到的命令和系统运行建议。 |
| `components/ProjectDataManager.vue` | 列出后端项目与占用，恢复丢失的前端状态并经确认完整删除资源。 |
| `components/ProjectFileTree.vue` | 可筛选的项目文件树。 |
| `components/SymbolOutline.vue` | 当前文件的符号层次，并向上发出 `locate`。 |
| `domain/symbolTree.js` | `buildSymbolTree(symbols)`：将扁平符号转换为显示树。 |

## 关键交互

删除项目时，`useProjectAnalysis.clearLocal()` 会先中止在途请求并清空响应式数据，图组件随 `v-if`/生命周期销毁，避免 Sigma 在零宽容器上继续刷新。点击右侧符号由 `ProjectExploreWorkspace.locateSymbol()` 切回全部安全路径，并调用图公开方法 `revealSymbol(target)` 定位。若符号不在已保存路径中则提示，不展开无关的全项目依赖图。

中间画布已替换为静态安全路径，左右栏不变。路径的类型、分组和身份复用后端唯一的
`SecurityEvidenceSelector`，不建立第二套安全分类。默认包含全部有效的已保存候选（包括结构/操作线索），
不是 LLM 初始摘要挑选的前几条。已保存但未进入候选预算的值流，只要端点事实仍可引用，也会用只读显示 ID
纳入画布和详情；缺少事实的值流单独计数，不凭空补全。未配对 Sink 作为独立操作显示在所属函数上，不补造路径或族；同一函数内的传播只绘制一个符号，
在详情展开语句定位和值名。画布边仍表示原始调用方向，不冒充返回值流或完整 DFG。

`domain/securityPaths.pathFamilies()` 根据完整路径目录为 `path_group_id` 分配颜色和显示序号，
`securityGraph()` 只按明确的 `node_ids` / `edge_ids` 汇总族归属，交给通用画布渲染。
选路不改变同族颜色；缺少分组的旧路线使用路线 ID 作为独立族，不推断合并。
下拉选项、图例和节点/边详情使用同一族序号，真实分组 ID 在提示和路径详情中保留。
这些是前端展示元数据，不修改服务端静态事实、候选排序或 LLM 输入。

安全路径交换版本 `1.1` 的 `operations` 与 `paths` 分离：前者只有 Sink 和函数节点身份，没有 Source、
路径族或传播边。默认并集显示全部保留操作；选择操作后只显示其所属函数节点，并通过同一个只读详情接口
查看原事实定位。未配对并不代表不存在输入来源，也不证明漏洞。函数节点使用既有符号中的类限定名，
避免多个 Provider 都只显示 `_query`。静态视图不提供关系和连接数量筛选，避免隐藏低度数/孤立操作。

`GET /api/projects/{id}/security-paths` 读取路径图和目录；选路时通过
`GET /api/projects/{id}/security-paths/{candidate_id}` 获取完整定位。刷新只重读产物；旧产物无安全证据时
明确要求重新分析，不悄悄回退到调用全图。达到分析预算/显示上限、引用缺失、扫描失败和未支持语言分别提示。

导入页和项目页都可打开“数据管理”。恢复操作通过服务端快照重新填充文件树、规范化依赖图、Manifest 和静态概览，不复制源码也不重新分析；删除复用后端生命周期事务，清理源码、产物及关联运行记录。资源缺失或产物损坏的条目会禁止恢复，但仍可删除。

所有后端通信均通过 `src/services`；组件不直接拼接 API 根地址。

导入仍复用原来的长请求和事务，额外通过轻量进度请求每秒查询一次；慢响应不会产生重叠轮询。
ZIP 上传百分比来自 Axios 的上传事件，静态文件尝试计数来自原分析器。文件解析 100% 不代表全部
分析完成；安全扫描、CFG/DFG、污点传播等无可靠总量的阶段显示不定进度与耗时。进度暂不可用时
明确提示并自动重试，不中断正在分析的主请求。成功、失败、清空或页面卸载时停止轮询，旧请求
的迟到结果不会污染新任务。浏览器中止等待并不取消服务端事务，可到数据管理检查已提交项目。

模型诊断与切换集中在“智能体分析”页面。模型状态标签只表示环境配置完整；用户点击“测试连接”后会针对当前选择发起一次最小函数工具请求，同时验证文本生成与 Agent 工具协议，HTTP 状态、余额、消费限制、权限和限流错误会以安全字段展示。“查看可用模型”使用当前凭据查询目录并填充选择器，选中值随下一次 Agent 运行提交，不会改写服务端默认配置。
