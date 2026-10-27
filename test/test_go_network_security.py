"""Go 网络角色、匿名路由和内容/状态隔离；不启动网络服务或调用模型。"""

from pathlib import Path
import tempfile
import unittest

from backend.app.services.dependency_analyzer import UnifiedCodeAnalyzer
from backend.app.services.security_analysis.frontends.go import GoSecurityFrontend
from backend.app.services.security_analysis.rule_engine import SecurityRuleEngine
from backend.app.services.security_analysis.rules.go import GO_RULE_PACKS
from backend.app.services.security_analysis.service import SecurityAnalysisService
from backend.app.services.semantic_index import ProjectSemanticResult
from backend.app.services.program_graph import ProgramGraphService


class NoNativeResolver:
    """刻意关闭原生工具，证明旧解析路径仍可独立完成这些用例。"""

    def resolve(self, project_root: str, *, file_paths: list[str], max_file_bytes: int) -> ProjectSemanticResult:
        """返回明确 unavailable，不安装依赖、不请求外部服务。"""
        return ProjectSemanticResult(backend="go", status="unavailable", files=file_paths)


class GoNetworkSecurityTests(unittest.TestCase):
    """围绕参数位置和真实接收者的正反例。"""

    def analyze(self, source: str) -> tuple:
        """在临时项目执行公共非原生依赖/CFG/DFG/规则流水线。"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.go").write_text(source, encoding="utf-8")
            program = GoSecurityFrontend().build(directory)
            scan = SecurityRuleEngine().evaluate(program, GO_RULE_PACKS)
            analysis = UnifiedCodeAnalyzer(directory, max_workers=1, semantic_resolver=NoNativeResolver()).run_full_analysis()
            pack = SecurityAnalysisService().analyze(project_root=directory, dependency_graph=analysis["dependency_graph"],
                analysis_diagnostics=analysis["diagnostics"])
        return program, scan, pack

    def test_inline_gin_groups_and_content_not_status(self) -> None:
        """匿名处理器独立建模，组路径保留，bool 返回不得通向内容 Sink。"""
        program, scan, pack = self.analyze('''package main
import ("github.com/gin-gonic/gin"; "os/exec")
func main() {
    r := gin.Default()
    group := r.Group("/api")
    group.GET("/run", func(c *gin.Context) {
        value, ok := c.GetQuery("cmd")
        exec.Command(value)
        exec.Command(ok)
    })
}
''')
        self.assertFalse(program.failures)
        self.assertEqual(["/api/run"], [entry.metadata["route_path"] for entry in scan.entrypoints])
        source = next(item for item in scan.sources if item.rule_id == "GO-SOURCE-GIN-CONTENT-STATUS")
        self.assertIn("anonymous@", source.symbol)
        verified = [pack.facts[item.sink_fact_id].location.line for item in pack.candidates if item.dataflow_verified]
        self.assertEqual([8], verified)

    def test_net_http_query_body_and_output_binding(self) -> None:
        """请求 Query、正文和 Decode(&value) 接入公共 DFG，error 返回保持干净。"""
        program, scan, pack = self.analyze('''package main
import ("net/http"; "os/exec"; "io"; "encoding/json")
func main() {
    mux := http.NewServeMux()
    mux.HandleFunc("/run", func(w http.ResponseWriter, r *http.Request) {
        query := r.URL.Query().Get("cmd")
        exec.Command(query)
        body, err := io.ReadAll(r.Body)
        exec.Command(string(body))
        exec.Command(err)
        var payload string
        decodeErr := json.NewDecoder(r.Body).Decode(&payload)
        exec.Command(payload)
        exec.Command(decodeErr)
    })
}
''')
        self.assertFalse(program.failures)
        self.assertEqual(1, len(scan.entrypoints))
        self.assertEqual(3, len(scan.sources))
        verified = {pack.facts[item.sink_fact_id].location.line for item in pack.candidates if item.dataflow_verified}
        self.assertEqual({7, 9, 13}, verified)

    def test_gin_bind_output_and_sensitive_slot(self) -> None:
        """Bind 污染目标对象而非错误；Redirect 追踪 URL 而不是 HTTP 状态。"""
        _, scan, pack = self.analyze('''package main
import "github.com/gin-gonic/gin"
func handler(c *gin.Context) {
    var destination string
    err := c.ShouldBindJSON(&destination)
    c.Redirect(302, destination)
    c.Redirect(err, "/safe")
}
func main(){ r := gin.Default(); r.GET("/run", handler) }
''')
        self.assertEqual(1, len(scan.entrypoints))
        verified = {pack.facts[item.sink_fact_id].location.line for item in pack.candidates if item.dataflow_verified}
        self.assertEqual({6}, verified)

    def test_local_and_outgoing_values_are_not_http_sources(self) -> None:
        """本地同名包、Header、JSON 字符串及主动构造的 Request 不冒充网络输入。"""
        _, scan, _ = self.analyze('''package main
import ("net/http"; "net/url"; "encoding/json"; "strings"; fake "example.test/gin")
func run(c *fake.Context) {
    c.Query("q")
    h := http.Header{}
    h.Get("x")
    q := url.Values{}
    q.Get("x")
    req, _ := http.NewRequest("GET", "http://local/", nil)
    req.FormValue("x")
    local := &http.Request{}
    local.FormValue("x")
    allocated := new(http.Request)
    allocated.FormValue("x")
    var payload string
    json.NewDecoder(strings.NewReader("safe")).Decode(&payload)
}
''')
        self.assertEqual([], scan.sources)
        self.assertEqual([], scan.entrypoints)

    def test_net_conn_read_does_not_taint_count_error_or_context(self) -> None:
        """连接读取写入缓冲区；DialContext 只把地址参数列为危险输入位置。"""
        _, scan, pack = self.analyze('''package main
import ("net"; "os/exec"; "context")
func run(c net.Conn, ctx context.Context) {
    buf := make([]byte, 100)
    n, err := c.Read(buf)
    exec.Command(string(buf))
    exec.Command(n)
    exec.Command(err)
    var dialer net.Dialer
    dialer.DialContext(ctx, "tcp", string(buf))
}
''')
        self.assertEqual(1, len(scan.sources))
        verified = {pack.facts[item.sink_fact_id].location.line for item in pack.candidates if item.dataflow_verified}
        self.assertEqual({6, 10}, verified)

    def test_same_name_methods_and_multiple_registrations(self) -> None:
        """接收者类型消除同名处理器歧义，同一处理器的两个路由均保留。"""
        _, scan, _ = self.analyze('''package main
import "github.com/gin-gonic/gin"
type One struct{}
type Two struct{}
func (o *One) handle(c *gin.Context){}
func (t *Two) handle(c *gin.Context){}
func register(r *gin.Engine, one *One){
    r.GET("/one", one.handle)
    r.POST("/two", one.handle)
}
''')
        self.assertEqual(2, len(scan.entrypoints))
        self.assertEqual({"main.go::One::handle"}, {item.symbol for item in scan.entrypoints})

    def test_known_local_literal_and_unknown_shadowed_handler(self) -> None:
        """局部匿名处理器保留身份，未知重绑定不能误关联同名顶层声明。"""
        program, scan, _ = self.analyze('''package main
import ("net/http"; "github.com/gin-gonic/gin")
func handler(w http.ResponseWriter, r *http.Request) {}
func main(){
    h := func(w http.ResponseWriter, r *http.Request){}
    http.Handle("/wrapped", http.HandlerFunc(h))
    handler := unknownFactory()
    http.HandleFunc("/unknown", handler)
    gin.Default().GET("/inline", func(c *gin.Context){})
}
''')
        self.assertEqual(2, len(scan.entrypoints))
        self.assertEqual(1, len(program.failures))
        self.assertTrue(all("anonymous@" in entry.symbol for entry in scan.entrypoints))

    def test_request_fields_do_not_taint_sibling_values(self) -> None:
        """请求路径、上传文件名只传播各自字段，不污染常量兄弟值。"""
        _, scan, pack = self.analyze('''package main
import ("net/http"; "os/exec")
func handler(w http.ResponseWriter, r *http.Request) {
    value := r.URL.Path
    exec.Command(value)
    exec.Command("safe")
    _, file, _ := r.FormFile("file")
    exec.Command(file.Filename)
}
''')
        self.assertEqual(2, len(scan.sources))
        verified = {pack.facts[item.sink_fact_id].location.line for item in pack.candidates if item.dataflow_verified}
        self.assertEqual({5, 8}, verified)

    def test_request_rebinding_does_not_keep_framework_role(self) -> None:
        """词法顺序上未知重绑定后不沿用网络角色，避免旧的全函数类型预索引误判。"""
        _, scan, _ = self.analyze('''package main
import "net/http"
func handler(r *http.Request) {
    r.FormValue("real")
    r = localFactory()
    r.FormValue("not-proven")
}
''')
        self.assertEqual(1, len(scan.sources))

    def test_binding_object_fields_and_clean_overwrite(self) -> None:
        """请求绑定对象沿公共字段 DFG 传播；覆盖字段为常量后不再传播旧输入。"""
        _, _, pack = self.analyze('''package main
import ("github.com/gin-gonic/gin"; "os/exec")
type Payload struct{ Command string }
func handler(c *gin.Context) {
    var payload Payload
    err := c.ShouldBindJSON(&payload)
    exec.Command(payload.Command)
    payload.Command = "safe"
    exec.Command(payload.Command)
    exec.Command(err)
}
''')
        verified = {pack.facts[item.sink_fact_id].location.line for item in pack.candidates if item.dataflow_verified}
        self.assertEqual({7}, verified)

    def test_overwritten_request_field_is_not_a_new_source(self) -> None:
        """请求属性的赋值不是读取；已覆盖属性不重新种入网络 Source。"""
        _, scan, pack = self.analyze('''package main
import ("net/http"; "os/exec")
func handler(r *http.Request){
    r.URL.Path = "/safe"
    exec.Command(r.URL.Path)
}
''')
        self.assertEqual([], scan.sources)
        self.assertFalse(any(item.dataflow_verified for item in pack.candidates))

    def test_discarded_result_does_not_connect_unrelated_range_values(self) -> None:
        """真实项目回归：丢弃响应内容不能经另一个 `_` 连接到循环值并回流请求地址。"""
        _, _, pack = self.analyze('''package main
import ("net/http"; "io")
func fetch(client http.Client, address string) (string, error) {
    response, err := client.Get(address)
    content, err := io.ReadAll(response.Body)
    return string(content), err
}
func run(client http.Client){
    addresses := []string{"http://trusted/"}
    for _, address := range addresses {
        _, err := fetch(client, address)
        _ = err
    }
}
''')
        self.assertFalse(any(item.dataflow_verified for item in pack.candidates))

    def test_discarded_return_preserves_second_component_position(self) -> None:
        """空接收槽保留序号；只接收 error 不等于接收内容，不发生位置左移。"""
        source = '''package main
import ("net/http"; "os/exec")
func read(r *http.Request)(string,error){ return r.FormValue("cmd"),nil }
func run(r *http.Request){
    _, err := read(r)
    exec.Command(err)
    value, _ := read(r)
    exec.Command(value)
}
'''
        _, _, pack = self.analyze(source)
        self.assertEqual({8}, {pack.facts[item.sink_fact_id].location.line for item in pack.candidates if item.dataflow_verified})
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.go").write_text(source, encoding="utf-8")
            graph = ProgramGraphService().analyze(directory)
        calls = [call for function in graph.functions.values() for node in function.nodes.values() for call in node.calls if call.name == "read"]
        self.assertEqual([["", "err"], ["value", ""]], [call.result_targets for call in calls])


if __name__ == "__main__":
    unittest.main()
