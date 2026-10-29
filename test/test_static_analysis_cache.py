"""有界纯语法缓存的隔离、深度、不可变性和产物等价回归；不调用模型。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import asdict
from inspect import signature
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app.services.syntax_analysis import DEFAULT_TREE_SITTER_PARSER_POOL, walk_named
from backend.app.services.syntax_analysis.javascript_bindings import (
    javascript_binding_scope, javascript_value, javascript_pattern, resolve_javascript_binding,
)
from backend.app.services.program_graph import ProgramGraphService
from backend.app.services.program_graph.contracts import FunctionProgramGraph, ProgramGraphLocation, ProgramGraphNode, ProgramGraphEdge
from backend.app.services.program_graph.passes import ReachingDefinitionsPass
from backend.app.services.security_analysis.scanner import SecurityScanner
from backend.app.services.security_analysis.service import SecurityAnalysisService
from backend.app.services.program_index.identity import _file_identity, ProgramIdentity


class StaticAnalysisCacheTests(unittest.TestCase):
    def parse(self, source, grammar='javascript'):
        return DEFAULT_TREE_SITTER_PARSER_POOL.get(grammar).parse(source)

    def test_cache_hits_are_immutable_equivalent_and_depth_sensitive(self):
        source=b'const {a: x, b: y = fallback, ...rest} = {a: recv.read(input), b: fixed};'
        tree=self.parse(source)
        declaration=next(node for node in walk_named(tree.root_node) if node.type=='variable_declarator')
        target=declaration.child_by_field_name('name')
        value=declaration.child_by_field_name('value')
        expected=resolve_javascript_binding(target,value,source,'file.js')
        with javascript_binding_scope(source,'file.js',max_entries=16) as cache:
            actual=resolve_javascript_binding(target,value,source,'file.js')
            self.assertEqual(expected,actual)
            self.assertIs(actual,resolve_javascript_binding(target,value,source,'file.js'))
            shallow=javascript_value(value,source,'file.js',0)
            deep=javascript_value(value,source,'file.js',25)
            self.assertNotEqual(shallow,deep)
            self.assertTrue(deep.uncertain)
            self.assertEqual('unknown',javascript_pattern(target,source,'file.js',25).kind)
            self.assertGreater(cache.functions[resolve_javascript_binding].cache_info().hits,0)
            self.assertLessEqual(cache.functions[javascript_value].cache_info().currsize,16)
        self.assertEqual(0,cache.functions[javascript_value].cache_info().currsize)
        self.assertEqual(0,cache.functions[resolve_javascript_binding].cache_info().currsize)

    def test_source_path_and_tree_identity_do_not_cross_contaminate(self):
        source=b'const value = left;'
        other=b'const value = evil;'
        first=self.parse(source)
        second=self.parse(other)
        node=next(node for node in walk_named(first.root_node) if node.type=='identifier' and node.text==b'left')
        other_node=next(node for node in walk_named(second.root_node) if node.type=='identifier' and node.text==b'evil')
        with javascript_binding_scope(source,'file.js'):
            self.assertEqual(('left',),javascript_value(node,source,'file.js').variables)
            self.assertEqual(('evil',),javascript_value(node,other,'file.js').variables)
            self.assertNotEqual(javascript_value(node,source,'file.js').origins,javascript_value(node,source,'other.js').origins)
            # 即使两棵树坐标相同，仍按传入源码计算，不能按字节范围误命中另一棵树。
            other_value=javascript_value(other_node,source,'file.js')
            self.assertEqual(('left',),other_value.variables)
            self.assertIsNot(javascript_value(node,source,'file.js'),other_value)

    def test_shared_decorator_preserves_keyword_calls_and_creates_only_used_caches(self):
        source=b'const {key: result} = {key: input};'
        declaration=next(node for node in walk_named(self.parse(source).root_node) if node.type=='variable_declarator')
        target=declaration.child_by_field_name('name')
        value=declaration.child_by_field_name('value')
        expected=resolve_javascript_binding(target,value,source,'file.js')
        with javascript_binding_scope(source,'file.js',max_entries=16) as cache:
            self.assertEqual({},cache.functions)
            for function in (javascript_pattern,javascript_value,resolve_javascript_binding):
                self.assertEqual(signature(function.__wrapped__),signature(function))
            arguments=dict(target=target,value=value,source=source,path='file.js')
            actual=resolve_javascript_binding(**arguments)
            self.assertEqual(expected,actual)
            self.assertIs(actual,resolve_javascript_binding(**arguments))
            self.assertEqual(javascript_value(value,source,'file.js',25),
                             javascript_value(node=value,source=source,path='file.js',depth=25))
            self.assertEqual(javascript_pattern(target,source,'file.js'),
                             javascript_pattern(node=target,source=source,path='file.js'))
            before=cache.functions[javascript_value].cache_info()
            copied=memoryview(source).tobytes()
            self.assertIsNot(source,copied)
            self.assertEqual(javascript_value(value,source,'file.js'),javascript_value(value,copied,'file.js'))
            self.assertEqual(before.misses,cache.functions[javascript_value].cache_info().misses)
        for function in cache.functions.values():self.assertEqual(0,function.cache_info().currsize)
        with self.assertRaises(ValueError):
            with javascript_binding_scope(source,'file.js',max_entries=0):pass

    def test_nested_scope_and_failure_restore_outer_cache_and_clear_nodes(self):
        source=b'const x = input;'
        tree=self.parse(source)
        node=tree.root_node
        with javascript_binding_scope(source,'outer.js') as outer:
            first=javascript_value(node,source,'outer.js')
            with self.assertRaises(RuntimeError):
                with javascript_binding_scope(source,'inner.js') as inner:
                    javascript_value(node,source,'inner.js')
                    raise RuntimeError('test')
            self.assertEqual(0,inner.functions[javascript_value].cache_info().currsize)
            self.assertIs(first,javascript_value(node,source,'outer.js'))
        self.assertEqual(0,outer.functions[javascript_value].cache_info().currsize)

    def test_thread_scopes_and_lru_capacity_are_bounded(self):
        def probe(path):
            source=b'const value = input; const other = second;'
            tree=self.parse(source)
            with javascript_binding_scope(source,path,max_entries=2) as cache:
                for node in walk_named(tree.root_node):javascript_value(node,source,path)
                self.assertLessEqual(cache.functions[javascript_value].cache_info().currsize,2)
                result=javascript_value(tree.root_node,source,path)
            self.assertEqual(0,cache.functions[javascript_value].cache_info().currsize)
            return result.origins
        with ThreadPoolExecutor(max_workers=2) as pool:
            left,right=pool.map(probe,['one.js','two.js'])
        self.assertNotEqual(left,right)

    def test_nested_call_does_not_treat_callee_name_as_input(self):
        source=b'const result = recv.execute(outer(inner(input)));'
        tree=self.parse(source)
        value=next(node for node in walk_named(tree.root_node) if node.type=='variable_declarator').child_by_field_name('value')
        result=javascript_value(value,source,'file.js')
        self.assertEqual(('input','recv'),result.variables)
        self.assertTrue(result.uncertain)

    def test_path_cache_keeps_normalization_and_has_fixed_capacity(self):
        self.assertEqual('pkg/app.py',ProgramIdentity.file_id('.\\pkg\\app.py'))
        self.assertEqual('../app.py',ProgramIdentity.file_id('../app.py'))
        self.assertEqual('',ProgramIdentity.file_id(None))
        self.assertEqual(4096,_file_identity.cache_info().maxsize)
        location=ProgramIdentity.location_id('pkg/main.py',3,4,5,6)
        self.assertEqual(ProgramIdentity.callsite_id('pkg/main.py',3,4,5,6),ProgramIdentity.callsite_for_location(location))
        with self.assertRaises(ValueError):ProgramIdentity.callsite_for_location('untrusted invalid identity')

    def test_js_ts_graphs_facts_and_flows_equal_without_cache(self):
        samples={
            'app.js': "const cp = require('child_process'); function run(input) { const { command, safe = 'fixed' } = { command: input }; cp.exec(command); return safe; }",
            'server.ts': "import * as cp from 'child_process'; function execute(value: string) { const {a: x, ...rest} = {a: value, b: 'constant'}; cp.exec(x); return rest; }",
            'view.vue': '<script setup>import {ref} from "vue"; const value=ref("");</script><template><input v-model="value"><div v-html="value"></div></template>',
        }
        with tempfile.TemporaryDirectory() as directory:
            for name,source in samples.items():Path(directory,name).write_text(source,encoding='utf-8')
            graph={'nodes':[{'kind':'module','file':name,'lang':'typescript' if name.endswith('.ts') else 'javascript'} for name in samples]}
            scan=SecurityScanner().scan(directory,dependency_graph=graph)
            program=ProgramGraphService().analyze(directory,dependency_graph=graph)
            pack=SecurityAnalysisService().analyze(project_root=directory,dependency_graph=graph)
            with patch('backend.app.services.security_analysis.language_adapters.treesitter.javascript_binding_scope',side_effect=lambda *args,**kwargs:nullcontext()), \
                    patch('backend.app.services.program_graph.frontends.treesitter.javascript_binding_scope',side_effect=lambda *args,**kwargs:nullcontext()):
                self.assertEqual(asdict(scan),asdict(SecurityScanner().scan(directory,dependency_graph=graph)))
                self.assertEqual(program.model_dump(),ProgramGraphService().analyze(directory,dependency_graph=graph).model_dump())
                self.assertEqual(pack.model_dump(),SecurityAnalysisService().analyze(project_root=directory,dependency_graph=graph).model_dump())

    def test_common_definition_index_preserves_parent_overwrite_and_may_semantics(self):
        for language in ('python','java','go','javascript','c','cpp'):
            for certainty in ('must','may'):
                with self.subTest(language=language,certainty=certainty):
                    location=ProgramGraphLocation(path='file',line=1,column=1,end_line=1,end_column=1,location_id='location')
                    nodes={name:ProgramGraphNode(node_id=name,kind='operation',language=language,method_id='method') for name in ('entry','write','read','exit')}
                    nodes['entry'].definitions=['root.child','root[0]','root2.child']
                    nodes['write'].definitions=['root']
                    nodes['write'].certainty=certainty
                    nodes['read'].uses=['root.child','root[0]','root2.child']
                    graph=FunctionProgramGraph(method_id='method',symbol_id='symbol',language=language,name='run',location=location,
                        entry_node_id='entry',exit_node_id='exit',nodes=nodes,edges=[
                            ProgramGraphEdge(edge_id=str(index),source=source,target=target,kind='cfg')
                            for index,(source,target) in enumerate(zip(('entry','write','read'),('write','read','exit')))])
                    result=ReachingDefinitionsPass().apply(graph)
                    reaching={(edge.source,edge.source_variable,edge.variable) for edge in result.edges if edge.kind=='reaching_def'}
                    owner='write' if certainty=='must' else 'entry'
                    parent='root' if certainty=='must' else 'root.child'
                    self.assertIn((owner,parent,'root.child'),reaching)
                    self.assertIn((owner,'root' if certainty=='must' else 'root[0]','root[0]'),reaching)
                    self.assertIn(('entry','root2.child','root2.child'),reaching)
                    self.assertEqual(3,len(graph.edges))  # 原 CFG 未原地改写。


if __name__=='__main__':
    unittest.main()
