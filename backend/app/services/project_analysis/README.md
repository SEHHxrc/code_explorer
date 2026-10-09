# project_analysis 纯项目分析模块

本模块只分析一个已经存在的源码目录，不负责用户身份、Git/ZIP 获取、工作区发布、数据库或
Artifact 持久化。稳定入口为：

```python
bundle = ProjectAnalysisPipeline().analyze(project_root, max_workers=4)
```

`ProjectAnalysisBundle` 包含原始依赖图、符号、Semantic Index、统计、未解析诊断、安全证据、
文件树、Manifest、Repo Map、确定性概览和前端交换图。

| 文件/类 | 职责 |
| --- | --- |
| `pipeline.py` / `ProjectAnalysisPipeline` | 编排依赖分析、安全分析和确定性派生数据，不执行持久化。 |
| `pipeline.py` / `ProjectAnalysisBundle` | 纯分析输出契约。 |
| `graph_exchange.py` / `GraphExchangeNormalizer` | 把原始图投影为有界、路径安全的公开 DTO。 |

项目创建用例位于 `services.projects.ProjectImportService`；`ProjectRepository`、Artifact、查询、删除
和事务不属于分析模块。

新增语言安全能力只需扩展 `security_analysis` 注册表。流水线继续调用同一安全服务，并将
`security_evidence` 与增强后的 `semantic_index` 放入分析 bundle，随后由 `projects` 持久化。
C/C++ 和 JS/TS 首批规则已接入这一路径，API、导入服务和仓储不包含语言规则分支。
