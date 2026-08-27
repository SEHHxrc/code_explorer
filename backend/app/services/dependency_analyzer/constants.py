# -*- coding: utf-8 -*-
"""多语言分析器共享的语言、符号和图谱常量。"""
from __future__ import annotations

import builtins as _py_builtins
import sys

SKIP_CHILDREN = 1

# ---------------------------------------------------------------------------
# 内置符号表
# ---------------------------------------------------------------------------

PY_BUILTINS = frozenset(name for name in dir(_py_builtins) if not name.startswith("__"))
PY_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()) or ())
# str/list/dict/set/... 的实例方法：接收者类型未知时用来判定“这是内置调用”
PY_TYPE_METHODS = frozenset(
    name for tp in (str, bytes, list, dict, set, tuple, int, float)
    for name in dir(tp) if not name.startswith("_")
)

GO_BUILTINS = frozenset("""
append cap clear close complex copy delete imag len make max min new panic print
println real recover
""".split())

GO_STDLIB = frozenset("""
bufio bytes container context crypto database embed encoding errors expvar flag fmt
go hash html image io log math mime net os path plugin reflect regexp runtime slices
sort strconv strings sync syscall testing text time unicode unsafe maps cmp
""".split())

C_BUILTINS = frozenset("""
printf fprintf sprintf snprintf scanf sscanf fscanf puts putchar getchar gets
malloc calloc realloc free memcpy memmove memset memcmp
strlen strcpy strncpy strcat strncat strcmp strncmp strchr strrchr strstr strdup
strtok strtol strtoul strtod atoi atol atof
fopen fclose fread fwrite fseek ftell rewind fflush feof ferror remove rename
exit abort atexit system getenv qsort bsearch rand srand abs labs
open close read write lseek stat fstat
assert va_start va_end va_arg sizeof offsetof
""".split())

CPP_BUILTINS = C_BUILTINS | frozenset("""
new delete static_cast dynamic_cast const_cast reinterpret_cast typeid
move forward make_shared make_unique swap begin end size to_string
""".split())

JAVA_BUILTINS = frozenset("""
println print printf toString equals hashCode getClass valueOf format
""".split())

JAVA_TYPE_METHODS = frozenset("""
toString equals hashCode length size isEmpty get put add remove contains containsKey
indexOf charAt substring toUpperCase toLowerCase trim split join replace startsWith
endsWith append stream forEach map filter collect iterator next hasNext keySet values
entrySet clear sort compareTo intValue doubleValue parseInt parseDouble name ordinal
""".split())

JAVA_STD_PREFIXES = ("java.", "javax.", "sun.", "jdk.")

RUST_BUILTINS = frozenset("""
println print eprintln eprint format vec write writeln panic assert assert_eq
assert_ne debug_assert todo unimplemented unreachable matches dbg include_str
Some None Ok Err Box Vec String drop
""".split())

RUST_STD_ROOTS = frozenset({"std", "core", "alloc", "proc_macro", "test"})

RUST_STD_TYPES = frozenset("""
String Vec Box Option Result HashMap HashSet BTreeMap VecDeque Rc Arc RefCell Cell
Mutex RwLock Path PathBuf File Duration Instant Iterator
""".split())

RUST_TYPE_METHODS = frozenset("""
clone unwrap unwrap_or unwrap_or_else expect to_string to_owned into from as_str as_ref
as_mut iter iter_mut into_iter collect map filter fold and_then or_else ok_or ok err
len is_empty push push_str pop insert remove get get_mut contains contains_key keys values
borrow borrow_mut lock read write send recv join split trim parse format next take
""".split())

JS_GLOBALS = frozenset("""
console JSON Math Object Array String Number Boolean Promise Symbol Map Set
WeakMap WeakSet Date RegExp Error TypeError RangeError Reflect Proxy globalThis
window document navigator localStorage sessionStorage process Buffer
""".split())

JS_BUILTINS = frozenset("""
parseInt parseFloat isNaN isFinite encodeURIComponent decodeURIComponent
setTimeout setInterval clearTimeout clearInterval fetch require structuredClone
alert prompt confirm queueMicrotask
""".split())

JS_STDLIB = frozenset("""
assert async_hooks buffer child_process cluster console constants crypto dgram
diagnostics_channel dns domain events fs http http2 https module net os path
perf_hooks process punycode querystring readline repl stream string_decoder sys
timers tls trace_events tty url util v8 vm wasi worker_threads zlib test
""".split())

JS_TYPE_METHODS = frozenset("""
map filter forEach reduce find findIndex some every includes indexOf lastIndexOf
push pop shift unshift slice splice concat join reverse sort flat flatMap
split replace replaceAll trim trimStart trimEnd toUpperCase toLowerCase startsWith
endsWith padStart padEnd charAt charCodeAt substring substr repeat match matchAll
then catch finally toString valueOf hasOwnProperty keys values entries assign
toFixed parse stringify bind call apply
""".split())

# class-like 节点的统一抽象
CLASS_LIKE = frozenset({"class", "struct", "interface", "enum", "trait", "union", "namespace", "type"})

# Definition.kind -> 前端大纲 symbol.kind（沿用旧版取值，保证前端零改动）
SYMBOL_KIND = {
    "class": "class", "struct": "class", "interface": "class", "enum": "class",
    "trait": "class", "union": "class", "namespace": "class", "type": "class",
    "function": "function", "method": "method", "constructor": "method",
    "field": "property", "variable": "constant", "constant": "constant", "macro": "constant",
}

# Definition.kind -> 图谱节点 level（前端按 level 着色分层）
GRAPH_LEVEL = {
    "class": "class", "struct": "class", "interface": "class", "enum": "class",
    "trait": "class", "union": "class", "namespace": "class", "type": "class",
    "function": "function", "method": "method", "constructor": "method",
    "field": "property", "variable": "variable", "constant": "variable", "macro": "variable",
}

# 无分析价值的临时变量名（仅用于过滤 *全局/字段* 噪音，局部变量本就不建节点）
NOISE_NAMES = frozenset({
    "i", "j", "k", "n", "x", "y", "z", "e", "ex", "f", "fp", "c", "s", "t", "v",
    "tmp", "temp", "ret", "res", "val", "err", "ok", "buf", "idx", "_",
})

IGNORED_DIRS = frozenset({
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache",
    "venv", ".venv", "env", "dist", "build", "out", "target", "vendor", "third_party",
    ".idea", ".vscode", ".next", ".nuxt", "coverage", "bin", "obj", "Pods", ".tox",
})

EXT_MAP = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c++": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp", ".h++": "cpp",
}

C_HEADER_EXTS = frozenset({".h", ".hpp", ".hh", ".hxx", ".h++"})

# 出现在 .h 里即可判定为 C++ 的标志
CPP_MARKERS = (b"namespace ", b"class ", b"template<", b"template <", b"public:", b"private:",
               b"protected:", b"::", b"std::", b"virtual ", b"operator", b"nullptr")

# 表示“该变量的类型 = 某次调用的结果”，阶段三再用返回值类型回填
CALL_TYPE_PREFIX = "@call:"

__all__ = [name for name in globals() if not name.startswith('__')]
