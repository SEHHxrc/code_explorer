"""FastAPI/APIRouter 入口点和参数 Source 规则。"""

from __future__ import annotations

from ...base import EntrypointRule, RulePack

FASTAPI_ENTRYPOINT_RULE = EntrypointRule(
    rule_id="FASTAPI-ROUTE",
    framework="fastapi",
    languages=("python",),
    factory_names=("FastAPI", "fastapi.FastAPI", "APIRouter", "fastapi.APIRouter"),
    decorator_methods=(
        "get", "post", "put", "patch", "delete", "options", "head", "trace", "websocket",
    ),
    dependency_suffixes=("Depends",),
    annotation_categories=(
        ("UploadFile", "file_upload"),
        ("WebSocket", "websocket_input"),
        ("Request", "http_request"),
    ),
    default_parameter_category="http_parameter",
)


FASTAPI_RULE_PACK = RulePack(
    name="python-fastapi",
    version="1.0",
    languages=("python",),
    entrypoint_rules=(FASTAPI_ENTRYPOINT_RULE,),
)
