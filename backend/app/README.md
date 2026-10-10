# 后端应用模块

`backend/app` 是 FastAPI 后端应用根包，负责项目导入与静态安全分析、依赖图展示、项目智能体、静态安全证据对照实验，以及隔离执行任务。

## 入口与调用关系

应用入口是 `main.py` 中的 `app: FastAPI`。启动时先初始化数据库并清理过期工作区；生命周期内可按 `AGENT_WORKER_EMBEDDED` 启动内嵌智能体 Worker。五组路由分别来自 `api/project.py`、`api/agent.py`、`api/experiment.py`、`api/execution.py` 和 `api/model.py`。

```text
FastAPI main.py
├─ api/                 HTTP、鉴权依赖、SSE
├─ services/            项目存储/导入、分析编排、公共程序图、静态安全证据、报告
├─ llm/                 在线或本地 OpenAI 兼容模型适配
├─ agents/              上下文、模型循环、只读工具、持久化队列
├─ experiments/         静态安全证据增强/仅原始文件的盲态配对实验
├─ execution/           Docker 隔离执行与安全扫描队列
├─ middleware/          异常脱敏、响应安全头和大小限制
├─ schemas/             项目分析与依赖图的公开 DTO
└─ models.py            SQLite 表、连接与初始化
```

## 顶层文件

| 文件 | 作用 |
| --- | --- |
| `main.py` | 创建 FastAPI 应用、安装中间件、注册路由并管理内嵌 Agent Worker。 |
| `models.py` | 定义 SQLite 持久化结构及数据库初始化/连接方法。 |

模块细节见各子目录中的 `README.md`。
