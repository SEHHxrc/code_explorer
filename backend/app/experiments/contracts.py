"""依赖图 A/B 配对实验的 API 与领域契约。"""

from typing import Literal

from pydantic import BaseModel, Field


class ComparisonRequest(BaseModel):
    """同一问题、同一模型预算下创建一组盲态配对运行。"""

    question: str = Field(min_length=1, max_length=8000)
    max_steps: int = Field(default=4, ge=1, le=6)


class LaneScores(BaseModel):
    correctness: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    evidence: int = Field(ge=1, le=5)
    hallucination_control: int = Field(ge=1, le=5)


class BlindReviewRequest(BaseModel):
    """揭盲前提交的左右答案偏好与独立评分。"""

    preferred_lane: Literal["left", "right", "tie"]
    left: LaneScores
    right: LaneScores
    notes: str = Field(default="", max_length=4000)


class ExperimentError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.public_message = message
        self.status_code = status_code