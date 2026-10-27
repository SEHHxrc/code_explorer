"""Go ``net/http`` 显式处理器注册入口规则。"""

from __future__ import annotations

from ...base import AccessRule, CallRule, EntrypointRule, RulePack

GO_NET_HTTP_ENTRYPOINT_RULE = EntrypointRule(
    rule_id="GO-NETHTTP-ROUTE",
    framework="net/http",
    languages=("go",),
    registration_call_methods=(
        ("http.HandleFunc", "request"),
        ("http.Handle", "request"),
        ("http.ServeMux.HandleFunc", "request"),
        ("http.ServeMux.Handle", "request"),
    ),
    registration_path_position=0,
    registration_handler_position=1,
    excluded_annotation_fragments=("http.ResponseWriter", "http.Request"),
    default_parameter_category="http_parameter",
    source_parameter_positions=(),
    require_exact_handler=True,
)

_BODY_ROLES = ("http.Request.Body", "http.Response.Body", "multipart.File")
_REQUEST_READS = (
    ("url.Values.Get", ("http.Request.QueryValues", "http.Request.Form", "http.Request.PostForm")),
    ("http.Header.Get", ("http.Request.Header",)),
    ("http.Request.PathValue", ("http.Request",)),
)
GO_NET_HTTP_CALL_RULES = tuple(
    CallRule("GO-SOURCE-NETHTTP-VALUE-" + str(index), "source", "http_request", (name,),
             languages=("go",), trust_class="untrusted", result_role="source", result_positions=(0,),
             receiver_api_types=roles)
    for index, (name, roles) in enumerate(_REQUEST_READS)
) + (
    CallRule("GO-SOURCE-HTTP-BODY", "source", "http_body", ("io.ReadAll", "ioutil.ReadAll"),
             languages=("go",), trust_class="untrusted", result_role="source", result_positions=(0,), result_count=2,
             argument_api_types=((0, _BODY_ROLES),)),
    CallRule("GO-SOURCE-HTTP-DECODE", "source", "http_body", ("json.Decoder.Decode", "xml.Decoder.Decode"),
             languages=("go",), trust_class="untrusted", output_argument_positions=(0,),
             receiver_api_types=tuple(role + suffix for role in _BODY_ROLES for suffix in (".JSONDecoder", ".XMLDecoder"))),
    CallRule("GO-SINK-HTTP-CLIENT", "sink", "network_request", ("http.Client.Get", "http.Client.Post", "http.Client.PostForm"),
             languages=("go",), cwe="CWE-918", severity="medium", argument_role="sink", argument_positions=(0,),
             receiver_api_types=("http.Client",)),
    CallRule("GO-SINK-HTTP-FILE", "sink", "file_read", ("http.ServeFile",), languages=("go",),
             cwe="CWE-22", severity="medium", argument_role="sink", argument_positions=(2,)),
    CallRule("GO-SINK-HTTP-REDIRECT", "sink", "redirect", ("http.Redirect",), languages=("go",),
             cwe="CWE-601", severity="medium", argument_role="sink", argument_positions=(2,)),
)

GO_NET_HTTP_ACCESS_RULES = (
    AccessRule("GO-SOURCE-HTTP-FIELD", "source", "http_request", tuple(
        "http.Request." + name for name in ("RequestURI", "Host", "RemoteAddr", "Method", "URL.Path", "URL.RawPath", "URL.RawQuery", "URL.Fragment")
    ) + tuple("http.Request." + name + ".item" for name in ("Header", "Form", "PostForm", "QueryValues"))
      + ("multipart.FileHeader.Filename", "http.Request.Cookie.Value"), languages=("go",), trust_class="untrusted", result_role="source",
        preconditions=("请求类型和字段读取是静态线索；调用是否来自外部请求以及攻击者权限仍需核实。",)),
)

GO_NET_HTTP_RULE_PACK = RulePack(
    name="go-net-http",
    version="1.1",
    languages=("go",),
    entrypoint_rules=(GO_NET_HTTP_ENTRYPOINT_RULE,),
    call_rules=GO_NET_HTTP_CALL_RULES,
    access_rules=GO_NET_HTTP_ACCESS_RULES,
)
