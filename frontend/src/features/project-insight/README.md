# 项目洞察工作台

这是导入项目后的前端总编排模块。入口 `ProjectInsight.vue` 管理导入态与工作台态，并按导航项懒加载概览、代码与图、Agent、图实验、执行与安全页面。`KeepAlive` 保留切换后的会话和任务输出。

## 文件与职责

| 文件或目录 | 作用 |
| --- | --- |
| `ProjectInsight.vue` | 顶层状态、工作区切换、Git/ZIP 导入、后端快照恢复、概览生成和项目删除。 |
| `composables/useProjectAnalysis.js` | 统一项目数据、加载/删除状态、AbortController 生命周期和 API 调用。 |
| `views/ProjectExploreWorkspace.vue` | 组合文件树、依赖图和符号包含关系；把右栏定位动作转为图节点聚焦。 |
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

删除项目时，`useProjectAnalysis.clearLocal()` 会先中止在途请求并清空响应式数据，图组件随 `v-if`/生命周期销毁，避免 Sigma 在零宽容器上继续刷新。点击右侧符号由 `ProjectExploreWorkspace.locateSymbol()` 调用依赖图公开方法 `revealSymbol(target)`，恢复全部节点、解析对应节点并模拟图中双击聚焦效果。

导入页和项目页都可打开“数据管理”。恢复操作通过服务端快照重新填充文件树、规范化依赖图、Manifest 和静态概览，不复制源码也不重新分析；删除复用后端生命周期事务，清理源码、产物及关联运行记录。资源缺失或产物损坏的条目会禁止恢复，但仍可删除。

所有后端通信均通过 `src/services`；组件不直接拼接 API 根地址。

导入仍复用原来的长请求和事务，额外通过轻量进度请求每秒查询一次；慢响应不会产生重叠轮询。
ZIP 上传百分比来自 Axios 的上传事件，静态文件尝试计数来自原分析器。文件解析 100% 不代表全部
分析完成；安全扫描、CFG/DFG、污点传播等无可靠总量的阶段显示不定进度与耗时。进度暂不可用时
明确提示并自动重试，不中断正在分析的主请求。成功、失败、清空或页面卸载时停止轮询，旧请求
的迟到结果不会污染新任务。浏览器中止等待并不取消服务端事务，可到数据管理检查已提交项目。

模型诊断与切换集中在“智能体分析”页面。模型状态标签只表示环境配置完整；用户点击“测试连接”后会针对当前选择发起一次最小函数工具请求，同时验证文本生成与 Agent 工具协议，HTTP 状态、余额、消费限制、权限和限流错误会以安全字段展示。“查看可用模型”使用当前凭据查询目录并填充选择器，选中值随下一次 Agent 运行提交，不会改写服务端默认配置。
