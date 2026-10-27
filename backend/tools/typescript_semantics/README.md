# JS/TS 编译器语义工作进程

本目录是后端分析工具，不是前端页面或待分析项目的依赖。使用官方 TypeScript 6.0.3
Compiler API 提取声明和类型，Python 服务校验结果并映射到现有符号。不生成 CFG/DFG、
不定义 Source/Sink、不调用模型，也不运行被分析项目。

## 安装与入口

在项目根目录执行：

```powershell
npm --prefix backend/tools/typescript_semantics ci --ignore-scripts --no-audit --no-fund
```

服务器同样需要 Node 和此工具依赖；无需为每个上传项目安装 npm 依赖。工具未安装时仍能
保守分析，但 backend_runs 和调用点会标记回退，不能宣称编译器成功。当前无需修改 .env。

Python 入口为 `services.language_backends.TypeScriptSemanticResolver.resolve()`，
默认由 UnifiedCodeAnalyzer 在调用边生成前调用一次。stdin/stdout 使用唯一 tables-v1
传输格式：共享声明表与类型文本表，Python 解引用为公共对象，不重复传输长声明。
默认超时 90 秒、V8 堆上限 1536 MiB、协议读取上限 64 MiB，可通过构造参数调整；不是整个
分析任务时限或进程 RSS 限制。协议、适配和依赖版本一起锁定。

## 文件与依赖

| 文件 | 职责 |
| --- | --- |
| package.json / package-lock.json | 固定工具依赖和完整性，不加载目标仓库里的 TypeScript 包。 |
| project.mjs / createSession | 受控读取、配置和 Program 会话，任务级缓存读取及不存在路径。 |
| worker.mjs | 查询 TypeChecker 的声明/类型，转换 UTF-8 范围，输出共享表和诊断。 |
| references.mjs | 消费 TypeChecker 的变量符号与声明；保留导入别名、遮蔽、解构和简写属性绑定，不推断运行时对象或新调用目标。 |

## 性能与缓存边界

工作进程对规范路径、目录元数据、最近配置目录和文件读取做任务级缓存，包括不存在路径；
规范路径使用操作系统 realpath 入口，仍检查解析后的真实路径是否在许可根内。
每个 AST 节点的声明和 UTF-8 位置只计算一次，重复调用共享声明 ID，不删除调用点本身。
缓存以节点或路径为键，不把 TypeChecker 的上下文相关类型文本粗略按类型对象跨位置合并。
不同 Program 不强行共享可变语法树，避免配置/符号绑定互相污染。

性能分阶段耗时和命中数经公共 timings_ms/cache_stats 记录到 backend_runs，不影响扫描预算
或安全语义。缓存随工作进程退出销毁，不跨项目/请求复用。改写源码后重新分析会读取新内容。

## 能力及安全边界

- 支持明确清单中的 JS/JSX/TS/TSX 等普通脚本；Vue 仍由现有源单元前端分析，并标记编译器回退。
- 最近的 tsconfig/jsconfig 提供路径和类型选项，不自动构建项目 references。
- 未显式设置 moduleDetection 时按独立模块处理，避免无关脚本/测试夹具的全局同名声明串联；传统共享全局脚本可能回退，不宣称全覆盖。
- 源码只读取清单；配置和声明依赖限于项目根内；工具自带 TypeScript lib 为唯一可信根外例外。
- 不下载缺失依赖、不加载仓库插件；移除 NODE_OPTIONS/NODE_PATH，不 emit 或执行构建/安装脚本。
- 此只读 Host 不是操作系统级沙箱，工具仍应服从 Worker 资源和主机隔离策略。
- 不做完整项目类型错误检查，只收集配置、语法及绑定缺口；诊断总计和有界样本分别记录。
- 声明/重载签名不等于 points-to 或污点分析，目标一律 may。显式 const 条件别名有界保留各声明。
- 坐标由 UTF-16 转成一基 UTF-8 字节列，结束位置为开区间，与 Tree-sitter 调用身份一致。
- variable_reference_binding 是可选能力；变量声明/引用分别通过 9 列/10 列共享表传输，声明
  位置沿用当前声明类型范围。any/unknown 仍有声明；项目范围外变量明确未解析。引用包含写入，
  不等于读取或 DFG；函数值、字段/动态属性不在本批普通变量绑定范围，绑定不证明运行时目标。

离线验证：`python -X utf8 -m unittest test.test_typescript_semantics -v`。
