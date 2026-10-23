"""Vercel entry: runs the unmodified Veil server for one request and keeps each
visitor's state in an encrypted, authenticated cookie (no shared storage)."""
import os, sys, json, hmac, hashlib, base64, zlib, secrets, shutil, socket, subprocess, tempfile, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler
from pathlib import Path
APP = Path(__file__).resolve().parent.parent / 'app'
KEY = hashlib.sha256((os.environ.get('VEIL_SECRET') or 'dev-only-secret').encode()).digest()

def _ks(nonce, n):
    out = b''; i = 0
    while len(out) < n:
        out += hmac.new(KEY, b'enc' + nonce + i.to_bytes(4, 'big'), hashlib.sha256).digest(); i += 1
    return out[:n]
def seal(obj):
    raw = zlib.compress(json.dumps(obj).encode()); nonce = secrets.token_bytes(12)
    ct = bytes(a ^ b for a, b in zip(raw, _ks(nonce, len(raw))))
    tag = hmac.new(KEY, b'mac' + nonce + ct, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + ct + tag).decode().rstrip('=')
def unseal(tok):
    try:
        b = base64.urlsafe_b64decode(tok + '=' * (-len(tok) % 4)); nonce, ct, tag = b[:12], b[12:-32], b[-32:]
        if not hmac.compare_digest(tag, hmac.new(KEY, b'mac' + nonce + ct, hashlib.sha256).digest()): return None
        raw = bytes(a ^ c for a, c in zip(ct, _ks(nonce, len(ct))))
        d = zlib.decompress(raw, 0, 200000); o = json.loads(d)
        return o if isinstance(o, dict) else None
    except Exception: return None
def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

class handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    def log_message(self, *a): pass
    def any(self):
        from urllib.parse import urlsplit, parse_qsl, urlencode
        u = urlsplit(self.path); q = parse_qsl(u.query, keep_blank_values=True)
        vp = next((v for k, v in q if k == '__vp'), '')
        rest = urlencode([(k, v) for k, v in q if k != '__vp'])
        self.path = '/' + vp.lstrip('/') + ('?' + rest if rest else '')
        if self.path.split('?')[0] == '/healthz':
            self.send_response(200); self.end_headers(); self.wfile.write(b'ok'); return
        tok = None
        for part in self.headers.get('Cookie', '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == 'veil_state': tok = v
        st = (unseal(tok) if tok else None)
        fresh = st is None
        st = st or {'op': secrets.token_hex(16), 'files': {}}
        if not isinstance(st.get('files'), dict) or not isinstance(st.get('op'), str): st = {'op': secrets.token_hex(16), 'files': {}}; fresh = True
        before = dict(st['files'])
        d = Path(tempfile.mkdtemp(prefix='veil-'))
        try:
            for f in APP.iterdir():
                (shutil.copytree if f.is_dir() else shutil.copy)(f, d / f.name)
            (d / '.state').mkdir()
            for name, text in st['files'].items():
                if '/' in name or name.startswith('.') or not isinstance(text, str): continue
                (d / '.state' / name).write_text(text)
            port = free_port()
            env = dict(os.environ, PORT=str(port), VEIL_PUBLIC_HOSTS='127.0.0.1:%d' % port, VEIL_OPERATOR_TOKEN=st['op'], VEIL_BIND='127.0.0.1')
            p = subprocess.Popen([sys.executable, 'veil_main.py'], cwd=d, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(150):
                    try: socket.create_connection(('127.0.0.1', port), 0.2).close(); break
                    except OSError: time.sleep(0.05)
                n = int(self.headers.get('Content-Length') or 0); body = self.rfile.read(n) if n else None
                hdrs = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'origin', 'cookie', 'connection')}
                hdrs['Host'] = '127.0.0.1:%d' % port
                if self.headers.get('Origin'): hdrs['Origin'] = 'http://127.0.0.1:%d' % port
                req = urllib.request.Request('http://127.0.0.1:%d%s' % (port, self.path), data=body, headers=hdrs, method=self.command)
                try: r = urllib.request.urlopen(req, timeout=50); code = r.status; rh = r.headers; data = r.read()
                except urllib.error.HTTPError as e: code = e.code; rh = e.headers; data = e.read()
            finally:
                p.terminate()
            files = {f.name: f.read_text() for f in (d / '.state').iterdir() if f.is_file() and not f.name.endswith('.tmp')}
            st['files'] = files
        except Exception:
            self.send_response(502); self.end_headers(); self.wfile.write(b'backend unavailable'); return
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.send_response(code)
        for k, v in rh.items():
            if k.lower() in ('transfer-encoding', 'connection', 'content-length', 'server', 'date'): continue
            self.send_header(k, v)
        if fresh or st['files'] != before:
          self.send_header('Set-Cookie', 'veil_state=%s; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=86400' % seal(st))
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    do_GET = do_POST = do_PUT = do_DELETE = do_OPTIONS = any
