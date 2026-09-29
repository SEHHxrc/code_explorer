"""Go ``net/http`` 显式处理器注册入口规则。"""

from __future__ import annotations

from ...base import EntrypointRule, RulePack

GO_NET_HTTP_ENTRYPOINT_RULE = EntrypointRule(
    rule_id="GO-NETHTTP-ROUTE",
    framework="net/http",
    languages=("go",),
    registration_call_methods=(
        ("http.HandleFunc", "request"),
        ("http.Handle", "request"),
    ),
    registration_path_position=0,
    registration_handler_position=1,
    excluded_annotation_fragments=("http.ResponseWriter", "http.Request"),
    default_parameter_category="http_parameter",
)


GO_NET_HTTP_RULE_PACK = RulePack(
    name="go-net-http",
    version="1.0",
    languages=("go",),
    entrypoint_rules=(GO_NET_HTTP_ENTRYPOINT_RULE,),
)
