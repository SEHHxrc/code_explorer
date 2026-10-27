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
