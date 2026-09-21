# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import shlex
from collections import Counter
from pathlib import Path
from typing import Any

from backend.app.schemas.manifest import CommandFact, Entrypoint, Evidence, ProjectManifest


LANGUAGE_BY_EXTENSION = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript",
    ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".vue": "Vue",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".go": "Go", ".rs": "Rust", ".c": "C", ".h": "C/C++",
    ".cc": "C++", ".cpp": "C++", ".hpp": "C++", ".cs": "C#",
    ".php": "PHP", ".rb": "Ruby", ".swift": "Swift",
}

IGNORED_DIRS = {
    ".git", ".idea", ".vscode", ".venv", "venv", "node_modules",
    "dist", "build", "__pycache__", ".code_explorer",
}

FRAMEWORK_RULES = (
    ("FastAPI", {".py"}, re.compile(r"\bFastAPI\s*\(")),
    ("Flask", {".py"}, re.compile(r"\bFlask\s*\(")),
    ("Django", {".py"}, re.compile(r"\bDJANGO_SETTINGS_MODULE\b|\burlpatterns\s*=")),
    ("Spring Boot", {".java", ".kt", ".kts"}, re.compile(r"@SpringBootApplication\b")),
    ("Express", {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}, re.compile(r"\bexpress\s*\(\s*\)")),
    ("NestJS", {".js", ".ts"}, re.compile(r"@Module\s*\(|NestFactory\.create")),
)


def _rel(path: Path, root: Path) -> str:
    """把文件路径转换为相对项目根目录的规范路径。"""
    return path.relative_to(root).as_posix()


def _read_text(path: Path, limit: int = 512 * 1024) -> str:
    """按字符上限容错读取文本文件。"""
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _line_of(text: str, offset: int) -> int:
    """返回指定文本首次出现时的单基行号。"""
    return text.count("\n", 0, offset) + 1


def _dedupe(values: list[str]) -> list[str]:
    """按稳定顺序去除字符串列表中的重复项。"""
    return list(dict.fromkeys(value for value in values if value))


class ProjectManifestBuilder:
    """不依赖大模型、根据文件和依赖图构建带证据的项目事实清单。"""

    def __init__(self, project_root: str) -> None:
        """输入项目根目录、文件树与依赖图，初始化 Manifest 构建器。"""
        self.root = Path(project_root).resolve()

    def build(self, dependency_graph: dict[str, Any] | None = None) -> ProjectManifest:
        """输入可选依赖图，输出语言、框架、入口、命令、模块和证据组成的 manifest。"""
        files = self._collect_files()
        language_counts = Counter(
            LANGUAGE_BY_EXTENSION[path.suffix.lower()]
            for path in files if path.suffix.lower() in LANGUAGE_BY_EXTENSION
        )
        frameworks: list[str] = []
        package_managers: list[str] = []
        entrypoints: list[Entrypoint] = []
        commands: list[CommandFact] = []
        suggested_commands: list[str] = []
        evidence: list[Evidence] = []

        for path in files:
            relative = _rel(path, self.root)
            name = path.name.lower()
            text = _read_text(path)

            for framework, extensions, pattern in FRAMEWORK_RULES:
                if path.suffix.lower() not in extensions:
                    continue
                match = pattern.search(text)
                if match:
                    frameworks.append(framework)
                    evidence.append(Evidence(
                        path=relative, line=_line_of(text, match.start()),
                        detail=f"检测到 {framework} 框架标识",
                    ))

            if name == "package.json":
                package_managers.append("npm")
                self._inspect_package_json(
                    path, text, frameworks, entrypoints,
                    commands, suggested_commands, evidence,
                )
            elif name in {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg"}:
                package_managers.append("pip")
            elif name == "poetry.lock":
                package_managers.append("Poetry")
            elif name == "uv.lock":
                package_managers.append("uv")
            elif name == "pnpm-lock.yaml":
                package_managers.append("pnpm")
            elif name == "yarn.lock":
                package_managers.append("Yarn")
            elif name == "pom.xml":
                package_managers.append("Maven")
            elif name in {"build.gradle", "build.gradle.kts"}:
                package_managers.append("Gradle")
            elif name == "go.mod":
                package_managers.append("Go Modules")
            elif name == "cargo.toml":
                package_managers.append("Cargo")

            self._detect_code_entrypoints(
                relative, text, entrypoints, commands, suggested_commands,
            )
            self._detect_deployment_commands(
                relative, name, text, entrypoints, commands, suggested_commands,
            )

        # 兼容旧的字符串字段，但其中只保留配置中直接观察到的命令。
        # 系统生成的运行建议进入 suggested_commands，绝不伪装成项目事实。
        run_commands = _dedupe([
            item.command for item in commands
            if item.origin == "observed" and item.purpose in {"serve", "run", "worker"}
        ])
        build_commands = _dedupe([
            item.command for item in commands
            if item.origin == "observed" and item.purpose == "build"
        ])
        test_commands = _dedupe([
            item.command for item in commands
            if item.origin == "observed" and item.purpose == "test"
        ])

        modules = self._build_modules(files)
        graph_summary = self._summarize_graph(dependency_graph or {})
        return ProjectManifest(
            project_name=self.root.name,
            languages=[name for name, _ in language_counts.most_common()],
            frameworks=_dedupe(frameworks),
            package_managers=_dedupe(package_managers),
            entrypoints=self._dedupe_entrypoints(entrypoints),
            build_commands=_dedupe(build_commands),
            run_commands=_dedupe(run_commands),
            test_commands=_dedupe(test_commands),
            commands=self._dedupe_commands(commands),
            suggested_commands=_dedupe(suggested_commands),
            modules=modules,
            graph_summary=graph_summary,
            evidence=evidence[:100],
            warnings=[] if files else ["项目中没有发现可分析文件"],
        )

    def _collect_files(self) -> list[Path]:
        """收集符合大小、扩展名和忽略规则的源码文件。"""
        files: list[Path] = []
        for current_root, dirs, names in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith("."))
            for name in sorted(names):
                path = Path(current_root) / name
                if not path.is_symlink():
                    files.append(path)
        return files

    def _inspect_package_json(
        self, path: Path, text: str, frameworks: list[str], entrypoints: list[Entrypoint],
        commands: list[CommandFact], suggested_commands: list[str],
        evidence: list[Evidence],
    ) -> None:
        """读取 package.json 并提取脚本与入口点信息。"""
        try:
            package = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return
        relative = _rel(path, self.root)
        dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
        framework_packages = {
            "vue": "Vue", "react": "React", "next": "Next.js", "nuxt": "Nuxt",
            "vite": "Vite", "express": "Express", "@nestjs/core": "NestJS",
        }
        for dependency, framework in framework_packages.items():
            if dependency in dependencies:
                frameworks.append(framework)
                evidence.append(Evidence(path=relative, detail=f"依赖 {dependency}"))
        scripts = package.get("scripts", {})
        for script_name, command in scripts.items():
            if not isinstance(command, str) or not command.strip():
                continue
            npm_command = f"npm run {script_name}"
            if script_name in {"dev", "start", "serve", "preview"}:
                purpose = "serve" if script_name in {"start", "serve"} else "run"
                entrypoints.append(Entrypoint(
                    kind="package_script", name=script_name, path=relative,
                    command=command, command_origin="observed",
                    suggested_command=npm_command, confidence=1.0,
                ))
            elif script_name in {"build", "compile"}:
                purpose = "build"
            elif script_name.startswith("test"):
                purpose = "test"
            else:
                purpose = "unknown"
            commands.append(self._command_fact(
                command,
                purpose=purpose,
                origin="observed",
                source_kind="package_script",
                path=relative,
                line=self._line_containing(text, f'"{script_name}"'),
            ))
            suggested_commands.append(npm_command)

    def _detect_code_entrypoints(
        self,
        relative: str,
        text: str,
        entrypoints: list[Entrypoint],
        commands: list[CommandFact],
        suggested_commands: list[str],
    ) -> None:
        """按框架和语言规则检测源码入口点。"""
        suffix = Path(relative).suffix.lower()
        patterns = (
            ("web_app", "FastAPI application", "FastAPI", {".py"}, re.compile(r"^(?P<name>\w+)\s*=\s*FastAPI\s*\(", re.M)),
            ("web_app", "Flask application", "Flask", {".py"}, re.compile(r"^(?P<name>\w+)\s*=\s*Flask\s*\(", re.M)),
            ("python_main", "Python module entry", None, {".py"}, re.compile(r"if\s+__name__\s*==\s*['\"]__main__['\"]\s*:")),
            ("java_main", "Java main", None, {".java"}, re.compile(r"public\s+static\s+void\s+main\s*\(")),
            ("spring_app", "Spring Boot application", "Spring Boot", {".java", ".kt", ".kts"}, re.compile(r"@SpringBootApplication\b")),
            ("go_main", "Go main", None, {".go"}, re.compile(r"^\s*func\s+main\s*\(\s*\)", re.M)),
            ("rust_main", "Rust main", None, {".rs"}, re.compile(r"^\s*fn\s+main\s*\(\s*\)", re.M)),
        )
        for kind, default_name, framework, extensions, pattern in patterns:
            if suffix not in extensions:
                continue
            match = pattern.search(text)
            if match:
                name = match.groupdict().get("name") or default_name
                command = None
                suggested_command = None
                if kind == "web_app" and framework == "FastAPI":
                    module = relative[:-3].replace("/", ".")
                    suggested_command = f"uvicorn {module}:{name} --reload"
                    suggested_commands.append(suggested_command)
                    commands.append(self._command_fact(
                        suggested_command,
                        purpose="serve",
                        origin="generated",
                        source_kind="framework_suggestion",
                        path=relative,
                        line=_line_of(text, match.start()),
                        execution_profile="development",
                        confidence="medium",
                    ))
                entrypoints.append(Entrypoint(
                    kind=kind, name=name, path=relative,
                    line=_line_of(text, match.start()), command=command,
                    suggested_command=suggested_command,
                    framework=framework, confidence=1.0,
                ))

    def _detect_deployment_commands(
        self,
        relative: str,
        name: str,
        text: str,
        entrypoints: list[Entrypoint],
        commands: list[CommandFact],
        suggested_commands: list[str],
    ) -> None:
        """从容器、进程管理器和部署配置中提取真实存在的启动命令。"""
        if name == "dockerfile" or name.startswith("dockerfile."):
            for match in re.finditer(r"^\s*(CMD|ENTRYPOINT)\s+(.+)$", text, re.M | re.I):
                command = match.group(2).strip()
                line = _line_of(text, match.start())
                entrypoints.append(Entrypoint(
                    kind="container", name=match.group(1).upper(), path=relative,
                    line=line, command=command, command_origin="observed", confidence=1.0,
                ))
                commands.append(self._command_fact(
                    command,
                    purpose="serve",
                    origin="observed",
                    source_kind="dockerfile_entrypoint",
                    path=relative,
                    line=line,
                    shell_interpreted=not command.lstrip().startswith("["),
                ))
            if re.search(r"^\s*(CMD|ENTRYPOINT)\s+", text, re.M | re.I):
                suggestion = f"docker build -t {self.root.name} ."
                suggested_commands.append(suggestion)
                commands.append(self._command_fact(
                    suggestion,
                    purpose="build",
                    origin="generated",
                    source_kind="container_build_suggestion",
                    path=relative,
                    confidence="medium",
                ))

        if name == "procfile":
            for match in re.finditer(r"^\s*([A-Za-z0-9_-]+)\s*:\s*(.+)$", text, re.M):
                process, command = match.group(1), match.group(2).strip()
                purpose = "serve" if process.casefold() == "web" else "worker"
                line = _line_of(text, match.start())
                commands.append(self._command_fact(
                    command,
                    purpose=purpose,
                    origin="observed",
                    source_kind="procfile",
                    path=relative,
                    line=line,
                    authority="service_manager",
                    shell_interpreted=True,
                ))
                entrypoints.append(Entrypoint(
                    kind="process", name=process, path=relative, line=line,
                    command=command, command_origin="observed", confidence=1.0,
                ))

        if name.endswith(".service"):
            for match in re.finditer(r"^\s*ExecStart\s*=\s*(.+)$", text, re.M | re.I):
                command = match.group(1).strip()
                line = _line_of(text, match.start())
                commands.append(self._command_fact(
                    command,
                    purpose="serve",
                    origin="observed",
                    source_kind="systemd_unit",
                    path=relative,
                    line=line,
                    authority="service_manager",
                ))
                entrypoints.append(Entrypoint(
                    kind="service", name="ExecStart", path=relative, line=line,
                    command=command, command_origin="observed", confidence=1.0,
                ))

        if name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
            for match in re.finditer(r"^\s*(command|entrypoint)\s*:\s*([^\n#]+)", text, re.M | re.I):
                command = match.group(2).strip().strip("'\"")
                if not command:
                    continue
                line = _line_of(text, match.start())
                commands.append(self._command_fact(
                    command,
                    purpose="serve",
                    origin="observed",
                    source_kind="compose_command",
                    path=relative,
                    line=line,
                    authority="service_manager",
                    shell_interpreted=isinstance(command, str) and not command.lstrip().startswith("["),
                ))
                entrypoints.append(Entrypoint(
                    kind="container", name=match.group(1), path=relative, line=line,
                    command=command, command_origin="observed", confidence=0.9,
                ))

    @staticmethod
    def _line_containing(text: str, needle: str) -> int | None:
        """返回文本片段所在行；找不到时返回 ``None``。"""
        offset = text.find(needle)
        return _line_of(text, offset) if offset >= 0 else None

    @staticmethod
    def _command_fact(
        command: str,
        *,
        purpose: str,
        origin: str,
        source_kind: str,
        path: str | None = None,
        line: int | None = None,
        authority: str = "unknown",
        shell_interpreted: bool | None = None,
        execution_profile: str = "unknown",
        confidence: str = "high",
    ) -> CommandFact:
        """把命令转换为带来源的结构化事实；不在此处进行风险判定。"""
        normalized = command.strip()
        argv: list[str] = []
        if normalized.startswith("["):
            try:
                parsed = json.loads(normalized)
                if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
                    argv = parsed
            except json.JSONDecodeError:
                argv = []
        if not argv:
            try:
                argv = shlex.split(normalized, posix=True)
            except ValueError:
                argv = []
        launcher = Path(argv[0]).name if argv else None
        if shell_interpreted is None:
            shell_interpreted = bool(re.search(r"(?:&&|\|\||[|;`]|\$\(|\$\{)", normalized))
        return CommandFact(
            command=normalized,
            purpose=purpose,
            launcher=launcher,
            argv=argv,
            origin=origin,
            source_kind=source_kind,
            path=path,
            line=line,
            execution_profile=execution_profile,
            authority=authority,
            shell_interpreted=shell_interpreted,
            confidence=confidence,
        )

    @staticmethod
    def _dedupe_commands(commands: list[CommandFact]) -> list[CommandFact]:
        """按命令、用途、来源和证据位置稳定去重。"""
        result: list[CommandFact] = []
        seen: set[tuple[str, str, str, str | None, int | None]] = set()
        for item in commands:
            key = (item.command, item.purpose, item.origin, item.path, item.line)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _build_modules(self, files: list[Path]) -> list[dict[str, Any]]:
        """按目录和语言汇总项目模块信息。"""
        counts: Counter[str] = Counter()
        for path in files:
            relative = path.relative_to(self.root)
            module = relative.parts[0] if len(relative.parts) > 1 else "."
            counts[module] += 1
        return [
            {"name": name, "path": name, "file_count": count}
            for name, count in counts.most_common(30)
        ]

    @staticmethod
    def _summarize_graph(graph: dict[str, Any]) -> dict[str, Any]:
        """汇总依赖图的节点、边和关系统计。"""
        nodes = graph.get("nodes", []) or []
        edges = graph.get("links", graph.get("edges", [])) or []
        relations = Counter(edge.get("relation", "unknown") for edge in edges)
        degrees: Counter[str] = Counter()
        for edge in edges:
            source = str(edge.get("source", ""))
            target = str(edge.get("target", ""))
            if source:
                degrees[source] += 1
            if target:
                degrees[target] += 1
        return {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "relations": dict(relations.most_common()),
            "central_nodes": [
                {"id": node_id, "degree": degree}
                for node_id, degree in degrees.most_common(20)
            ],
        }

    @staticmethod
    def _dedupe_entrypoints(entrypoints: list[Entrypoint]) -> list[Entrypoint]:
        """按入口类型、路径和符号去重。"""
        seen: set[tuple[str, str, int | None, str | None]] = set()
        result: list[Entrypoint] = []
        for item in entrypoints:
            key = (item.kind, item.path, item.line, item.command)
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result
