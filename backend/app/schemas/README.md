# 公开数据模型模块

本目录定义项目分析响应的 Pydantic DTO，是后端服务层与前端之间的显式契约。

| 文件 | 主要模型 | 作用 |
| --- | --- | --- |
| `dependency_graph.py` | `GraphNodeDTO`、`GraphEdgeDTO`、`DependencyGraphDTO` | 版本化依赖图节点、边、警告和统计。 |
| `manifest.py` | `ProjectManifest` 及语言、框架、入口、命令等子模型 | 为概览和 Agent 提供确定性项目事实。 |
| `project_analysis.py` | `ProjectAnalysisData`、`ProjectAnalysisResponse` | 组合项目 ID、清洗报告、文件树、依赖图、Manifest 和概览。 |

原始分析器字典必须先由 `services.project_analysis.graph_exchange` 转换为 DTO，不能由中间件临时改写。若字段发生不兼容变化，应更新依赖图 `schema_version` 并同步前端 `features/dependency-graph/domain/graphModel.js`。
