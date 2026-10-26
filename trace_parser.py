"""Allowlisted readers for explicitly supplied Codex and Claude JSONL traces.

Only normalized labels and timing/status metadata leave this module. Raw event
objects are never retained, exported, or included in an exception message.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path

MAX_TRACE_BYTES = 10 * 1024 * 1024
MAX_LINE_BYTES = 256 * 1024
MAX_EVENTS = 10_000
TOOL_LABEL = re.compile(r"^[A-Za-z0-9_.:/@+()-]{1,100}$")
AGENT_LABEL = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SECRET_MARKER = re.compile(r"(?i)(?:canary|sk-[\w-]{8,}|gh[pousr]_[\w]{8,}|bearer\s+\S{12,}|(?:token|password|secret|api[_-]?key)\s*[=:]\s*\S+)")


def _safe_name(value, pattern):
    if not isinstance(value, str):
        return None
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value).strip()
    if len(value) > 128 or not pattern.fullmatch(value) or SECRET_MARKER.search(value):
        return None
    return value


def _stamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).isoformat()
    except ValueError:
        return None


def _opaque(value, prefix):
    if not isinstance(value, str) or not value or len(value) > 256:
        return None
    return prefix + hashlib.sha256(value.encode('utf-8', 'replace')).hexdigest()[:16]


def _duration_ms(value):
    if type(value) in (int, float) and 0 <= value <= 86_400_000:
        return int(value)
    return None


def _delta_ms(start, end):
    if not start or not end:
        return None
    try:
        delta = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000
        return int(delta) if 0 <= delta <= 86_400_000 else None
    except (ValueError, TypeError):
        return None


def _agent_fields(row, payload):
    agent_raw = (row.get('agentName') or row.get('agent_type') or row.get('subagentType') or
                 payload.get('agent_name') or payload.get('agent_type'))
    agent_id = row.get('agentId') or payload.get('agent_id')
    if agent_raw is not None:
        agent_name = _safe_name(agent_raw, AGENT_LABEL) or '代理未知'
    elif isinstance(agent_id, str):
        agent_name = '代理 ' + hashlib.sha256(agent_id.encode()).hexdigest()[:6]
    else:
        agent_name = '主代理'
    agent_key = _opaque(agent_id, 'agent:') if isinstance(agent_id, str) else ('agent:main' if agent_name == '主代理' else None)
    parent_id = row.get('parent_agent_id') or row.get('parentAgentId') or payload.get('parent_agent_id') or payload.get('parentAgentId')
    parent_key = _opaque(parent_id, 'agent:') if isinstance(parent_id, str) else None
    return agent_name, agent_key, parent_key


def _codex(row, active_session):
    kind = row.get('type')
    payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    if kind == 'session_meta':
        current = payload.get('id')
        return current if isinstance(current, str) else active_session, None, True
    stamp = _stamp(row.get('timestamp') or payload.get('timestamp'))
    event_kind = tool = call_id = status = None
    duration = _duration_ms(payload.get('duration_ms'))
    item_type = payload.get('type') if kind == 'response_item' else payload.get('type') if kind == 'event_msg' else None
    if kind == 'response_item' and item_type in ('function_call', 'mcp_tool_call', 'tool_call'):
        event_kind, status = 'tool', 'started'
        tool = payload.get('name') or payload.get('tool_name')
        call_id = payload.get('call_id') or payload.get('id')
    elif kind == 'response_item' and item_type in ('function_call_output', 'mcp_tool_call_output', 'tool_result'):
        event_kind, status = 'tool', 'reported'
        tool = payload.get('name') or payload.get('tool_name')
        call_id = payload.get('call_id') or payload.get('id') or payload.get('call_id')
        if type(payload.get('is_error')) is bool and payload['is_error']:
            status = 'error'
    elif kind == 'event_msg' and item_type in ('mcp_tool_call_begin', 'tool_call_begin', 'function_call_begin'):
        event_kind, status = 'tool', 'started'
        tool = payload.get('tool_name') or payload.get('name')
        call_id = payload.get('call_id') or payload.get('id')
    elif kind == 'event_msg' and item_type in ('mcp_tool_call_end', 'tool_call_end', 'function_call_end'):
        event_kind, status = 'tool', 'reported'
        tool = payload.get('tool_name') or payload.get('name')
        call_id = payload.get('call_id') or payload.get('id')
        if type(payload.get('is_error')) is bool and payload['is_error']:
            status = 'error'
    elif kind == 'event_msg' and item_type in ('agent_spawn', 'subagent_spawn'):
        event_kind, status = 'agent', 'reported'
        tool = payload.get('agent_name')
        call_id = payload.get('agent_id') or payload.get('id')
    if not event_kind:
        return active_session, None, False
    name, agent_key, parent_agent_key = _agent_fields(row, payload)
    safe_tool = (_safe_name(tool, TOOL_LABEL) or '工具名称已隐藏') if event_kind == 'tool' else None
    return active_session, {'at': stamp, 'kind': event_kind, 'tool': safe_tool, 'agent': name,
        'agent_key': agent_key, 'parent_agent_key': parent_agent_key, 'status': status,
        'duration_ms': duration, 'call_key': _opaque(call_id, 'call:') if call_id is not None else None}, False


def _claude(row):
    session = row.get('sessionId')
    kind = row.get('type')
    stamp = _stamp(row.get('timestamp'))
    message = row.get('message') if isinstance(row.get('message'), dict) else {}
    content = message.get('content') if isinstance(message.get('content'), list) else []
    agent, agent_key, parent_agent_key = _agent_fields(row, message)
    events = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get('type')
        if kind == 'assistant' and item_type == 'tool_use':
            tool = _safe_name(item.get('name'), TOOL_LABEL) or '工具名称已隐藏'
            events.append({'at': stamp, 'kind': 'tool', 'tool': tool, 'agent': agent,
                'agent_key': agent_key, 'parent_agent_key': parent_agent_key, 'status': 'started',
                'duration_ms': None, 'call_key': _opaque(item.get('id'), 'call:')})
        elif kind == 'user' and item_type == 'tool_result':
            events.append({'at': stamp, 'kind': 'tool', 'tool': None, 'agent': agent,
                'agent_key': agent_key, 'parent_agent_key': parent_agent_key,
                'status': 'error' if item.get('is_error') is True else 'reported',
                'duration_ms': None, 'call_key': _opaque(item.get('tool_use_id'), 'call:')})
    return session if isinstance(session, str) else None, events


def _merge_calls(events):
    merged, by_call = [], {}
    for event in events:
        call_key = event.get('call_key')
        if call_key and call_key in by_call:
            start = by_call[call_key]
            if event.get('status') in ('reported', 'error'):
                start['status'] = event['status']
                start['duration_ms'] = event.get('duration_ms') or _delta_ms(start.get('at'), event.get('at'))
                if not start.get('tool') and event.get('tool'):
                    start['tool'] = event['tool']
            continue
        row = dict(event)
        if call_key:
            by_call[call_key] = row
        merged.append(row)
    for event in merged:
        event.pop('call_key', None)
        if event['kind'] == 'tool' and not event.get('tool'):
            event['tool'] = '工具名称未知'
    return merged


def parse_trace_file(path):
    """Read one bounded JSONL file and return allowlisted observations."""
    path = Path(path).expanduser()
    result = {'source': str(path), 'status': 'ok', 'format': 'unknown', 'sessions': []}
    try:
        resolved = path.resolve(strict=True)
        st = resolved.stat()
        if not stat.S_ISREG(st.st_mode) or any(p.suffix.lower() in ('.dtbase2', '.dtsparse', '.dtarchive') for p in (resolved, *resolved.parents)):
            result['status'] = 'unsupported_file'
            return result
        if st.st_size > MAX_TRACE_BYTES:
            result['status'] = 'too_large'
            return result
        sessions, active_session = {}, None
        default_session = _opaque(str(resolved), 'session:')
        total_bytes = total_lines = total_events = malformed = 0
        with resolved.open('rb') as stream:
            while True:
                raw_line = stream.readline(MAX_LINE_BYTES + 1)
                if not raw_line:
                    break
                total_bytes += len(raw_line)
                total_lines += 1
                if total_bytes > MAX_TRACE_BYTES:
                    result['status'] = 'too_large'
                    break
                if total_lines > MAX_EVENTS:
                    result['status'] = 'event_limit'
                    break
                if len(raw_line) > MAX_LINE_BYTES:
                    malformed += 1
                    # readline(size) may stop in the middle of one physical
                    # line. Drain the rest in bounded chunks so a valid JSON
                    # suffix cannot be mistaken for a new record.
                    while not raw_line.endswith(b'\n'):
                        chunk = stream.readline(min(64 * 1024, MAX_TRACE_BYTES - total_bytes + 1))
                        if not chunk:
                            break
                        total_bytes += len(chunk)
                        if total_bytes > MAX_TRACE_BYTES:
                            result['status'] = 'too_large'
                            break
                        raw_line = chunk
                    if result['status'] == 'too_large':
                        break
                    continue
                try:
                    row = json.loads(raw_line)
                except (ValueError, UnicodeDecodeError):
                    malformed += 1
                    continue
                if not isinstance(row, dict):
                    malformed += 1
                    continue
                if row.get('type') in ('session_meta', 'response_item', 'event_msg'):
                    result['format'] = 'codex-jsonl'
                    active_session, event, _ = _codex(row, active_session)
                    session_id, events = active_session, [event] if event else []
                elif row.get('type') in ('assistant', 'user') and isinstance(row.get('sessionId'), str):
                    result['format'] = 'claude-jsonl'
                    session_id, events = _claude(row)
                    if row.get('isSidechain') is True and not row.get('agentId'):
                        # No stable branch identity is available; preserve the
                        # observation under an explicit unknown branch.
                        for event in events:
                            event['agent'] = '代理未知'
                            event['agent_key'] = None
                else:
                    continue
                sid = _opaque(session_id, 'session:') if session_id else default_session
                bucket = sessions.setdefault(sid, {'id': sid, 'label': '会话 ' + sid[-6:], 'events': [], 'source': str(resolved)})
                for event in events:
                    total_events += 1
                    if total_events > MAX_EVENTS:
                        result['status'] = 'event_limit'
                        break
                    bucket['events'].append(event)
                if result['status'] == 'event_limit':
                    break
        for bucket in sessions.values():
            bucket['events'] = _merge_calls(bucket['events'])
            bucket['events'].sort(key=lambda e: (e.get('at') or '', e.get('tool') or '', e['kind']))
            for event in bucket['events']:
                event['parentage'] = 'unknown'
                if event.get('parent_agent_key'):
                    event['parentage'] = 'known'
                event['id'] = 'observation:' + hashlib.sha256((bucket['id'] + json.dumps(event, sort_keys=True)).encode()).hexdigest()[:18]
            bucket['events'] = [e for e in bucket['events'] if e.get('tool') or e['kind'] == 'agent']
        if malformed and result['status'] == 'ok':
            result['status'] = 'partial'
            result['malformed_lines'] = malformed
        result['sessions'] = list(sessions.values())
        return result
    except OSError as exc:
        result['status'] = type(exc).__name__
        return result

