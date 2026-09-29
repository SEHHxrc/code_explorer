"""Go 标准库首批高置信 Source、Sink 与 Guard 规则。"""

from __future__ import annotations

from ..base import CallRule, RulePack

GO_CORE_CALL_RULES: tuple[CallRule, ...] = (
    CallRule(
        "GO-SOURCE-ENV", "source", "environment_read",
        ("os.Getenv", "os.LookupEnv"), languages=("go",),
        trust_class="external_configuration", match_suffix=True,
        result_role="source",
    ),
    CallRule(
        "GO-SOURCE-HTTP-REQUEST", "source", "http_request",
        (
            "http.Request.FormValue", "http.Request.PostFormValue",
            "Header.Get",
        ),
        languages=("go",), trust_class="untrusted", match_suffix=True,
        result_role="source",
    ),
    CallRule(
        "GO-SOURCE-FILE-READ", "source", "file_read",
        ("os.ReadFile",), languages=("go",),
        trust_class="external_file", match_suffix=True,
        result_role="source",
    ),
    CallRule(
        "GO-SINK-PROCESS", "sink", "process_execution",
        ("exec.Command", "exec.CommandContext"), languages=("go",),
        cwe="CWE-78", severity="high", match_suffix=True,
        argument_role="sink", argument_positions=(0,),
    ),
    CallRule(
        "GO-SINK-SQL", "sink", "sql_execution",
        (
            "sql.DB.Exec", "sql.DB.ExecContext", "sql.DB.Query",
            "sql.DB.QueryContext", "sql.DB.QueryRow", "sql.DB.QueryRowContext",
            "sql.DB.Prepare", "sql.DB.PrepareContext", "sql.Tx.Exec",
            "sql.Tx.ExecContext", "sql.Tx.Query", "sql.Tx.QueryContext",
        ),
        languages=("go",), cwe="CWE-89", severity="high", match_suffix=True,
        argument_role="sink", argument_positions=(0,),
    ),
    CallRule(
        "GO-SINK-FILE-WRITE", "sink", "file_write",
        ("os.WriteFile", "os.Create", "os.Remove", "os.RemoveAll", "os.Rename"),
        languages=("go",), cwe="CWE-22", severity="medium", match_suffix=True,
        argument_role="sink", argument_positions=(0,),
    ),
    CallRule(
        "GO-SINK-NETWORK-URL", "sink", "network_request",
        ("http.Get", "http.Post", "http.PostForm"), languages=("go",),
        cwe="CWE-918", severity="medium", match_suffix=True,
        argument_role="sink", argument_positions=(0,),
    ),
    CallRule(
        "GO-SINK-NETWORK-REQUEST", "sink", "network_request",
        ("http.NewRequest", "http.NewRequestWithContext"), languages=("go",),
        cwe="CWE-918", severity="medium", match_suffix=True,
        argument_role="sink", argument_positions=(1,),
    ),
    CallRule(
        "GO-SINK-WEAK-RANDOM", "sink", "security_sensitive_random",
        (
            "rand.Int", "rand.Intn", "rand.Int31", "rand.Int31n",
            "rand.Int63", "rand.Int63n", "rand.Float32", "rand.Float64",
        ),
        languages=("go",), cwe="CWE-330", severity="low", match_suffix=True,
    ),
    CallRule(
        "GO-GUARD-VALIDATION", "guard", "validation",
        ("regexp.MatchString", "url.ParseRequestURI"), languages=("go",),
        match_suffix=True, argument_role="guard", argument_positions=(0,),
    ),
)


GO_STANDARD_LIBRARY_PACK = RulePack(
    name="go-core",
    version="1.0",
    languages=("go",),
    call_rules=GO_CORE_CALL_RULES,
)
