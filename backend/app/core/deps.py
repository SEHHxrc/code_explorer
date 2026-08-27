# -*- coding: utf-8 -*-
import re

from fastapi import Header, HTTPException


async def get_current_user(x_user_id: str = Header(default="default_user")):
  """预留的权限与用户上下文依赖。

  初期通过 Header 简单的隔离；后期可升级为 JWT Token 解析。
  """
  user_id = x_user_id or "default_user"
  if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user_id):
    raise HTTPException(status_code=400, detail="Invalid X-User-Id header.")
  return {"user_id": user_id, "roles": ["user"]}
