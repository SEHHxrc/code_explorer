"""开发启动的监听范围回归：不启动服务、不加载应用或读取真实配置及实验记录。"""

from __future__ import annotations

import shlex
import unittest
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from uvicorn.config import Config
from uvicorn.main import main
from uvicorn.supervisors.statreload import StatReload


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "backend" / "app"


class BackendReloadTests(unittest.TestCase):
    """避免重载扫描回退到包含 WSL 环境和上传项目的仓库根目录。"""

    def test_documented_reload_commands_limit_scope(self) -> None:
        """启动示例均应向真实 CLI 传入唯一的应用源码监听目录。"""
        for filename in ("README.md", "docs/AGENT_QUEUE.md"):
            commands = [shlex.split(line) for line in (PROJECT_ROOT / filename).read_text(encoding="utf-8").splitlines()
                        if line.startswith("uvicorn ") and "--reload" in line.split()]
            self.assertTrue(commands, filename)
            for command in commands:
                with self.subTest(document=filename, command=command):
                    self.assertIn("--reload-dir", command)
                    index = command.index("--reload-dir") + 1
                    self.assertEqual(APP_ROOT, (PROJECT_ROOT / command[index]).resolve())
                    command[index] = str(APP_ROOT)
                    with patch.object(import_module("uvicorn.main"), "run") as run:
                        result = CliRunner().invoke(main, command[1:])
                    self.assertEqual(0, result.exit_code, result.output)
                    run.assert_called_once()
                    self.assertTrue(run.call_args.kwargs["reload"])
                    self.assertEqual([str(APP_ROOT)], list(run.call_args.kwargs["reload_dirs"]))

    def test_stat_reloader_never_traverses_workspace_root_and_still_detects_changes(self) -> None:
        """实际 StatReload 仅扫描 backend/app，并仍能识别源码时间戳变化。"""
        config = Config("backend.app.main:app", reload=True, reload_dirs=[str(APP_ROOT)],
                        reload_delay=0, log_config=None)
        self.assertEqual([APP_ROOT], config.reload_dirs)
        reloader = StatReload(config, target=lambda sockets: None, sockets=[])
        original_rglob = Path.rglob

        def guarded_rglob(directory: Path, pattern: str) -> Iterator[Path]:
            """任何意外的仓库根扫描都模拟为本次 Windows 访问错误。"""
            if directory != APP_ROOT:
                raise OSError("禁止扫描 WSL 环境、虚拟环境或项目存储目录")
            return original_rglob(directory, pattern)

        with patch.object(Path, "rglob", guarded_rglob):
            self.assertIsNone(reloader.should_restart())
            source = (APP_ROOT / "main.py").resolve()
            self.assertIn(source, reloader.mtimes)
            reloader.mtimes[source] -= 1  # 只改测试重载器的缓存，不修改任何源码文件。
            self.assertEqual([source], reloader.should_restart())


if __name__ == "__main__":
    unittest.main()
