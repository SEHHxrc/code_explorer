# 前端功能模块

`features` 按用户可感知的能力组织代码，使页面组件、组合式状态、领域转换与局部子组件保持在同一边界内。

| 目录 | 页面入口 | 依赖 |
| --- | --- | --- |
| `project-insight/` | `ProjectInsight.vue` | 项目 API、其余三个工作区、共享 Agent 组件。 |
| `dependency-graph/` | `DependencyGraph.vue` | Graphology、Sigma.js、ForceAtlas2、NoOverlap。 |
| `experiment-comparison/` | `ExperimentComparison.vue` | 实验 API 与 SSE。 |
| `execution/` | `ExecutionWorkspace.vue` | 执行 API、SSE 和 `parseArgv()`。 |

功能模块之间通过 Props、事件和稳定服务函数通信，不应直接访问彼此的内部 `ref` 或私有辅助函数。
