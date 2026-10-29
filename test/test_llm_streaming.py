"""生产流式传输与有限预算回归；只用内存响应和本机 socketpair，不调用模型。"""

import asyncio
import io
import json
import os
import socket
import threading
import time
import unittest
from email.message import Message
from unittest.mock import patch

from backend.app.agents.reporting import execution_policy
from backend.app.llm.http import ModelDeadlineError, ModelResponseError, ModelStreamInterruptedError, post_json
from backend.app.llm.observation import observe_requests
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.response_reader import ResponseReader
from backend.app.llm.settings import get_model_transport_settings
from test.probe_static_request_diff import Receipt


class Response(io.BytesIO):
    """带 HTTP 元数据的离线响应。"""

    status = 200

    def __init__(self, body, content_type='text/event-stream'):
        super().__init__(body)
        self.headers = Message()
        self.headers['Content-Type'] = content_type


def frames(*events, done=True):
    """生成含 UTF-8/多行 data 的标准 SSE 测试流。"""
    raw = ': keepalive\r\n\r\n' + ''.join('data: ' + json.dumps(e, ensure_ascii=False) + '\n\n' for e in events)
    return (raw + ('data: [DONE]\n\n' if done else '')).encode()


class ChatStreamTests(unittest.TestCase):
    def call(self, response, *, payload=None, observer=None):
        with patch('backend.app.llm.http.urlopen', return_value=response) as send, observe_requests(observer):
            result = asyncio.run(post_json('http://offline.invalid', payload or {'stream': True}, {}, 30,
                                          total_timeout=60, stream_idle_timeout=10))
        self.assertEqual(1, send.call_count)
        return result

    def test_tool_fragments_usage_and_native_reasoning_roundtrip(self):
        hidden = '隐藏续接仅在内存'
        response = Response(frames(
            {'model': 'actual-model', 'choices': [{'delta': {'reasoning_content': hidden}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 1, 'id': 'b', 'function': {'name': 'second', 'arguments': '{'}},
                                                  {'index': 0, 'id': 'a', 'function': {'name': 'read_', 'arguments': '{"pa'}}]}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'name': 'file', 'arguments': 'th":"中文.py"}'}},
                                                  {'index': 1, 'function': {'arguments': '}'}}]}, 'finish_reason': 'tool_calls'}]},
            {'choices': [], 'usage': {'prompt_tokens': 17, 'completion_tokens': 23, 'total_tokens': 40,
                                     'completion_tokens_details': {'reasoning_tokens': 11}}}))
        receipt = Receipt()
        with patch.dict(os.environ, {'CODE_EXPLORER_LLM_CHAT_STREAM': '1'}, clear=True):
            provider = OpenAICompatibleProvider(provider_name='compatible', base_url='http://offline.invalid', model='mock')
        with patch('backend.app.llm.http.urlopen', return_value=response) as send, observe_requests(receipt):
            turn = asyncio.run(provider.generate_with_tools(instructions='safe', prompt='question', tools=[]))
        self.assertEqual([('a', 'read_file', {'path': '中文.py'}), ('b', 'second', {})],
                         [(c.id, c.name, c.arguments) for c in turn.tool_calls])
        self.assertEqual(hidden, turn.continuation['reasoning_content'])
        self.assertEqual('tool_calls', turn.metadata.finish_reason)
        self.assertEqual((17, 23, 40, 11), (turn.metadata.usage.input_tokens, turn.metadata.usage.output_tokens,
                                         turn.metadata.usage.total_tokens, turn.metadata.usage.reasoning_tokens))
        self.assertNotIn(hidden, json.dumps(receipt.data, ensure_ascii=False))
        self.assertTrue(json.loads(send.call_args.args[0].data)['stream_options']['include_usage'])
        self.assertTrue(receipt.data['transport']['done'])

    def test_text_refusal_length_and_unknown_usage_preserved(self):
        result = self.call(Response(frames({'choices': [{'delta': {'content': '答', 'refusal': '拒绝'}}]},
                                          {'choices': [{'delta': {'content': '案'}, 'finish_reason': 'length'}]})))
        self.assertEqual('答案', result['choices'][0]['message']['content'])
        self.assertEqual('拒绝', result['choices'][0]['message']['refusal'])
        self.assertEqual('length', result['choices'][0]['finish_reason'])
        self.assertNotIn('usage', result)

    def test_invalid_incomplete_streams_and_arguments_are_not_retried(self):
        examples = [frames({'choices': [{'delta': {'content': '{}'}, 'finish_reason': 'stop'}]}, done=False),
                    frames({'choices': [{'delta': {'content': '{}'}}]}),
                    frames({'error': {'message': 'PRIVATE'}}), b'data: not-json\n\n',
                    frames({'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'a', 'function': {
                        'name': 'tool', 'arguments': '{'}}]}, 'finish_reason': 'tool_calls'}]})]
        for raw in examples:
            receipt = Receipt()
            with self.subTest(raw=raw), patch('backend.app.llm.http.urlopen', return_value=Response(raw)) as send, observe_requests(receipt):
                with self.assertRaises(ModelResponseError):
                    asyncio.run(post_json('http://offline.invalid', {'stream': True}, {}, 30, total_timeout=60))
                self.assertEqual(1, send.call_count)
                self.assertNotIn('response', receipt.data)
                self.assertNotIn('PRIVATE', json.dumps(receipt.data))

    def test_connection_reset_after_first_event_is_not_automatically_replayed(self):
        class BrokenResponse(Response):
            def readline(self, limit=-1):
                if self.tell() == len(self.getvalue()):
                    raise ConnectionResetError('PRIVATE')
                return super().readline(limit)

        receipt = Receipt()
        response = BrokenResponse(frames({'choices': [], 'usage': {'total_tokens': 9}}, done=False))
        with patch('backend.app.llm.http.urlopen', return_value=response) as send, observe_requests(receipt):
            with self.assertRaises(ModelStreamInterruptedError):
                asyncio.run(post_json('http://offline.invalid', {'stream': True}, {}, 30, total_timeout=60))
        self.assertEqual(1, send.call_count)
        self.assertEqual(9, receipt.data['transport']['partial_usage']['total_tokens'])

    def test_ignored_stream_json_is_one_response_not_an_automatic_fallback(self):
        payload = {'choices': [{'message': {'content': 'answer'}, 'finish_reason': 'stop'}]}
        receipt = Receipt()
        self.assertEqual(payload, self.call(Response(json.dumps(payload).encode(), 'application/json'), observer=receipt))
        self.assertTrue(receipt.data['transport']['server_ignored_stream'])

    def test_response_and_event_limits_fail_without_truncation(self):
        with patch('backend.app.llm.response_reader.MAX_RESPONSE_BYTES', 20):
            with self.assertRaises(ModelResponseError):
                self.call(Response(b'x' * 21, 'application/json'), payload={'stream': False})
        with patch('backend.app.llm.http.MAX_EVENT_BYTES', 10):
            with self.assertRaises(ModelResponseError):
                self.call(Response(frames({'choices': []})))

    def test_total_deadline_expires_before_reading_instead_of_retrying(self):
        receipt = Receipt()
        def expired(reader):
            reader.expired.set()
            raise TimeoutError('deadline')
        with patch('backend.app.llm.http.urlopen', return_value=Response(frames())) as send, \
                patch.object(ResponseReader, '__enter__', expired), observe_requests(receipt):
            with self.assertRaises(ModelDeadlineError):
                asyncio.run(post_json('http://offline.invalid', {'stream': True}, {}, 30, total_timeout=60))
        self.assertEqual(1, send.call_count)
        self.assertEqual('attempt_total', receipt.data['transport']['timeout_kind'])

    def test_slow_drip_cannot_keep_total_read_deadline_alive(self):
        left, right = socket.socketpair()
        stop = threading.Event()

        def writer():
            while not stop.wait(0.01):
                try:
                    right.sendall(b'x')
                except OSError:
                    return

        thread = threading.Thread(target=writer)
        response = type('SocketResponse', (), {})()
        response.fp = left.makefile('rb')
        response.readline = response.fp.readline
        thread.start()
        started = time.monotonic()
        try:
            with ResponseReader(response, started=started, total_timeout=0.15, idle_timeout=1) as reader:
                with self.assertRaises(TimeoutError):
                    reader.line()
            self.assertLess(time.monotonic() - started, 2)
        finally:
            stop.set()
            thread.join(1)
            response.fp.close()
            left.close()
            right.close()

    def test_cancelling_established_stream_closes_reader_without_late_audit(self):
        left, right = socket.socketpair()
        reading = threading.Event()
        response = type('SocketResponse', (), {})()
        response.fp = left.makefile('rb')
        response.status = 200
        response.headers = Message()
        response.headers['Content-Type'] = 'text/event-stream'

        def readline(limit=-1):
            reading.set()
            return response.fp.readline(limit)

        response.readline = readline
        class Context:
            def __enter__(self):
                return response
            def __exit__(self, *args):
                response.fp.close()

        receipt = Receipt()
        async def scenario():
            with patch('backend.app.llm.http.urlopen', return_value=Context()), observe_requests(receipt):
                task = asyncio.create_task(post_json('http://offline.invalid', {'stream': True}, {}, 0.3, total_timeout=0.3))
                for _ in range(100):
                    if reading.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(reading.is_set())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        try:
            asyncio.run(scenario())  # 等待系统读取解除阻塞后退出，仍不得晚写审计。
            self.assertNotIn('response', receipt.data)
            self.assertNotIn('error_type', receipt.data)
        finally:
            response.fp.close()
            left.close()
            right.close()

    def test_policy_frozen_and_transport_changes_experiment_contract(self):
        with patch.dict(os.environ, {'CODE_EXPLORER_LLM_PROVIDER': 'compatible', 'CODE_EXPLORER_LLM_CHAT_STREAM': '1'}, clear=True):
            provider = OpenAICompatibleProvider(provider_name='compatible', base_url='http://offline.invalid', model='mock')
            stream_policy = execution_policy(4, 'tools_none')['transport']
            self.assertEqual('exponential-jitter-v2', execution_policy(4, 'tools_none')['http_retry_policy'])
            self.assertEqual('chat_sse', stream_policy['mode'])
            self.assertEqual('json', get_model_transport_settings().snapshot('openai')['mode'])
            with patch.dict(os.environ, {'CODE_EXPLORER_LLM_CHAT_STREAM': '0'}):
                self.assertTrue(provider.request_payload(instructions='safe', messages=[], tools=[])['stream'])
                self.assertNotEqual(stream_policy, execution_policy(4, 'tools_none')['transport'])
        for setting, value in (('CODE_EXPLORER_LLM_CHAT_STREAM', 'invalid'),
                               ('CODE_EXPLORER_LLM_STREAM_IDLE_TIMEOUT_SECONDS', '0'),
                               ('CODE_EXPLORER_LLM_REQUEST_TOTAL_TIMEOUT_SECONDS', '7201')):
            with patch.dict(os.environ, {setting: value}, clear=True), self.assertRaises(ValueError):
                get_model_transport_settings()


if __name__ == '__main__':
    unittest.main()
