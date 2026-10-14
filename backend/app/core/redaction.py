"""源码、工具观察和审计共用的敏感文本遮蔽；不提供通用 DLP 保证。"""

import re

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|token|password)[\"']?\s*[:=]\s*(['\"]?)[^\s,'\"]+\2"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.DOTALL),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*"),
)


def redact_secrets(text: str) -> str:
    """按统一规则遮蔽常见凭据和私钥，返回不执行、不写入的纯文本。"""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text
