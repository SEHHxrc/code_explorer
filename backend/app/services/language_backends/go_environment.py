"""Go 原生工具环境的白名单配置；不属于语言无关的语义事实索引。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re

from .worker import run_worker

_CACHE_KEYS = ("GOPATH", "GOMODCACHE", "GOCACHE")
LOCAL_ENVIRONMENT = Path(__file__).resolve().parents[4] / ".goenv"
_OFFLINE = {
    "GOTOOLCHAIN": "local", "GOPACKAGESDRIVER": "off", "GOPROXY": "off",
    "GOSUMDB": "off", "GOWORK": "off", "GOFLAGS": "-mod=readonly",
    "CGO_ENABLED": "0", "GOCACHEPROG": "", "GOAUTH": "off", "GOVCS": "*:off",
}


@dataclass(frozen=True)
class GoAnalysisConfig:
    """仅允许缓存路径、目标平台和构建标签；不接受任意 GOFLAGS 或命令。"""

    target_os: str = ""
    target_arch: str = ""
    build_tags: tuple[str, ...] = ()
    module_cache: str = ""
    build_cache: str = ""

    @classmethod
    def from_environment(cls) -> GoAnalysisConfig:
        """读取服务端应用配置；不加载目标仓库 .env，不修改宿主 Go 配置。"""
        return cls(
            target_os=os.getenv("CODE_EXPLORER_GO_TARGET_OS", "").strip(),
            target_arch=os.getenv("CODE_EXPLORER_GO_TARGET_ARCH", "").strip(),
            build_tags=tuple(filter(None, re.split(r"[,\s]+", os.getenv("CODE_EXPLORER_GO_BUILD_TAGS", "").strip()))),
            module_cache=os.getenv("CODE_EXPLORER_GO_MODULE_CACHE", "").strip(),
            build_cache=os.getenv("CODE_EXPLORER_GO_BUILD_CACHE", "").strip(),
        )

    def prepare(self, go: str, *, project_root: Path, tool_directory: Path) -> tuple[dict[str, str], dict[str, object]]:
        """安全查询宿主有效缓存，返回分析进程环境和不含宿主路径的审计摘要。"""
        for target in (self.target_os, self.target_arch):
            if target and re.fullmatch(r"[a-z0-9]{1,16}", target) is None:
                raise ValueError("invalid_go_build_target")
        tags = sorted(set(self.build_tags))
        if len(tags) > 32 or any(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.]{0,63}", tag) is None for tag in tags):
            raise ValueError("invalid_go_build_tags")
        # 保留非 Go 宿主环境；分析进程不会继承 Go 插件、认证命令、实验开关或自定义工具链。
        environment = {key: value for key, value in os.environ.items()
                       if not key.upper().startswith(("GO", "CGO_"))}
        environment.update(_OFFLINE)
        probe_environment = dict(environment)
        # 只对 go env 读取用户配置；钩子/联网/工具链由进程变量覆盖，之后分析使用 GOENV=off。
        if "GOENV" in os.environ:
            probe_environment["GOENV"] = os.environ["GOENV"]
        for key in _CACHE_KEYS:
            if key in os.environ:
                probe_environment[key] = os.environ[key]
        response = run_worker(
            [go, "env", "-json", *_CACHE_KEYS, "GOHOSTOS", "GOHOSTARCH"], {},
            directory=tool_directory, environment=probe_environment,
            timeout_seconds=10, max_output_bytes=64 * 1024)
        effective = response.payload
        sources: dict[str, str] = {}
        for key in _CACHE_KEYS:
            override = {"GOMODCACHE": self.module_cache, "GOCACHE": self.build_cache}.get(key, "")
            local_cache = LOCAL_ENVIRONMENT / {"GOMODCACHE": "modules", "GOCACHE": "build"}.get(key, "")
            use_local = (not override and key != "GOPATH" and (LOCAL_ENVIRONMENT / "ready").is_file()
                         and local_cache.is_dir() and not local_cache.resolve().is_relative_to(project_root.resolve()))
            value = override or (str(local_cache.resolve()) if use_local else effective.get(key))
            if not isinstance(value, str) or not value or any(character in value for character in "\x00\r\n"):
                raise ValueError("invalid_go_cache_path")
            parts = value.split(os.pathsep) if key == "GOPATH" else [value]
            for part in parts:
                path = Path(part)
                if not path.is_absolute() or path.resolve().is_relative_to(project_root.resolve()):
                    raise ValueError("go_cache_must_be_outside_project")
            environment[key] = value
            sources[key] = "application_config" if override else ("project_local_environment" if use_local else "host_go_env")
        environment.update({"GOENV": "off", "GOOS": self.target_os or str(effective["GOHOSTOS"]),
                            "GOARCH": self.target_arch or str(effective["GOHOSTARCH"])})
        return environment, {
            "environment_policy_version": "1.0",
            "target_os": environment["GOOS"], "target_arch": environment["GOARCH"],
            "build_tags": tags, "cgo_enabled": False, "network": "disabled",
            "module_mode": "readonly", "workspace": "disabled", "cache_sources": sources,
            "cache_paths_sha256": hashlib.sha256(json.dumps(
                {key: environment[key] for key in _CACHE_KEYS}, sort_keys=True).encode()).hexdigest(),
        }
