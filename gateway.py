"""Per-visitor gateway for the public Veil demo.
Each browser gets its own unmodified Veil server process (own state dir), so
judges cannot see or change each other's runs. Idle processes are stopped."""
import http.server, socketserver, subprocess, sys, os, time, threading, uuid, shutil, socket, urllib.request, urllib.error, tempfile
from pathlib import Path
SRC = Path(__file__).parent
MAX = int(os.environ.get('VEIL_MAX_SESSIONS', '6'))
IDLE = int(os.environ.get('VEIL_IDLE_SECONDS', '900'))
LOCK = threading.Lock()
SESS = {}  # sid -> dict(port, proc, last, dir)

def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

def reap():
    while True:
        time.sleep(30)
        with LOCK:
            for sid, s in list(SESS.items()):
                if time.time() - s['last'] > IDLE or s['proc'].poll() is not None:
                    stop(sid)

def stop(sid):
    s = SESS.pop(sid, None)
    if not s: return
    try: s['proc'].terminate()
    except Exception: pass
    shutil.rmtree(s['dir'], ignore_errors=True)

def start():
    if len(SESS) >= MAX:
        oldest = min(SESS, key=lambda k: SESS[k]['last']); stop(oldest)
    sid = uuid.uuid4().hex; port = free_port(); d = tempfile.mkdtemp(prefix='veil-')
    for f in SRC.iterdir():
        if f.name in ('.state', '__pycache__', 'validation-results') or f.name.startswith('.'): continue
        (shutil.copytree if f.is_dir() else shutil.copy)(f, Path(d) / f.name)
    env = dict(os.environ, PORT=str(port), VEIL_PUBLIC_HOSTS='127.0.0.1:%d' % port)
    p = subprocess.Popen([sys.executable, 'server.py'], cwd=d, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try: socket.create_connection(('127.0.0.1', port), 0.2).close(); break
        except OSError: time.sleep(0.1)
    SESS[sid] = dict(port=port, proc=p, last=time.time(), dir=d)
    return sid

class H(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    def log_message(self, *a): pass
    def handle_any(self):
        if self.path == '/healthz':
            self.send_response(200); self.end_headers(); self.wfile.write(b'ok'); return
        cookie = self.headers.get('Cookie', ''); sid = None
        for part in cookie.split(';'):
            k, _, v = part.strip().partition('=')
            if k == 'veil_sid': sid = v
        new = False
        with LOCK:
            if sid not in SESS: sid = start(); new = True
            s = SESS[sid]; s['last'] = time.time(); port = s['port']
        n = int(self.headers.get('Content-Length') or 0); body = self.rfile.read(n) if n else None
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'origin', 'cookie', 'connection')}
        hdrs['Host'] = '127.0.0.1:%d' % port
        if self.headers.get('Origin'): hdrs['Origin'] = 'http://127.0.0.1:%d' % port
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, self.path), data=body, headers=hdrs, method=self.command)
        try: r = urllib.request.urlopen(req, timeout=120); code = r.status; rh = r.headers; data = r.read()
        except urllib.error.HTTPError as e: code = e.code; rh = e.headers; data = e.read()
        except Exception: self.send_response(502); self.end_headers(); self.wfile.write(b'backend unavailable'); return
        self.send_response(code)
        for k, v in rh.items():
            if k.lower() in ('transfer-encoding', 'connection', 'content-length', 'server', 'date'): continue
            self.send_header(k, v)
        if new: self.send_header('Set-Cookie', 'veil_sid=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400' % sid)
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    do_GET = do_POST = do_PUT = do_DELETE = do_OPTIONS = handle_any

class S(socketserver.ThreadingMixIn, http.server.HTTPServer): daemon_threads = True
if __name__ == '__main__':
    threading.Thread(target=reap, daemon=True).start()
    S((os.environ.get('VEIL_BIND', '0.0.0.0'), int(os.environ.get('PORT', '10000'))), H).serve_forever()
