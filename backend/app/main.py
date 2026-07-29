# -*- coding: utf-8 -*-
from fastapi import FastAPI
from backend.app.api.project import router as project_router
from backend.app.middleware.exception_handler import setup_exception_handler
from backend.app.models import init_db

# 初始化数据库
init_db()

app = FastAPI(title="AI Code Insight Assistant", version="1.0.0")

# 挂载全局异常与输出脱敏中间件
setup_exception_handler(app)

# 挂载路由
app.include_router(project_router)


@app.get("/")
def read_root():
  return {"code": 200, "message": "Backend service is running securely."}