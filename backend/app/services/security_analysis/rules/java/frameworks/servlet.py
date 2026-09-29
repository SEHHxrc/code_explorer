"""Jakarta/Javax HttpServlet 继承式入口规则。"""

from __future__ import annotations

from ...base import EntrypointRule, RulePack

SERVLET_ENTRYPOINT_RULE = EntrypointRule(
    rule_id="JAVA-SERVLET-ENDPOINT",
    framework="servlet",
    languages=("java",),
    owner_method_mappings=(
        ("doGet", "get"),
        ("doPost", "post"),
        ("doPut", "put"),
        ("doDelete", "delete"),
        ("service", "request"),
    ),
    owner_type_suffixes=("HttpServlet",),
    annotation_categories=(
        ("HttpServletRequest", "http_request"),
        ("Part", "file_upload"),
    ),
    excluded_annotation_fragments=("HttpServletResponse",),
    default_parameter_category="http_parameter",
)


SERVLET_RULE_PACK = RulePack(
    name="java-servlet",
    version="1.0",
    languages=("java",),
    entrypoint_rules=(SERVLET_ENTRYPOINT_RULE,),
)
