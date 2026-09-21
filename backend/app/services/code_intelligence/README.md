# 代码智能派生模块

本模块把依赖分析结果转换成适合人和大模型消费的高密度、确定性上下文。

| 文件/入口 | 输入 | 输出 |
| --- | --- | --- |
| `manifest_builder.py` / `ProjectManifestBuilder.build(project_root, dependency_graph)` | 已清洗项目目录与原始依赖图 | `ProjectManifest`：语言、框架、包管理器、入口点、带来源命令事实、运行建议和图摘要。 |
| `repo_map_builder.py` / `build_repo_map(project_root, dependency_graph, manifest, ...)` | 项目目录、图与 Manifest | 有字符预算的仓库地图文本，包含重要文件和符号证据。 |

`ProjectAnalysisService` 在依赖图生成后调用本模块，并把结果存入分析产物。`ProjectContextBuilder`、项目概览和 A/B 实验随后复用同一份 Manifest/Repo Map，从而减少模型重复浏览、Token 消耗和无证据推断。

命令事实使用 `CommandFact` 区分 `observed`、`inferred`、`generated`、`documented`。Dockerfile、Compose、Procfile、systemd 和 package scripts 中的命令属于观察结果；框架启动命令和容器构建命令属于系统建议。命令存在本身不是安全结论，`generated` 内容不得进入静态安全证据包。

本模块只做确定性派生，不调用大模型、不读取 API Key，也不执行项目代码。
