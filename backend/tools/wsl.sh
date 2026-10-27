#!/usr/bin/env bash
# WSL 开发入口：仅选择 Linux 解释器/缓存，不安装依赖，不从 Windows 隐式转发命令。
set -euo pipefail
if [[ $# -eq 0 ]]; then
    echo '用法：bash backend/tools/wsl.sh python -m <模块> [参数...]' >&2
    exit 2
fi
workspace=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
environment="$workspace/.goenv/python-linux"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/code-explorer"
if [[ ! -x "$environment/bin/python" ]]; then
    echo '缺少 .goenv/python-linux；请按 Go 工具 README 准备独立 Linux 虚拟环境。' >&2
    exit 1
fi
export PATH="$environment/bin:$cache/runtimes/node-v24.18.0-linux-x64/bin:/snap/bin:$PATH"
export CODE_EXPLORER_GO_MODULE_CACHE="${CODE_EXPLORER_GO_MODULE_CACHE:-$cache/go/modules}"
export CODE_EXPLORER_GO_BUILD_CACHE="${CODE_EXPLORER_GO_BUILD_CACHE:-$cache/go/build}"
if [[ -z "${CODE_EXPLORER_PARSER_CACHE_DIR:-}" ]]; then
    parser_platform=$("$environment/bin/python" -c 'import platform; from importlib.metadata import version; print(version("tree-sitter-language-pack") + "/" + platform.system().lower() + "-" + platform.machine().lower())')
    export CODE_EXPLORER_PARSER_CACHE_DIR="$cache/tree-sitter/$parser_platform"
fi
cd -- "$workspace"
exec "$@"
