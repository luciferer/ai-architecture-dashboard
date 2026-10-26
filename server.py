"""Loopback-only read-only graph API and local assets, with SSE updates."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import threading
import time
from urllib.parse import urlsplit
from collector import Collector, mermaid
from demo_data import DemoCollector

SOURCE = Path(__file__).resolve().parent

def now(): return datetime.now(timezone.utc).isoformat()

class GraphState:
    def __init__(self, collector, interval):
        self.collector, self.interval = collector, interval
        self.condition = threading.Condition()
        self.stop = threading.Event()
        self.graph = {}
        self.signature = None
        self.revision = 0
        self.history = []
        self.scan()

    def scan(self):
        data = self.collector.collect()
        signature = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.condition:
            stamp = now()
            if signature != self.signature:
                old = {n['id']: n for n in self.graph.get('nodes', [])}
                new = {n['id']: n for n in data['nodes']}
                added = [new[i] for i in sorted(new.keys() - old.keys())]
                removed = [old[i] for i in sorted(old.keys() - new.keys())]
                updated = [new[i] for i in sorted(new.keys() & old.keys()) if new[i] != old[i]]
                if self.revision:
                    self.history.insert(0, {'at': stamp, 'added': len(added), 'removed': len(removed), 'updated': len(updated),
                        'items': [{'label': n['label'], 'kind': n['kind'], 'change': label} for label, rows in [('新增', added), ('删除', removed), ('修改', updated)] for n in rows][:12]})
                    self.history = self.history[:20]
                self.revision += 1
                self.signature = signature
                self.graph = data | {'schema_version': 2, 'revision': self.revision, 'updated_at': stamp, 'collected_at': stamp, 'poll_seconds': self.interval, 'changes': copy.deepcopy(self.history)}
                self.condition.notify_all()
            else:
                self.graph['collected_at'] = stamp

    def snapshot(self):
        with self.condition: return copy.deepcopy(self.graph)

    def worker(self):
        while not self.stop.wait(self.interval):
            try: self.scan()
            except Exception as exc:
                # Exception text can contain source data; log only the type.
                print('collector error:', type(exc).__name__, flush=True)

class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_): pass

    def allowed(self):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts: return False
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{host}' for host in hosts}: return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            # Opening the page from another app is safe; cross-site API reads
            # and embedded documents remain blocked.
            navigation = (urlsplit(self.path).path == '/' and
                self.headers.get('Sec-Fetch-Mode') == 'navigate' and
                self.headers.get('Sec-Fetch-Dest') == 'document')
            if not navigation: return False
        return True

    def send_headers(self, status, mime, length=None, attachment=None):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if length is not None: self.send_header('Content-Length', str(length))
        if attachment: self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        self.end_headers()

    def reply(self, body, mime='application/json; charset=utf-8', status=200, attachment=None):
        if not isinstance(body, bytes): body = body.encode()
        self.send_headers(status, mime, len(body), attachment)
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed():
            self.reply('{"error":"origin_not_allowed"}', status=403); return
        path = urlsplit(self.path).path
        if path == '/api/health':
            self.reply(json.dumps({'status': 'running', 'pid': os.getpid(), 'instance_id': self.server.instance_id, 'app': 'ai-architecture-dashboard'})); return
        if path in ('/api/graph', '/api/export.json'):
            self.reply(json.dumps(self.server.state.snapshot(), ensure_ascii=False), attachment='ai-architecture.json' if path.endswith('export.json') else None); return
        if path == '/api/export.mmd':
            self.reply(mermaid(self.server.state.snapshot()), 'text/plain; charset=utf-8', attachment='ai-architecture.mmd'); return
        if path == '/api/events':
            self.events(); return
        assets = {'/': (SOURCE/'public/index.html', 'text/html; charset=utf-8'), '/app.js': (SOURCE/'public/app.js', 'text/javascript; charset=utf-8'),
            '/style.css': (SOURCE/'public/style.css', 'text/css; charset=utf-8'), '/favicon.svg': (SOURCE/'public/favicon.svg', 'image/svg+xml'),
            '/vendor/cytoscape.min.js': (SOURCE/'vendor/cytoscape.min.js', 'text/javascript; charset=utf-8')}
        if path not in assets:
            self.reply('{"error":"not_found"}', status=404); return
        file, mime = assets[path]
        try: self.reply(file.read_bytes(), mime)
        except OSError: self.reply('{"error":"asset_unavailable"}', status=503)

    def events(self):
        # Bounded HTTP/1.1 streams leave slots for navigation/static/API reads
        # when several dashboard tabs share the browser's per-origin pool.
        self.close_connection = True
        self.send_headers(200, 'text/event-stream; charset=utf-8')
        self.wfile.write(b'retry: 1000\n\n')
        deadline = time.monotonic() + 4
        revision = -1
        try:
            while not self.server.state.stop.is_set() and time.monotonic() < deadline:
                data = self.server.state.snapshot()
                if data['revision'] != revision:
                    self.wfile.write(('event: graph\ndata: '+json.dumps(data,ensure_ascii=False)+'\n\n').encode()); revision = data['revision']
                else:
                    self.wfile.write(('event: heartbeat\ndata: '+json.dumps({'collected_at':data['collected_at']})+'\n\n').encode())
                self.wfile.flush()
                with self.server.state.condition: self.server.state.condition.wait(timeout=2)
        except (BrokenPipeError, ConnectionResetError, OSError): pass

    def do_POST(self): self.reply('{"error":"read_only"}', status=405)
    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

def main():
    p=argparse.ArgumentParser(description='Read-only AI architecture dashboard; safe synthetic demo by default')
    p.add_argument('--port',type=int,default=8768)
    p.add_argument('--live',action='store_true',help='Opt in to read configuration from explicitly supplied roots')
    p.add_argument('--dotfiles-root',type=Path,help='Configuration root used with --live')
    p.add_argument('--user-root',type=Path,help='User configuration root used with --live')
    p.add_argument('--project-root',type=Path,action='append',default=[],help='Explicit project root; may be repeated')
    p.add_argument('--no-default-projects',action='store_true',help='Disable bounded workspaces project discovery')
    p.add_argument('--trace-file',type=Path,action='append',default=[],help='Explicit Codex or Claude JSONL trace; may be repeated')
    p.add_argument('--poll',type=float,default=2)
    p.add_argument('--instance-id',default='foreground')
    args=p.parse_args()
    if not 1024 <= args.port <= 65535 or not .5 <= args.poll <= 60: p.error('port/poll outside supported range')
    if args.live:
        if args.dotfiles_root is None or args.user_root is None:
            p.error('--live requires both --dotfiles-root and --user-root')
        collector=Collector(args.dotfiles_root,args.user_root,args.project_root,args.trace_file,
                            default_projects=not args.no_default_projects)
    else:
        if args.project_root or args.trace_file or args.dotfiles_root or args.user_root or args.no_default_projects:
            p.error('live input paths and project/trace options require --live')
        collector=DemoCollector()
    state=GraphState(collector,args.poll)
    server=Server(('127.0.0.1',args.port),Handler);server.state=state;server.instance_id=args.instance_id
    def stop(*_):
        state.stop.set()
        with state.condition: state.condition.notify_all()
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    threading.Thread(target=state.worker,daemon=True).start()
    print(f'Local AI architecture dashboard: http://127.0.0.1:{args.port} (read-only)',flush=True)
    try: server.serve_forever(poll_interval=.2)
    finally: state.stop.set();server.server_close()

if __name__ == '__main__': main()
