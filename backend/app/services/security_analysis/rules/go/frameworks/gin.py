"""Gin 的可观察路由、请求内容及敏感参数角色；不把 Context 整体当作不可信值。"""

from ...base import CallRule, EntrypointRule, RulePack

_ROUTERS = ("gin.Engine", "gin.RouterGroup", "gin.IRouter", "gin.IRoutes")
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "Any")
_SINGLE_READS = ("Query", "DefaultQuery", "QueryArray", "QueryMap", "Param", "PostForm", "DefaultPostForm", "PostFormArray", "PostFormMap", "GetHeader")
_STATUS_READS = ("GetQuery", "GetQueryArray", "GetQueryMap", "GetPostForm", "GetPostFormArray", "GetPostFormMap", "GetRawData", "Cookie")
_BINDS = ("Bind", "BindJSON", "BindXML", "BindYAML", "BindQuery", "BindUri", "BindHeader", "ShouldBind", "ShouldBindJSON", "ShouldBindXML", "ShouldBindYAML", "ShouldBindQuery", "ShouldBindUri", "ShouldBindHeader", "ShouldBindTOML", "BindTOML")

GO_GIN_RULE_PACK = RulePack(
    name="go-gin", version="1.0", languages=("go",),
    entrypoint_rules=(
        EntrypointRule("GO-GIN-ROUTE", "gin", ("go",),
            registration_call_methods=tuple((owner + "." + method, method) for owner in _ROUTERS for method in _METHODS),
            registration_handler_position=-1, source_parameter_positions=(), require_exact_handler=True),
        EntrypointRule("GO-GIN-HANDLE", "gin", ("go",),
            registration_call_methods=tuple((owner + ".Handle", "<dynamic-method>") for owner in _ROUTERS),
            registration_path_position=1, registration_handler_position=-1, source_parameter_positions=(), require_exact_handler=True),
    ),
    call_rules=(
        CallRule("GO-SOURCE-GIN-PARAMS", "source", "http_request", ("gin.Params.ByName",),
            languages=("go",), trust_class="untrusted", result_role="source", result_positions=(0,), receiver_api_types=("gin.Context.Params",)),
        CallRule("GO-SOURCE-GIN-REQUEST", "source", "http_request", tuple("gin.Context." + name for name in _SINGLE_READS),
            languages=("go",), trust_class="untrusted", result_role="source", result_positions=(0,), receiver_api_types=("gin.Context",)),
        CallRule("GO-SOURCE-GIN-CONTENT-STATUS", "source", "http_request", tuple("gin.Context." + name for name in _STATUS_READS),
            languages=("go",), trust_class="untrusted", result_role="source", result_positions=(0,), result_count=2, receiver_api_types=("gin.Context",)),
        CallRule("GO-SOURCE-GIN-BIND", "source", "http_body", tuple("gin.Context." + name for name in _BINDS),
            languages=("go",), trust_class="untrusted", output_argument_positions=(0,), receiver_api_types=("gin.Context",),
            preconditions=("绑定可能写入目标对象；错误返回不是请求内容，标签校验不自动证明所有敏感上下文安全。",)),
        CallRule("GO-SINK-GIN-UPLOAD", "sink", "file_write", ("gin.Context.SaveUploadedFile",),
            languages=("go",), cwe="CWE-22", severity="medium", argument_role="sink", argument_positions=(1,), receiver_api_types=("gin.Context",)),
        CallRule("GO-SINK-GIN-REDIRECT", "sink", "redirect", ("gin.Context.Redirect",),
            languages=("go",), cwe="CWE-601", severity="medium", argument_role="sink", argument_positions=(1,), receiver_api_types=("gin.Context",)),
        CallRule("GO-SINK-GIN-FILE", "sink", "file_read", ("gin.Context.File", "gin.Context.FileAttachment"),
            languages=("go",), cwe="CWE-22", severity="medium", argument_role="sink", argument_positions=(0,), receiver_api_types=("gin.Context",)),
    ),
)
