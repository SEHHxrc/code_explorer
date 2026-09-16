# 确定性报告模块

| 文件 | 入口 | 作用 |
| --- | --- | --- |
| `overview_report.py` | `render_deterministic_overview(manifest)` | 仅根据 `ProjectManifest` 生成中文 Markdown 概览，不调用模型。 |

该报告是未配置模型、用户关闭模型或模型不可用时的稳定回退，也可作为大模型结果的基准。上游入口是 `services.project_overview.generate_project_overview()`。
