import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector import Collector, mermaid


class ProjectDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root, self.user = self.base / 'dotfiles', self.base / 'user'
        self.root.mkdir(); self.user.mkdir()
        self.alpha, self.beta, self.unlisted = (self.base / name for name in ('alpha', 'beta', 'unlisted'))
        for path in (self.alpha, self.beta, self.unlisted): path.mkdir()
        self.collector = Collector(self.root, self.user, [self.alpha, self.beta], default_projects=False)

    def tearDown(self):
        self.temp.cleanup()

    def test_project_scopes_client_ownership_hooks_and_claude_projects(self):
        (self.alpha / '.mcp.json').write_text(json.dumps({'mcpServers': {'shared': {'command': 'python3', 'ignoredPrompt': 'CANARY_PROJECT_UNKNOWN'}}}))
        (self.beta / '.mcp.json').write_text(json.dumps({'mcpServers': {'shared': {'command': 'python3'}}}))
        claude = self.alpha / '.claude'; claude.mkdir()
        (claude / 'settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [{'matcher': 'CANARY_MATCHER', 'hooks': [{'type': 'command', 'command': 'CANARY_HOOK_COMMAND'}]}], 'UserPromptSubmit': [{'prompt': 'CANARY_PROMPT'}]}}))
        codex = self.beta / '.codex'; codex.mkdir()
        (codex / 'config.toml').write_text('[mcp_servers.shared]\ncommand="python3"\n')
        settings = self.user / '.claude.json'
        settings.write_text(json.dumps({'projects': {
            str(self.alpha.resolve()): {'mcpServers': {'claude-project': {'url': 'https://fixture.example/mcp'}}},
            str(self.unlisted.resolve()): {'mcpServers': {'outside-scope': {'command': 'CANARY_OUTSIDE'}}}}}))

        graph = self.collector.collect()
        mcp = [n for n in graph['nodes'] if n['kind'] == 'mcp' and n['label'] == 'shared']
        self.assertEqual(len(mcp), 3)
        self.assertEqual({n['project_root'] for n in mcp}, {str(self.alpha.resolve()), str(self.beta.resolve())})
        self.assertEqual({n['clients'][0] for n in mcp}, {'claude-code', 'codex'})
        self.assertTrue(all('project' in n['scopes'] for n in mcp))
        self.assertTrue(any(n['kind'] == 'mcp' and n['label'] == 'claude-project' and n['clients'] == ['claude-code'] for n in graph['nodes']))
        self.assertFalse(any(n['kind'] == 'mcp' and n['label'] == 'outside-scope' for n in graph['nodes']))
        hook = next(n for n in graph['nodes'] if n['kind'] == 'hook')
        self.assertEqual(hook['event_counts'], {'PreToolUse': 1, 'UserPromptSubmit': 1})
        exported = json.dumps(graph, ensure_ascii=False) + mermaid(graph)
        for marker in ('CANARY_PROJECT_UNKNOWN', 'CANARY_MATCHER', 'CANARY_HOOK_COMMAND', 'CANARY_PROMPT', 'CANARY_OUTSIDE'):
            self.assertNotIn(marker, exported)
        self.assertTrue(all(e['source'] in {n['id'] for n in graph['nodes']} and e['target'] in {n['id'] for n in graph['nodes']} for e in graph['edges']))

    def test_project_plugins_and_global_cache_versions_discover_mcp_and_hook_counts(self):
        plugin = self.alpha / '.claude' / 'plugins' / 'demo'
        (plugin / '.claude-plugin').mkdir(parents=True)
        (plugin / '.claude-plugin' / 'plugin.json').write_text(json.dumps({
            'name': 'demo-plugin', 'mcpServers': {'manifest-server': {'command': 'node'}},
            'hooks': {'PostToolUse': [{'matcher': 'CANARY_M', 'hooks': [{'command': 'CANARY_C'}]}]}}))
        (plugin / '.mcp.json').write_text(json.dumps({'mcpServers': {'file-server': {'command': 'node'}}}))
        cache = self.root / 'apps/codex/home/plugins/cache/provider/package'
        for version in ('1.0.0', '2.0.0'):
            folder = cache / version
            (folder / 'hooks').mkdir(parents=True)
            (folder / '.mcp.json').write_text(json.dumps({'mcpServers': {'cached-server': {'command': 'python3'}}}))
            (folder / 'hooks' / 'hooks.json').write_text(json.dumps({'hooks': {'SessionStart': [{'command': 'CANARY_CACHE_COMMAND'}]}}))

        graph = self.collector.collect()
        plugins = [n for n in graph['nodes'] if n['kind'] == 'plugin' and n['label'].startswith('package@provider')]
        self.assertEqual({n['cache_version'] for n in plugins}, {'1.0.0', '2.0.0'})
        self.assertTrue(all(n['status'] == 'cache' and 'version' not in n for n in plugins))
        self.assertTrue(any(n['kind'] == 'mcp' and n['label'] == 'manifest-server' and n['plugin'] == 'demo-plugin' for n in graph['nodes']))
        self.assertTrue(any(n['kind'] == 'mcp' and n['label'] == 'file-server' for n in graph['nodes']))
        cached_servers = [n for n in graph['nodes'] if n['kind'] == 'mcp' and n['label'] == 'cached-server']
        self.assertEqual(len(cached_servers), 2)
        self.assertTrue(all(n['status'] == 'cache' and n['enabled'] is None for n in cached_servers))
        for plugin_node in plugins:
            self.assertTrue(any(e['source'] == plugin_node['id'] and e['target'] in {n['id'] for n in cached_servers} for e in graph['edges']))
            self.assertTrue(any(e['source'] == plugin_node['id'] and graph_node['kind'] == 'hook' and e['target'] == graph_node['id'] for e in graph['edges'] for graph_node in graph['nodes']))
        exported = json.dumps(graph, ensure_ascii=False) + mermaid(graph)
        self.assertNotIn('CANARY_', exported)
        ids = {n['id'] for n in graph['nodes']}
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in graph['edges']))

    def test_default_discovery_is_bounded_and_symlink_plugin_is_skipped(self):
        workspaces = self.root / 'workspaces'; (workspaces / 'one').mkdir(parents=True)
        (workspaces / 'one' / '.mcp.json').write_text(json.dumps({'mcpServers': {'known': {'command': 'python3'}}}))
        nested = workspaces / 'codex' / 'projects' / 'two'; nested.mkdir(parents=True)
        (nested / '.mcp.json').write_text(json.dumps({'mcpServers': {'known-nested': {'command': 'python3'}}}))
        deep = workspaces / 'codex' / 'projects' / 'two' / 'nested'; deep.mkdir()
        (deep / '.mcp.json').write_text(json.dumps({'mcpServers': {'too-deep': {'command': 'python3'}}}))
        outside = self.base / 'outside'; outside.mkdir()
        (outside / '.mcp.json').write_text(json.dumps({'mcpServers': {'escaped': {'command': 'python3'}}}))
        link = workspaces / 'one' / 'plugins'; link.mkdir()
        (link / 'linked').symlink_to(outside, target_is_directory=True)

        graph = Collector(self.root, self.user).collect()
        labels = {n['label'] for n in graph['nodes'] if n['kind'] == 'mcp'}
        self.assertIn('known', labels); self.assertIn('known-nested', labels)
        self.assertNotIn('too-deep', labels); self.assertNotIn('escaped', labels)

    def test_read_cache_isolated_by_extractor_schema(self):
        self.collector.sources = {}
        path = self.alpha / '.mcp.json'
        path.write_text(json.dumps({'mcpServers': {'safe': {'command': 'node'}}, 'hooks': {'PreToolUse': [{'command': 'CANARY'}]}}))
        first = lambda d: {'names': [r['name'] for r in __import__('collector').servers_only(d.get('mcpServers'))]}
        first_result = self.collector.read(path, first)
        second_result = self.collector.read(path, self.collector._hook_inventory)
        path.write_text('{broken')
        self.assertEqual(self.collector.read(path, first), first_result)
        self.assertEqual(self.collector.read(path, self.collector._hook_inventory), second_result)
        self.assertNotIn('CANARY', json.dumps(self.collector.cache))

    def test_manifest_relative_mcp_and_hook_paths_are_confined_to_plugin_root(self):
        plugin = self.alpha / 'plugins' / 'references'
        (plugin / '.claude-plugin').mkdir(parents=True)
        (plugin / 'hooks').mkdir()
        (plugin / '.claude-plugin' / 'plugin.json').write_text(json.dumps({
            'name': 'reference-plugin', 'mcpServers': ['servers/mcp.json', '../escape.json'],
            'hooks': 'hooks/hooks.json'}))
        (plugin / 'servers').mkdir()
        (plugin / 'servers' / 'mcp.json').write_text(json.dumps({'mcpServers': {'inside-ref': {'command': 'node'}}}))
        (plugin / 'hooks' / 'hooks.json').write_text(json.dumps({'hooks': {'SessionStart': [{'command': 'CANARY_REF_COMMAND', 'matcher': 'CANARY_REF_MATCHER'}]}}))
        outside = self.base / 'escape.json'; outside.write_text(json.dumps({'mcpServers': {'outside-ref': {'command': 'node'}}}))
        graph = self.collector.collect()
        self.assertTrue(any(n['kind'] == 'mcp' and n['label'] == 'inside-ref' for n in graph['nodes']))
        self.assertFalse(any(n['kind'] == 'mcp' and n['label'] == 'outside-ref' for n in graph['nodes']))
        self.assertTrue(any(n['kind'] == 'hook' and n.get('plugin') == 'reference-plugin' and n['event_counts'] == {'SessionStart': 1} for n in graph['nodes']))
        self.assertNotIn('CANARY_REF_', json.dumps(graph, ensure_ascii=False))

    def test_global_codex_and_claude_hooks_only_export_event_counts(self):
        codex = self.root / 'apps/codex/home/hooks.json'; codex.parent.mkdir(parents=True)
        codex.write_text(json.dumps({'hooks': {'Stop': [{'command': 'CANARY_CODEX_COMMAND', 'matcher': 'CANARY'}]}}))
        claude = self.user / '.claude/settings.json'; claude.parent.mkdir(parents=True)
        claude.write_text(json.dumps({'hooks': {'SessionStart': [{'command': 'CANARY_CLAUDE_COMMAND'}]}}))
        graph = Collector(self.root, self.user, default_projects=False).collect()
        hooks = [n for n in graph['nodes'] if n['kind'] == 'hook']
        self.assertEqual({(n['clients'][0], tuple(n['event_counts'])) for n in hooks}, {('codex', ('Stop',)), ('claude-code', ('SessionStart',))})
        out = json.dumps(graph, ensure_ascii=False) + mermaid(graph)
        self.assertNotIn('CANARY', out)


if __name__ == '__main__':
    unittest.main()
