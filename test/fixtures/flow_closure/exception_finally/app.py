"""清理和异常流程服务；仅用于解析和只读审查，不应部署或执行。"""

import os
from fastapi import FastAPI

app = FastAPI()


def pending(command: str) -> str:
    """在清理流程中更新局部状态。"""
    try:
        return command
    finally:
        command = "printf ready"


def replaced(command: str) -> str:
    """返回清理阶段的状态。"""
    try:
        return command
    finally:
        return "printf ready"


def fail() -> str:
    """模拟失败的准备操作。"""
    raise ValueError("not ready")


@app.get("/pending")
def run_pending(command: str) -> dict:
    """执行准备结果。"""
    result = pending(command)
    os.system(result)
    return {"status": "ok"}


@app.get("/replaced")
def run_replaced(command: str) -> dict:
    """执行最终状态。"""
    result = replaced(command)
    os.system(result)
    return {"status": "ok"}


@app.get("/caught")
def run_caught(command: str) -> dict:
    """在准备失败时处理原始命令。"""
    try:
        command = fail()
    except ValueError:
        os.system(command)
    return {"status": "ok"}
