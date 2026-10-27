# project_analysis 纯项目分析模块

一次 analyze 调用建立 syntax_analysis.session，依赖、安全语言适配与程序图可复用相同内容的
Tree-sitter 树；默认有界保留批次，不把整仓源码/树跨任务常驻。diagnostics.syntax_reuse 仅为观测，
不参与规则、置信度或候选排序。访问检查和实际文件读取仍保留；缓存结束或异常时释放。

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
| `progress.py` / `AnalysisProgressReporter` | 兼容导出 core.progress 的同一接口；不在项目编排层拥有通用阶段观测实现。 |
| `graph_exchange.py` / `GraphExchangeNormalizer` | 把原始图投影为有界、路径安全的公开 DTO。 |
| `security_paths.py` / `SecurityPathProjection` | 把已保存安全候选投影为函数级图并集及按 ID 展开的步骤定位；复用安全域路径目录，不重新求解。 |

项目创建用例位于 `services.projects.ProjectImportService`；`ProjectRepository`、Artifact、查询、删除
和事务不属于分析模块。

静态路径查看由 `projects.ProjectQueryService.security_paths(project_id, user_id, candidate_id=None)`
校验所有权后调用投影器；HTTP 入口为 `GET /api/projects/{project_id}/security-paths` 及其候选 ID 子路径。
交换契约在 `schemas/security_paths.py`：只公开节点、引用关系和相对源码位置，不返回完整内部图、
源码片段表或任意元数据。原 Artifact 和实验输入均不修改。`1.1` 交换契约将未配对 Sink 放入独立
`operations` 列表，沿用安全域目录中的事实 ID；只显示所属函数和操作位置，没有 Source、路径族或推断连线。
同一个函数的多个操作共用节点；方法名称沿用符号中的类限定名，避免所有 Provider 都只显示 `_query`。
`detail(id)` 同时支持候选和未配对操作 ID，后者只返回一处已保存 Sink 定位，确定性保持 unknown，
不会把它伪装成完整传播步骤。函数内路径不伪造自环，
候选预算外的已保存值流若端点仍可引用，也纳入只读显示投影，不沿用 LLM 摘要的交付预算。
函数间调用边仅来自候选实际引用，不因两个节点都出现而附加其他依赖。无安全产物、扫描未完成、
分析/展示截断与引用缺失必须显式区分；“已保存候选全部可见”不代表全路径覆盖。

查询服务最多缓存一个项目的轻量投影（安全证据与引用符号，而非完整 Semantic Index/原产物），
每次先检查所有权，再用 Artifact 的修改时间、大小与文件身份核对版本；原子替换、删除或查询其他项目
会失效。并发加载串行化，避免多人点击同一巨型产物时同时解析数份完整 JSON；按需详情复用该投影。

新增语言安全能力只需扩展 `security_analysis` 注册表。流水线继续调用同一安全服务，并将
`security_evidence` 与增强后的 `semantic_index` 放入分析 bundle，随后由 `projects` 持久化。
C/C++ 和 JS/TS 首批规则已接入这一路径，API、导入服务和仓储不包含语言规则分支。

`analyze(project_root, max_workers=4, progress=reporter)` 可接收符合协议的观察者。
流水线发布依赖分析、安全扫描与结果投影阶段；安全服务继续发布 ProgramGraph、污点传播与
证据组装阶段。进度任务的用户身份、保存和事务终态由 `projects` 管理，语言前端不依赖 HTTP
或进度登记表。文件解析结束不等于安全分析结束，阶段进度不参与 A/B 实验证据或结论计算。
