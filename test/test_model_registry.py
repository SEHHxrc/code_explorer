# -*- coding: utf-8 -*-
import os
import unittest
from unittest.mock import patch

from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.providers.openai_provider import OpenAIResponsesProvider
from backend.app.llm.registry import create_model_provider, get_model_configuration


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

    def test_responses_output_text_fallback_parser(self):
        payload = {
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": "overview"}],
            }]
        }
        self.assertEqual(OpenAIResponsesProvider._extract_output_text(payload), "overview")


if __name__ == "__main__":
    unittest.main()

