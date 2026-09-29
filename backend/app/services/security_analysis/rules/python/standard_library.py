"""Python 标准库与常见第三方 API 的安全规则。"""

from __future__ import annotations

from ..base import AccessRule, CallCondition, CallRule, RulePack

MODE_IS_WRITE = CallCondition(
    operator="contains_any", values=("w", "a", "x", "+"),
    argument_position=1, keyword="mode", has_default=True, default="r",
)
MODE_IS_READ = CallCondition(
    operator="excludes_any", values=("w", "a", "x", "+"),
    argument_position=1, keyword="mode", has_default=True, default="r",
)


PYTHON_CORE_CALL_RULES: tuple[CallRule, ...] = (
    CallRule(
        "PY-SOURCE-CLI-INPUT", "source", "cli_input", ("input",),
        trust_class="untrusted", result_role="source",
    ),
    CallRule(
        "PY-SOURCE-ENV", "source", "environment_read",
        ("os.getenv", "os.environ.get"), trust_class="external_configuration",
        result_role="source",
    ),
    CallRule(
        "PY-SOURCE-CLI-ARGS", "source", "cli_arguments",
        ("parse_args", "parse_known_args"), trust_class="untrusted",
        match_suffix=True, result_role="source",
    ),
    CallRule(
        "PY-SOURCE-FILE-READ", "source", "file_read",
        ("read_text", "read_bytes"), trust_class="external_file",
        match_suffix=True, result_role="source", receiver_role="source",
    ),
    CallRule(
        "PY-SINK-FILE-OPEN", "sink", "file_write",
        ("open", "builtins.open"), cwe="CWE-22", severity="medium",
        match_suffix=True, argument_role="sink", argument_positions=(0,),
        conditions=(MODE_IS_WRITE,),
    ),
    CallRule(
        "PY-SINK-SHELL", "sink", "process_execution",
        ("os.system", "os.popen", "subprocess.call", "subprocess.check_call",
         "subprocess.check_output", "subprocess.Popen", "subprocess.run",
         "asyncio.create_subprocess_shell"),
        cwe="CWE-78", severity="high", argument_role="sink",
        argument_positions=(0,), argument_keywords=("args", "cmd", "command"),
    ),
    CallRule(
        "PY-SINK-PROCESS", "sink", "process_execution",
        ("asyncio.create_subprocess_exec",), cwe="CWE-78", severity="medium",
        argument_role="sink", argument_positions=(0,),
    ),
    CallRule(
        "PY-SINK-SQL", "sink", "sql_execution",
        ("execute", "executemany", "executescript", "raw"),
        cwe="CWE-89", severity="high", match_suffix=True,
        argument_role="sink", argument_positions=(0,),
        argument_keywords=("query", "sql", "statement"),
    ),
    CallRule(
        "PY-SINK-CODE-EVAL", "sink", "dynamic_code_execution",
        ("eval", "exec", "compile"), cwe="CWE-95", severity="high",
        argument_role="sink", argument_positions=(0,),
    ),
    CallRule(
        "PY-SINK-DESERIALIZE", "sink", "deserialization",
        ("pickle.load", "pickle.loads", "marshal.load", "marshal.loads",
         "yaml.load", "torch.load"), cwe="CWE-502", severity="high",
        argument_role="sink", argument_positions=(0,),
    ),
    CallRule(
        "PY-SINK-FILE-WRITE", "sink", "file_write",
        ("write_text", "write_bytes", "unlink", "remove", "rmdir", "rename", "replace"),
        cwe="CWE-22", severity="medium", match_suffix=True,
        argument_role="sink", argument_positions=(0,), receiver_role="sink",
    ),
    CallRule(
        "PY-SINK-REDIRECT", "sink", "redirect",
        ("RedirectResponse", "redirect"), cwe="CWE-601", severity="medium",
        match_suffix=True, argument_role="sink", argument_positions=(0,),
        argument_keywords=("url", "location"),
    ),
    CallRule(
        "PY-SINK-TEMPLATE", "sink", "template_rendering",
        ("render_template", "render_template_string", "TemplateResponse", "from_string"),
        cwe="CWE-1336", severity="medium", match_suffix=True,
        argument_role="sink", argument_positions=(0,),
    ),
    CallRule(
        "PY-SINK-NETWORK", "sink", "network_request",
        ("requests.request", "requests.get", "requests.post", "requests.put",
         "requests.patch", "requests.delete", "httpx.request", "httpx.get",
         "httpx.post", "urllib.request.urlopen", "urlopen"),
        cwe="CWE-918", severity="medium", argument_role="sink",
        argument_positions=(0,), argument_keywords=("url",),
    ),
    CallRule(
        "PY-SINK-WEAK-RANDOM", "sink", "security_sensitive_random",
        ("random.random", "random.randint", "random.randrange", "random.choice",
         "random.choices", "random.getrandbits"), cwe="CWE-330", severity="low",
    ),
    CallRule(
        "PY-GUARD-VALIDATION", "guard", "validation",
        ("validate", "verify", "check", "is_safe", "is_valid", "authorize", "authenticate"),
        match_suffix=True, argument_role="guard", argument_positions=(0,),
    ),
    CallRule(
        "PY-SANITIZER-ESCAPE", "sanitizer", "escaping",
        ("escape", "quote", "quote_plus", "secure_filename", "basename", "resolve"),
        match_suffix=True, result_role="sanitized", argument_role="sanitizer_input",
        argument_positions=(0,), receiver_role="sanitizer_input",
    ),
)


PYTHON_CORE_ACCESS_RULES = (
    AccessRule(
        rule_id="PY-SOURCE-ENV", fact_kind="source", category="environment_read",
        names=("os.environ[]",), trust_class="external_configuration",
        result_role="source",
    ),
)


PYTHON_STANDARD_LIBRARY_PACK = RulePack(
    name="python-core",
    version="1.0",
    languages=("python",),
    call_rules=PYTHON_CORE_CALL_RULES,
    access_rules=PYTHON_CORE_ACCESS_RULES,
)
