# -*- coding: utf-8 -*-
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.api.project import router as project_router
from backend.app.middleware.exception_handler import setup_exception_handler
from backend.app.models import init_db

# 初始化数据库
init_db()

app = FastAPI(title="AI Code Insight Assistant", version="1.0.0")

# 配置跨域，允许前端 5173 端口访问后端
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有前端源访问
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载全局异常与输出脱敏中间件
setup_exception_handler(app)

# 挂载路由
app.include_router(project_router)


@app.get("/")
def read_root():
  return {"code": 200, "message": "Backend service is running securely."}