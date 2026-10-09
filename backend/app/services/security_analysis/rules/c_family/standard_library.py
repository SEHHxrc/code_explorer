"""C/C++ 首批库 API 规则；只声明安全角色，供公共规则引擎执行。"""

from __future__ import annotations

from ..base import CallRule, RulePack

_LANGUAGES = ("c", "cpp")
_STDLIB = ("stdlib.h", "cstdlib")
_STDIO = ("stdio.h", "cstdio")

C_FAMILY_CALL_RULES: tuple[CallRule, ...] = (
    CallRule(
        "C-SOURCE-ENV", "source", "environment_read", ("getenv", "std.getenv"),
        languages=_LANGUAGES, required_headers=_STDLIB,
        trust_class="external_configuration", result_role="source",
    ),
    CallRule(
        "C-SOURCE-STDIN", "source", "standard_input", ("getchar", "std.getchar"),
        languages=_LANGUAGES, required_headers=_STDIO,
        trust_class="untrusted", result_role="source",
    ),
    CallRule(
        "C-SOURCE-FILE-CHAR", "source", "stream_read",
        ("fgetc", "getc", "std.fgetc", "std.getc"),
        languages=_LANGUAGES, required_headers=_STDIO,
        trust_class="unknown", result_role="source",
        preconditions=("需要验证 FILE 输入流的来源及攻击者能否提供其中的数据。",),
    ),
    CallRule(
        "C-SOURCE-FILE-BUFFER", "source", "stream_read",
        ("fgets", "fread", "std.fgets", "std.fread"),
        languages=_LANGUAGES, required_headers=_STDIO,
        trust_class="unknown", output_argument_positions=(0,),
        preconditions=("需要验证 FILE 输入流的来源及攻击者能否提供其中的数据。",),
    ),
    CallRule(
        "C-SOURCE-FD-BUFFER", "source", "stream_read", ("read", "pread"),
        languages=_LANGUAGES, required_headers=("unistd.h",),
        trust_class="unknown", output_argument_positions=(1,),
        preconditions=("需要验证文件描述符对应标准输入、文件、管道或其他流，以及攻击者是否能提供数据。",),
    ),
    CallRule(
        "C-SOURCE-NETWORK-BUFFER", "source", "network_input", ("recv", "recvfrom"),
        languages=_LANGUAGES, required_headers=("sys/socket.h",),
        trust_class="untrusted", output_argument_positions=(1,),
    ),
    CallRule(
        "C-SINK-SHELL", "sink", "process_execution", ("system", "std.system"),
        languages=_LANGUAGES, required_headers=_STDLIB,
        cwe="CWE-78", severity="high", argument_role="sink", argument_positions=(0,),
        implicit_shell=True,
    ),
    CallRule(
        "C-SINK-POPEN", "sink", "process_execution", ("popen",),
        languages=_LANGUAGES, required_headers=("stdio.h",),
        cwe="CWE-78", severity="high", argument_role="sink", argument_positions=(0,),
        implicit_shell=True,
    ),
    CallRule(
        "C-SINK-EXEC-PATH", "sink", "process_execution",
        ("execl", "execlp", "execle", "execv", "execvp", "execve"),
        languages=_LANGUAGES, required_headers=("unistd.h",),
        cwe="CWE-78", severity="high", argument_role="sink", argument_positions=(0,),
        preconditions=("exec 系列调用只匹配可执行文件路径；固定可执行文件的参数不自动等同于 Shell 注入。",),
        implicit_shell=False,
    ),
    CallRule(
        "C-SINK-FILE-PATH", "sink", "file_write",
        ("remove", "rename", "std.remove", "std.rename"),
        languages=_LANGUAGES, required_headers=_STDIO,
        cwe="CWE-22", severity="medium", argument_role="sink", argument_positions=(0,),
    ),
    CallRule(
        "C-SINK-FILE-DESTINATION", "sink", "file_write", ("rename", "std.rename"),
        languages=_LANGUAGES, required_headers=_STDIO,
        cwe="CWE-22", severity="medium", argument_role="sink", argument_positions=(1,),
    ),
    CallRule(
        "C-SINK-FILE-OPEN", "sink", "file_path", ("fopen", "std.fopen"),
        languages=_LANGUAGES, required_headers=_STDIO,
        cwe="CWE-22", severity="medium", argument_role="sink", argument_positions=(0,),
    ),
    CallRule(
        "C-SINK-FORMAT", "sink", "format_string", ("printf", "std.printf"),
        languages=_LANGUAGES, required_headers=_STDIO,
        cwe="CWE-134", severity="high", argument_role="sink", argument_positions=(0,),
    ),
    CallRule(
        "C-SINK-FORMAT-STREAM", "sink", "format_string",
        ("fprintf", "sprintf", "std.fprintf", "std.sprintf"),
        languages=_LANGUAGES, required_headers=_STDIO,
        cwe="CWE-134", severity="high", argument_role="sink", argument_positions=(1,),
    ),
    CallRule(
        "C-SINK-FORMAT-BOUNDED", "sink", "format_string", ("snprintf", "std.snprintf"),
        languages=_LANGUAGES, required_headers=_STDIO,
        cwe="CWE-134", severity="high", argument_role="sink", argument_positions=(2,),
    ),
    CallRule(
        "C-GUARD-COMPARE", "guard", "comparison",
        ("strcmp", "strncmp", "std.strcmp", "std.strncmp"),
        languages=_LANGUAGES, required_headers=("string.h", "cstring"),
        argument_role="guard", argument_positions=(0, 1),
    ),
)

C_FAMILY_STANDARD_LIBRARY_PACK = RulePack(
    name="c-family-core", version="1.0", languages=_LANGUAGES,
    call_rules=C_FAMILY_CALL_RULES,
)

C_FAMILY_SQLITE_PACK = RulePack(
    name="c-family-sqlite", version="1.0", languages=_LANGUAGES,
    call_rules=(CallRule(
        "C-SINK-SQLITE", "sink", "sql_execution", ("sqlite3_exec",),
        languages=_LANGUAGES, required_headers=("sqlite3.h",),
        cwe="CWE-89", severity="high", argument_role="sink", argument_positions=(1,),
        sql_parameter_argument_position=None,
    ),),
)
