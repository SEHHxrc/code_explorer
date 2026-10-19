"""只读重放两组原始请求，显式选择 JSON/SSE 和连接路径。

默认仅预检；显式选取变体和连接路径，零自动重试，不修改 .env 或实验数据库。
复用正式传输层；不附加固定答案指令，隐藏推理不进入收据。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import ipaddress
import json
import sqlite3
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener, getproxies, proxy_bypass, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.agents.orchestrator import AgentRunManager
from backend.app.experiments.audit.policy import digest, visible_request, visible_response
from backend.app.experiments.findings import parse_ledger
from backend.app.experiments.report import assess_answer
from backend.app.llm.base import ModelTurn
from backend.app.llm.budget import validate_provider_request
from backend.app.llm.http import post_json
from backend.app.llm.observation import observe_requests
from backend.app.llm.providers.compatible_provider import OpenAICompatibleProvider
from backend.app.llm.registry import create_model_provider
from backend.app.llm.response_metadata import response_metadata
from backend.app.llm.settings import pre_model_rejection_policy


def load_pair(comparison_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """只读取得本人首个实发请求；拒绝无法恢复精确正文的隐藏状态/脱敏投影。"""
    with sqlite3.connect((ROOT / 'database.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        pair = conn.execute('SELECT baseline_run_id,graph_run_id,execution_order FROM experiment_comparisons WHERE id=? AND user_id=?',
                            (comparison_id, 'default_user')).fetchone()
        if pair is None:
            raise ValueError('配对不存在或无权读取。')
        requests = {}
        for name, run in zip(('baseline', 'static'), pair[:2]):
            row = conn.execute('SELECT payload FROM experiment_audit_records WHERE run_id=? AND kind=? ORDER BY id LIMIT 1',
                               (run, 'request')).fetchone()
            record = json.loads(row[0])
            payload = record['visible']
            wire = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
            if hashlib.sha256(wire).hexdigest() != record['wire_sha256']:
                raise ValueError('审计投影不等于原实发正文，不能声称精确重放。')
            requests[name] = payload
        row = conn.execute('SELECT payload FROM agent_events WHERE run_id=? AND event_type=? ORDER BY sequence LIMIT 1',
                           (pair[0], 'run.started')).fetchone()
        endpoint = json.loads(row[0])['execution_policy']['network_accounting']['endpoint_sha256']
    return requests, {'original_execution_order': json.loads(pair[2]), 'endpoint_sha256': endpoint}


def plan(requests: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """只改变 stream，保留两组原始消息，不再尝试用提示词强制固定生成工作量。"""
    variants = []
    for name in ('baseline', 'static'):
        for streaming, label in ((False, 'nonstream'), (True, 'stream')):
            payload = copy.deepcopy(requests[name])
            payload['stream'] = streaming
            variants.append((name + '_original_' + label, payload))
    return variants


def route_opener(url: str, route: str) -> tuple[Any, dict[str, Any]]:
    """创建单次诊断的独立 opener；不改全局 opener、环境或系统代理，不控制 TUN 分流。"""
    scheme = urlsplit(url).scheme
    proxies = getproxies()
    info: dict[str, Any] = {'requested_route': route, 'endpoint_scheme': scheme,
                          'transparent_os_routing_not_controlled': True}
    if route == 'system':
        open_request = urlopen
        info['system_proxy_configured_for_scheme'] = bool(proxies.get(scheme))
        info['system_target_bypassed'] = bool(proxy_bypass(urlsplit(url).netloc))
    elif route == 'direct':
        open_request = build_opener(ProxyHandler({})).open
        info['urllib_proxy_disabled'] = True
    elif route == 'proxy':
        proxy = proxies.get(scheme)
        if not proxy or proxy_bypass(urlsplit(url).netloc):
            raise ValueError('当前目标未配置可生效的系统代理，拒绝冒充强制代理测试。')
        parts = urlsplit(proxy if '://' in proxy else 'http://' + proxy)
        info['proxy_origin_sha256'] = digest(f'{parts.scheme}://{parts.hostname}:{parts.port}')
        open_request = build_opener(ProxyHandler({scheme: proxy})).open
    else:
        raise ValueError('未知连接路径。')

    def traced_open(*args: Any, **kwargs: Any) -> Any:
        """在响应可用时核对本机第一跳；不保存 IP、端口或任何认证请求头。"""
        response = open_request(*args, **kwargs)
        sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
        try:
            info['response_peer_is_loopback'] = ipaddress.ip_address(sock.getpeername()[0]).is_loopback
        except (AttributeError, OSError, ValueError):
            info['response_peer_is_loopback'] = None
        return response

    return traced_open, info


def differences(requests: dict[str, Any]) -> dict[str, Any]:
    """只接受已核对的单消息追加差异，分别计量纯摘要、消息后缀和 JSON 线体积。"""
    baseline, static = requests['baseline'], requests['static']
    if baseline.keys() != static.keys() or any(baseline[key] != static[key] for key in baseline if key != 'messages'):
        raise ValueError('请求参数存在额外差异，不能执行本组单变量探针。')
    changed = [index for index, (left, right) in enumerate(zip(baseline['messages'], static['messages'])) if left != right]
    if len(baseline['messages']) != len(static['messages']) or changed != [1]:
        raise ValueError('消息结构与预期不一致，未发送。')
    left, right = baseline['messages'][1]['content'], static['messages'][1]['content']
    if not right.startswith(left + '\nSTATIC_SECURITY_EVIDENCE\n'):
        raise ValueError('静态输入不是相同完整源码后的摘要追加，未发送。')
    suffix = right[len(left):]
    return {'changed_message_indices': changed, 'all_other_request_fields_equal': True,
        'system_instructions_sha256': digest(baseline['messages'][0]['content']),
        'user_message_suffix_utf8_bytes': len(suffix.encode()),
        'static_summary_utf8_bytes': len(suffix.split('\nSTATIC_SECURITY_EVIDENCE\n', 1)[1].encode()),
        'user_message_suffix_sha256': digest(suffix)}


class Receipt:
    """复用正式 HTTP 观察接口，仅保存可见投影和脱敏计时，不写实验表。"""

    def __init__(self) -> None:
        """创建单次诊断收据。"""
        self.data: dict[str, Any] = {}

    def sent(self, payload: dict[str, Any], attempt: int, **wire: Any) -> int:
        """记录实际发送正文及指纹，不接受请求头。"""
        self.data.update(request=visible_request(payload), attempt=attempt, **wire)
        return 1

    def received(self, request_id: int, payload: dict[str, Any], *, diagnostics: dict[str, Any] | None = None) -> None:
        """只保存可见响应；供应商隐藏推理由公共投影移除。"""
        self.data.update(response=visible_response(payload), transport=diagnostics)

    def failed(self, request_id: int, error_type: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        """记录安全错误类型与发生阶段，不保存异常字符串。"""
        self.data.update(error_type=error_type, transport=diagnostics)

    def retry_wait(self, attempt: int, requested_seconds: float, elapsed_seconds: float, *,
                   status_code: int | None, completed: bool) -> None:
        """补全正式观察协议；记录意外退避，零重试探针正常不会进入此分支。"""
        self.data['unexpected_retry_wait'] = {'attempt': attempt, 'requested_seconds': requested_seconds,
            'elapsed_seconds': elapsed_seconds, 'status_code': status_code, 'completed': completed}


def check_report(payload: dict[str, Any]) -> dict[str, Any]:
    """复用正式终止检查与台账解析，不补字段、不改状态、不评价安全语义。"""
    metadata = response_metadata(payload, responses_api=False)
    choices = payload.get('choices') or [{}]
    message = choices[0].get('message') or {}
    result = {'metadata': asdict(metadata), 'actual_model': payload.get('model'), 'passed': False}
    try:
        if message.get('tool_calls'):
            raise ValueError('unexpected_tool_calls')
        content = message.get('content') or ''
        if not isinstance(content, str):
            raise ValueError('unexpected_content_type')
        text = AgentRunManager._require_answer(ModelTurn(content, 'diagnostic', payload.get('model') or '', metadata=metadata), forbid_tools=True)
        ledger = parse_ledger(text)
        result.update(passed=assess_answer(ledger.render(), metadata, require_report=True)['status'] == 'complete',
                      visible_chars=len(text), ledger=ledger.model_dump(mode='json'), semantic_coverage='not_proven')
    except (ValueError, RuntimeError) as exc:
        result['validation_error'] = str(exc)
    return result


def stream_request(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float, receipt: Receipt) -> dict[str, Any]:
    """复用正式 SSE 传输，不再维护另一套解析器；收据只保存可见投影。"""
    from backend.app.llm.http import _request_json_sync
    with observe_requests(receipt), patch('backend.app.llm.http.urlopen', urlopen):
        return _request_json_sync("POST", url, payload, headers, timeout,
                                  total_timeout=600, stream_idle_timeout=timeout)

async def run(*, comparison_id: str, send: bool, cooldown: float, variant_names: tuple[str, ...] | None = None,
              routes: tuple[str, ...] = ('system',)) -> dict[str, Any]:
    """执行选定独立诊断；429/503 停止后续，不重试超时，不改写任何已结束结果。"""
    requests, origin = load_pair(comparison_id)
    available = dict(plan(requests))
    selected = ('static_original_stream',) if variant_names is None else tuple(dict.fromkeys(variant_names))
    if not selected or any(name not in available for name in selected):
        raise ValueError('请选择已定义的诊断变体；未发送请求。')
    if not routes or any(route not in {'system', 'direct', 'proxy'} for route in routes):
        raise ValueError('请选择有效连接路径。')
    variants = [(name, available[name], route) for name in selected for route in dict.fromkeys(routes)]
    result: dict[str, Any] = {'purpose': 'request-difference-diagnostic-not-formal-ab', 'origin_comparison': comparison_id,
        **origin, 'differences': differences(requests), 'original_wire_bytes': {name: len(json.dumps(p, ensure_ascii=False, separators=(',', ':')).encode()) for name, p in requests.items()},
        'planned_variants': [name for name, _, _ in variants], 'planned_routes': list(routes),
        'automatic_retries': 0, 'cooldown_seconds': cooldown, 'results': []}
    if not send:
        return result
    provider = create_model_provider()
    if not isinstance(provider, OpenAICompatibleProvider) or provider.model != requests['static']['model']:
        raise ValueError('当前模型或协议与原请求不一致，未发送。')
    if pre_model_rejection_policy()['endpoint_sha256'] != origin['endpoint_sha256']:
        raise ValueError('端点指纹与原实验不一致，未发送。')
    output = ROOT / 'backend/storage/experiments' / ('request-diff-' + uuid.uuid4().hex + '.json')

    def save() -> None:
        """只保存新诊断产物，不写入历史实验表或实际配置。"""
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    print('RESULT_FILE ' + str(output), flush=True)
    save()
    headers = {'Authorization': 'Bearer ' + provider.api_key} if provider.api_key else {}
    for index, (name, payload, route) in enumerate(variants):
        if index:
            print(json.dumps({'cooldown_seconds': cooldown}), flush=True)
            await asyncio.sleep(cooldown)
        validate_provider_request(provider, payload)
        open_request, connection = route_opener(provider.base_url + '/chat/completions', route)
        receipt = Receipt()
        row: dict[str, Any] = {'variant': name, 'request_sha256': digest(payload), 'connection': connection,
                               'started_at': datetime.now(timezone.utc).isoformat(), 'report': None}
        result['results'].append(row)
        save()
        print('REQUEST_START ' + name + ' ' + route, flush=True)
        try:
            # 仅在独立诊断进程内替换两个传输入口；退出后恢复，不影响生产进程。
            with patch('backend.app.llm.http.urlopen', open_request), patch(__name__ + '.urlopen', open_request):
                if payload['stream']:
                    response = await asyncio.to_thread(stream_request, provider.base_url + '/chat/completions', headers, payload, provider.request_timeout_seconds, receipt)
                else:
                    with observe_requests(receipt):
                        response = await post_json(provider.base_url + '/chat/completions', payload, headers, provider.request_timeout_seconds, transient_retries=0)
            row['report'] = check_report(response)
        except Exception as exc:
            row['error_type'] = type(exc).__name__
        row['receipt'] = receipt.data
        save()
        print(json.dumps({'variant': name, 'route': route, 'connection': connection,
                          'passed': (row['report'] or {}).get('passed'), 'transport': receipt.data.get('transport')}), flush=True)
        if (receipt.data.get('transport') or {}).get('status_code') in {429, 503}:
            result['stopped_without_extra_requests'] = True
            break
    save()
    return result


def main() -> None:
    """仅显式 --send 使用现有配置联网，默认预检不会导入项目或发送请求。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparison-id', default='e606eb28b38644bd9249fe7750b0ce84')
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--cooldown', type=float, default=35)
    parser.add_argument('--variant', action='append', dest='variants', choices=('baseline_original_stream', 'baseline_original_nonstream', 'static_original_stream', 'static_original_nonstream'))
    parser.add_argument('--route', action='append', choices=('system', 'direct', 'proxy'), help='按所列顺序仅改变连接路径；默认继承系统设置')
    args = parser.parse_args()
    if not 30 <= args.cooldown <= 60:
        parser.error('请求间隔须为 30～60 秒。')
    from dotenv import load_dotenv
    load_dotenv(ROOT / 'backend/.env', override=False)
    result = asyncio.run(run(comparison_id=args.comparison_id, send=args.send, cooldown=args.cooldown,
                             variant_names=tuple(args.variants) if args.variants is not None else None,
                             routes=tuple(args.route) if args.route is not None else ('system',)))
    print(json.dumps({'requests_finished': len(result['results']), 'planned_variants': result['planned_variants']}))


if __name__ == '__main__':
    main()
