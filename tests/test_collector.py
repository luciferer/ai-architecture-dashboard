import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector import Collector, mermaid

class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root, self.user = self.base/'dotfiles', self.base/'user'
        self.home = self.root/'apps/codex/home'
        self.home.mkdir(parents=True); self.user.mkdir()
        self.config = self.home/'config.toml'
        self.config.write_text('[mcp_servers.base]\ncommand="python3"\n')
        self.collector = Collector(self.root, self.user)

    def tearDown(self): self.temp.cleanup()

    def skill(self, root, name):
        p=root/name;p.mkdir(parents=True);(p/'SKILL.md').write_text('---\nname: '+name+'\n---\nPRIVATE_BODY_NOT_READ\n');return p

    def test_skill_add_delete(self):
        initial=self.collector.collect();p=self.skill(self.home/'skills','new-skill')
        added=self.collector.collect();self.assertEqual(added['counts']['skill'],initial['counts']['skill']+1)
        (p/'SKILL.md').unlink();p.rmdir();self.assertEqual(self.collector.collect()['counts']['skill'],initial['counts']['skill'])

    def test_mcp_add_disable_delete(self):
        self.collector.collect();self.config.write_text('[mcp_servers.base]\ncommand="python3"\n[mcp_servers.new]\ncommand="node"\nenabled=false\n')
        data=self.collector.collect();self.assertEqual(data['counts']['mcp'],2);self.assertEqual(data['counts']['enabled_mcp'],1)
        self.config.write_text('[mcp_servers.base]\ncommand="python3"\n');self.assertEqual(self.collector.collect()['counts']['mcp'],1)

    def test_parse_failure_retains_and_recovers(self):
        self.collector.collect();self.config.write_text('[mcp_servers. broken')
        failed=self.collector.collect();self.assertEqual(failed['counts']['mcp'],1);self.assertTrue(next(n for n in failed['nodes'] if n['kind']=='mcp')['stale'])
        self.config.write_text('[mcp_servers.restored]\ncommand="node"\n');data=self.collector.collect();self.assertEqual(next(n['label'] for n in data['nodes'] if n['kind']=='mcp'),'restored')
        self.assertFalse(any(n['stale'] for n in data['nodes']))

    def test_physical_skill_deduplicates_client_edges(self):
        p=self.skill(self.home/'skills','shared');other=self.user/'.claude/skills';other.mkdir(parents=True);(other/'shared').symlink_to(p,target_is_directory=True)
        data=self.collector.collect();skills=[n for n in data['nodes'] if n['kind']=='skill'];self.assertEqual(len(skills),1);self.assertEqual(skills[0]['clients'],['claude-code','codex'])
        edges=[e for e in data['edges'] if e['target']==skills[0]['id']];self.assertEqual(len(edges),2)

    def test_secrets_excluded_from_inventory_and_export(self):
        self.config.write_text('[mcp_servers.private]\nurl="https://fixture-user:MOCK_PASSWORD@fixture.example/mcp?token=MOCK_QUERY"\nargs=["--token","MOCK_ARGUMENT"]\n[mcp_servers.private.env]\nSECRET="MOCK_ENVIRONMENT"\n')
        data=self.collector.collect();out=json.dumps(data)+mermaid(data)
        for value in ['MOCK_PASSWORD','MOCK_QUERY','MOCK_ARGUMENT','MOCK_ENVIRONMENT','fixture-user']:self.assertNotIn(value,out)
        self.assertEqual(next(n['endpoint_host'] for n in data['nodes'] if n['kind']=='mcp'),'fixture.example')

    def test_no_skill_body_or_implicit_owner_invocation(self):
        p=self.skill(self.home/'skills','owner');(p/'agents').mkdir();(p/'agents/openai.yaml').write_text('policy:\n  allow_implicit_invocation: false\n')
        data=self.collector.collect();skill=next(n for n in data['nodes'] if n['kind']=='skill');self.assertIs(skill['allow_implicit_invocation'],False);self.assertNotIn('PRIVATE_BODY_NOT_READ',json.dumps(data))

    def test_malformed_schema_retains(self):
        settings=self.user/'.claude/settings.json';settings.parent.mkdir();settings.write_text('{"enabledPlugins":{"demo@official":true}}');self.collector.collect()
        settings.write_text('{"enabledPlugins":null}');data=self.collector.collect();self.assertEqual(data['counts']['enabled_plugins'],1);self.assertTrue(any(s['status']=='error' and s['retained'] for s in data['sources']))

if __name__=='__main__':unittest.main()
