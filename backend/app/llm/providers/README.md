# 大模型 Provider 实现

本目录实现 `llm.base.ModelProvider`，用于隔离不同模型服务的请求和响应格式。

| 文件/类 | 请求端点 | 工具调用解析 |
| --- | --- | --- |
| `openai_provider.py` / `OpenAIResponsesProvider` | `POST {base_url}/responses` | 从 Responses API `output` 中读取 `function_call`。 |
| `compatible_provider.py` / `OpenAICompatibleProvider` | `POST {base_url}/chat/completions` | 从首个 choice 的 `message.tool_calls` 读取函数调用。 |

两个类的构造参数均包含模型名和根地址；OpenAI 实现要求 `api_key`，兼容实现的 `api_key` 可为空。公共方法为：

- `generate(*, instructions: str, prompt: str) -> ModelResult`
- `generate_with_tools(*, instructions: str, messages: list[ModelMessage], tools: list[dict], ...) -> ModelTurn`
- `request_payload(...) -> dict`：纯序列化，发送与预算使用同一个请求。
- `capabilities() -> ProviderCapabilities`

新增 Provider 时，应实现上述接口，并在 `../registry.py` 中增加显式映射；不要让路由或 Agent 编排器直接判断厂商。

单轮文本接口继续兼容 `prompt`，正式 Agent 使用 `messages`。Chat 保留助手工具调用、全部匹配结果及供应商返回的 `reasoning_content`；Responses 保留原生输出项、加密状态与 `function_call_output`。`ModelTurn.continuation` 只供本次运行内存续接，不写前端事件或实验记录。消息压缩不能产生孤立工具结果。

兼容适配器新增 `output_token_parameter`、`reasoning_effort` 显式参数；未知平台不自动设置 temperature 或关闭思考。探测可传 `max_output_tokens`、`timeout`、`transient_retries=0`，最多两个生成请求，不复用旧字符拼接流程。
