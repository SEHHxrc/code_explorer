"""大模型配置状态、连通性探测和可见模型目录路由。"""

from __future__ import annotations

import math
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.core.deps import get_current_user
from backend.app.llm.diagnostics import list_available_models, probe_model_connection
from backend.app.llm.registry import get_model_configuration, get_model_limits

router = APIRouter(prefix="/api/models", tags=["Models"])
MODEL_PROBE_COOLDOWN_SECONDS = 10.0
_model_probe_last_at: dict[str, float] = {}


class ModelProbeRequest(BaseModel):
    """模型连通性探测输入；模型为空时使用服务端默认配置。"""

    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )


def _model_probe_retry_after(user_id: str) -> int | None:
    """记录单进程用户探测时间；冷却期内返回需等待的秒数。"""
    now = time.monotonic()
    previous = _model_probe_last_at.get(user_id)
    if previous is not None and now - previous < MODEL_PROBE_COOLDOWN_SECONDS:
        return max(1, math.ceil(MODEL_PROBE_COOLDOWN_SECONDS - (now - previous)))
    _model_probe_last_at[user_id] = now
    if len(_model_probe_last_at) > 2_048:
        cutoff = now - MODEL_PROBE_COOLDOWN_SECONDS
        for key, timestamp in list(_model_probe_last_at.items()):
            if timestamp < cutoff:
                _model_probe_last_at.pop(key, None)
    return None


@router.get("/status")
async def get_model_status(
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """输出不含密钥的模型配置状态；本接口不会产生外部请求。"""
    del current_user
    config = get_model_configuration()
    limits = get_model_limits()
    return {
        "code": 200,
        "data": {
            "configured": config.configured,
            "provider": config.provider if config.configured else None,
            "model": config.model if config.configured else None,
            "live_checked": False,
            "max_context_chars": limits.max_context_chars,
            "max_output_tokens": limits.max_output_tokens,
            "max_input_tokens": limits.max_input_tokens,
            "context_window_tokens": limits.context_window_tokens,
            "capacity_source": "operator_configured" if limits.context_window_tokens else "unknown",
            "tokenizer": limits.tokenizer,
            "transport_secure": config.base_url.startswith("https://") if config.configured else None,
        },
    }


@router.post("/probe")
async def probe_model(
    request: ModelProbeRequest | None = None,
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """执行用户显式触发的最小生成请求并返回安全诊断。"""
    retry_after = _model_probe_retry_after(current_user["user_id"])
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail=f"模型连接测试冷却中，请在 {retry_after} 秒后重试。",
            headers={"Retry-After": str(retry_after)},
        )
    return {
        "code": 200,
        "message": "Model connectivity probe completed.",
        "data": await probe_model_connection(request.model if request else None),
    }


@router.get("")
async def get_available_models(
    current_user: dict[str, str] = Depends(get_current_user),
) -> dict[str, Any]:
    """返回当前模型凭据通过兼容模型目录接口可见的模型 ID。"""
    del current_user
    return {
        "code": 200,
        "message": "Visible model lookup completed.",
        "data": await list_available_models(),
    }
