"""Go 安全规则包集合。"""

from .frameworks import GO_NET_HTTP_RULE_PACK
from .standard_library import GO_STANDARD_LIBRARY_PACK

GO_RULE_PACKS = (
    GO_STANDARD_LIBRARY_PACK,
    GO_NET_HTTP_RULE_PACK,
)

__all__ = [
    "GO_NET_HTTP_RULE_PACK",
    "GO_RULE_PACKS",
    "GO_STANDARD_LIBRARY_PACK",
]
