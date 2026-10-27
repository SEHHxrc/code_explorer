"""任务写回服务；仅用于解析和只读审查，不应部署或执行。"""

import os
from fastapi import FastAPI

app = FastAPI()


class Job:
    """保存任务属性。"""

    def __init__(self) -> None:
        """建立初始属性。"""
        self.command = "printf ready"
        self.enabled = False


def fill_job(job: Job, command: str, enabled: bool) -> None:
    """更新任务属性。"""
    job.command = command
    job.enabled = enabled


def fill_record(record: dict, command: str, enabled: bool) -> None:
    """更新任务记录。"""
    record["command"] = command
    record["enabled"] = enabled


@app.get("/job")
def run_job(command: str) -> dict:
    """建立任务后执行。"""
    job = Job()
    fill_job(job, command, True)
    if job.enabled:
        os.system(job.command)
    return {"status": "ok"}


@app.get("/job-preview")
def job_preview(command: str) -> dict:
    """返回任务预览。"""
    job = Job()
    fill_job(job, command, False)
    if job.enabled:
        os.system(job.command)
    return {"command": job.command}


@app.get("/record")
def run_record(command: str) -> dict:
    """建立记录后执行。"""
    record = {"command": "printf ready", "enabled": False}
    fill_record(record, command, True)
    if record["enabled"]:
        os.system(record["command"])
    return {"status": "ok"}


@app.get("/record-preview")
def record_preview(command: str) -> dict:
    """返回记录预览。"""
    record = {"command": "printf ready", "enabled": True}
    fill_record(record, command, False)
    if record["enabled"]:
        os.system(record["command"])
    return record
