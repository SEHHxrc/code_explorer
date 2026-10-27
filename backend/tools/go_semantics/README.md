# Go 类型与原生 SSA 工作进程

此目录是后端可信分析工具，不是上传项目的依赖。固定使用官方
`golang.org/x/tools v0.52.0` 的 `go/packages`、`go/ssa` 和 `ssautil`；
`go.mod/go.sum` 固定传递依赖，不调用目标项目的脚本、插件、测试或服务。

`references.go` 使用本次 go/types 的变量符号实体登记声明并解析 Uses；收集完成后统一链接，
支持前向/跨文件引用，不为此额外遍历 AST。普通具名变量与形参使用公共声明身份，含写引用，
不表示运行时对象、SSA 版本或污点传播边。外部或未登记实体保持未知；字段/完整堆与接口调用
仍不是本批变量绑定能力。变量声明/引用分别通过可选的 9 列/10 列共享表传输，Python 与 TS
共用同一解码和投影；旧表格式继续读取，64 MiB 输出保护不变。

## 准备与入口

需要 Go 1.26 或更高版本；在项目根目录执行一次：

Windows 推荐先建立类似 `.venv` 的项目级缓存（依然使用已安装的 Go SDK，不复制解释器）：

```powershell
./backend/tools/go_semantics/prepare.ps1 -ProjectRoot '你的 Go 测试项目目录'
```

需要 PowerShell 7。脚本把模块源码、下载缓存、构建缓存和临时文件放入工作区 `.goenv/`，
不使用 `go env -w` 修改全局配置。仅在 go.mod/go.sum 清单的暂存副本执行 `go mod download all`，
保留目录布局以支持项目内 replace，拒绝越界的本地替换；不运行项目代码、测试、generate 或安装脚本。
默认使用 HTTPS 模块镜像并启用 sum.golang.org 校验；可用 -Proxy 指定其他 HTTPS Go 模块代理，
不以关闭校验、TLS 或直连 VCS 来处理下载错误。失败可重跑复用已校验缓存，暂存清单留作诊断。
所有模块成功后才创建 ready 标记，分析器自动优先使用这套缓存，不需要改实际 backend/.env。
显式配置的缓存路径优先级最高；分析代码仓库本身时不会向其内部 .goenv 写缓存。
正常静态分析继续禁用联网，不会因为安装步骤曾联网就自动下载新项目的依赖。

下面的基础命令使用当前 shell 的 Go 缓存，适合已有受控环境时准备可信工具：

```powershell
go -C backend/tools/go_semantics mod download
go -C backend/tools/go_semantics build -o ../../storage/runtime/tools/go-semantics.exe .
```

Linux 的输出文件名去掉 `.exe`。仅编译此可信工具，不在待分析项目执行 `go get`、
`go mod tidy`、`go build` 或安装命令。工具缺失、被系统应用控制拒绝、超时分别记录明确
错误码并回退；不建议关闭系统安全策略。二进制位于忽略的 runtime 目录，迁移机器后需重建。

### WSL Linux 环境

已在 Ubuntu WSL 2 的 Snap Go `1.27.2 linux/amd64` 验证编译、单元测试和 `go vet`。
可复用 Windows 准备好的模块下载缓存，但大项目应将依赖展开到 Linux 文件系统，避免
`/mnt/d` 上大量跨文件系统的小文件操作。本机已离线复用已校验的 840 份模块归档，
缓存位于 `~/.cache/code-explorer/go/{modules,build,tmp}`；没有重新联网安装目标依赖。
下面命令要求模块缓存已准备完成；缺依赖时明确失败，不临时联网下载：

```bash
cd /mnt/d/project_py/code_explorer
export PATH="/snap/bin:$PATH"
export GOENV=off GOTOOLCHAIN=local GOWORK=off GOPACKAGESDRIVER=off
export GOPROXY=off GOSUMDB=off GOFLAGS=-mod=readonly CGO_ENABLED=0
export GOAUTH=off GOVCS='*:off' GOCACHEPROG=
export GOMODCACHE="$HOME/.cache/code-explorer/go/modules"
export GOCACHE="$HOME/.cache/code-explorer/go/build" GOTMPDIR="$HOME/.cache/code-explorer/go/tmp"
mkdir -p "$GOCACHE" "$GOTMPDIR" backend/storage/runtime/tools
go -C backend/tools/go_semantics build -o ../../storage/runtime/tools/go-semantics .
go -C backend/tools/go_semantics test -v ./...
go -C backend/tools/go_semantics vet ./...
```

这些配置只作用于当前终端，不修改 Windows 的 `.env` 或全局 Go 配置。
Linux 与 Windows 分别使用 `go-semantics` 和 `go-semantics.exe`，互不覆盖。
**Windows 后端不会因为安装了 WSL Go 就自动使用 Linux 工具**。本机另建
`.goenv/python-linux`，安装了 `requirements-dev.txt`；原 Windows `.venv` 保留不动。
Ubuntu 缺少 ensurepip 时，可创建 `python3 -m venv --without-pip .goenv/python-linux`，
再用 [PyPA 官方引导程序](https://pip.pypa.io/en/stable/installation/) 仅为该虚拟环境安装 pip。
本机 Linux Node 24.18.0 在私有运行时目录，已核对官方 SHA256，复用现有锁定的 TypeScript JS 文件。
系统 `java`/`javac` 25.0.4.1 也已确认可用；当前 Java 分析仍走已有前端和公共 ProgramGraph，
安装 JDK 不等于已接入 JavaCompiler/JDT 的原生语义，后续适配应复用同一项目级解析协议。

在 WSL 中显式运行统一开发入口，不需要每次手写配置：

```bash
bash backend/tools/wsl.sh python -m unittest test.test_go_semantics test.test_semantic_wire -v
bash backend/tools/wsl.sh python -c 'from backend.app.services.syntax_analysis.parser_runtime import prepare_parsers; print(prepare_parsers())'
```

`wsl.sh` 只选择 Linux 解释器、Node/Go PATH 和私有缓存，不自动安装、启动服务或转发到 Windows。
`CODE_EXPLORER_GO_MODULE_CACHE`、`CODE_EXPLORER_GO_BUILD_CACHE`、
`CODE_EXPLORER_PARSER_CACHE_DIR` 的已有显式值优先；默认以用户 XDG 缓存目录为根。
解析器按版本/平台隔离，准备入口显式联网，正常分析只使用本地资源。不要把需要私有目录的
解析器缓存指向显示为 777 的 Windows 挂载目录，也不要关闭库的权限检查。
安装目录和缓存均不纳入项目源码扫描。历史数据库中的 Windows 绝对路径不会被此脚本改写；
本轮仅运行隔离测试和只读分析，不自动接管旧数据库或启动会执行清理的应用入口。

WSL 内已验证提供的 chaos-mesh 全部 697 个 Go 文件范围：展开输出 80,145,616 字节，
紧凑输出 23,506,349 字节，减少 70.67%，默认 64 MiB 保护不变。调用、类型、SSA 完整事实
逐条指纹一致，源码哈希不变；这是同版本单次体积核对，不是性能基准或完整污点覆盖证明。
测试文件、需要 CGO 的依赖、实际类型错误和未映射匿名函数仍明确记录，不会为了成功清空诊断。

Python 入口：`language_backends.GoSemanticResolver.resolve(project_root,
file_paths=[相对路径...], max_file_bytes=单文件字节上限)`。默认由统一依赖分析器调用，
普通 Go 和 JS/TS 混合项目分别运行后端，共享声明映射和传输基础设施。

## 文件与数据

| 文件 | 功能 |
| --- | --- |
| `main.go` | 读取明确文件范围，按最近 go.mod 分组、加载包、提取声明/类型/调用和诊断。 |
| `ssa.go` | 导出函数内值身份、基本块 CFG、指令 use-def、φ、接收者及有序调用实参。 |
| `wire.go` | 与 TS 共用的紧凑调用/类型表，追加 SSA 位置、值、指令行；仅去重复表达，不截断事实。 |
| `main_test.go` | 离线正反例；只分析临时源码，不执行夹具。 |
| `go.mod/go.sum` | 可信工具依赖的固定版本和完整性。 |
| `prepare.ps1` | 一次性在线依赖准备；清单副本下载、隔离缓存、校验和环境恢复。 |

stdin/stdout 是 1.0 JSON 语义协议，唯一线格式为 `tables-v1`，Python 的公共 `language_backends.wire.decode_table_result`
恢复为 `ProjectSemanticResult`，Go/TS 不维护两套解码器。升级后须重建本机二进制，
不保留展开 JSON 的自动兼容回退。类型文本格式化只在本次会话内缓存，不跨项目复用。
共享文件/声明/类型表与 TS 一致；Go 可选 SSA 扩展使用共享位置表和以下定长行：

- 位置：`[file_id,line,column,end_line,end_column]`；
- 函数：`[name,kind,location_id,parameters,receiver,values,instructions,blocks,limitations]`；
- 值：`[kind,name,type_id,location_id_or_null]`，以原局部值 ID 为键；
- 指令：`[opcode,block,operands,result,location_id,call_location_id,source_name,call_arguments,receiver,invocation_kind,projection_index,operator]`，以原指令 ID 为键；
- 块：`[predecessors,successors,instruction_ids]`。

外键零值有效、null 才表示缺失；拒绝布尔值/负索引/越界和错误行长，解码后仍校验 CFG、φ 和 use-def 外键。
位置为一基 UTF-8 字节列、结束位置开区间；没有源码位置的合成 SSA 指令保持缺省，
不能伪造为函数首行。`DebugRef` 保留源变量/表达式与 SSA 值的对应。
调用实参不包含隐式 receiver，receiver 单独保存；函数参数也单独区分接收者。
φ 的操作数与前驱顺序一致；Extract/Field 保存投影序号，不把多返回值混成一个值。
Python 机械检查块前后继、指令归属、参数、结果和操作数引用。

声明和类型进入现有语义索引；原生 SSA 按既有函数符号存入 `native_ssa`，不重复生成调用图、
不发送完整 SSA 给模型。目前 SSA 是可持久化、可核对的基础产物，尚未取代安全分析的公共
ProgramGraph CFG/DFG。不能将本工具接通解释成完整 Go 污点分析已经由 SSA 驱动。

## 缓存与构建配置

Python 的 `GoAnalysisConfig` 先用明确变量清单运行只读 `go env -json`，取得宿主实际生效的
GOPATH/GOMODCACHE/GOCACHE，因此能复用用户 Go 环境文件中的缓存配置。只读探测不执行目标
包；GOFLAGS、外部驱动、认证命令及工具链下载仍受显式进程变量约束。正式分析将 GOENV 关闭，
只传入验证过的缓存路径与白名单构建条件，不继承任意 Go 配置。
缓存选择顺序为显式配置 → 已准备且位于分析目标之外的工作区 .goenv → 宿主有效缓存。
project_local_environment 来源会随 analysis_configuration 记录；.goenv 已加入 Git、扫描、
搜索及导入清洗忽略范围，不将第三方缓存冒充待分析项目源码。

可在服务进程环境或加载的 backend/.env 中配置以下变量（示例已更新，不改实际 .env）：

| 变量 | 作用与默认值 |
| --- | --- |
| `CODE_EXPLORER_GO_TARGET_OS` | 空值为宿主平台；可显式选 linux 等目标，不自动猜测。 |
| `CODE_EXPLORER_GO_TARGET_ARCH` | 空值为宿主架构；例如 amd64、arm64。 |
| `CODE_EXPLORER_GO_BUILD_TAGS` | 逗号/空格分隔的显式标签，规范化后送入 packages.Load。 |
| `CODE_EXPLORER_GO_MODULE_CACHE` | 空值复用 go env 的有效模块缓存；也可指定项目外绝对路径。 |
| `CODE_EXPLORER_GO_BUILD_CACHE` | 空值复用 go env 的有效构建缓存；也可指定项目外绝对路径。 |

也可调用 `GoSemanticResolver(configuration=GoAnalysisConfig(...))` 指定任务条件。
目标平台、标签、缓存来源、缓存路径摘要及离线策略随 analysis_configuration 记录，即使工具
启动被拒绝也保留这些条件；不把宿主绝对缓存路径或完整环境写入证据。Go 的构建上下文诊断
区分目标 GOOS/GOARCH 和工作进程宿主平台，不能在按 Linux 分析时仍标为 Windows 目标。
缓存不能位于待分析项目内；不接受任意 GOFLAGS，也不因复用缓存自动补依赖。
go.work 和 CGO 仍明确禁用，本次没有以继承全部配置来绕过这两个安全边界。

## Windows 应用控制

本机诊断已观察到 Code Integrity 3077/3033 事件，指向此未签名工具，且 Smart App Control
处于开启状态。它与 Go 是否安装是两回事。不同构建可能被重新评估，某次成功启动不等于
之后重编译都能运行；没有改名、go run 回退、关闭安全策略或创建自签根来绕过限制。

稳定部署需要系统接受的可信签名发布产物；当前没有可用签名凭据，不能由代码自动完成。
Microsoft 说明 Smart App Control 没有单应用放行入口，开发者应提供有效签名：
[官方说明](https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions)。
保留错误码 semantic_worker_execution_denied，并在工具不可用时明确回退。

## 加载与安全边界

- `LoadSyntax` 仅为明确选择的包加载源码，不用 `LoadAllSyntax` 把第三方依赖全部生成 SSA。
- 源码解析执行相对路径、真实路径和单文件预算检查；包配置/导入信息由 Go 工具读取，
  依赖可以使用已存在的编译器导出缓存。`go list` 可能生成导出缓存，这不等于执行目标程序。
- 关闭外部 `GOPACKAGESDRIVER`、`go.work`、用户 Go 环境文件、CGO、自动工具链下载和在线模块查找；
  固定 `-mod=readonly`，不改目标 go.mod/go.sum，不自动补依赖。
- 默认使用当前主机 GOOS/GOARCH，允许显式选择目标平台；没有假装选择 Linux 就覆盖其全部系统依赖。
  Tests=false，因此 `_test.go`、构建标签排除、缺依赖等文件可能没有原生结果，
  但原 Tree-sitter/安全扫描范围不因此被删除，回退与未加载计数保留。
- 类型错误的包不提供高置信编译器绑定或伪造 SSA。接口 invoke、函数值动态调用、外部目标
  仍可能回退，声明绑定统一为 inferred/may，不是动态唯一目标证明。
- 原生 SSA 的 Store、Load、FieldAddr 等操作数关系不是完整内存 SSA/别名分析；
  goroutine/defer 仅保留指令，不分析并发调度、happens-before、反射或完整路径可行性。
- 路径解析缓存仅属于本次任务，并加锁供包加载的并行 ParseFile 使用；不跨快照缓存 AST。
- 默认工作进程超时 150 秒、紧凑协议输出 64 MiB；包加载每模块 100 秒。超限仍明确失败，
  不删除函数、采样指令或静默截断；本次没有引入无限输出或以增大上限代替去重。
  这不是操作系统级沙箱、硬 RSS/磁盘限制，生产部署仍需 Worker 隔离与资源治理。

验证：`go -C backend/tools/go_semantics test ./...`，以及
`python -X utf8 -m unittest test.test_go_semantics -v`。测试报告若跳过系统拒绝的原生工具，
不能宣称原生集成已经验证通过。

匿名函数现在使用与公共 Go 语法前端一致的 `anonymous@行:列` 身份，并按原生 AnonFuncs 递归导出。
局部变量/形参归属该匿名函数，而不是外层函数；捕获变量与闭包的完整内存传播仍未覆盖。
更新工具源码后需离线重新构建可信工具，旧二进制不会自动获得新的匿名映射；接口及表行格式不变。
原生事实的消费与逐函数回退见 `program_graph/README.md` 的 1.15 小节。
