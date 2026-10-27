"""语言无关局部存储契约；值对象、堆分配和可取址标量单元不是同一种复制语义。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict


class StorageAllocation(BaseModel):
    """前端观察到的存储声明；类型布局与引用求解由消费者完成。"""

    model_config = ConfigDict(frozen=True)
    type_literal: str
    kind: Literal["value", "heap", "scalar_cell"] = "value"
