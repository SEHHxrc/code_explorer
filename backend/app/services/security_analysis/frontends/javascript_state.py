"""历史状态适配入口；唯一实现位于 language_adapters。"""

from ..language_adapters.javascript_state import collect_javascript_value_boundaries

__all__ = ["collect_javascript_value_boundaries"]
