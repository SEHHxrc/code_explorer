# Code Explorer

Code Explorer 是一个面向代码安全分析的本地原型平台。从 ZIP 上传或 Git 仓库构建文件树、符号与依赖图，再通过共享程序图生成有界静态安全证据，交给受策略约束的智能体分析。完整依赖图用于展示和内部路径查询，不作为安全 A/B 实验的模型输入。

## 当前能力

- Python、JavaScript/TypeScript、Go、Java、C/C++、Rust 的静态结构分析。
- 文件、符号、调用、继承、重写和导入依赖图。
- 项目入口点、框架、语言占比和关键文件识别。
- 八种语言的公共 CFG/变量级值流，以及 Python、Java、Go、C/C++、JS/TS 的安全规则和有界跨过程传播；规则覆盖不等于完整语义覆盖。
- 静态安全证据增强与仅原始文件访问的盲态 A/B 实验，记录真实模型用量和回答完整性。
- 在线 OpenAI 兼容接口及离线 Ollama 模型接入。
- 带持久化队列、租约恢复、工具调用、事件流和跨进程取消能力的分析智能体。
- 队列驱动的隔离 Docker 命令与安全扫描任务。
- Vue 依赖图、项目概览和智能体工作台。
- 上传与静态分析进度、项目数据管理和 Agent 历史恢复。

完整的模块边界、数据流、类与关键函数说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
其他功能域的候选拆分与合并顺序见 [docs/MODULE_REFACTORING.md](docs/MODULE_REFACTORING.md)。

主要维护入口：

- [后端应用与功能域](backend/app/README.md)
- [大模型接入](backend/app/llm/README.md)
- [项目智能体与工具调用](backend/app/agents/README.md)
- [多语言依赖分析器](backend/app/services/dependency_analyzer/README.md)
- [前端源码与功能模块](frontend/src/README.md)

## 启动

后端：

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn backend.app.main:app --reload --env-file backend/.env
```

前端：

```powershell
cd frontend
npm install
npm run dev
```

默认前端通过 `/api` 访问 FastAPI。数据库使用项目根目录的 `database.sqlite`，分析产物写入 `backend/storage/artifacts/<project_id>.json`。 FastAPI 默认嵌入一个 Agent Worker；独立进程部署方式见 [docs/AGENT_QUEUE.md](docs/AGENT_QUEUE.md)。

后端命令应在项目根目录运行。实际 `.env`、数据库、导入源码和实验结果仅保存在本机，不提交 Git；迁移源码或会话并不自动恢复数据库里的项目与 Agent 历史。上述 `--reload` 仅是本系统开发启动示例，不是被分析项目的安全事实。

## 验证

先安装测试依赖；其中 HTTPX 仅用于本地 ASGI 请求测试，不是模型 SDK：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s test -v
cd frontend
npm run build
```

解析器固定缓存在 `backend/storage/runtime/tree-sitter/<版本>/<系统-架构>`，避免 IDE、服务进程和不同用户目录各用一份缓存。安装/迁移时执行下列准备命令，先复用当前版本本机缓存，再下载缺少的语法（含 Vue 模板的 HTML）：

```powershell
.\.venv\Scripts\python.exe -c "from backend.app.services.syntax_analysis.parser_runtime import prepare_parsers; print(prepare_parsers())"
```

离线机器需迁移与所装版本、操作系统匹配的上述缓存。可通过 `CODE_EXPLORER_PARSER_CACHE_DIR` 指定管理员管理的其他缓存根。项目分析期间不下载安装资源：缺少语法立即失败并回滚，不把空图保存为成功分析；环境不完整不能解释为项目没有安全风险，也不能作为有效实验样本。准备完成后重启后端。

## 安全边界

后端运行日志采用统一 JSON 格式，API 和两个独立 Worker 共用入口；默认控制台输出并写入 `backend/storage/logs/` 的角色/PID 轮转文件。配置、字段与脱敏边界见 `backend/app/core/README.md`，示例见 `backend/.env.example`。日志不包含源码、模型正文或认证内容，不与实验私有审计混用。模型单次 socket 超时通过 `CODE_EXPLORER_LLM_REQUEST_TIMEOUT_SECONDS` 配置，缺省 300 秒；不是整个 Agent 的时限。修改配置后须重启对应进程。

智能体的分析工具仍保持只读。Docker、命令和安全扫描位于独立 execution 功能域：FastAPI 只写入持久化队列，单独的 Worker 才能访问 Docker。执行功能默认禁用，启用前必须配置精确镜像白名单；容器默认断网、非 root、只读挂载并受到 CPU、内存、PID、超时、输出和用户队列配额限制。运行方式与剩余风险见 docs/EXECUTION.md。
