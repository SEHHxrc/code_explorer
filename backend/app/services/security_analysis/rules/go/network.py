"""Go 连接读取和拨号参数规则；监听配置不等于不可信输入或已证实的漏洞。"""

from ..base import CallRule, RulePack

GO_NETWORK_RULE_PACK = RulePack(
    name="go-net", version="1.0", languages=("go",),
    call_rules=(
        CallRule("GO-SOURCE-NET-READ", "source", "network_read", ("net.Conn.Read", "net.TCPConn.Read", "net.UnixConn.Read", "net.UDPConn.Read", "net.UDPConn.ReadFrom", "net.UDPConn.ReadFromUDP", "net.UDPConn.ReadFromUDPAddrPort", "net.PacketConn.ReadFrom"),
            languages=("go",), trust_class="untrusted", output_argument_positions=(0,),
            receiver_api_types=("net.Conn", "net.TCPConn", "net.UnixConn", "net.UDPConn", "net.PacketConn"),
            preconditions=("连接输入是否由攻击者控制取决于对端及部署；仅读取缓冲区被标为 may 写入，不传播字节数和错误返回。",)),
        CallRule("GO-SINK-NET-DIAL", "sink", "network_request", ("net.Dial", "net.DialTimeout", "net.Dialer.Dial"),
            languages=("go",), cwe="CWE-918", severity="medium", argument_role="sink", argument_positions=(1,)),
        CallRule("GO-SINK-NET-DIAL-CONTEXT", "sink", "network_request", ("net.Dialer.DialContext",),
            languages=("go",), cwe="CWE-918", severity="medium", argument_role="sink", argument_positions=(2,)),
    ),
)
