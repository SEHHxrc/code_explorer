# 依赖图可视化模块

本模块把后端 `DependencyGraphDTO` 转换为 Graphology 图，在固定画布内使用 Sigma.js 交互渲染。所有节点保持统一视觉尺寸；重要性通过连接关系、筛选和聚焦体现，而不是节点大小。

“代码与图谱”工作区现在传入静态安全候选的函数级投影，而不是项目调用全图；此模块仍是通用画布，
不负责静态分析、选择证据或判断漏洞。路径载荷指定 `default_preset: 'all'`，确保外部/孤立的路径成员不会
被旧的“核心”过滤预设隐藏。节点详情附带 Source/Sink 角色和候选成员数量，不改变节点尺寸。
静态路径场景不显示“核心/架构/调用/全部”、关系筛选和重要度（连接数量）筛选，保留搜索、节点筛选及布局操作；
通用依赖图载荷仍保留预设，内部“全部成员”规则也继续用于初始化和符号定位。
Graphology 使用有向多重图并保留边 ID，同一节点对的不同关系不再被前端静默丢弃；重复调用点由
服务端交换层按关系/派发聚合，所有路线引用映射到保留的边。

静态路径载荷按 `path_group_id` 给节点和边统一配色，同族多条路线保持同色；切换单条路线不重新编号或换色。
跨族共享的节点/边在总览中使用中性色，详情保留全部所属族，选中单条路线后按该族着色，不复制边。
画布图例最多展开六个族，更多族通过选路和详情查看，避免图例挤占画布；颜色不表示风险等级或已确认漏洞。
曲线使用已有 `@sigma/edge-curve` 的 `createEdgeCurveProgram()` 启用并放大目标箭头，从 source 指向 target。
静态安全路径投影中的箭头仍是保存的调用方向，不等同污点/返回值传播方向。非安全载荷继续使用节点类型和关系类型配色。

独立操作不属于路径族：`operationCount` 和节点 `security.operations` 保留事实 ID、调用名和安全位置；
只在没有族颜色时使用统一操作色，不补造 Source 或边。同一函数的多个操作共用一个函数节点，
详情最多展开 20 项并提示余量，完整操作目录可通过顶部虚拟选择器按 ID 查看。

## 文件与核心入口

| 文件/入口 | 作用 |
| --- | --- |
| `DependencyGraph.vue` | 功能入口；组合筛选、布局、渲染、搜索、选择、隐藏和 `revealSymbol()`。 |
| `domain/graphModel.js` | `createGraph()`、种子位置、可见图、筛选、节点/边视图和符号到节点 ID 解析。 |
| `composables/useGraphModel.js` | 完整图/显示图状态、预设、隐藏节点和摘要。 |
| `composables/useGraphLayout.js` | 分层种子布局、ForceAtlas2 Worker、NoOverlap 和停止/销毁。 |
| `composables/useSigmaRenderer.js` | Sigma 实例、相机、拖拽、悬停、选择与刷新生命周期。 |
| `composables/observeGraphSize.js` | 容器尺寸去重、120ms 尾部合并和取消；不额外监听窗口事件。 |
| `components/GraphControls.vue` | 预设、层级/关系、最小度数、搜索、运行和重置控件。 |
| `components/GraphInspector.vue` | 当前节点/边及入边、出边详情。 |
| `graphStyle.js` | 转发共享视觉规则，并提供路径族色板、共享色和安全路径线宽。 |

## 数据和调用关系

```text
DependencyGraphDTO
→ createGraph() 归一化节点/边
→ useGraphModel.filterGraph()
→ useGraphLayout 仅对可见子图布局
→ useSigmaRenderer 渲染和交互
→ select/select-edge 事件返回父组件
```

`revealSymbol(target)` 是供文件符号栏调用的公开入口：输入路径、名称、完全限定名和可选行号，恢复全部节点后由 `resolveSymbolNodeId()` 找到最匹配节点，再选择、聚焦并刷新关系面板。

布局在 Worker 中运行，并按节点数缩短时长；组件停用、卸载或容器无有效尺寸时停止计算和渲染。画布容器隐藏 overflow，不使用内部滚动条；缩放与平移由 Sigma 相机处理。

Sigma 自身已监听 `window.resize`，组件不再叠加窗口全图刷新。`ResizeObserver` 只处理容器尺寸，
连续变化在 120ms 后合并，尺寸未变或已被 Sigma 同步时不再刷新；仅更新视口时复用节点/边属性缓存，
不重建模型、不重新启动布局、不重置相机。容器观察仍覆盖路径详情折叠/展开等非窗口尺寸变化。
KeepAlive 停用时销毁 Sigma 并取消观察，避免隐藏画布继续接收窗口事件；恢复时保留相机，
只有此前尚在运行的布局才恢复。已排队的原生绘制遇到短暂零尺寸时容忍过渡，但零尺寸下不创建实例。
异步重建使用代次和激活状态校验，销毁时断开观察者，
零尺寸容器不创建 Sigma，避免快速选路或切换工作区造成旧画布复活。少量节点的适配视图保留额外边距，
避免端点被统计浮层遮挡。
