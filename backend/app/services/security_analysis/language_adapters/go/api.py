"""Go 导入/显式类型与 API 角色；不定义漏洞规则，不把整个 request 当作污点。"""

from __future__ import annotations

import ast
from typing import Any

from backend.app.services.syntax_analysis import field, normalize_type, semantic_children, text

PACKAGES = {
    path: name for path, name in (
        ("github.com/gin-gonic/gin", "gin"), ("net/http", "http"), ("net/url", "url"),
        ("net", "net"), ("mime/multipart", "multipart"), ("encoding/json", "json"),
        ("encoding/xml", "xml"), ("io", "io"), ("io/ioutil", "ioutil"), ("os", "os"),
        ("os/exec", "exec"), ("database/sql", "sql"), ("math/rand", "rand"), ("regexp", "regexp"),
    )
}
ROUTERS = ("gin.Engine", "gin.RouterGroup", "gin.IRouter", "gin.IRoutes")
ROUTE_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "Any", "Handle"})
REQUEST_FIELDS = frozenset({"RequestURI", "Host", "RemoteAddr", "Method", "URL.Path", "URL.RawPath", "URL.RawQuery", "URL.Fragment"})
BODY_TYPES = ("http.Request.Body", "http.Response.Body", "multipart.File")


class GoAPIBindings:
    """任务内有限词法类型环境；请求来源角色与真实 API 类型分开保留。"""

    def __init__(self, source: bytes, imports: dict[str, str]) -> None:
        """输入源码及已观察导入；未知同名包保留完整路径，不按包尾名冒充标准库。"""
        self.source = source
        self.imports = imports

    def canonical_type(self, annotation: str) -> str:
        """规范化指针和导入别名；局部同名类型不会自动成为框架类型。"""
        value = normalize_type(annotation)
        head, separator, tail = value.partition(".")
        return self.imports.get(head, head) + (separator + tail if separator else "")

    @staticmethod
    def owner_type(role: str) -> str:
        """从来源角色还原实际 API 类型，不把语义角色伪装成源码调用名。"""
        if role.endswith(".JSONDecoder"):
            return "json.Decoder"
        if role.endswith(".XMLDecoder"):
            return "xml.Decoder"
        return {
            "http.Request.QueryValues": "url.Values", "http.Request.Form": "url.Values",
            "http.Request.PostForm": "url.Values", "http.Request.Header": "http.Header",
            "http.Request.URL": "url.URL", "gin.Context.Params": "gin.Params",
        }.get(role, role)

    def call_name(self, node: Any, variables: dict[str, str]) -> str:
        """返回有导入或显式类型依据的调用名，未知接收者保持原始词法名称。"""
        function = field(node, "function")
        if function is not None and function.type == "selector_expression":
            owner = field(function, "operand")
            role = self.expression_type(owner, variables)
            return f"{self.owner_type(role) or text(owner, self.source)}.{text(field(function, 'field'), self.source)}"
        return text(function, self.source)

    def expression_type(self, node: Any | None, variables: dict[str, str]) -> str:
        """只解析静态接收者/返回角色，不求解别名堆、反射或任意自定义工厂。"""
        if node is None:
            return ""
        if node.type == "identifier":
            name = text(node, self.source)
            return variables.get(name, self.imports.get(name, ""))
        if node.type in {"parenthesized_expression", "unary_expression"}:
            return self.expression_type(field(node, "operand") or next(iter(semantic_children(node)), None), variables)
        if node.type in {"composite_literal", "qualified_type", "type_identifier"}:
            if node.type != "composite_literal":
                return self.canonical_type(text(node, self.source))
            role = self.canonical_type(text(field(node, "type"), self.source))
            return "http.ClientRequest" if role == "http.Request" else role
        if node.type == "selector_expression":
            owner = self.expression_type(field(node, "operand"), variables)
            member = text(field(node, "field"), self.source)
            if owner == "gin.Context" and member == "Request":
                return "http.Request"
            if owner == "http.Request" and member in {"URL", "Header", "Body", "Form", "PostForm"}:
                return f"{owner}.{member}"
            if owner == "http.Response" and member == "Body":
                return "http.Response.Body"
            if owner == "gin.Context" and member == "Params":
                return "gin.Context.Params"
            return ""
        if node.type == "call_expression":
            return next(iter(self.result_types(node, variables)), "")
        return ""

    def result_types(self, call: Any, variables: dict[str, str]) -> tuple[str, ...]:
        """区分多返回的内容与状态；只为已知 API 返回接收者类型，不产生 Source。"""
        name = self.call_name(call, variables)
        function = field(call, "function")
        receiver = self.expression_type(field(function, "operand"), variables)
        arguments = semantic_children(field(call, "arguments"))
        if name in {"gin.New", "gin.Default"}:
            return ("gin.Engine",)
        if receiver in ROUTERS and name.endswith(".Group"):
            return ("gin.RouterGroup",)
        if name == "new" and arguments:
            role = self.canonical_type(text(arguments[0], self.source))
            return ("http.ClientRequest" if role == "http.Request" else role,)
        if name == "http.NewServeMux":
            return ("http.ServeMux",)
        if name in {"http.HandlerFunc", "http.TimeoutHandler", "http.StripPrefix"}:
            return ("http.Handler",)
        if name in {"http.NewRequest", "http.NewRequestWithContext"}:
            return ("http.ClientRequest", "")
        if name in {"http.Get", "http.Post", "http.PostForm", "http.Client.Get", "http.Client.Post", "http.Client.PostForm", "http.Client.Do"}:
            return ("http.Response", "")
        if receiver == "http.Request.URL" and name == "url.URL.Query":
            return ("http.Request.QueryValues",)
        if name in {"json.NewDecoder", "xml.NewDecoder"} and arguments:
            role = self.expression_type(arguments[0], variables)
            return ((role + (".JSONDecoder" if name.startswith("json.") else ".XMLDecoder")) if role in BODY_TYPES else name.split(".")[0] + ".Decoder",)
        if name == "gin.Context.FormFile":
            return ("multipart.FileHeader", "")
        if name == "http.Request.FormFile":
            return ("multipart.File", "multipart.FileHeader", "")
        if name == "http.Request.Cookie":
            return ("http.Request.Cookie", "")
        if name == "multipart.FileHeader.Open":
            return ("multipart.File", "")
        if name in {"net.Listen", "net.ListenTCP", "net.ListenUnix"}:
            return ("net.Listener", "")
        if name in {"net.Dial", "net.DialTimeout", "net.Dialer.Dial", "net.Dialer.DialContext", "net.Listener.Accept"}:
            return ("net.Conn", "")
        if name in {"net.ListenPacket", "net.ListenUDP", "net.DialUDP"}:
            return ("net.UDPConn", "")
        return ()

    def route_prefix(self, node: Any | None, variables: dict[str, str]) -> str:
        """仅组合源码常量 Group 前缀；动态或外部传入路由组明确保留未知标记。"""
        if node is None:
            return "<dynamic>"
        if node.type == "identifier":
            return variables.get("@gin-prefix:" + text(node, self.source), "<dynamic>")
        if node.type != "call_expression":
            return "<dynamic>"
        name = self.call_name(node, variables)
        if name in {"gin.New", "gin.Default"}:
            return ""
        if name.rsplit(".", 1)[-1] == "Group":
            parent = self.route_prefix(field(field(node, "function"), "operand"), variables)
            args = semantic_children(field(node, "arguments"))
            if args and args[0].type in {"interpreted_string_literal", "raw_string_literal"} and parent != "<dynamic>":
                raw = text(args[0], self.source)
                try:
                    value = raw[1:-1] if raw.startswith("`") else ast.literal_eval(raw)
                except (SyntaxError, ValueError):
                    return "<dynamic>"
                return parent.rstrip("/") + "/" + value.lstrip("/")
        return "<dynamic>"
