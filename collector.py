"""Read-only, allowlisted configuration inventory. Never execute MCP entries."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tomllib
from urllib.parse import urlsplit
from trace_parser import parse_trace_file

CLIENTS = {"codex": "Codex", "claude-code": "Claude Code", "claude-desktop": "Claude Desktop", "antigravity": "Antigravity"}
KINDS = ("mcp", "skill", "plugin", "runtime", "cli", "sdk", "subagent", "rule", "suite", "source", "workflow", "hook")
SECRET = re.compile(r"(?:\bsk-[\w-]{16,}|\bgh[pousr]_[\w]{20,}|(?i:bearer)\s+[\w.-]{16,}|(?i:token|password|secret|api[_-]?key)\s*[=:]\s*[^\s]+)")
HOOK_EVENTS = {'PreToolUse','PostToolUse','PostToolUseFailure','Notification','UserPromptSubmit','SessionStart','SessionEnd','Stop','SubagentStart','SubagentStop','PermissionRequest','Setup','PreCompact','ConfigChange','WorktreeCreate','WorktreeRemove'}

def clean(value, limit=240):
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value))
    def public_url(match):
        try:
            p = urlsplit(match.group(0))
            return p.scheme + '://' + (p.hostname or '[已隐藏]') + (':' + str(p.port) if p.port else '')
        except ValueError: return '[URL 已隐藏]'
    text = re.sub(r'https?://[^\s]+', public_url, text)
    return SECRET.sub("[已隐藏]", text)[:limit]

def identifier(kind, value):
    return kind + ":" + hashlib.sha256(str(value).encode()).hexdigest()[:18]

def version_key(path):
    return tuple(int(v) for v in re.findall(r"\d+", path.name)) or (0,)

def endpoint(value):
    try:
        parsed = urlsplit(str(value))
        host = clean(parsed.hostname or "")
        if not host or parsed.scheme not in ("http", "https", "ws", "wss"):
            return ""
        return host + (":" + str(parsed.port) if parsed.port else "")
    except (TypeError, ValueError):
        return ""

def servers_only(mapping):
    out = []
    if not isinstance(mapping, dict):
        return out
    for name, spec in mapping.items():
        if not isinstance(spec, dict):
            continue
        command = spec.get("command", "")
        command = clean(command, 1000) if isinstance(command, str) else ""
        if "[已隐藏]" in command:
            command = ""
        remote = endpoint(spec.get("url", spec.get("serverUrl", "")))
        out.append({"key": identifier("registration", name), "name": clean(name), "enabled": spec.get("enabled", True) is not False and spec.get("disabled", False) is not True,
                    "command": command, "endpoint": remote, "transport": "URL" if remote else "stdio", "env_count": len(spec.get("env", {})) if isinstance(spec.get("env"), dict) else 0})
    return out

class Collector:
    def __init__(self, dotfiles: Path, user_root: Path, project_roots=None, trace_files=None, default_projects=True):
        self.root = dotfiles.resolve()
        self.user = user_root.resolve()
        self.project_roots = self.resolve_project_roots(project_roots or [], default_projects)
        self.trace_files = [Path(p).expanduser().resolve() for p in (trace_files or [])]
        self.cache = {}

    @staticmethod
    def _safe_project_path(root, path):
        try:
            resolved = path.resolve(strict=True)
            return resolved.is_relative_to(root.resolve()) and not any(
                part.suffix.lower() in ('.dtbase2', '.dtsparse', '.dtarchive') for part in (resolved, *resolved.parents))
        except (OSError, RuntimeError):
            return False

    def resolve_project_roots(self, explicit, default_projects):
        roots = []
        if default_projects:
            workspace = self.root / 'workspaces'
            if workspace.is_dir():
                try:
                    workspace_real = workspace.resolve(strict=True)
                    if workspace_real.is_relative_to(self.root):
                        roots.extend((p, workspace_real) for p in workspace.iterdir() if p.is_dir())
                    codex = workspace / 'codex' / 'projects'
                    if codex.is_dir():
                        codex_real = codex.resolve(strict=True)
                        if codex_real.is_relative_to(workspace_real):
                            roots.extend((p, codex_real) for p in codex.iterdir() if p.is_dir())
                except OSError:
                    pass
        roots.extend((Path(p).expanduser(), None) for p in explicit)
        out, seen = [], set()
        for path, allowed_parent in roots:
            try:
                resolved = path.resolve(strict=True)
                if not resolved.is_dir() or resolved in seen:
                    continue
                if allowed_parent and not resolved.is_relative_to(allowed_parent): continue
                if any(p.suffix.lower() in ('.dtbase2', '.dtsparse', '.dtarchive') for p in (resolved, *resolved.parents)): continue
                seen.add(resolved); out.append(resolved)
            except (OSError, RuntimeError):
                continue
        return out

    def read(self, path, extractor, fmt="json"):
        """Retain only sanitized extracted data, not raw config or credential values."""
        path = Path(path)
        key = str(path)
        cache_key = key + '\0' + getattr(extractor, '__qualname__', type(extractor).__name__) + '\0' + fmt
        prior_info = self.sources.get(key)
        info = prior_info if prior_info and prior_info.get('status') == 'error' else {"path": clean(key, 1200), "status": "ok", "retained": False}
        self.sources[key] = info
        try:
            if not path.exists():
                info["status"] = "missing"
                for cached_key in [k for k in self.cache if k.startswith(key + '\0')]: self.cache.pop(cached_key, None)
                return {}
            if path.stat().st_size > 2_000_000:
                raise ValueError("read limit")
            with path.open("rb") as f:
                raw = tomllib.load(f) if fmt == "toml" else json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("root type")
            data = extractor(raw)
            self.cache[cache_key] = data
            return data
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            info.update(status="error", retained=cache_key in self.cache, error=type(exc).__name__)
            return self.cache.get(cache_key, {})

    def collect(self):
        self.nodes, self.edges, self.sources = {}, {}, {}
        self.agent_registrations = []
        for client, name in CLIENTS.items():
            self.add_node({"id": "client:" + client, "kind": "client", "label": name, "clients": [client], "status": "configured", "sources": []})
        codex = self.root / "apps/codex/home"
        self.config("codex", codex / "config.toml", "toml")
        self.config("claude-code", self.user / ".claude.json")
        self.claude_project_inventory()
        self.config("claude-desktop", self.user / "Library/Application Support/Claude/claude_desktop_config.json")
        self.config("antigravity", self.user / ".gemini/config/mcp_config.json")
        self.claude_plugins()
        self.global_hooks()
        self.skills("codex", codex / "skills", "user")
        self.skills("codex", codex / "skills/.system", "system")
        self.skills("claude-code", self.user / ".claude/skills", "user")
        self.skills("claude-code", self.root / "apps/claude/plugins/ai-dotfiles-claude/skills", "user")
        self.plugin_cache("codex", codex / "plugins/cache")
        self.plugin_cache("claude-code", self.user / ".claude/plugins/cache")
        self.registry("antigravity", self.user / ".gemini/config/skills.json", "skill")
        self.registry("antigravity", self.user / ".gemini/config/plugins.json", "plugin")
        self.project_inventory()
        self.collect_traces()
        # A failed config read marks its retained entities without losing unrelated roots.
        for node in self.nodes.values():
            node["stale"] = any(self.sources.get(p, {}).get("status") == "error" for p in node.get("sources", []))
            node["clients"] = sorted(set(node["clients"]))
            node["sources"] = sorted(set(node.get("sources", [])))
        nodes = sorted(self.nodes.values(), key=lambda n: n["id"])
        edges = sorted(self.edges.values(), key=lambda e: e["id"])
        counts = {kind: sum(n["kind"] == kind and n.get('status') != 'proposed' for n in nodes) for kind in KINDS}
        counts["enabled_plugins"] = sum(n["kind"] == "plugin" and n.get("enabled") is True for n in nodes)
        counts["enabled_mcp"] = sum(n["kind"] == "mcp" and n.get("enabled") is True for n in nodes)
        client_counts = {c: {k: sum(n["kind"] == k and n.get('status') != 'proposed' and c in n["clients"] for n in nodes) for k in KINDS} for c in CLIENTS}
        return {"nodes": nodes, "edges": edges, "counts": counts, "client_counts": client_counts,
                "architecture_views": [], "suite_summary": None,
                "runtime_traces": getattr(self, 'runtime_traces', []),
                "sources": sorted(self.sources.values(), key=lambda s: s["path"]), "clients": CLIENTS,
                "coverage": "Known global registrations, explicitly scoped project metadata, installed/cache directories and explicitly supplied local traces; observed tool status is reported metadata, not independent verification"}

    def add_node(self, node):
        existing = self.nodes.get(node["id"])
        if existing:
            existing["clients"] += node.get("clients", [])
            existing["sources"] += node.get("sources", [])
            existing.setdefault("aliases", []).extend(node.get("aliases", []))
            existing["scopes"] = sorted(set(existing.get("scopes", []) + node.get("scopes", [])))
        else:
            self.nodes[node["id"]] = node

    def edge(self, source, target, relation):
        key = identifier("edge", source + target + relation)
        self.edges[key] = {"id": key, "source": source, "target": target, "label": relation}

    def config(self, client, path, fmt="json", scope="global", project_root=None, plugin_name=None):
        def extract(d):
            p = {}
            agents = []
            if client == "codex":
                p = {clean(k): {"enabled": v.get("enabled", True) is not False} for k, v in d.get("plugins", {}).items() if isinstance(v, dict)}
                for name, spec in d.get('agents', {}).items():
                    if not isinstance(spec, dict) or not isinstance(spec.get('config_file'), str): continue
                    role = Path(spec['config_file'])
                    if not role.is_absolute(): role = Path(path).parent / role
                    if role.resolve().is_relative_to(self.root):
                        agents.append({'name': clean(name), 'path': clean(role, 1200)})
            return {"servers": servers_only(d.get("mcp_servers", d.get("mcpServers", {}))), "plugins": p, 'agents': agents}
        data = self.read(path, extract, fmt)
        if client == 'codex' and scope == 'global': self.agent_registrations = data.get('agents', [])
        for s in data.get("servers", []):
            scope_id = str(project_root or path.parent) if scope != 'global' else 'global'
            node_id = identifier("mcp", client + scope + scope_id + str(path) + s["key"] + str(plugin_name or ''))
            cached = scope == 'plugin-cache'
            node = {"id": node_id, "kind": "mcp", "label": s["name"], "clients": [client], "status": "cache" if cached else "configured" if s["enabled"] else "disabled",
                    "enabled": None if cached else s["enabled"], "transport": s["transport"], "endpoint_host": s["endpoint"], "env_variable_count": s["env_count"], "sources": [str(path)], "runtime_status": "仅缓存配置；未核实活动状态" if cached else "未探测", "scopes": ["global"]}
            if scope != 'global':
                node.update(scopes=['plugin', 'cache'] if cached else [scope])
                if project_root: node.update(project=Path(project_root).name, project_root=str(project_root))
            if plugin_name: node['plugin'] = clean(plugin_name)
            self.add_node(node); self.edge("client:" + client, node_id, "注册")
            command = s["command"]
            if command:
                located = command if command.startswith("/") else shutil.which(command) if re.fullmatch(r"[\w.+-]{1,80}", command) else None
                label = Path(command).name if located else clean(command, 80)
                rid = identifier("runtime", located or command)
                self.add_node({"id": rid, "kind": "runtime", "label": label, "clients": [client], "status": "path" if located and Path(located).is_file() else "unresolved",
                               "path": clean(located or command, 1200), "path_exists": bool(located and Path(located).is_file()), "sources": [str(path)], "runtime_status": "未执行", "scopes": ["runtime"]})
                self.edge(node_id, rid, "启动入口")
        for name, p in data.get("plugins", {}).items():
            self.plugin(client, name, path, p["enabled"])

    def plugin(self, client, name, source, enabled=None, version=None, installed_path=None, cache_version=None, project_root=None):
        pid = identifier("plugin", client + name + ('#cache=' + str(cache_version) if cache_version else '') + ('#project=' + str(project_root) if project_root else ''))
        label = clean(name + (' · 缓存 ' + str(cache_version) if cache_version else ''))
        new = {"id": pid, "kind": "plugin", "label": label, "clients": [client], "status": "configured" if enabled is True else "disabled" if enabled is False else "cache" if cache_version else "discovered",
               "enabled": enabled, "sources": [str(source)], "runtime_status": "未探测", "scopes": ["plugin"]}
        if version and not cache_version: new["version"] = clean(version, 80)
        if cache_version: new['cache_version'] = clean(cache_version, 80)
        if project_root:
            new.update(scopes=['project', 'plugin'], project=Path(project_root).name, project_root=str(project_root))
        if installed_path: new["path"] = clean(installed_path, 1200)
        if pid in self.nodes:
            old = self.nodes[pid]
            old["sources"].append(str(source))
            for k in ("version", "path"):
                if k in new: old[k] = new[k]
        else:
            self.add_node(new)
        self.edge("client:" + client, pid, "插件")
        return pid

    def skills(self, client, root, scope, plugin=None, plugin_id=None):
        root = Path(root)
        if not root.is_dir(): return
        try:
            dirs = sorted(root.iterdir())
        except OSError:
            self.sources[str(root)] = {"path": str(root), "status": "error", "retained": False, "error": "OSError"}
            return
        for folder in dirs:
            if folder.name.startswith(".") or not folder.is_dir() or not (folder / "SKILL.md").is_file(): continue
            resolved = folder.resolve()
            if any(p.suffix.lower() in (".dtbase2", ".dtsparse", ".dtarchive") for p in resolved.parents): continue
            policy = None
            meta = folder / "agents/openai.yaml"
            if meta.is_file():
                try:
                    with meta.open() as f: header = f.read(16_384)
                    match = re.search(r"(?m)^\s*allow_implicit_invocation:\s*(true|false)\s*(?:#.*)?$", header)
                    if match: policy = match[1] == "true"
                except OSError: pass
            sid = identifier("skill", resolved)
            node = {"id": sid, "kind": "skill", "label": clean(folder.name), "clients": [client], "status": "discovered", "path": clean(resolved, 1200),
                    "sources": [clean(folder / "SKILL.md", 1200)], "aliases": [clean(folder, 1200)], "scopes": [scope], "allow_implicit_invocation": policy, "runtime_status": "未执行"}
            if plugin: node["plugin"] = clean(plugin)
            self.add_node(node); self.edge("client:" + client, sid, "发现")
            if plugin_id:
                self.edge(plugin_id, sid, "提供 Skill")
            elif plugin:
                plugin_ids = [nid for nid, n in self.nodes.items() if n.get('kind') == 'plugin' and n['label'].split(' · 缓存 ')[0] == clean(plugin) and client in n.get('clients', [])]
                for candidate in plugin_ids: self.edge(candidate, sid, "提供 Skill")

    def plugin_cache(self, client, root):
        root = Path(root)
        if not root.is_dir(): return
        trusted = self.root / 'apps/codex/home' if client == 'codex' else self.user / '.claude'
        try:
            if not root.resolve(strict=True).is_relative_to(trusted.resolve(strict=True)): return
        except (OSError, RuntimeError):
            return
        # Report every cached version as cache-only; cache order does not imply active install.
        try:
            for provider in sorted(root.iterdir()):
                if not provider.is_dir(): continue
                for package in sorted(provider.iterdir()):
                    if not package.is_dir(): continue
                    versions = [p for p in package.iterdir() if p.is_dir() and re.match(r"\d", p.name)]
                    if not versions: continue
                    name = package.name + "@" + provider.name
                    for cached in sorted(versions, key=version_key):
                        if not self._safe_project_path(root, cached): continue
                        pid = self.plugin(client, name, cached, installed_path=cached, cache_version=cached.name)
                        self.skills(client, cached / "skills", "plugin", name, plugin_id=pid)
                        self.plugin_contents(client, cached, name, pid, cached, cache_version=cached.name)
        except OSError:
            self.sources[str(root)] = {"path": str(root), "status": "error", "retained": False, "error": "OSError"}

    def claude_plugins(self):
        path = self.user / ".claude/settings.json"
        data = self.read(path, lambda d: {"plugins": {clean(k): bool(v) for k, v in d.get("enabledPlugins", {}).items()}})
        for name, enabled in data.get("plugins", {}).items(): self.plugin("claude-code", name, path, enabled)

    def registry(self, client, path, kind):
        def paths(d):
            values = []
            for item in d.get("entries", []):
                value = item if isinstance(item, str) else item.get("path") if isinstance(item, dict) else None
                if not value: continue
                p = Path(str(value)).expanduser()
                # Constrain registry roots to the declared tool/config areas.
                allowed = [self.root, self.user / ".gemini/config", self.user / ".claude/plugins"]
                if any(p.resolve().is_relative_to(a.resolve()) for a in allowed): values.append(clean(p, 1200))
            return {"paths": values}
        data = self.read(path, paths)
        for root in data.get("paths", []):
            if kind == "skill": self.skills(client, Path(root), "user")
            elif Path(root).is_dir():
                for package in sorted(Path(root).iterdir()):
                    if package.is_dir() and not package.name.startswith(".") and self._safe_project_path(Path(root), package):
                        pid = self.plugin(client, package.name, path, installed_path=package)
                        self.skills(client, package / "skills", "plugin", package.name, plugin_id=pid)
                        self.plugin_contents(client, package, package.name, pid, Path(root))

    @staticmethod
    def _hook_inventory(raw):
        hooks = raw.get('hooks', {}) if isinstance(raw, dict) else {}
        if isinstance(hooks, dict) and isinstance(hooks.get('hooks'), dict):
            hooks = hooks['hooks']
        if not isinstance(hooks, dict): return {}
        counts = {}
        for event, rows in hooks.items():
            if not isinstance(event, str) or not isinstance(rows, list): continue
            # Retain only known event names and aggregate row counts. Never
            # retain matcher, command, prompt, or arbitrary hook properties.
            if event not in HOOK_EVENTS: continue
            counts[event] = min(len(rows), 10_000)
        return counts

    def global_hooks(self):
        codex_hooks = self.root / 'apps/codex/home/hooks.json'
        claude_settings = self.user / '.claude/settings.json'
        for client, path in (('codex', codex_hooks), ('claude-code', claude_settings)):
            if not path.is_file(): continue
            counts = self.read(path, self._hook_inventory)
            if not counts: continue
            nid = identifier('hook', 'global:' + client + str(path))
            self.add_node({'id': nid, 'kind': 'hook', 'label': CLIENTS[client] + ' 全局 Hooks',
                'clients': [client], 'status': 'discovered', 'sources': [str(path)], 'scopes': ['global'],
                'event_counts': counts, 'runtime_status': '只发现事件类型与数量；未读取或执行 hook 命令'})
            self.edge('client:' + client, nid, '全局 Hooks')

    @staticmethod
    def _safe_manifest_refs(value, plugin_root):
        values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
        out = []
        for item in values:
            if not isinstance(item, str) or len(item) > 240: continue
            rel = Path(item)
            if rel.is_absolute() or '..' in rel.parts: continue
            try:
                resolved = (plugin_root / rel).resolve(strict=True)
                if resolved.is_file() and resolved.is_relative_to(plugin_root.resolve()) and not any(
                    p.suffix.lower() in ('.dtbase2', '.dtsparse', '.dtarchive') for p in (resolved, *resolved.parents)):
                    out.append(rel.as_posix())
            except (OSError, RuntimeError):
                continue
        return out

    def _plugin_manifest(self, raw, plugin_root):
        if not isinstance(raw, dict): return {}
        name = raw.get('name', plugin_root.name)
        if not isinstance(name, str): name = plugin_root.name
        mcp = raw.get('mcpServers', {})
        hooks = raw.get('hooks', {})
        inline_mcp = servers_only(mcp) if isinstance(mcp, dict) else []
        mcp_refs = self._safe_manifest_refs(mcp, plugin_root)
        inline_hooks = self._hook_inventory({'hooks': hooks}) if isinstance(hooks, dict) else {}
        hook_refs = self._safe_manifest_refs(hooks, plugin_root)
        return {'name': clean(name), 'mcpServers': inline_mcp, 'mcpReferences': mcp_refs,
                'hooks': inline_hooks, 'hookReferences': hook_refs}

    def project_inventory(self):
        for root in self.project_roots:
            project = clean(root.name, 120)
            candidates = [
                (root / '.mcp.json', 'json', 'claude-code'),
                (root / '.claude' / 'settings.json', 'json', 'claude-code'),
                (root / '.claude' / 'settings.local.json', 'json', 'claude-code'),
                (root / '.codex' / 'config.toml', 'toml', 'codex'),
            ]
            for path, fmt, client in candidates:
                if not path.is_file() or not self._safe_project_path(root, path): continue
                self.config(client, path, fmt, scope='project', project_root=root)
                if fmt == 'json':
                    hooks = self.read(path, self._hook_inventory)
                    if hooks:
                        self.add_node({'id': identifier('hook', str(root) + str(path)), 'kind': 'hook',
                            'label': project + ' 项目 Hooks', 'clients': [client], 'status': 'discovered',
                            'sources': [str(path)], 'scopes': ['project'], 'project': project,
                            'project_root': str(root), 'event_counts': hooks,
                            'runtime_status': '只发现事件类型与数量；未读取或执行 hook 命令'})
                        self.edge('client:' + client, identifier('hook', str(root) + str(path)), '项目 Hooks')
            # Project plugin folders are bounded to one directory level.
            plugin_roots = [(root / '.claude' / 'plugins', 'claude-code'), (root / '.codex' / 'plugins', 'codex'), (root / 'plugins', 'claude-code')]
            for base, client in plugin_roots:
                if not base.is_dir(): continue
                try: entries = sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith('.'))
                except OSError: continue
                for plugin_root in entries:
                    if not self._safe_project_path(root, plugin_root): continue
                    manifest = plugin_root / '.claude-plugin' / 'plugin.json'
                    manifest_data = self.read(manifest, lambda d: self._plugin_manifest(d, plugin_root)) if manifest.is_file() and self._safe_project_path(root, manifest) else {}
                    plugin_name = manifest_data.get('name', plugin_root.name)
                    pid = self.plugin(client, plugin_name, manifest if manifest.is_file() else plugin_root,
                                      installed_path=plugin_root, project_root=root)
                    self.skills(client, plugin_root / 'skills', 'plugin', plugin_name, plugin_id=pid)
                    self.plugin_contents(client, plugin_root, plugin_name, pid, root, manifest_data, project_root=root)

    def _add_plugin_servers(self, client, servers, source, plugin_root, plugin_name, plugin_id, anchor, project_root=None, cache_version=None):
        for server in servers:
            node_id = identifier('mcp', client + str(plugin_root) + plugin_name + server['key'])
            node = {'id': node_id, 'kind': 'mcp', 'label': server['name'], 'clients': [client],
                'status': 'cache' if cache_version else 'configured' if server['enabled'] else 'disabled', 'enabled': None if cache_version else server['enabled'],
                'transport': server['transport'], 'endpoint_host': server['endpoint'],
                'env_variable_count': server['env_count'], 'sources': [str(source)], 'scopes': ['plugin', 'cache'] if cache_version else ['plugin'],
                'plugin': clean(plugin_name), 'path': str(plugin_root), 'runtime_status': '仅缓存配置；未核实活动状态' if cache_version else '未探测'}
            if project_root:
                node.update(project=Path(project_root).name, project_root=str(project_root), scopes=['project', 'plugin'])
            self.add_node(node)
            self.edge(plugin_id, node_id, '包含 MCP')

    def plugin_contents(self, client, plugin_root, plugin_name, plugin_id, anchor, manifest_data=None, project_root=None, cache_version=None):
        plugin_root, anchor = Path(plugin_root), Path(anchor)
        if not self._safe_project_path(anchor, plugin_root): return
        manifest_data = manifest_data or {}
        manifest = plugin_root / '.claude-plugin' / 'plugin.json'
        if not manifest_data and manifest.is_file() and self._safe_project_path(anchor, manifest):
            manifest_data = self.read(manifest, lambda d: self._plugin_manifest(d, plugin_root))
        for config_path in (plugin_root / '.mcp.json', plugin_root / 'mcp.json'):
            if config_path.is_file() and self._safe_project_path(anchor, config_path):
                self.config(client, config_path, scope='plugin-cache' if cache_version else 'plugin', project_root=project_root, plugin_name=plugin_name)
                for node in self.nodes.values():
                    if node.get('kind') == 'mcp' and str(config_path) in node.get('sources', []) and node.get('plugin') == clean(plugin_name):
                        self.edge(plugin_id, node['id'], '包含 MCP')
        if manifest_data.get('mcpServers'):
            self._add_plugin_servers(client, manifest_data['mcpServers'], manifest, plugin_root, plugin_name, plugin_id, anchor, project_root, cache_version)
        for rel in manifest_data.get('mcpReferences', []):
            referenced = plugin_root / rel
            if not self._safe_project_path(anchor, referenced): continue
            servers = self.read(referenced, lambda d: servers_only(d.get('mcpServers', d.get('mcp_servers', {}))))
            if servers: self._add_plugin_servers(client, servers, referenced, plugin_root, plugin_name, plugin_id, anchor, project_root, cache_version)
        hooks = manifest_data.get('hooks', {})
        if hooks:
            self._add_hook_node(client, plugin_root, manifest, anchor, plugin_name, hooks, plugin_id, project_root, cache_version)
        for rel in manifest_data.get('hookReferences', []):
            referenced = plugin_root / rel
            if not self._safe_project_path(anchor, referenced): continue
            counts = self.read(referenced, self._hook_inventory)
            if counts: self._add_hook_node(client, plugin_root, referenced, anchor, plugin_name, counts, plugin_id, project_root, cache_version)
        hook_file = plugin_root / 'hooks' / 'hooks.json'
        if hook_file.is_file() and self._safe_project_path(anchor, hook_file):
            counts = self.read(hook_file, self._hook_inventory)
            if counts: self._add_hook_node(client, plugin_root, hook_file, anchor, plugin_name, counts, plugin_id, project_root, cache_version)

    def _add_hook_node(self, client, plugin_root, source, anchor, plugin_name, counts, plugin_id, project_root=None, cache_version=None):
        hook_id = identifier('hook', str(plugin_root) + str(source))
        self.add_node({'id': hook_id, 'kind': 'hook',
            'label': clean(plugin_name + ' Hooks'), 'clients': [client], 'status': 'cache' if cache_version else 'discovered',
            'sources': [str(source)], 'scopes': ['plugin'] + (['project'] if project_root else []) + (['cache'] if cache_version else []),
            'project': Path(project_root).name if project_root else None,
            'project_root': str(project_root) if project_root else None, 'plugin': clean(plugin_name), 'event_counts': counts,
            'runtime_status': '仅缓存中发现 Hooks；未核实活动状态' if cache_version else '只发现事件类型与数量；未读取或执行 hook 命令'})
        self.edge(plugin_id, hook_id, '声明 Hooks')

    def claude_project_inventory(self):
        path = self.user / '.claude.json'
        allowed = {str(root): root for root in self.project_roots}
        if not allowed or not path.is_file(): return
        def extract(data):
            projects = data.get('projects', {})
            out = []
            if not isinstance(projects, dict): return {'projects': out}
            # Only inspect exact paths in the caller-supplied/default allowlist.
            for key, root in allowed.items():
                entry = projects.get(key)
                if not isinstance(entry, dict): continue
                out.append({'root': str(root), 'servers': servers_only(entry.get('mcpServers', {}))})
            return {'projects': out}
        data = self.read(path, extract)
        for item in data.get('projects', []):
            root = Path(item['root'])
            for server in item.get('servers', []):
                node_id = identifier('mcp', 'claude-project' + str(root) + server['key'])
                self.add_node({'id': node_id, 'kind': 'mcp', 'label': server['name'], 'clients': ['claude-code'],
                    'status': 'configured' if server['enabled'] else 'disabled', 'enabled': server['enabled'],
                    'transport': server['transport'], 'endpoint_host': server['endpoint'],
                    'env_variable_count': server['env_count'], 'sources': [str(path)], 'scopes': ['project'],
                    'project': root.name, 'project_root': str(root), 'runtime_status': '未探测'})
                self.edge('client:claude-code', node_id, '项目注册')

    def collect_traces(self):
        self.runtime_traces = []
        for path in self.trace_files:
            parsed = parse_trace_file(path)
            self.sources[parsed['source']] = {'path': clean(parsed['source'], 1200), 'status': parsed['status'],
                'retained': False, 'kind': 'trace', 'format': parsed['format']}
            source_namespace = identifier('trace-source', parsed['source'])[-10:]
            for session in parsed['sessions']:
                old_id = session['id']
                session['id'] = old_id + ':' + source_namespace
                for event in session['events']:
                    event['id'] = identifier('observation', parsed['source'] + old_id + event['id'])
                session['label'] = session.get('label', '会话') + ' · ' + source_namespace[-4:]
            self.runtime_traces.extend(parsed['sessions'])

def mermaid(graph):
    names = {n["id"]: "n" + hashlib.sha256(n["id"].encode()).hexdigest()[:16] for n in graph["nodes"]}
    lines = ["flowchart LR"]
    for n in graph["nodes"]:
        label = clean(n["label"]).replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f'  {names[n["id"]]}["{label}"]')
    for e in graph["edges"]:
        if e["source"] in names and e["target"] in names:
            label = clean(e['label']).replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
            relation = e.get('relation_type', 'registration')
            if relation in ('proposal', 'conditional', 'fallback'):
                label += ' / ' + {'proposal': '待实施', 'conditional': '按需', 'fallback': '有条件回退'}[relation]
            arrow = '-.->' if relation in ('proposal', 'conditional', 'fallback') else '-->'
            lines.append(f'  {names[e["source"]]} {arrow}|"{label}"| {names[e["target"]]}')
    return "\n".join(lines) + "\n"
