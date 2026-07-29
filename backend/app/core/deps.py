# -*- coding: utf-8 -*-
from fastapi import Header


async def get_current_user(x_user_id: str = Header(default="default_user")):
  """预留的权限与用户上下文依赖。

  初期通过 Header 简单的隔离；后期可升级为 JWT Token 解析。
  """
  return {"user_id": x_user_id or "default_user", "roles": ["user"]}