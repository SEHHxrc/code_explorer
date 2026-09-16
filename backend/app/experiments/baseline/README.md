# 临时无依赖图对照组

> **临时对照组：依赖图实验结束且有图方案获胜后删除。**

本目录中的代码仅用于生成“不向模型提供依赖图”的实验对照结果，不是受支持的产品模式，也绝不能被普通 Agent API 导入。

| 文件 | 作用 |
| --- | --- |
| `context_builder.py` | 从分析产物构建剔除依赖图事实的上下文。 |
| `strategy.py` | `prepare_baseline_artifact()` 清除图数据；`BaselineExperimentStrategy` 装配对照运行。 |
| `tool_registry.py` | `BaselineManifestTool` 返回不含图摘要的 Manifest，并创建没有依赖邻居工具的注册表。 |
| `__init__.py` | 导出对照策略。 |

若有图方案胜出，应同时删除本目录、`experiments/service.py` 的对照创建分支、相应测试与比较界面。历史数据是否保留应通过显式数据库迁移决定；完整清单见 `../../../../docs/EXPERIMENT_PROTOCOL.md`。
