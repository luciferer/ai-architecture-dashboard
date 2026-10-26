import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector import Collector, mermaid
from trace_parser import parse_trace_file
from server import GraphState


class RuntimeTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root, self.user = self.base / 'dotfiles', self.base / 'user'
        self.root.mkdir(); self.user.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_codex_call_merge_and_session_continuity_without_raw_payload(self):
        path = self.base / 'codex.jsonl'
        rows = [
            {'type': 'session_meta', 'payload': {'id': 'session-fixture'}},
            {'type': 'response_item', 'timestamp': '2026-10-25T10:00:00+00:00', 'payload': {'type': 'function_call', 'name': 'safe_tool', 'call_id': 'call-one', 'arguments': 'CANARY_ARGUMENTS'}},
            {'type': 'response_item', 'timestamp': '2026-10-25T10:00:02+00:00', 'payload': {'type': 'function_call_output', 'call_id': 'call-one', 'output': 'CANARY_RESULT'}},
            {'type': 'response_item', 'timestamp': '2026-10-25T10:01:00+00:00', 'payload': {'type': 'function_call', 'name': 'pending_tool', 'call_id': 'call-two', 'arguments': 'CANARY_PENDING'}},
            {'type': 'event_msg', 'payload': {'type': 'turn_context', 'prompt': 'CANARY_PROMPT', 'token': 'CANARY_SECRET'}}]
        path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
        graph = Collector(self.root, self.user, default_projects=False, trace_files=[path]).collect()
        sessions = graph['runtime_traces']
        self.assertEqual(len(sessions), 1)
        events = sessions[0]['events']
        self.assertEqual(len(events), 2)
        self.assertEqual({e['tool']: e['status'] for e in events}, {'safe_tool': 'reported', 'pending_tool': 'started'})
        safe = next(e for e in events if e['tool'] == 'safe_tool')
        self.assertEqual(safe['duration_ms'], 2000)
        self.assertEqual(safe['agent'], '主代理')
        self.assertEqual(sessions[0]['id'].startswith('session:'), True)
        exported = json.dumps(graph, ensure_ascii=False) + mermaid(graph)
        for canary in ('CANARY_ARGUMENTS', 'CANARY_RESULT', 'CANARY_PENDING', 'CANARY_PROMPT', 'CANARY_SECRET', 'call-one', 'session-fixture'):
            self.assertNotIn(canary, exported)

    def test_claude_result_error_timing_and_parent_uuid_not_agent_parent(self):
        path = self.base / 'claude.jsonl'
        rows = [
            {'type': 'assistant', 'sessionId': 'claude-session', 'timestamp': '2026-10-25T11:00:00+00:00', 'uuid': 'msg-1', 'parentUuid': 'msg-parent', 'isSidechain': True, 'agentId': 'agent-private-id', 'message': {'content': [{'type': 'tool_use', 'id': 'tool-call', 'name': 'Read', 'input': {'file_path': 'CANARY_ARGS'}}]}},
            {'type': 'user', 'sessionId': 'claude-session', 'timestamp': '2026-10-25T11:00:02+00:00', 'uuid': 'msg-2', 'parentUuid': 'msg-1', 'isSidechain': True, 'agentId': 'agent-private-id', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'tool-call', 'is_error': True, 'content': 'CANARY_RESULT'}]}}]
        path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
        parsed = parse_trace_file(path)
        self.assertEqual(parsed['format'], 'claude-jsonl')
        self.assertEqual(len(parsed['sessions']), 1)
        events = parsed['sessions'][0]['events']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['status'], 'error')
        self.assertEqual(events[0]['duration_ms'], 2000)
        self.assertEqual(events[0]['parentage'], 'unknown')
        self.assertTrue(events[0]['agent'].startswith('代理 '))
        exported = json.dumps(parsed, ensure_ascii=False)
        for canary in ('CANARY_ARGS', 'CANARY_RESULT', 'tool-call', 'agent-private-id', 'msg-parent', 'msg-1'):
            self.assertNotIn(canary, exported)

    def test_explicit_agent_parent_key_is_hashed_and_only_then_linkable(self):
        path = self.base / 'branch.jsonl'
        rows = [
            {'type': 'event_msg', 'timestamp': '2026-10-25T12:00:00+00:00', 'payload': {'type': 'agent_spawn', 'agent_id': 'child-id', 'agent_name': 'researcher', 'parent_agent_id': 'parent-id'}},
            {'type': 'event_msg', 'timestamp': '2026-10-25T12:00:01+00:00', 'payload': {'type': 'tool_call_begin', 'tool_name': 'search', 'call_id': 'call-1', 'agent_id': 'child-id', 'agent_name': 'researcher', 'parent_agent_id': 'parent-id'}}]
        path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
        parsed = parse_trace_file(path)
        events = parsed['sessions'][0]['events']
        self.assertTrue(all(e['parentage'] == 'known' for e in events))
        self.assertTrue(all(e['agent_key'].startswith('agent:') and e['parent_agent_key'].startswith('agent:') for e in events))
        serialized = json.dumps(parsed)
        self.assertNotIn('child-id', serialized); self.assertNotIn('parent-id', serialized)

    def test_partial_malformed_and_file_limits_are_explicit(self):
        path = self.base / 'partial.jsonl'
        path.write_text('{not-json}\n' + json.dumps({'type': 'session_meta', 'payload': {'id': 'valid'}}) + '\n')
        self.assertEqual(parse_trace_file(path)['status'], 'partial')
        path.write_text('x' * 32 + '\n')
        with patch('trace_parser.MAX_LINE_BYTES', 16):
            self.assertEqual(parse_trace_file(path)['status'], 'partial')
        path.write_text('x' * 64)
        with patch('trace_parser.MAX_TRACE_BYTES', 32):
            self.assertEqual(parse_trace_file(path)['status'], 'too_large')

    def test_oversized_physical_line_is_discarded_as_one_record(self):
        path = self.base / 'oversized-line.jsonl'
        phantom = json.dumps({'type': 'response_item', 'timestamp': '2026-10-25T12:30:00+00:00', 'payload': {'type': 'function_call', 'name': 'phantom_tool', 'call_id': 'phantom'}})
        real = json.dumps({'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'real_tool', 'call_id': 'real'}})
        path.write_text('x' * 129 + phantom + '\n' + real + '\n')
        with patch('trace_parser.MAX_LINE_BYTES', 128):
            parsed = parse_trace_file(path)
        events = [event for session in parsed['sessions'] for event in session['events']]
        self.assertEqual(parsed['status'], 'partial')
        self.assertEqual([event['tool'] for event in events], ['real_tool'])

    def test_protected_suffix_checks_are_case_insensitive(self):
        protected = self.base / 'Library.DTBASE2'
        protected.mkdir()
        trace = protected / 'trace.jsonl'
        trace.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'hidden'}}) + '\n')
        self.assertEqual(parse_trace_file(trace)['status'], 'unsupported_file')
        self.assertEqual(Collector(self.root, self.user, project_roots=[protected], default_projects=False).project_roots, [])

    def test_secret_like_tool_and_agent_names_are_rejected_or_anonymized(self):
        path = self.base / 'unsafe-names.jsonl'
        rows = [
            {'type': 'assistant', 'sessionId': 'safe-session', 'timestamp': '2026-10-25T13:00:00+00:00', 'agentName': 'CANARY_PROMPT', 'message': {'content': [{'type': 'tool_use', 'id': 'call', 'name': 'sk-abcdef1234567890', 'input': {'token': 'CANARY_SECRET'}}]}}]
        path.write_text(json.dumps(rows[0]) + '\n')
        parsed = parse_trace_file(path)
        self.assertEqual(len(parsed['sessions'][0]['events']), 1)
        self.assertEqual(parsed['sessions'][0]['events'][0]['tool'], '工具名称已隐藏')
        self.assertNotIn('CANARY', json.dumps(parsed))

    def test_equal_session_ids_from_distinct_files_remain_selectable(self):
        first, second = self.base / 'first.jsonl', self.base / 'second.jsonl'
        raw = json.dumps({'type': 'session_meta', 'payload': {'id': 'same-provider-id'}}) + '\n' + json.dumps({'type': 'response_item', 'timestamp': '2026-10-25T14:00:00+00:00', 'payload': {'type': 'function_call', 'name': 'safe_tool', 'call_id': 'same-call'}}) + '\n'
        first.write_text(raw); second.write_text(raw)
        graph = Collector(self.root, self.user, default_projects=False, trace_files=[first, second]).collect()
        self.assertEqual(len(graph['runtime_traces']), 2)
        self.assertEqual(len({s['id'] for s in graph['runtime_traces']}), 2)
        event_ids = [e['id'] for session in graph['runtime_traces'] for e in session['events']]
        self.assertEqual(len(event_ids), 2); self.assertEqual(len(set(event_ids)), 2)

    def test_trace_changes_advance_snapshot_without_mixing_static_edges(self):
        path = self.base / 'watch.jsonl'
        def write(tool):
            path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'one'}}) + '\n' +
                json.dumps({'type': 'response_item', 'timestamp': '2026-10-25T15:00:00+00:00', 'payload': {'type': 'function_call', 'name': tool, 'call_id': 'call'}}) + '\n')
        write('first_tool')
        collector = Collector(self.root, self.user, default_projects=False, trace_files=[path])
        state = GraphState(collector, .5)
        before = state.snapshot()
        write('second_tool')
        state.scan()
        after = state.snapshot()
        self.assertGreater(after['revision'], before['revision'])
        self.assertEqual(before['nodes'], after['nodes'])
        self.assertEqual(before['edges'], after['edges'])
        self.assertNotEqual(before['runtime_traces'], after['runtime_traces'])
        self.assertFalse(any(e.get('relation_type') == 'observed' for e in after['edges']))


if __name__ == '__main__':
    unittest.main()
