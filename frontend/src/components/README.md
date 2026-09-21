# 前端共享与兼容组件

本目录包含跨功能共享组件，以及重构后为保持旧导入路径而保留的薄转发层。

| 文件 | 作用 | 维护说明 |
| --- | --- | --- |
| `AgentWorkspace.vue` | 智能体问答、历史运行恢复、请求级模型切换、连接测试、模型目录、取消、SSE 步骤时间线和证据列表。 | 按项目发现普通 Agent 历史并回放安全快照，A/B 运行不混入列表。 |
| `ProjectInsight.vue` | 转发到 `features/project-insight/ProjectInsight.vue`。 | 仅兼容 `App.vue` 等旧路径，不在此复制业务。 |
| `DependencyGraph.vue` | 转发图数据与选择事件到功能组件。 | 正式实现位于 `features/dependency-graph/`。 |
| `graphStyle.js` | 依赖图节点类别、统一尺寸、颜色、边关系和布局权重。 | 被功能目录中的同名转发文件重新导出。 |

修改项目工作台或依赖图行为时，应优先编辑 `features/` 下的实现；只有需要调整公共视觉规则时才编辑本目录的 `graphStyle.js`。兼容文件可在所有调用方迁移后统一删除。
