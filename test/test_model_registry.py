# -*- coding: utf-8 -*-
import os
import unittest
from unittest.mock import patch

from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.providers.openai_provider import OpenAIResponsesProvider
from backend.app.llm.registry import (
    create_model_provider,
    get_model_api_key,
    get_model_configuration,
    get_model_limits,
)


class ModelRegistryTests(unittest.TestCase):
    def test_disabled_without_model_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(get_model_configuration().configured)
            self.assertIsNone(create_model_provider())

    def test_ollama_uses_local_default_base_url(self):
        with patch.dict(os.environ, {
            "CODE_EXPLORER_LLM_PROVIDER": "ollama",
            "CODE_EXPLORER_LLM_MODEL": "local-model",
            "CODE_EXPLORER_LLM_BASE_URL": "",
        }, clear=True):
            config = get_model_configuration()
            provider = create_model_provider()
            self.assertTrue(config.configured)
            self.assertEqual(config.base_url, "http://localhost:11434/v1")
            self.assertIsInstance(provider, OpenAICompatibleProvider)

    def test_openai_requires_api_key(self):
        environment = {
            "CODE_EXPLORER_LLM_PROVIDER": "openai",
            "CODE_EXPLORER_LLM_MODEL": "configured-model",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertFalse(get_model_configuration().configured)
        environment["CODE_EXPLORER_LLM_API_KEY"] = "test-key"
        with patch.dict(os.environ, environment, clear=True):
            self.assertIsInstance(create_model_provider(), OpenAIResponsesProvider)

    def test_request_model_overrides_environment_default(self):
        """请求级模型只覆盖本次 Provider，不修改环境变量默认值。"""
        with patch.dict(os.environ, {
            "CODE_EXPLORER_LLM_PROVIDER": "openai",
            "CODE_EXPLORER_LLM_MODEL": "default-model",
            "CODE_EXPLORER_LLM_API_KEY": "test-key",
        }, clear=True):
            provider = create_model_provider("selected-model")
            self.assertEqual(provider.model, "selected-model")
            self.assertEqual(get_model_configuration().model, "default-model")

    def test_responses_output_text_fallback_parser(self):
        payload = {
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": "overview"}],
            }]
        }
        self.assertEqual(OpenAIResponsesProvider._extract_output_text(payload), "overview")

    def test_matching_environment_quotes_are_removed(self):
        """手工设置环境变量时的成对引号不应进入认证请求。"""
        with patch.dict(os.environ, {
            "CODE_EXPLORER_LLM_API_KEY": "'quoted-key'",
        }, clear=True):
            self.assertEqual(get_model_api_key(), "quoted-key")

    def test_model_budgets_are_configurable_and_bounded(self):
        """输入字符和输出 Token 预算应支持配置并收敛到安全范围。"""
        with patch.dict(os.environ, {
            "CODE_EXPLORER_LLM_MAX_CONTEXT_CHARS": "12000",
            "CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS": "1024",
        }, clear=True):
            limits = get_model_limits()
            self.assertEqual(limits.max_context_chars, 12000)
            self.assertEqual(limits.max_output_tokens, 1024)
        with patch.dict(os.environ, {
            "CODE_EXPLORER_LLM_MAX_CONTEXT_CHARS": "999999999",
            "CODE_EXPLORER_LLM_MAX_OUTPUT_TOKENS": "invalid",
        }, clear=True):
            limits = get_model_limits()
            self.assertEqual(limits.max_context_chars, 200000)
            self.assertEqual(limits.max_output_tokens, 2400)


if __name__ == "__main__":
    unittest.main()
