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
| `metrics.py` | 汇总时延、步骤、工具调用、证据和估算字符/Token 指标。 |
| `baseline/` | 带明显标记的临时原始文件对照组。 |

`ExperimentRepository.create_pair(record, request)` 在一个事务中创建两个运行、队列项和配对；Worker 按 strategy 构造隔离上下文。两组都成功才可评分，通信失败/取消只算流程失败。旧数据库 `graph_run_id` 为兼容列名，新运行实际策略为 `security_evidence`，旧图实验不可混入新实验。

具体规则、指标含义、已知限制和删除清单见 `../../../docs/EXPERIMENT_PROTOCOL.md`，功能验证见 `../../../docs/FUNCTIONAL_VALIDATION.md`。若重复试验确认安全证据更优并结束对照实验，按清单移除临时对照代码；普通 Agent API 不直接依赖 baseline 包。
