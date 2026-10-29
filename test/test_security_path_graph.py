"""静态路径投影、按需详情和项目所有权测试；不改产物、不调用模型。"""

import copy
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.app.api.project import router
from backend.app.services.project_analysis.graph_exchange import GraphExchangeNormalizer
from backend.app.services.project_analysis.security_paths import SecurityPathProjection
from backend.app.services.projects import ProjectQueryError, ProjectQueryService
from backend.app.services.security_analysis.contracts import EvidenceLocation, PathEdgeEvidence
from test.test_evidence_selection import fixture, fact


def artifact():
    """包含局部值流、跨函数结构路线和不应显示的无关依赖。"""
    pack = fixture()
    source = pack.facts['source']
    sink = fact('remote-sink', 'sink', 22, 'src/callee.py')
    pack.facts[sink.fact_id] = sink
    edge = PathEdgeEvidence(edge_id='call', callsite_id='site', source=source.symbol, target=sink.symbol,
                            callsite=EvidenceLocation(path=source.location.path, line=8), confidence='medium')
    pack.call_edges[edge.edge_id] = edge
    pack.candidates.append(pack.candidates[0].model_copy(update={
        'candidate_id': 'remote', 'sink_fact_id': sink.fact_id, 'dataflow_id': None,
        'dataflow_verified': False, 'path_kind': 'structural_call_path', 'call_edge_ids': ['call'],
    }))
    pack.facts['unpaired'] = fact('unpaired', 'sink', 30)
    return {
        'security_evidence': pack.model_dump(),
        'dependency_graph': {
            'nodes': [{'id': item.symbol, 'name': name, 'level': 'method', 'kind': 'method',
                       'file': item.location.path, 'line': 1} for item, name in [(source, 'handle'), (sink, 'execute')]]
                     + [{'id': 'unrelated.py', 'name': 'unrelated', 'level': 'module'}],
            'links': [{'id': 'not-in-path', 'source': sink.symbol, 'target': source.symbol, 'relation': 'imports'}],
        },
    }


class SecurityPathGraphTests(unittest.TestCase):
    def test_possible_connections_keep_the_same_meaning_in_catalog_graph_and_details(self):
        """目标不确定性不能在前后端交换时丢失，也不能被统计成新的传播路线。"""
        raw = artifact()
        raw['security_evidence']['call_edges']['call'].update(
            target_certainty='may', resolution_method='bounded_dynamic_member', dispatch='dynamic', confidence='medium')
        before = copy.deepcopy(raw)
        projection = SecurityPathProjection(raw)
        graph = projection.build()
        route = next(item for item in graph.paths if item.id == 'remote')
        catalog = next(item for item in projection.selector.rows() if item['id'] == route.id)
        self.assertEqual({'possible': 1}, catalog['call_connection_counts'])
        self.assertEqual(catalog['call_connection_counts'], route.call_connection_counts)
        self.assertEqual(route.edge_ids, route.possible_edge_ids)
        self.assertEqual(route.edge_ids, graph.possible_edge_ids)
        connection = projection.detail(route.id).call_connections[0]
        self.assertEqual('possible', connection.status)
        self.assertTrue(connection.speculative_target)
        self.assertEqual('bounded_dynamic_member', connection.resolution_method)
        self.assertEqual(8, connection.location.line)
        self.assertEqual('structural_lead', route.kind)
        self.assertEqual(before, raw)

    def test_merged_display_edges_preserve_uncertainty_per_selected_route(self):
        """相同端点的不同调用点可合并绘图，选路时不能继承另一条路线的确定性。"""
        raw = artifact()
        pack = raw['security_evidence']
        pack['call_edges']['call'].update(target_certainty='must', confidence='high')
        pack['call_edges']['maybe'] = {**pack['call_edges']['call'], 'edge_id': 'maybe', 'target_certainty': 'may',
                                      'resolution_method': 'unique_member_heuristic'}
        remote = next(item for item in pack['candidates'] if item['candidate_id'] == 'remote')
        pack['candidates'].append({**remote, 'candidate_id': 'possible-route', 'call_edge_ids': ['maybe']})
        result = SecurityPathProjection(raw).build()
        self.assertEqual(1, len(result.graph.edges))
        routes = {item.id: item for item in result.paths}
        self.assertFalse(routes['remote'].possible_edge_ids)
        merged_id = result.graph.edges[0].id
        self.assertEqual([merged_id], routes['possible-route'].possible_edge_ids)
        self.assertEqual([merged_id], result.possible_edge_ids)

    def test_target_binding_labels_do_not_upgrade_incomplete_or_fallback_evidence(self):
        """must 标签仍须保留低置信、回退与截断；未知目标不能改成确定绑定。"""
        edge = PathEdgeEvidence(edge_id='edge', callsite_id='site', source='a', target='b',
                                target_certainty='must', confidence='high')
        self.assertEqual('modelled', edge.connection_status)
        for fields in ({'confidence': 'low'}, {'truncated': True}, {'fallback_reason': 'missing_type'},
                       {'unresolved_reason': 'unknown_binding'}, {'target_certainty': 'may'}):
            self.assertEqual('possible', edge.model_copy(update=fields).connection_status)
        self.assertEqual('unresolved', edge.model_copy(update={'target_certainty': 'unresolved'}).connection_status)

    def test_compiler_fallback_may_does_not_turn_every_recorded_call_into_a_virtual_link(self):
        """编译器未映射调用点会统一降为 may；线型必须独立于保守分析状态。"""
        for method, confidence in [('local_scope', 'high'), ('package_scope', 'high'), ('receiver_type', 'medium')]:
            with self.subTest(method=method):
                raw = artifact()
                raw['security_evidence']['call_edges']['call'].update(target_certainty='may', confidence=confidence,
                    resolution_method=method, fallback_reason='compiler_callsite_not_mapped', truncated=True)
                projection = SecurityPathProjection(raw)
                result = projection.build()
                route = next(item for item in result.paths if item.id == 'remote')
                self.assertFalse(route.possible_edge_ids)
                self.assertFalse(result.possible_edge_ids)
                self.assertEqual({'possible': 1}, route.call_connection_counts)
                connection = projection.detail(route.id).call_connections[0]
                self.assertEqual('possible', connection.status)
                self.assertEqual('may', connection.target_certainty)
                self.assertTrue(connection.truncated)
                self.assertFalse(connection.speculative_target)

    def test_only_explicit_candidate_resolution_changes_line_style(self):
        """动态/虚派发与启发式目标用虚线；旧记录缺少元数据不伪装成新推测关系。"""
        edge = PathEdgeEvidence(edge_id='edge', callsite_id='site', source='a', target='b')
        self.assertFalse(edge.is_speculative_target)
        for fields in ({'dispatch': 'dynamic'}, {'dispatch': 'virtual'},
                       {'resolution_method': 'unique_member_heuristic'}, {'resolution_method': 'nearest_module_heuristic'}):
            self.assertTrue(edge.model_copy(update=fields).is_speculative_target)

    def test_existing_structure_is_visible_without_becoming_a_taint_path(self):
        """父类到 Provider 的真实调用可见，参数传播缺口独立呈现，不补 Source 或路径族。"""
        raw = artifact()
        raw['security_evidence'].update(candidates=[], dataflows={})
        sink = raw['security_evidence']['facts']['remote-sink']
        raw['security_evidence']['facts'] = {'remote-sink': sink}
        raw['semantic_index'] = {'files': {'src/callee.py': 'python'}, 'callsites': {'invoke': {
            'callsite_id': 'invoke', 'caller_symbol': 'src/base.py::Base::query', 'language': 'python',
            'name': '_query', 'receiver': 'self', 'location': {'path': 'src/base.py', 'line': 40},
            'targets': [{'edge_id': 'virtual', 'target_symbol': sink['symbol'], 'relation': 'calls',
                         'dispatch': 'virtual', 'certainty': 'may'}],
            'binding_diagnostics': ['mapping_keys_or_duplicates_unknown'],
        }}}
        before = copy.deepcopy(raw)
        projection = SecurityPathProjection(raw)
        result = projection.build()
        self.assertFalse(result.paths)
        self.assertEqual(0, result.candidate_count)
        self.assertEqual(['virtual'], result.structural_edge_ids)
        self.assertEqual(['virtual'], result.operations[0].context_edge_ids)
        self.assertEqual(2, len(result.graph.nodes))
        self.assertEqual({'source_to_operation_not_established', 'mapping_keys_or_duplicates_unknown'},
                         {reason for item in projection.detail('remote-sink').boundaries for reason in item.reasons})
        self.assertEqual(before, raw)

    def test_structure_fan_in_is_bounded_and_does_not_invent_edges_for_unknown_calls(self):
        """上下文超限明确标记；未解析目标只给位置，不连到碰巧同名的 Sink。"""
        raw = artifact()
        raw['security_evidence'].update(candidates=[], dataflows={})
        sink = raw['security_evidence']['facts']['remote-sink']
        raw['security_evidence']['facts'] = {'remote-sink': sink}
        raw['dependency_graph']['links'] = [{'id': str(i), 'source': f'src/a.py::caller{i}',
            'target': sink['symbol'], 'relation': 'calls'} for i in range(20)]
        projection = SecurityPathProjection(raw).build()
        self.assertTrue(projection.structure_truncated)
        self.assertEqual(8, len(projection.structural_edge_ids))
        self.assertFalse(projection.paths)

    def test_direct_context_is_fair_across_targets_before_expanding_ancestors(self):
        """同一目标的大扇入不能挤掉后续目标的唯一真实入边。"""
        from backend.app.services.project_analysis.structure_context import incoming_context
        from backend.app.services.semantic_index.projection import ProgramFacts
        graph = {'links': [{'id': str(i), 'source': f'caller{i}', 'target': 'early', 'relation': 'calls'}
                           for i in range(10)] + [{'id': 'last', 'source': 'base', 'target': 'late', 'relation': 'calls'}]}
        incoming, limited = incoming_context(ProgramFacts(legacy_graph=graph), {'early', 'late'}, max_edges=2)
        self.assertEqual(1, len(incoming['early']))
        self.assertEqual(['last'], [edge['id'] for edge in incoming['late']])
        self.assertTrue(limited)

    def test_union_keeps_only_actual_path_members_and_same_catalog_classification(self):
        raw = artifact()
        before = copy.deepcopy(raw)
        result = SecurityPathProjection(raw).build()
        self.assertEqual(2, result.candidate_count)
        self.assertEqual(2, len(result.graph.nodes))
        self.assertEqual(['call'], [edge.id for edge in result.graph.edges])
        self.assertEqual({'propagation_path': 1, 'structural_lead': 1}, result.kind_counts)
        self.assertEqual(1, result.unpaired_sink_count)
        self.assertTrue(all(path.path_group_id for path in result.paths))
        self.assertEqual(before, raw)

    def test_local_flow_has_one_node_no_fake_self_edge_and_complete_detail(self):
        result = SecurityPathProjection({'security_evidence': fixture().model_dump()})
        graph = result.build()
        self.assertEqual(1, len(graph.graph.nodes))
        self.assertEqual([], graph.graph.edges)
        detail = result.detail('candidate')
        self.assertEqual(['raw_value'], detail.steps[0].uses)
        self.assertEqual(['value'], detail.steps[0].defines)
        self.assertEqual(7, detail.steps[0].location.line)
        self.assertEqual(['if raw_value'], [item.name for item in detail.guards])
        self.assertEqual(['basename'], [item.name for item in detail.sanitizers])
        self.assertNotIn('value = raw_value', detail.model_dump_json())

    def test_unpaired_operations_share_the_model_catalog_without_inventing_routes(self):
        """Bash/Python 一类单点操作可见，但不能因为有调用事实就补造输入传播。"""
        pack = fixture().model_dump()
        pack.update(candidates=[], dataflows={}, facts={})
        nodes = []
        for provider, names in [('BashProvider', ['subprocess.run', 'subprocess.check_output', 'subprocess.Popen', 'subprocess.check_output']),
                                ('PythonProvider', ['eval'])]:
            path = f'src/{provider.lower()}.py'
            symbol = f'{path}::{provider}::_query'
            nodes.append({'id': symbol, 'name': '_query', 'kind': 'method', 'level': 'method', 'file': path})
            for line, name in enumerate(names, 20):
                item = fact(f'{provider}-{line}', 'sink', line, path, name=name).model_copy(update={'symbol': symbol})
                pack['facts'][item.fact_id] = item.model_dump()
        raw = {'security_evidence': pack, 'dependency_graph': {'nodes': nodes, 'edges': []}}
        before = copy.deepcopy(raw)
        projection = SecurityPathProjection(raw)
        result = projection.build()
        self.assertFalse(result.paths)
        self.assertFalse(result.graph.edges)
        self.assertEqual(5, result.unpaired_sink_count)
        self.assertEqual(5, len(result.operations))
        self.assertEqual({'BashProvider._query', 'PythonProvider._query'}, {node.name for node in result.graph.nodes})
        self.assertEqual(2, len(result.graph.nodes))  # 同一函数的多处 Sink 不重复创建函数节点。
        for operation in result.operations:
            self.assertEqual('unpaired_operation', operation.kind)
            self.assertNotIn('source', operation.model_dump())
            self.assertNotIn('path_group_id', operation.model_dump())
            detail = projection.detail(operation.id)
            self.assertEqual(['sink'], [step.kind for step in detail.steps])
            self.assertEqual('unknown', detail.steps[0].certainty)
            self.assertEqual(operation.sink.location, detail.steps[0].location)
            self.assertFalse(detail.steps[0].uses or detail.steps[0].defines or detail.guards or detail.sanitizers)
        self.assertEqual(before, raw)

    def test_operation_on_existing_path_node_does_not_change_route_membership(self):
        raw = artifact()
        result = SecurityPathProjection(raw).build()
        operation = result.operations[0]
        self.assertEqual('unpaired', operation.id)
        self.assertIn(operation.node_id, result.paths[0].node_ids)
        self.assertEqual(['call'], [edge.id for edge in result.graph.edges])
        self.assertFalse(SecurityPathProjection(raw).detail('unpaired').value_boundary_count)

    def test_operation_limits_and_unsafe_locations_remain_explicit(self):
        raw = artifact()
        # 独立操作的宿主函数与候选节点不同；显示上限不能遗留无效节点引用。
        raw['security_evidence']['facts']['unpaired']['symbol'] = 'src/other.py::Provider::_query'
        result = SecurityPathProjection(raw, GraphExchangeNormalizer(max_nodes=1)).build()
        operation = result.operations[0]
        self.assertEqual('', operation.node_id)
        self.assertGreater(operation.projection_missing, 0)
        raw['security_evidence']['facts']['unpaired']['symbol'] = 'C:/private.py::handler'
        raw['security_evidence']['facts']['unpaired']['location']['path'] = '../private.py'
        raw['security_evidence']['facts']['unpaired']['metadata']['secret'] = 'DO-NOT-EXPOSE'
        projection = SecurityPathProjection(raw)
        operation = projection.build().operations[0]
        self.assertEqual('', operation.node_id)
        self.assertIsNone(operation.sink.location)
        self.assertEqual(1, projection.detail(operation.id).unavailable_locations)
        self.assertNotIn('DO-NOT-EXPOSE', projection.build().model_dump_json())

    def test_duplicate_calls_remap_membership_and_different_relations_survive(self):
        raw = artifact()
        pack = raw['security_evidence']
        for name, relation in [('call2', 'calls'), ('construct', 'instantiates')]:
            pack['call_edges'][name] = {**pack['call_edges']['call'], 'edge_id': name, 'relation': relation}
            pack['candidates'].append({**pack['candidates'][1], 'candidate_id': name, 'call_edge_ids': [name]})
        result = SecurityPathProjection(raw).build()
        self.assertEqual(2, len(result.graph.edges))
        by_id = {path.id: path for path in result.paths}
        self.assertEqual(by_id['remote'].edge_ids, by_id['call2'].edge_ids)
        self.assertNotEqual(by_id['remote'].edge_ids, by_id['construct'].edge_ids)
        self.assertEqual(2, next(edge for edge in result.graph.edges if edge.relation == 'calls').occurrence_count)

    def test_empty_old_partial_and_missing_references_are_not_safe_results(self):
        self.assertFalse(SecurityPathProjection({}).build().available)
        pack = fixture().model_dump()
        pack.update(completed=False, candidates=[], dataflows={})
        empty = SecurityPathProjection({'security_evidence': pack}).build()
        self.assertTrue(empty.available)
        self.assertFalse(empty.completed)
        self.assertFalse(empty.paths)
        raw = artifact()
        raw['security_evidence']['candidates'][1]['call_edge_ids'].append('missing')
        result = SecurityPathProjection(raw).build()
        self.assertEqual(1, next(path for path in result.paths if path.id == 'remote').projection_missing)
        raw['security_evidence']['candidates'][1]['sink_fact_id'] = 'missing-fact'
        self.assertEqual(1, SecurityPathProjection(raw).build().unavailable_candidates)

    def test_display_limits_are_explicit_and_do_not_leave_dangling_members(self):
        result = SecurityPathProjection(artifact(), GraphExchangeNormalizer(max_nodes=1)).build()
        self.assertTrue(result.graph.summary.truncated)
        nodes, edges = {item.id for item in result.graph.nodes}, {item.id for item in result.graph.edges}
        for path in result.paths:
            self.assertTrue(set(path.node_ids) <= nodes)
            self.assertTrue(set(path.edge_ids) <= edges)
        self.assertGreater(sum(path.projection_missing for path in result.paths), 0)

    def test_host_paths_and_unrelated_artifacts_never_cross_the_api(self):
        raw = artifact()
        raw['api_key'] = 'DO-NOT-EXPOSE'
        pack = raw['security_evidence']
        pack['facts']['source']['location']['path'] = 'C:/private/secret.py'
        pack['dataflows']['flow']['steps'][0]['location']['path'] = '../private.py'
        projection = SecurityPathProjection(raw)
        self.assertIsNone(projection.build().paths[0].source.location)
        self.assertIsNone(projection.detail('candidate').steps[0].location)
        self.assertNotIn('DO-NOT-EXPOSE', projection.build().model_dump_json())
        self.assertEqual('', projection.safe_symbol('C:/private.py::handler'))

    def test_saved_flows_outside_llm_candidate_budget_are_visible_without_mutating_pack(self):
        raw = artifact()
        pack = raw['security_evidence']
        pack['dataflows']['extra'] = {**copy.deepcopy(pack['dataflows']['flow']), 'flow_id': 'extra',
            'scope': 'interprocedural', 'sink_fact_id': 'remote-sink', 'call_edge_ids': ['outside-budget']}
        pack['coverage']['candidate_limit_reached'] = True
        raw['dependency_graph']['links'].append({'id': 'outside-budget', 'source': pack['facts']['source']['symbol'],
            'target': pack['facts']['remote-sink']['symbol'], 'relation': 'calls',
            'callsite': {'path': 'src/impl.py', 'line': 6}})
        before = copy.deepcopy(raw)
        projection = SecurityPathProjection(raw)
        result = projection.build()
        self.assertEqual(3, len(result.paths))
        self.assertEqual(2, result.candidate_count)
        self.assertEqual(1, result.additional_dataflow_count)
        self.assertTrue(result.analysis_truncated)
        added = next(path for path in result.paths if path.id.startswith('display-flow:'))
        self.assertTrue(added.edge_ids)
        self.assertTrue(projection.detail(added.id).steps)
        self.assertEqual(before, raw)
        pack['dataflows']['extra']['source_fact_id'] = 'not-retained'
        self.assertEqual(1, SecurityPathProjection(raw).build().unavailable_dataflows)


class SecurityPathAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_is_bounded_invalidates_and_checks_ownership_even_on_hit(self):
        repository = Mock(load=Mock(return_value=artifact()), revision=Mock(return_value=(1, 123, 8)))
        projects = Mock(get_owned=Mock(return_value=object()))
        service = ProjectQueryService(projects=projects, artifacts=repository)
        await service.security_paths('project', 'owner')
        await service.security_paths('project', 'owner', 'candidate')
        self.assertEqual('unpaired', (await service.security_paths('project', 'owner', 'unpaired'))['id'])
        self.assertEqual(1, repository.load.call_count)
        projects.get_owned.return_value = None
        with self.assertRaises(ProjectQueryError):
            await service.security_paths('project', 'other-user', 'candidate')
        projects.get_owned.return_value = object()
        repository.revision.return_value = (2, 123, 9)
        await service.security_paths('project', 'owner')
        self.assertEqual(2, repository.load.call_count)
        await service.security_paths('other-project', 'owner')
        await service.security_paths('project', 'owner')
        self.assertEqual(4, repository.load.call_count)
        repository.revision.return_value = None
        repository.load.return_value = None
        with self.assertRaises(ProjectQueryError):
            await service.security_paths('project', 'owner')
        self.assertIsNone(service._security_cache)

    async def test_service_checks_ownership_before_read_and_preserves_artifact(self):
        loader = Mock(return_value=artifact())
        service = ProjectQueryService(projects=Mock(get_owned=Mock(return_value=None)), artifact_loader=loader)
        with self.assertRaises(ProjectQueryError) as error:
            await service.security_paths('project', 'other-user')
        self.assertEqual(404, error.exception.status_code)
        loader.assert_not_called()
        service.projects.get_owned.return_value = object()
        self.assertEqual(2, len((await service.security_paths('project', 'owner'))['paths']))
        self.assertEqual('candidate', (await service.security_paths('project', 'owner', 'candidate'))['id'])
        with self.assertRaises(ProjectQueryError) as missing:
            await service.security_paths('project', 'owner', 'nonexistent')
        self.assertEqual(404, missing.exception.status_code)
        loader.return_value = {'security_evidence': {'schema_version': 'broken'}}
        with self.assertRaises(ProjectQueryError) as invalid:
            await service.security_paths('project', 'owner')
        self.assertEqual(409, invalid.exception.status_code)

    async def test_both_routes_are_read_only_and_pass_user_context(self):
        app = FastAPI()
        app.include_router(router)
        service = Mock()
        from unittest.mock import AsyncMock
        service.security_paths = AsyncMock(return_value={'available': False})
        with patch('backend.app.api.project.project_query_service', service):
            async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
                for suffix, candidate in [('', None), ('/candidate', 'candidate'), ('/unpaired', 'unpaired')]:
                    response = await client.get('/api/projects/project/security-paths' + suffix,
                                                headers={'X-User-Id': 'owner'})
                    self.assertEqual(200, response.status_code)
                    service.security_paths.assert_awaited_with('project', 'owner', candidate)


if __name__ == '__main__':
    unittest.main()
