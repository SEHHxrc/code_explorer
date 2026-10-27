"""静态安全证据 A/B 配对实验的 API 与领域契约。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from backend.app.agents.contracts import DEFAULT_INVESTIGATION_ROUNDS, MAX_INVESTIGATION_ROUNDS


class ComparisonRequest(BaseModel):
    """同一问题、同一模型预算下创建一组盲态配对运行。"""

    question: str = Field(min_length=1, max_length=8000)
    max_steps: int = Field(default=DEFAULT_INVESTIGATION_ROUNDS, ge=1, le=MAX_INVESTIGATION_ROUNDS)


class LaneScores(BaseModel):
    """三个独立人工维度，不计算可被条目数量抵消的总分；旧记录原样读取。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    discovery_breadth: int = Field(ge=1, le=5)
    chain_verification: int = Field(ge=1, le=5)
    conclusion_credibility: int = Field(ge=1, le=5)


class BlindReviewRequest(BaseModel):
    """揭盲前提交的左右答案偏好与独立评分。"""

    preferred_lane: Literal["left", "right", "tie"]
    rubric_version: Literal["independent-review-v2"] = "independent-review-v2"
    left: LaneScores
    right: LaneScores
    notes: str = Field(default="", max_length=4000)


class ExperimentError(Exception):
    """携带安全公开信息和 HTTP 状态的实验领域错误。"""
    def __init__(self, message: str, status_code: int) -> None:
        """保存可安全公开的实验错误消息及对应 HTTP 状态码。"""
        super().__init__(message)
        self.public_message = message
        self.status_code = status_code
