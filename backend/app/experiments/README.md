# 静态安全证据对照实验模块

本模块比较“相同原始文件访问能力 + 静态安全证据”与“仅原始文件”的安全分析效果，不预设输赢。两组共享指令、模型名、工具 Schema 和预算；独立随机化入队顺序与左右标签，不再输入完整图、Manifest 或 Repo Map。静态分析由 `services/security_analysis` 负责，本模块只负责编排、输入隔离和评审。

## 入口与数据流

HTTP 入口位于 `api/experiment.py`，应用入口是 `ExperimentComparisonService.create(project_id, user_id, request)`。创建后，两个运行进入同一 Agent 持久化队列；前端通过比较 SSE 查看盲态结果，提交 `BlindReviewRequest` 后才能揭盲。

```text
ComparisonRequest
  → ExperimentComparisonService
  ├─ security_evidence → 紧凑安全证据 JSON + 原始文件工具
  └─ baseline          → 仅相同原始文件工具（临时对照）
  → AgentQueueWorker
  → collect_run_metrics()
  → BlindReviewRequest
  → reveal()
```

## 文件与核心类型

| 文件/类 | 作用 |
| --- | --- |
| `contracts.py` | `ComparisonRequest`、`LaneScores`、`BlindReviewRequest` 和公开错误。 |
| `service.py` / `ExperimentComparisonService` | 创建配对运行、随机化、查询、评分和揭盲。 |
| `repository.py` / `ExperimentRepository` | 比较记录、左右映射和评审结果的持久化边界。 |
| `context.py` | 共同安全指令、产物允许列表、`SecurityExperimentContextBuilder`，保证完整 JSON 预算。 |
| `tools.py` | `ListProjectFilesTool` 和两组相同的原始文件工具注册表，不查询静态索引。 |
| `metrics.py` | 汇总时延、工具、真实供应商用量及其完整性、字符估算、终止原因和回答完整性。 |
| `report.py` | 两组共享的四段简洁报告约束，以及传输/报告结构完整性检查；不证明安全审计完整。 |
| `baseline/` | 带明显标记的临时原始文件对照组。 |

`ExperimentRepository.create_pair(record, request)` 在一个事务中创建两个运行、队列项和配对；Worker 按 strategy 构造隔离上下文。创建前两组都经过公共 `BudgetPlanner` 验证完整初始 JSON 和实际请求表示，避免只有对照组消耗模型。两组使用相同原生工具消息协议；真实 usage 与本地请求 Token 估算分别统计，保留估算方法和预算快照。窗口版本、输入/输出预算、分词策略或思考配置不一致，不进入正式配对评价；运行中预算改变同样拒绝。旧数据库 `graph_run_id` 为兼容列名，不表示新实验输入完整图；历史记录不回填。

新运行使用 `static-security-v2` / `concise-security-v1`。供应商未返回 usage 时保留空值，不能用字符估算冒充真实用量；历史运行缺失终止元数据时标为未知，不回填或修改原记录。报告完整只说明正常终止且四段内容和结束标记存在，不等于漏洞已全部检出。

新请求预算版本为 `utf8-token-budget-v2`：输入按完整请求 JSON 的 UTF-8 字节估算，输出按可见文本和规范化工具调用内容的字节估算，不包含未公开的思考文本，不能代替供应商消耗。旧事件缺少输出字节数时继续标记为历史字符近似。失败或取消后的证据数也从上下文/工具/完成事件联合去重，不再因缺少最终完成事件显示为零；不改写原事件。

具体规则、指标含义、已知限制和删除清单见 `../../../docs/EXPERIMENT_PROTOCOL.md`，功能验证见 `../../../docs/FUNCTIONAL_VALIDATION.md`。若重复试验确认安全证据更优并结束对照实验，按清单移除临时对照代码；普通 Agent API 不直接依赖 baseline 包。
