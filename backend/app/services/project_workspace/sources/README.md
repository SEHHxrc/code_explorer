# 项目来源适配器

本目录只负责把外部来源获取到一次工作区操作的暂存目录，不负责发布或静态分析。

| 文件/入口 | 输入 | 关键约束与输出 |
| --- | --- | --- |
| `git.py` / `validate_repo_url(repo_url, policy)` | 仓库 URL 与策略 | 校验协议、主机和 URL 形状；失败抛出 `SourceValidationError`。 |
| `git.py` / `GitProjectSource.acquire(repo_url, destination)` | URL 与空暂存目录 | 以受限环境和超时执行浅克隆，输出不含凭据的来源标签。 |
| `zip.py` / `ZipProjectSource.acquire(file_obj, filename, operation_root, destination)` | 上传流、文件名和操作目录 | 有界写入、逐项校验并解压，输出来源标签。 |

ZIP 适配器限制压缩包大小、文件数、展开后总字节数和单文件大小，并拒绝绝对路径、`..` 穿越和链接条目。Git 适配器不把用户 URL 拼接进 Shell；凭据和详细底层错误不会进入公开响应。
