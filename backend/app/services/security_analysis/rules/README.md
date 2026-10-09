# 安全规则包

规则包只声明安全 API、入口点和调用边界值角色，不遍历文件、不解析 AST，也不计算调用路径。

- `base.py`：`RulePack`、`CallRule`、`AccessRule`、`EntrypointRule` 和 `CallCondition`；
- `python/standard_library.py`：Python 核心与常见第三方 API；
- `python/frameworks/fastapi.py`：FastAPI/APIRouter 入口和参数 Source；
- `java/standard_library.py`：Java 环境/HTTP/文件 Source，以及进程、SQL、文件、反序列化、网络和弱随机数 Sink；
- `java/frameworks/spring.py`：Spring Web 映射注解入口；
- `java/frameworks/servlet.py`：受 `HttpServlet` 继承约束的 `doGet/doPost/...` 入口；
- `go/standard_library.py`：Go 标准库首批高置信 Source、Sink 与 Guard；
- `go/frameworks/net_http.py`：`http.HandleFunc/Handle(path, handler)` 注册式入口；
- `c_family/standard_library.py`：C/C++ 标准库、POSIX 与 SQLite 的共用规则；
- `javascript.py`：JS/TS 共用的 DOM、Node.js 和 Express 声明式规则；
- `python_rules.py`：旧 `PYTHON_CALL_RULES` 的兼容导出，新代码不应依赖它。

每个规则包拥有独立名称和版本，例如 `python-core/1.0`、`java-core/1.0`、
`go-core/1.1`、`go-net-http/1.0`。`RulePackRegistry` 按语言选择规则，并拒绝相同标识被静默覆盖。
C/C++ 共用 `c-family-core/1.0`、`c-family-sqlite/1.0`；`CallRule.required_headers` 约束可见
头文件，`output_argument_positions` 明确读取数据写入哪个缓冲区。公共消费者仅支持独立调用
的具名输出缓冲区，不等于通用副作用分析。`implicit_shell` 区分 system/popen 的隐式 Shell，`preconditions` 保存规则特有的
利用前提。字符串比较只记录 Guard，不能当作已验证 Sanitizer 截断传播。

`CallRule.result_positions/result_count` 声明多返回 API 的内容分量，不把错误/状态当数据。
Go 1.1 区分 ReadFile/LookupEnv 内容和状态；Context 系列规则明确跳过首位 context，
SQL 参数绑定角色也相应顺移。规则不解析调用图或执行污点传播。

CWE 映射只表示规则类别，不证明项目已存在该 CWE。后续语言规则应尽量使用完整限定名称、接收者类型、参数角色和静态条件，避免仅依赖宽泛方法名后缀。

`javascript-typescript-core` 区分 `exec/execSync` 的隐式 Shell 与默认不经过 Shell 的 `execFile/spawn`，显式 `shell:true` 才将普通参数列为 Shell Sink；动态 options 诊断。DOM 写入限于观察到的接收者；React 匹配原生 DOM 的 `dangerouslySetInnerHTML.__html`/`createElement`，Vue 匹配 `v-html`/`h` 原始 HTML。普通 JSX children 与模板插值不是该 Sink。Node 增加 HTTP handler、CLI 和 `fs/promises`；props、配置与存储保留可控性前提。DOMPurify 未经上下文验证不自动截断。完整覆盖边界见 `docs/SECURITY_COVERAGE.md`，没有全量 SQL/npm 框架保证。

`javascript-typescript-core/1.2` 增加原生 React 表单 value、已确认 Node 请求体 data 首形参、
有限 Vue 文本 v-model 三类输入规则。回调注册、脚本/模板身份及可能写入由共享语法层和公共
ProgramGraph 实现，不在规则包复制传播算法。Vue 事件保留 binding_certainty=may；
识别这些 Source 不证明事件发生、攻击者可控、跨回调路径或真实漏洞。
