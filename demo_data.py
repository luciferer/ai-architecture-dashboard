"""Synthetic, self-contained examples used by the safe default server mode."""
from datetime import datetime, timezone, timedelta
from collector import CLIENTS, KINDS

class DemoCollector:
    """Return hand-authored synthetic data; never inspect local configuration."""

    def __init__(self):
        self._base_time = datetime.now(timezone.utc)

    def _iso(self, seconds_ago=0):
        return (self._base_time - timedelta(seconds=seconds_ago)).isoformat()

    def collect(self):
        nodes = [
            {"id": "client:codex", "kind": "client", "label": "Codex 示例", "clients": ["codex"], "status": "configured", "sources": []},
            {"id": "demo:mcp:research", "kind": "mcp", "label": "research-server", "clients": ["codex"], "status": "configured", "enabled": True, "transport": "stdio", "env_variable_count": 0, "scopes": ["global"], "sources": ["synthetic demo"], "runtime_status": "示例；未连接"},
            {"id": "demo:skill:review", "kind": "skill", "label": "review-notes", "clients": ["codex"], "status": "discovered", "scopes": ["user"], "sources": ["synthetic demo"], "runtime_status": "示例；未执行"},
            {"id": "demo:plugin:calendar", "kind": "plugin", "label": "calendar-tools", "clients": ["codex"], "status": "configured", "enabled": True, "scopes": ["plugin"], "sources": ["synthetic demo"], "runtime_status": "示例；未探测"},
            {"id": "demo:hook:events", "kind": "hook", "label": "示例 Hooks", "clients": ["codex"], "status": "discovered", "scopes": ["global"], "event_counts": {"SessionStart": 1, "PostToolUse": 2}, "sources": ["synthetic demo"], "runtime_status": "仅示例事件数量"},
        ]
        edges = [
            {"id": "demo:edge:mcp", "source": "client:codex", "target": "demo:mcp:research", "label": "示例注册", "relation_type": "registration"},
            {"id": "demo:edge:skill", "source": "client:codex", "target": "demo:skill:review", "label": "示例发现", "relation_type": "registration"},
            {"id": "demo:edge:plugin", "source": "client:codex", "target": "demo:plugin:calendar", "label": "示例插件", "relation_type": "registration"},
            {"id": "demo:edge:hook", "source": "client:codex", "target": "demo:hook:events", "label": "示例 Hooks", "relation_type": "registration"},
        ]
        client_counts = {client: {kind: 0 for kind in KINDS} for client in CLIENTS}
        for node in nodes:
            if node["kind"] != "client":
                for client in node["clients"]:
                    client_counts[client][node["kind"]] += 1
        counts = {kind: sum(node["kind"] == kind for node in nodes) for kind in KINDS}
        counts.update(enabled_plugins=1, enabled_mcp=1)
        return {
            "nodes": nodes,
            "edges": edges,
            "counts": counts,
            "client_counts": client_counts,
            "architecture_views": [],
            "suite_summary": None,
            "runtime_traces": [{
                "id": "demo-session-001",
                "label": "合成演示会话",
                "source": "synthetic demo",
                "events": [
                    {"id": "demo-tool-1", "kind": "tool", "tool": "search_notes", "status": "reported", "at": self._iso(12), "duration_ms": 1200, "agent": "主代理", "agent_key": "agent:main", "parentage": "unknown"},
                    {"id": "demo-agent-1", "kind": "agent", "tool": "整理候选摘要", "status": "reported", "at": self._iso(10), "duration_ms": 3100, "agent": "研究代理", "agent_key": "agent:child", "parent_agent_key": "agent:main", "parentage": "known"},
                    {"id": "demo-tool-2", "kind": "tool", "tool": "read_summary", "status": "started", "at": self._iso(7), "duration_ms": None, "agent": "研究代理", "agent_key": "agent:child", "parent_agent_key": "agent:main", "parentage": "known"},
                    {"id": "demo-tool-3", "kind": "tool", "tool": "format_report", "status": "error", "at": self._iso(4), "duration_ms": 850, "agent": "主代理", "agent_key": "agent:main", "parentage": "unknown"},
                ],
            }],
            "sources": [{"path": "synthetic demo data", "status": "ok", "retained": False, "kind": "demo"}],
            "clients": CLIENTS,
            "coverage": "Synthetic demonstration only; no local configuration, log, credentials, network or external service was read.",
        }
