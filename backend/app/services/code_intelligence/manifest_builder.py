# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from backend.app.schemas.manifest import Entrypoint, Evidence, ProjectManifest


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
    return path.relative_to(root).as_posix()


def _read_text(path: Path, limit: int = 512 * 1024) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


class ProjectManifestBuilder:
    """不依赖大模型、根据文件和依赖图构建带证据的项目事实清单。"""

    def __init__(self, project_root: str):
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
        build_commands: list[str] = []
        run_commands: list[str] = []
        test_commands: list[str] = []
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
                    build_commands, run_commands, test_commands, evidence,
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

            self._detect_code_entrypoints(relative, text, entrypoints)
            self._detect_container_entrypoints(relative, name, text, entrypoints, run_commands)

        run_commands.extend(
            entry.command for entry in entrypoints
            if entry.kind == "web_app" and entry.command
        )

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
            modules=modules,
            graph_summary=graph_summary,
            evidence=evidence[:100],
            warnings=[] if files else ["项目中没有发现可分析文件"],
        )

    def _collect_files(self) -> list[Path]:
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
        build_commands: list[str], run_commands: list[str], test_commands: list[str],
        evidence: list[Evidence],
    ) -> None:
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
            npm_command = f"npm run {script_name}"
            if script_name in {"dev", "start", "serve", "preview"}:
                run_commands.append(npm_command)
                entrypoints.append(Entrypoint(
                    kind="package_script", name=script_name, path=relative,
                    command=npm_command, confidence=1.0,
                ))
            elif script_name in {"build", "compile"}:
                build_commands.append(npm_command)
            elif script_name.startswith("test"):
                test_commands.append(npm_command)

    def _detect_code_entrypoints(self, relative: str, text: str, entrypoints: list[Entrypoint]) -> None:
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
                if kind == "web_app" and framework == "FastAPI":
                    module = relative[:-3].replace("/", ".")
                    command = f"uvicorn {module}:{name} --reload"
                entrypoints.append(Entrypoint(
                    kind=kind, name=name, path=relative,
                    line=_line_of(text, match.start()), command=command,
                    framework=framework, confidence=1.0,
                ))

    def _detect_container_entrypoints(
        self, relative: str, name: str, text: str,
        entrypoints: list[Entrypoint], run_commands: list[str],
    ) -> None:
        if name == "dockerfile" or name.startswith("dockerfile."):
            for match in re.finditer(r"^\s*(CMD|ENTRYPOINT)\s+(.+)$", text, re.M | re.I):
                command = match.group(2).strip()
                entrypoints.append(Entrypoint(
                    kind="container", name=match.group(1).upper(), path=relative,
                    line=_line_of(text, match.start()), command=command, confidence=1.0,
                ))
                run_commands.append(f"docker build -t {self.root.name} .")

    def _build_modules(self, files: list[Path]) -> list[dict[str, Any]]:
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
        seen: set[tuple[str, str, int | None, str | None]] = set()
        result: list[Entrypoint] = []
        for item in entrypoints:
            key = (item.kind, item.path, item.line, item.command)
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result
