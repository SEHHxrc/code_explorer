"""源码只遮蔽敏感字面量；运行日志另处理裸凭据，均保留行边界。不提供通用 DLP 保证。"""

import re
from pathlib import PurePosixPath

REDACTION_VERSION = "source-literals-v2"
_KEY = r"(?:[A-Za-z_][\w-]*[_-])?(?:api[_-]?key|secret|token|password)"
_PREFIX = rf"(?<![\w-])(?:[\"']?{_KEY}[\"']?)[ \t]*[:=][ \t]*"
_LITERAL = re.compile(
    _PREFIX + r"(?P<quote>[\"'])(?P<value>(?:\\[^\r\n]|(?!(?P=quote))[^\r\n\\])*)(?P=quote)",
    re.IGNORECASE,
)
_MULTILINE_LITERAL = re.compile(
    _PREFIX + r"(?P<quote>\"{3}|'{3})(?P<value>.*?)(?P=quote)", re.IGNORECASE | re.DOTALL,
)
_BARE = re.compile(_PREFIX + r"(?P<value>\[REDACTED\]|[^\s,\"'\)\]\}]+)", re.IGNORECASE)
_API_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
    r"(?:-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\Z)", re.DOTALL,
)


def _mask_lines(value: str) -> str:
    """遮蔽多行敏感值而保留 LF/CRLF 与源码行号前缀，避免相邻语句合行。"""
    return re.sub(r"[^\r\n]+", lambda match: re.sub(
        r"^(\d+:[ \t]*)?.+", lambda line: (line[1] or "") + "[REDACTED]", match[0]), value)


def _mask_value(match: re.Match[str]) -> str:
    """仅替换敏感值；保留字段名、赋值符、引号与后续标点。"""
    start, end = match.span("value")
    masked = "[REDACTED]" if start != end else ""
    if "\n" in match["value"] or "\r" in match["value"]:
        masked = _mask_lines(match["value"])
    return match[0][:start - match.start()] + masked + match[0][end - match.start():]


def redact_source(text: str, *, path: str = "") -> str:
    """源码只遮蔽字面量；明确的配置文件也遮蔽裸值，均不改变行结构。"""
    text = _PRIVATE_KEY.sub(lambda match: _mask_lines(match[0]), text)
    text = _MULTILINE_LITERAL.sub(_mask_value, text)
    text = _API_KEY.sub("[REDACTED]", _LITERAL.sub(_mask_value, text))
    name = PurePosixPath(path.replace("\\", "/")).name.lower()
    if name.startswith(".env") or PurePosixPath(name).suffix in {".env", ".ini", ".cfg", ".properties", ".yaml", ".yml", ".toml"}:
        text = _BARE.sub(_mask_value, text)
    return text


def redact_secrets(text: str) -> str:
    """运行文本遮蔽：除源码字面量外，继续遮蔽 password=裸值 等日志凭据。"""
    return _BARE.sub(_mask_value, redact_source(text))
