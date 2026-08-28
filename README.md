# Code Explorer

Code Explorer 是一个面向源代码仓库的本地分析平台。它从上传文件、本地目录或 Git 仓库构建文件树、符号与依赖图，生成可供人和大模型共同使用的项目清单（manifest）与压缩仓库地图（repo map），并通过受策略约束的智能体执行只读分析工具。

## 当前能力

- Python、JavaScript/TypeScript、Go、Java、C/C++、Rust 的静态结构分析。
- 文件、符号、调用、继承、重写和导入依赖图。
- 项目入口点、框架、语言占比和关键文件识别。
- 在线 OpenAI 兼容接口及离线 Ollama 模型接入。
- 带持久化队列、租约恢复、工具调用、事件流和跨进程取消能力的分析智能体。
- 队列驱动的隔离 Docker 命令与安全扫描任务。
- Vue 依赖图、项目概览和智能体工作台。

完整的模块边界、数据流、类与关键函数说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
其他功能域的候选拆分与合并顺序见 [docs/MODULE_REFACTORING.md](docs/MODULE_REFACTORING.md)。

## 启动

后端：

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn backend.app.main:app --reload
```

前端：

```powershell
cd frontend
npm install
npm run dev
```

默认前端通过 `/api` 访问 FastAPI。数据库使用项目根目录的 `database.sqlite`，分析产物写入 `backend/storage/artifacts/<project_id>.json`。 FastAPI 默认嵌入一个 Agent Worker；独立进程部署方式见 [docs/AGENT_QUEUE.md](docs/AGENT_QUEUE.md)。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s test -v
cd frontend
npm run build
```

## 安全边界

智能体的分析工具仍保持只读。Docker、命令和安全扫描位于独立 execution 功能域：FastAPI 只写入持久化队列，单独的 Worker 才能访问 Docker。执行功能默认禁用，启用前必须配置精确镜像白名单；容器默认断网、非 root、只读挂载并受到 CPU、内存、PID、超时、输出和用户队列配额限制。运行方式与剩余风险见 docs/EXECUTION.md。
