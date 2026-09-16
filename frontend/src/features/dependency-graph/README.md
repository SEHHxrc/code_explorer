# 依赖图可视化模块

本模块把后端 `DependencyGraphDTO` 转换为 Graphology 图，在固定画布内使用 Sigma.js 交互渲染。所有节点保持统一视觉尺寸；重要性通过连接关系、筛选和聚焦体现，而不是节点大小。

## 文件与核心入口

| 文件/入口 | 作用 |
| --- | --- |
| `DependencyGraph.vue` | 功能入口；组合筛选、布局、渲染、搜索、选择、隐藏和 `revealSymbol()`。 |
| `domain/graphModel.js` | `createGraph()`、种子位置、可见图、筛选、节点/边视图和符号到节点 ID 解析。 |
| `composables/useGraphModel.js` | 完整图/显示图状态、预设、隐藏节点和摘要。 |
| `composables/useGraphLayout.js` | 分层种子布局、ForceAtlas2 Worker、NoOverlap 和停止/销毁。 |
| `composables/useSigmaRenderer.js` | Sigma 实例、相机、拖拽、悬停、选择与刷新生命周期。 |
| `components/GraphControls.vue` | 预设、层级/关系、最小度数、搜索、运行和重置控件。 |
| `components/GraphInspector.vue` | 当前节点/边及入边、出边详情。 |
| `graphStyle.js` | 转发共享的节点颜色、统一尺寸、边样式和布局权重。 |

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
