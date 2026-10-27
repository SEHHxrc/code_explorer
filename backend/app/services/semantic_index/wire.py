"""紧凑交换协议的旧路径兼容入口；不保留旧解码器或自动协议回退。"""

from ..language_backends.wire import decode_table_result

__all__ = ["decode_table_result"]
