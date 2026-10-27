# 安全规则包

规则包只声明安全 API、入口点和调用边界值角色，不遍历文件、不解析 AST，也不计算调用路径。

- `base.py`：`RulePack`、`CallRule`、`AccessRule`、`EntrypointRule` 和 `CallCondition`；
- `python/standard_library.py`：Python 核心与常见第三方 API；
- `python/frameworks/fastapi.py`：FastAPI/APIRouter 入口和参数 Source；
- `java/standard_library.py`：Java 环境/HTTP/文件 Source，以及进程、SQL、文件、反序列化、网络和弱随机数 Sink；
- `java/frameworks/spring.py`：Spring Web 映射注解入口；
- `java/frameworks/servlet.py`：受 `HttpServlet` 继承约束的 `doGet/doPost/...` 入口；
- `go/standard_library.py`：Go 标准库首批高置信 Source、Sink 与 Guard；
- `go/frameworks/net_http.py`：默认/ServeMux 注册入口、请求字段、Query/Header/Form、正文读取及 Decode 输出；
- `go/frameworks/gin.py`：Gin 路由、请求读取、绑定输出和文件/跳转敏感参数；
- `go/network.py`：net 连接读取输出缓冲区及 Dial/DialContext 地址参数；
- `c_family/standard_library.py`：C/C++ 标准库、POSIX 与 SQLite 的共用规则；
- `javascript.py`：JS/TS 共用的 DOM、Node.js 和 Express 声明式规则；

每个规则包拥有独立名称和版本，例如 `python-core/1.0`、`java-core/1.0`、
`go-core/1.2`、`go-net-http/1.1`、`go-gin/1.0`、`go-net/1.0`。`RulePackRegistry` 按语言选择规则，并拒绝相同标识被静默覆盖。
C/C++ 共用 `c-family-core/1.0`、`c-family-sqlite/1.0`；`CallRule.required_headers` 约束可见
头文件，`output_argument_positions` 明确读取数据写入哪个缓冲区。公共消费者支持独立或确定最外层调用的
具名输出目标，不猜多个平级调用或嵌套输出顺序，不等于通用副作用分析。`implicit_shell` 区分 system/popen 的隐式 Shell，`preconditions` 保存规则特有的
利用前提。字符串比较只记录 Guard，不能当作已验证 Sanitizer 截断传播。

`CallRule.result_positions/result_count` 声明多返回 API 的内容分量，不把错误/状态当数据。
Go 1.1 区分 ReadFile/LookupEnv 内容和状态；Context 系列规则明确跳过首位 context，
SQL 参数绑定角色也相应顺移。规则不解析调用图或执行污点传播。

`receiver_api_types/argument_api_types` 约束前端观察的真实 API 角色，避免本地同名 Header、
出站 Request、任意 Decoder 冒充网络输入。绑定输出使用显式 `&具名变量`，不把 error 返回或整个 Context 污染。

CWE 映射只表示规则类别，不证明项目已存在该 CWE。后续语言规则应尽量使用完整限定名称、接收者类型、参数角色和静态条件，避免仅依赖宽泛方法名后缀。

`javascript-typescript-core` 区分 `exec/execSync` 的隐式 Shell 与默认不经过 Shell 的 `execFile/spawn`，显式 `shell:true` 才将普通参数列为 Shell Sink；动态 options 诊断。DOM 写入限于观察到的接收者；React 匹配原生 DOM 的 `dangerouslySetInnerHTML.__html`/`createElement`，Vue 匹配 `v-html`/`h` 原始 HTML。普通 JSX children 与模板插值不是该 Sink。Node 增加 HTTP handler、CLI 和 `fs/promises`；props、配置与存储保留可控性前提。DOMPurify 未经上下文验证不自动截断。完整覆盖边界见 `docs/SECURITY_COVERAGE.md`，没有全量 SQL/npm 框架保证。

历史版本 `javascript-typescript-core/1.2` 增加原生 React 表单 value、已确认 Node 请求体 data 首形参、
有限 Vue 文本 v-model 三类输入规则。回调注册、脚本/模板身份及可能写入由共享语法层和公共
ProgramGraph 实现，不在规则包复制传播算法。Vue 事件保留 binding_certainty=may；
识别这些 Source 不证明事件发生、攻击者可控、跨回调路径或真实漏洞。

当前 `javascript-typescript-core/1.3` 沿用这些规则，新增导入类型与已解析参数转发的 API
上下文识别支持。语法识别和角色转发位于前端及公共 `parameter_apis`，不在规则文件另建
调用图或传播算法；匹配原有字段 Source，推断保留 may 和解析来源，不因类型名称自动确认漏洞。
