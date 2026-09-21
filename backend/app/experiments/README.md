# 依赖图效果对照实验模块

本模块只用于验证当前“依赖图上下文 + 依赖邻居工具”完整策略是否优于临时无图策略。两条通道使用同一问题、模型、步骤预算和项目快照，并随机化左右展示位置。当前虽保存了随机 `execution_order`，队列项仍固定先创建 baseline、后创建 graph，因此实际执行顺序尚未随机化；正式实验前必须修正。它不是长期支持的两种产品模式。

## 入口与数据流

HTTP 入口位于 `api/experiment.py`，应用入口是 `ExperimentComparisonService.create(project_id, user_id, request)`。创建后，两个运行进入同一 Agent 持久化队列；前端通过比较 SSE 查看盲态结果，提交 `BlindReviewRequest` 后才能揭盲。

```text
ComparisonRequest
  → ExperimentComparisonService
  ├─ GraphAugmentedExperimentStrategy → 有图上下文 + 依赖邻居工具
  └─ BaselineExperimentStrategy       → 无图上下文 + 无依赖图工具
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
| `graph_strategy.py` | 构造正式的依赖图增强运行。 |
| `graph_context.py` | 将有界依赖图事实加入模型上下文。 |
| `context.py` | 构建两组共享的中性 Manifest 与无中心性排序仓库地图。 |
| `metrics.py` | 汇总时延、步骤、工具调用、证据和估算字符/Token 指标。 |
| `baseline/` | 带明显标记的临时无图对照组。 |

具体实验规则和删除清单见 `../../../docs/EXPERIMENT_PROTOCOL.md`。若实验确认有图方案更优，必须按清单移除整个无图对照分支，普通 `/api/agent` 路径不受影响。
