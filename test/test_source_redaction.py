"""源码脱敏的控制流保真、配置凭据保护及二次审计回归；不调用模型。"""

import ast
import json
import unittest

from backend.app.core.redaction import redact_secrets, redact_source
from backend.app.experiments.audit.policy import visible_request


class SourceRedactionTests(unittest.TestCase):
    def test_parameter_annotations_variable_bindings_and_return_are_preserved(self) -> None:
        source = ('def verify(token: str, api_key: str):\n'
                  '    tenant_api_key = lookup(api_key)\n'
                  '    if not tenant_api_key:\n'
                  '        return token\n'
                  '    token = tenant_api_key\n'
                  '    return token\n')
        self.assertEqual(source, redact_source(source))
        self.assertEqual(ast.dump(ast.parse(source)), ast.dump(ast.parse(redact_source(source))))
        numbered = '\n'.join(f'{index}: {line}' for index, line in enumerate(source.splitlines(), 51))
        payload = {'messages': [{'role': 'tool', 'content': json.dumps({'path': 'noauth.py', 'text': numbered})}]}
        self.assertEqual(payload, visible_request(payload))
        self.assertIn('54:         return token', numbered)

    def test_sensitive_literals_keep_quotes_punctuation_and_line_endings(self) -> None:
        source = 'api_key = "actual-value"\r\nparams = {"password": "pw-value", "token": value}\r\nreturn_value = 1\r\n'
        masked = redact_source(source)
        self.assertEqual('api_key = "[REDACTED]"\r\nparams = {"password": "[REDACTED]", "token": value}\r\nreturn_value = 1\r\n', masked)
        self.assertEqual(masked, redact_source(masked))
        ast.parse(masked)

    def test_key_blocks_preserve_numbered_lines_and_do_not_merge_return(self) -> None:
        text = '51: key = """-----BEGIN PRIVATE KEY-----\r\n52: private-material\r\n53: -----END PRIVATE KEY-----"""\r\n54: return value'
        masked = redact_source(text)
        self.assertNotIn('private-material', masked)
        self.assertEqual([line.split(':', 1)[0] for line in text.splitlines()],
                         [line.split(':', 1)[0] for line in masked.splitlines()])
        self.assertTrue(masked.endswith('\r\n54: return value'))

    def test_runtime_and_configuration_bare_credentials_still_masked(self) -> None:
        for value in ('password=bare-credential', 'api_key: bare-credential', 'ACCESS_TOKEN=bare-credential'):
            self.assertNotIn('bare-credential', redact_secrets(value))
            self.assertEqual(redact_secrets(value), redact_secrets(redact_secrets(value)))
            for path in ('.env', 'config.yaml', 'app.properties'):
                self.assertNotIn('bare-credential', redact_source(value, path=path))
        # 同样的单词在程序里可以是变量绑定，不能按裸凭据处理。
        self.assertEqual('token = other_token', redact_source('token = other_token', path='app.py'))

    def test_multiline_credential_literals_keep_following_control_flow(self) -> None:
        source = 'password = """first-secret\r\nsecond-secret\r\n"""\r\nif valid:\r\n    return_value = 1\r\n'
        masked = redact_source(source)
        self.assertNotIn('first-secret', masked)
        self.assertNotIn('second-secret', masked)
        self.assertEqual(source.count('\r\n'), masked.count('\r\n'))
        self.assertTrue(masked.endswith('if valid:\r\n    return_value = 1\r\n'))
        ast.parse(masked)


if __name__ == '__main__':
    unittest.main()
