import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo_data import DemoCollector
from server import GraphState


class PublicDefaultsTests(unittest.TestCase):
    def test_default_demo_is_synthetic_and_graph_endpoints_exist(self):
        graph = DemoCollector().collect()
        serialized = json.dumps(graph, ensure_ascii=False)
        node_ids = {node['id'] for node in graph['nodes']}
        self.assertTrue(all(edge['source'] in node_ids and edge['target'] in node_ids for edge in graph['edges']))
        self.assertTrue(all(source['kind'] == 'demo' and source['path'] == 'synthetic demo data' for source in graph['sources']))
        self.assertTrue(all(node['sources'] in ([], ['synthetic demo']) for node in graph['nodes']))
        self.assertIn('合成演示会话', serialized)
        self.assertNotIn('/' + 'Users' + '/', serialized)
        self.assertNotIn('AI-' + 'Dotfiles', serialized)
        self.assertNotIn('credential' + '-broker', serialized)
        component_positions = {node['kind']: node['positions']['components'] for node in graph['nodes']}
        self.assertLess(component_positions['client']['x'], component_positions['mcp']['x'])
        self.assertEqual({component_positions[kind]['x'] for kind in ('mcp', 'skill', 'plugin', 'hook')}, {480})
        self.assertEqual(len({component_positions[kind]['y'] for kind in ('mcp', 'skill', 'plugin', 'hook')}), 4)

    def test_demo_timestamps_are_frozen_per_instance_and_scan_is_stable(self):
        collector = DemoCollector()
        first = collector.collect()
        second = collector.collect()
        self.assertEqual(first, second)

        state = GraphState(collector, 2)
        initial_revision = state.revision
        initial_signature = state.signature
        self.assertEqual(state.history, [])
        state.scan()
        self.assertEqual(state.revision, initial_revision)
        self.assertEqual(state.signature, initial_signature)
        self.assertEqual(state.history, [])
        self.assertEqual(state.snapshot()['changes'], [])


if __name__ == '__main__':
    unittest.main()
