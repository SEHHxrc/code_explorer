"""JS/TS 服务端、Node.js 和 Express 规则：结构候选不等同于可利用漏洞。"""

from .base import AccessRule, CallCondition, CallRule, EntrypointRule, RulePack

_LANGUAGES = ("javascript", "typescript")
_CONTROL = (
    "必须验证攻击者能控制相应输入；环境配置由可信部署人员设置时不能据此认定远程漏洞。",
)

JAVASCRIPT_RULE_PACKS = (
    RulePack(
        name="javascript-typescript-core",
        version="1.4",
        languages=_LANGUAGES,
        entrypoint_rules=(
            EntrypointRule(
                "JS-NODE-HTTP-SERVER",
                "node:http",
                _LANGUAGES,
                registration_call_methods=(
                    ("http.createServer", "all"),
                    ("https.createServer", "all"),
                ),
                registration_path_position=-1,
                registration_handler_position=-1,
                source_parameter_positions=(),
            ),
            EntrypointRule(
                rule_id="JS-EXPRESS-ROUTE",
                framework="express",
                languages=_LANGUAGES,
                registration_call_methods=tuple(
                    (f"express.Application.{method}", method)
                    for method in (
                        "get",
                        "post",
                        "put",
                        "patch",
                        "delete",
                        "head",
                        "options",
                        "all",
                    )
                ),
                source_parameter_positions=(),  # req/res/next 不是三个外部标量输入。
            ),
        ),
        access_rules=(
            AccessRule(
                "JS-SOURCE-NODE-BODY", "source", "http_body",
                ("node.IncomingMessage.bodyChunk",),
                languages=_LANGUAGES, trust_class="untrusted", result_role="source",
                preconditions=("仅注册 data 回调的首个形参；未建模跨回调累积、end 时序或 Buffer 解码边界。",),
            ),
            AccessRule(
                "JS-SOURCE-ARGV",
                "source",
                "cli_argument",
                ("process.argv",),
                languages=_LANGUAGES,
                trust_class="unknown",
                result_role="source",
                preconditions=_CONTROL,
            ),
            AccessRule(
                "JS-SOURCE-NODE-HTTP",
                "source",
                "http_parameter",
                ("node.IncomingMessage.url", "node.IncomingMessage.headers"),
                languages=_LANGUAGES,
                trust_class="untrusted",
                result_role="source",
            ),
            AccessRule(
                "JS-SOURCE-HTTP",
                "source",
                "http_parameter",
                tuple(
                    f"express.Request.{part}"
                    for part in ("query", "body", "params", "headers", "cookies")
                ),
                languages=_LANGUAGES,
                trust_class="untrusted",
                result_role="source",
            ),
            AccessRule(
                "JS-SOURCE-ENV",
                "source",
                "environment_input",
                ("process.env",),
                languages=_LANGUAGES,
                trust_class="external_configuration",
                result_role="source",
                preconditions=_CONTROL,
            ),
        ),
        call_rules=(
            CallRule(
                "JS-SOURCE-FILE",
                "source",
                "external_file",
                ("fs.readFileSync", "fs.promises.readFile"),
                languages=_LANGUAGES,
                trust_class="external_file",
                result_role="source",
            ),
            CallRule(
                "JS-SINK-SHELL",
                "sink",
                "shell_execution",
                ("child_process.exec", "child_process.execSync"),
                languages=_LANGUAGES,
                cwe="CWE-78",
                severity="high",
                argument_role="sink",
                argument_positions=(0,),
                implicit_shell=True,
                preconditions=_CONTROL,
            ),
            CallRule(
                "JS-SINK-SHELL-OPTION",
                "sink",
                "shell_execution",
                (
                    "child_process.execFile",
                    "child_process.execFileSync",
                    "child_process.spawn",
                    "child_process.spawnSync",
                ),
                languages=_LANGUAGES,
                cwe="CWE-78",
                severity="high",
                argument_role="sink",
                argument_positions=(0, 1),
                implicit_shell=True,
                conditions=(CallCondition("equals", (True,), keyword="shell"),),
                preconditions=_CONTROL,
            ),
            CallRule(
                "JS-SINK-PROCESS",
                "sink",
                "process_execution",
                (
                    "child_process.execFile",
                    "child_process.execFileSync",
                    "child_process.spawn",
                    "child_process.spawnSync",
                ),
                languages=_LANGUAGES,
                cwe="CWE-78",
                severity="medium",
                argument_role="sink",
                argument_positions=(0,),
                implicit_shell=False,
                conditions=(
                    CallCondition(
                        "equals",
                        (False,),
                        keyword="shell",
                        has_default=True,
                        default=False,
                    ),
                ),
                preconditions=(
                    "仅检查可控可执行文件路径；默认无 Shell，不把普通命令参数认定为 Shell 注入。",
                ),
            ),
            CallRule(
                "JS-SINK-EVAL",
                "sink",
                "dynamic_code_execution",
                ("eval", "Function"),
                languages=_LANGUAGES,
                cwe="CWE-95",
                severity="high",
                argument_role="sink",
                argument_positions=(0,),
            ),
            CallRule(
                "JS-SINK-FILE",
                "sink",
                "file_path",
                tuple(
                    f"fs.{name}"
                    for name in (
                        "readFileSync",
                        "writeFileSync",
                        "readFile",
                        "writeFile",
                        "unlink",
                        "unlinkSync",
                        "createReadStream",
                        "createWriteStream",
                        "promises.readFile",
                        "promises.writeFile",
                        "promises.unlink",
                    )
                ),
                languages=_LANGUAGES,
                cwe="CWE-22",
                severity="medium",
                argument_role="sink",
                argument_positions=(0,),
                preconditions=(
                    "必须验证路径越出允许目录且攻击者可控；正常可信文件访问不是漏洞。",
                ),
            ),
            CallRule(
                "JS-SINK-NETWORK",
                "sink",
                "network_request",
                (
                    "fetch",
                    "http.get",
                    "http.request",
                    "https.get",
                    "https.request",
                    "axios",
                    "axios.get",
                    "axios.post",
                ),
                languages=_LANGUAGES,
                cwe="CWE-918",
                severity="medium",
                argument_role="sink",
                argument_positions=(0,),
                preconditions=(
                    "需核实代码运行于服务端、目标可控性和目标限制；请求调用本身不证明 SSRF。",
                ),
            ),
        ),
    ),
)
