"""命令预览服务；仅用于解析和只读审查，不应部署或执行。"""

import os
from fastapi import FastAPI

app = FastAPI()


def prepare(command: str, enabled: bool = False) -> tuple[str, bool]:
    """构造展示命令和执行标志。"""
    decorated = command + " --display"
    return decorated, enabled


@app.get("/execute")
def execute(command: str) -> dict:
    """接收命令并按标志执行。"""
    text, enabled = prepare(command, True)
    if enabled:
        os.system(text)
    return {"text": text}


@app.get("/preview")
def preview(command: str) -> dict:
    """返回命令预览。"""
    text, enabled = prepare(command)
    if enabled:
        os.system(text)
    return {"text": text}
