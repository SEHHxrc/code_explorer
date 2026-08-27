# 模块重构与实施状态

本文记录当前功能边界、已完成的重构和后续扩展原则。`test/` 保留历史测试材料与当前回归测试；正常功能重构不整理其中的历史文件，只在验证对应功能时按需使用。

## 优先级概览

| 状态 | 功能域 | 当前结果 |
| --- | --- | --- |
| 已完成 P0 | 前端依赖图 | 图模型、布局、Sigma、控件和检查器已分离并异步加载 |
| 已完成 P0 | 项目分析流程 | API 收薄，应用服务、图 DTO、仓储和响应安全边界已建立 |
| 已完成 P1 | 受控项目工作区 | 暂存导入、安全策略、补偿事务、Journal 和 Janitor 已建立 |
| 已完成 P1 | 项目生命周期 | 活动任务保护、文件/Artifact/数据库顺序删除已建立 |
| 已完成 P1 | ProjectInsight | 页面、组件、composable、API 和纯领域函数已分离 |
| 已完成 P1 | 智能体工具 | 按发现/源码/图拆分，并加入共享证据索引和搜索预算 |
| P2 | Manifest 检测器 | 框架和入口规则明显增长后再按检测器拆分 |
| P2 | 隔离执行域 | Docker、命令、安全扫描必须通过任务队列和独立工作节点接入 |

## 1. 前端依赖图

```text
frontend/src/features/dependency-graph/
├── DependencyGraph.vue
├── components/
│   ├── GraphControls.vue
│   └── GraphInspector.vue
├── composables/
│   ├── useGraphModel.js
│   ├── useGraphLayout.js
│   └── useSigmaRenderer.js
├── domain/
│   └── graphModel.js
└── graphStyle.js
```

旧组件保留为 props/emits 薄包装。后端 Exchange DTO 固定输出 `edges`，前端不再兼容 `links`。节点大小、过滤、布局和渲染边界互相独立。

## 2. 项目分析应用服务

```text
backend/app/services/project_analysis/
├── service.py
├── transaction.py
├── contracts.py
├── repository.py
├── artifact_repository.py
├── graph_exchange.py
└── exceptions.py

backend/app/schemas/
├── dependency_graph.py
└── project_analysis.py
```

`ProjectAnalysisService` 负责用例编排，API 负责 HTTP 输入、身份和响应映射。原始依赖图保存在 Artifact 中供 Manifest、Repo Map 和智能体使用；前端仅获得经过白名单、路径脱敏和端点校验的版本化图 DTO。

跨文件系统、Artifact 和数据库不能使用单一 ACID 事务，因此使用逆序补偿：

```text
暂存获取 → 安全清洗 → 静态分析 → 原子发布 → 原子 Artifact → 数据库提交
```

数据库最后提交。提交前任一步骤失败都会删除本次创建的资源；数据库已提交后，即使 Journal 清理失败也不得回滚有效项目。

## 3. 受控项目工作区与崩溃恢复

```text
backend/app/services/project_workspace/
├── contracts.py
├── policy.py
├── paths.py
├── filesystem.py
├── sanitizer.py
├── journal.py
├── janitor.py
├── service.py
└── sources/
    ├── git.py
    └── zip.py
```

工作区布局：

```text
backend/storage/users/<user_id>/
├── .staging/<operation_id>/
│   ├── operation.json
│   ├── upload.part
│   └── workspace/
└── projects/<project_id>/
```

核心约束：

- Git 与 ZIP 只进入暂存区，清洗和分析成功后才原子发布。
- Git URL 拒绝凭据、非 HTTP(S)、私网 DNS 结果，并支持 `GIT_ALLOWED_HOSTS` 白名单。
- ZIP 按归档大小、文件数、压缩比和实际写入字节限制，拒绝穿越、链接和特殊路径。
- Sanitizer 采用失败关闭，统一删除敏感文件、危险二进制、链接、噪音目录和超限文件。
- 所有删除路径都由受控 ID 重新计算，不使用数据库或 Journal 中的绝对路径。
- Journal 只保存 ID 和阶段；启动 Janitor 清理超时的未提交操作。

旧 `loader.py`、Middleware 目录下的 `sanitizer.py` 和 `project_cleaner.py` 已删除。

## 4. 项目生命周期

```text
backend/app/services/project_lifecycle/
├── contracts.py
└── service.py
```

删除项目时先校验所有权和活动智能体运行。存在 `queued/running` 任务时返回 409。文件工作区与 Artifact 删除成功后，最后在一个数据库事务中删除 AgentEvent、AgentRun 和 Project。任何失败都返回真实错误，不再出现数据库已删除却报告项目不存在的情况。

## 5. ProjectInsight

```text
frontend/src/features/project-insight/
├── ProjectInsight.vue
├── components/
│   ├── ProjectImportPanel.vue
│   ├── ProjectFileTree.vue
│   ├── SymbolOutline.vue
│   └── ProjectOverviewPanel.vue
├── composables/
│   └── useProjectAnalysis.js
└── domain/
    └── symbolTree.js

frontend/src/services/
├── httpClient.js
├── projectApi.js
└── agentApi.js
```

功能页容器约 110 行，旧 `components/ProjectInsight.vue` 保留为 7 行兼容入口。请求状态集中为 `idle/importing/ready/generating_overview/deleting/error`；成功响应一次性替换项目状态，删除失败保留当前视图。所有 HTTP 地址和错误提取集中在服务层，不引入 Pinia。

## 6. 智能体只读工具

```text
backend/app/agents/tools/
├── base.py
├── arguments.py
├── discovery.py
├── source.py
├── graph.py
├── evidence_index.py
└── registry.py
```

工具名称和严格 JSON Schema 保持兼容。`ProjectEvidenceIndex` 每次运行只构建一次符号列表和入/出邻接表；依赖邻居查询不再扫描整张图。文本搜索增加文件数、总字节和耗时预算。注册表拒绝重复工具名，未知内部错误不再原样写入前端事件。

Docker、Shell、安全扫描或部署能力不能加入这一只读工具目录。下一阶段应创建独立 `execution` 功能域，通过持久化任务队列连接隔离工作节点。

## 后续建议

1. P2 优先建设执行任务协议、队列、Docker Worker、资源配额和审计事件。
2. 将进程内 AgentRunManager 迁移到可恢复任务队列后，再支持多 Worker 和服务重启续跑。
3. Manifest 规则明显增长时拆分为 framework、entrypoint、package manager 和 command 检测器。
4. 为生产环境配置明确的 `GIT_ALLOWED_HOSTS`，并由网络层限制 Worker 对内网元数据地址的访问。