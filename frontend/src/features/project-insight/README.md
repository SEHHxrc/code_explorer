# 项目洞察工作台

这是导入项目后的前端总编排模块。入口 `ProjectInsight.vue` 管理导入态与工作台态，并按导航项懒加载概览、代码与图、Agent、图实验、执行与安全页面。`KeepAlive` 保留切换后的会话和任务输出。

## 文件与职责

| 文件或目录 | 作用 |
| --- | --- |
| `ProjectInsight.vue` | 顶层状态、工作区切换、Git/ZIP 导入、概览生成和项目删除。 |
| `composables/useProjectAnalysis.js` | 统一项目数据、加载/删除状态、AbortController 生命周期和 API 调用。 |
| `views/ProjectExploreWorkspace.vue` | 组合文件树、依赖图和符号包含关系；把右栏定位动作转为图节点聚焦。 |
| `components/ProjectImportPanel.vue` | Git URL 与 ZIP 文件输入。 |
| `components/ProjectWorkspaceHeader.vue` | 项目标识、语言、图规模和模型状态。 |
| `components/ProjectWorkspaceNav.vue` | 分组工作区导航。 |
| `components/ProjectOverviewPanel.vue` | Manifest 摘要和静态/模型概览。 |
| `components/ProjectFileTree.vue` | 可筛选的项目文件树。 |
| `components/SymbolOutline.vue` | 当前文件的符号层次，并向上发出 `locate`。 |
| `domain/symbolTree.js` | `buildSymbolTree(symbols)`：将扁平符号转换为显示树。 |

## 关键交互

删除项目时，`useProjectAnalysis.reset()` 会先中止在途请求并清空响应式数据，图组件随 `v-if`/生命周期销毁，避免 Sigma 在零宽容器上继续刷新。点击右侧符号由 `ProjectExploreWorkspace.locateSymbol()` 调用依赖图公开方法 `revealSymbol(target)`，恢复全部节点、解析对应节点并模拟图中双击聚焦效果。

所有后端通信均通过 `src/services`；组件不直接拼接 API 根地址。
