# 大模型 Provider 实现

本目录实现 `llm.base.ModelProvider`，用于隔离不同模型服务的请求和响应格式。

| 文件/类 | 请求端点 | 工具调用解析 |
| --- | --- | --- |
| `openai_provider.py` / `OpenAIResponsesProvider` | `POST {base_url}/responses` | 从 Responses API `output` 中读取 `function_call`。 |
| `compatible_provider.py` / `OpenAICompatibleProvider` | `POST {base_url}/chat/completions` | 从首个 choice 的 `message.tool_calls` 读取函数调用。 |

两个类的构造参数均包含模型名和根地址；OpenAI 实现要求 `api_key`，兼容实现的 `api_key` 可为空。公共方法为：

- `generate(*, instructions: str, prompt: str) -> ModelResult`
- `generate_with_tools(*, instructions: str, prompt: str, tools: list[dict]) -> ModelTurn`
- `capabilities() -> ProviderCapabilities`

新增 Provider 时，应实现上述接口，并在 `../registry.py` 中增加显式映射；不要让路由或 Agent 编排器直接判断厂商。
