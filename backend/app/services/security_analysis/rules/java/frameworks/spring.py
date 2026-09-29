"""Spring Web 注解入口和请求参数 Source 规则。"""

from __future__ import annotations

from ...base import EntrypointRule, RulePack

SPRING_WEB_ENTRYPOINT_RULE = EntrypointRule(
    rule_id="SPRING-WEB-ROUTE",
    framework="spring-web",
    languages=("java",),
    direct_decorator_methods=(
        ("GetMapping", "get"),
        ("PostMapping", "post"),
        ("PutMapping", "put"),
        ("PatchMapping", "patch"),
        ("DeleteMapping", "delete"),
        ("RequestMapping", "request"),
    ),
    annotation_categories=(
        ("MultipartFile", "file_upload"),
        ("HttpServletRequest", "http_request"),
        ("WebSocketSession", "websocket_input"),
        ("@RequestBody", "http_body"),
        ("@RequestHeader", "http_header"),
        ("@PathVariable", "http_path_parameter"),
    ),
    default_parameter_category="http_parameter",
)


SPRING_WEB_RULE_PACK = RulePack(
    name="java-spring-web",
    version="1.0",
    languages=("java",),
    entrypoint_rules=(SPRING_WEB_ENTRYPOINT_RULE,),
)
