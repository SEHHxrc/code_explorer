"""Java 标准库和常见基础 API 的首批安全结构规则。"""

from __future__ import annotations

from ..base import CallRule, RulePack

JAVA_CORE_CALL_RULES: tuple[CallRule, ...] = (
    CallRule(
        "JAVA-SOURCE-ENV", "source", "environment_read",
        ("System.getenv", "System.getProperty"), languages=("java",),
        trust_class="external_configuration", match_suffix=True,
        result_role="source",
    ),
    CallRule(
        "JAVA-SOURCE-HTTP-REQUEST", "source", "http_request",
        (
            "HttpServletRequest.getParameter", "HttpServletRequest.getHeader",
            "HttpServletRequest.getQueryString", "HttpServletRequest.getInputStream",
        ),
        languages=("java",), trust_class="untrusted", match_suffix=True,
        result_role="source",
    ),
    CallRule(
        "JAVA-SOURCE-FILE-READ", "source", "file_read",
        (
            "Files.readString", "Files.readAllBytes", "Files.readAllLines",
            "BufferedReader.readLine",
        ),
        languages=("java",), trust_class="external_file", match_suffix=True,
        result_role="source", receiver_role="source",
    ),
    CallRule(
        "JAVA-SINK-PROCESS", "sink", "process_execution",
        ("Runtime.getRuntime().exec", "Runtime.exec"),
        languages=("java",), cwe="CWE-78", severity="high",
        match_suffix=True, argument_role="sink", argument_positions=(0,),
    ),
    CallRule(
        "JAVA-SINK-SQL", "sink", "sql_execution",
        (
            "Statement.execute", "Statement.executeQuery", "Statement.executeUpdate",
            "Statement.addBatch", "Connection.prepareStatement",
            "EntityManager.createNativeQuery", "JdbcTemplate.execute",
            "JdbcTemplate.query", "JdbcTemplate.update",
        ),
        languages=("java",), cwe="CWE-89", severity="high",
        match_suffix=True, argument_role="sink", argument_positions=(0,),
    ),
    CallRule(
        "JAVA-SINK-FILE-WRITE", "sink", "file_write",
        (
            "Files.write", "Files.writeString", "Files.delete", "Files.deleteIfExists",
            "Files.move", "FileOutputStream", "FileWriter",
        ),
        languages=("java",), cwe="CWE-22", severity="medium",
        match_suffix=True, argument_role="sink", argument_positions=(0,),
    ),
    CallRule(
        "JAVA-SINK-DESERIALIZE", "sink", "deserialization",
        ("ObjectInputStream.readObject", "XMLDecoder.readObject"),
        languages=("java",), cwe="CWE-502", severity="high",
        match_suffix=True, receiver_role="sink",
    ),
    CallRule(
        "JAVA-SINK-NETWORK", "sink", "network_request",
        ("URL.openConnection", "URL.openStream", "HttpClient.send", "HttpClient.sendAsync"),
        languages=("java",), cwe="CWE-918", severity="medium",
        match_suffix=True, argument_role="sink", argument_positions=(0,),
        receiver_role="sink",
    ),
    CallRule(
        "JAVA-SINK-WEAK-RANDOM", "sink", "security_sensitive_random",
        ("Random.nextInt", "Random.nextLong", "Random.nextBytes", "Math.random"),
        languages=("java",), cwe="CWE-330", severity="low", match_suffix=True,
    ),
    CallRule(
        "JAVA-GUARD-VALIDATION", "guard", "validation",
        ("validate", "verify", "check", "matches", "authorize", "authenticate"),
        languages=("java",), match_suffix=True,
        argument_role="guard", argument_positions=(0,),
    ),
)


JAVA_STANDARD_LIBRARY_PACK = RulePack(
    name="java-core",
    version="1.0",
    languages=("java",),
    call_rules=JAVA_CORE_CALL_RULES,
)
