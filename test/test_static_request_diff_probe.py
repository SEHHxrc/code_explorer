"""差异探针离线回归：不发真实 HTTP，不修改任何实验或配置。"""

import copy
import io
import json
import unittest
from email.message import Message
from urllib.error import HTTPError
from backend.app.llm.http import ModelEndpointError
from unittest.mock import Mock, patch

from test.probe_static_request_diff import Receipt, check_report, differences, plan, route_opener, stream_request
from test.test_on_demand_evidence import LEDGER_JSON


class RequestDiffProbeTests(unittest.TestCase):
    def pair(self):
        baseline = {'model': 'fixture', 'stream': False, 'messages': [
            {'role': 'system', 'content': '共同契约'}, {'role': 'user', 'content': '相同源码'}, {'role': 'user', 'content': '最终报告'}]}
        static = copy.deepcopy(baseline)
        static['messages'][1]['content'] += '\nSTATIC_SECURITY_EVIDENCE\n{"items":[]}'
        return {'baseline': baseline, 'static': static}

    def test_variants_preserve_original_payload_and_only_change_declared_variables(self):
        pair = self.pair();before = copy.deepcopy(pair);variants = dict(plan(pair))
        self.assertEqual(before, pair)
        self.assertEqual(pair['static'] | {'stream': True}, variants['static_original_stream'])
        self.assertEqual(pair['static'], variants['static_original_nonstream'])
        for lane in ('baseline', 'static'):
            for label, streaming in (('stream', True), ('nonstream', False)):
                self.assertEqual(pair[lane] | {'stream': streaming}, variants[lane + '_original_' + label])
        self.assertFalse(any('fixed_report' in name for name in variants))
        self.assertEqual([1], differences(pair)['changed_message_indices'])
        pair['static']['model'] = 'other'
        with self.assertRaises(ValueError):differences(pair)

    def test_fixed_ledger_parses_and_transport_status_is_not_corrected(self):
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': LEDGER_JSON}}]}
        self.assertTrue(check_report(response)['passed'])
        for reason in ('length', 'tool_calls'):
            response['choices'][0]['finish_reason'] = reason
            self.assertFalse(check_report(response)['passed'])

    def test_stream_counts_but_never_persists_hidden_reasoning(self):
        secret = 'private-reasoning-fixture'
        events = [{'choices': [{'delta': {'reasoning_content': secret}}]},
                  {'choices': [{'delta': {'content': LEDGER_JSON}, 'finish_reason': 'stop'}]},
                  {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}}]
        raw = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events) + 'data: [DONE]\n\n'

        class Response(io.BytesIO):
            status = 200
            headers = Message()
            headers['Content-Type'] = 'text/event-stream'

        receipt = Receipt()
        with patch('test.probe_static_request_diff.urlopen', return_value=Response(raw.encode())):
            result = stream_request('https://offline.invalid/v1/chat/completions', {}, {'model': 'fixture', 'stream': True}, 30, receipt)
        self.assertTrue(check_report(result)['passed'])
        self.assertTrue(receipt.data['transport']['done'])
        self.assertEqual(len(secret), receipt.data['transport']['reasoning_chars'])
        self.assertNotIn(secret, json.dumps(receipt.data))

    def test_direct_uses_empty_proxy_handler_without_installing_global_opener(self):
        response = Mock()
        response.fp.raw._sock.getpeername.return_value = ('203.0.113.12', 80)
        with patch('test.probe_static_request_diff.getproxies', return_value={'http': 'http://localhost:1'}), \
                patch('test.probe_static_request_diff.build_opener') as build, \
                patch('urllib.request.install_opener') as install:
            build.return_value.open.return_value = response
            open_request, info = route_opener('http://offline.invalid/v1/chat/completions', 'direct')
            self.assertEqual({}, build.call_args.args[0].proxies)
            open_request('request', timeout=300)
            build.return_value.open.assert_called_once_with('request', timeout=300)
            install.assert_not_called()
        self.assertTrue(info['urllib_proxy_disabled'])
        self.assertFalse(info['response_peer_is_loopback'])

    def test_http_rejection_preserves_safe_metadata_without_saving_error_body(self):
        headers = Message()
        headers['x-request-id'] = 'offline-correlation-id'
        headers['Retry-After'] = '60'
        body = b'{"error":{"type":"rate_limit_error","code":"RateLimitReached","message":"private-body-fixture"}}'
        error = HTTPError('https://offline.invalid', 429, 'rate limit', headers, io.BytesIO(body))
        receipt = Receipt()
        with patch('test.probe_static_request_diff.urlopen', side_effect=error):
            with self.assertRaises(ModelEndpointError):
                stream_request('https://offline.invalid', {}, {'stream': True}, 30, receipt)
        transport = receipt.data['transport']
        self.assertEqual(429, transport['status_code'])
        self.assertEqual('RateLimitReached', transport['provider_error']['code'])
        self.assertEqual('rate_limit_error', transport['provider_error']['type'])
        self.assertEqual('offline-correlation-id', transport['provider_error']['request_id'])
        self.assertEqual('60', transport['provider_error']['retry_after'])
        self.assertEqual('json_object', transport['error_body_format'])
        self.assertNotIn('private-body-fixture', json.dumps(receipt.data))

    def test_explicit_proxy_uses_same_config_but_does_not_save_proxy_credentials(self):
        proxies = {'http': 'http://secret-user:secret-password@127.0.0.1:12345'}
        response = Mock()
        response.fp.raw._sock.getpeername.return_value = ('127.0.0.1', 12345)
        with patch('test.probe_static_request_diff.getproxies', return_value=proxies), \
                patch('test.probe_static_request_diff.proxy_bypass', return_value=False), \
                patch('test.probe_static_request_diff.build_opener') as build:
            build.return_value.open.return_value = response
            open_request, info = route_opener('http://offline.invalid/v1/chat/completions', 'proxy')
            self.assertEqual(proxies, build.call_args.args[0].proxies)
            open_request('request', timeout=300)
        self.assertTrue(info['response_peer_is_loopback'])
        for private in ('secret-user', 'secret-password', '127.0.0.1', '12345'):
            self.assertNotIn(private, json.dumps(info))

    def test_proxy_rejects_missing_or_bypassed_configuration(self):
        for proxies, bypassed in (({}, False), ({'http': 'http://localhost:1'}, True)):
            with self.subTest(proxies=proxies, bypassed=bypassed), \
                    patch('test.probe_static_request_diff.getproxies', return_value=proxies), \
                    patch('test.probe_static_request_diff.proxy_bypass', return_value=bypassed):
                with self.assertRaises(ValueError):
                    route_opener('http://offline.invalid/v1/chat/completions', 'proxy')


if __name__ == '__main__':unittest.main()
