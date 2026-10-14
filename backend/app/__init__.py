"""应用包；未配置日志的嵌入调用不使用可能回显原始异常的 lastResort 处理器。"""

import logging

_logger = logging.getLogger(__name__)
_logger.addHandler(logging.NullHandler())
_logger.propagate = False
