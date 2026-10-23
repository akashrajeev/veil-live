"""Local demonstration server. Not a secure multi-user deployment."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from fractions import Fraction
from dataclasses import asdict
import json, hashlib, os, threading, uuid
from engine import Controller, make_worlds, PARITY, BIT1, Policy
from oracle import audit
from urllib.parse import urlsplit,parse_qs
from scenario_lab import run_scenario,comparison,SCENARIOS
from audit_export import export as export_guarded
from intents import parse as parse_intent
import dialog_store
import mcp_server
import real_demo
from dialog import audit_dialogue
from experiment_spec import run as run_spec

ROOT = Path(__file__).parent
PUBLIC=[h for h in os.environ.get('VEIL_PUBLIC_HOSTS','').split(',') if h]
HOSTS=tuple(['127.0.0.1:8765','localhost:8765']+PUBLIC)
ORIGINS=tuple(['http://127.0.0.1:8765','http://localhost:8765']+['http://'+h for h in PUBLIC])
STORE = ROOT / '.state'
STORE.mkdir(exist_ok=True)
LOCK = threading.Lock()
MCP = mcp_server.Session(STORE / 'mcp-dialog.json')
SESSIONS=set()  # Mcp-Session-Id values issued by this process (assistant surface)
KNOWN_PROTOCOLS=('2025-03-26','2025-06-18','2025-11-25')
OPERATOR_TOKEN = uuid.uuid4().hex  # demo-only: embedded in the served /alexa page, new each server start
WORLDS = make_worlds()
MODEL = hashlib.sha256(json.dumps([asdict(w) for w in WORLDS], sort_keys=True).encode()).hexdigest()
ACTUAL = next(w for w in WORLDS if w.key == 117)
KNOWN = tuple(w for w in WORLDS if w.bob == ACTUAL.bob)
PATH = STORE / 'dinner.json'
REAL_GUARDS = {}  # case name -> CalendarGuard (pinned public feeds only)
REQUESTS = ('parity','slot1','schedule')
NAIVE = {'parity':PARITY, 'slot1':BIT1,
         'schedule':Policy('first-common-0-1-2-3','schedule',(0,1,2,3))}

def save(c):
    # Use the same flushed atomic checkpoint path as the free dialogue.
    from dialog import write
    write(PATH,{'model':MODEL, 'ledger':c.dump(), 'generation':getattr(c,'generation','initial')})

def load():
    if not PATH.exists(): return Controller(KNOWN)
    if PATH.stat().st_size > 65536: raise ValueError('saved state exceeds supported size')
    data = json.loads(PATH.read_text())
    if not isinstance(data,dict) or set(data) not in ({'model','ledger'},{'model','ledger','generation'}): raise ValueError('invalid state schema')
    if data['model'] != MODEL: raise ValueError('model version changed; reset demo')
    c = Controller.load(KNOWN,data['ledger'])
    c.generation=data.get('generation','legacy')
    if not isinstance(c.generation,str) or not 1<=len(c.generation)<=64: raise ValueError('invalid generation')
    if c.budget != 0 or not c.complete:
        raise ValueError('saved demo configuration changed')
    if [e['request'] for e in c.transcript] != list(REQUESTS[:len(c.transcript)]):
        raise ValueError('saved request sequence changed')
    if len(c.transcript) > len(REQUESTS) or ACTUAL.key not in {w.key for w in c.compatible}:
        raise ValueError('saved ledger is not the selected demo world')
    return c

def snapshot(c):
    # Replay actual sequence under BOTH policies in every observer-compatible world.
    requests = [e['request'] for e in c.transcript]
    decision=Controller(KNOWN)
    evidence=[]
    for request in requests:
        evidence.append(decision.evidence(request))
        decision.step(ACTUAL.key,request)
    naive_traces = {}
    guarded_traces = {}
    for w in KNOWN:
        other = Controller(KNOWN)
        for r in requests: other.step(w.key,r)
        guarded_traces[w.key] = other.transcript
        naive_traces[w.key] = [{'request':r,'policy':NAIVE[r].name,'output':NAIVE[r].output(w)} for r in requests]
    result = {'generation':getattr(c,'generation','initial'), 'step':len(requests),'model':MODEL[:12], 'worlds':len(KNOWN),
              'audience':'Bob already knows his own free windows: early prep and dinner.',
              'fixture':{'alice':[0,1,2],'bob':[0,2]}, 'decision_evidence':evidence, 'sides':{}}
    for name,traces in [('naive',naive_traces),('guarded',guarded_traces)]:
        events = traces[ACTUAL.key]
        compatible = [w for w in KNOWN if traces[w.key] == events]
        support = sum(w.secret for w in compatible)/len(compatible)
        metrics = audit([asdict(w) for w in KNOWN],traces,Fraction(0)) if requests else None
        result['sides'][name] = {'events':events, 'compatible':[w.alice for w in compatible],
            'probability':support, 'possibilities':len(compatible),
            'violations':metrics['prefix_violation_cells'] if metrics else 0,
            'max_shift':metrics['max_posterior_shift'] if metrics else '0'}
    return result

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def reply(self,value,code=200,headers=None):
        body = json.dumps(value).encode()
        self.send_response(code); self.send_header('Content-Type','application/json')
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(body)
    def local_host(self):
        return self.headers.get('Host') in HOSTS
    def do_GET(self):
        if len(self.path)>2048:return self.reply({'error':'Local query too long'},414)
        if not self.local_host(): return self.reply({'error':'Local host required'},403)
        route=urlsplit(self.path)
        if route.path in ('/mcp','/mcp-operator'):
            self.send_response(405);self.send_header('Allow','POST');self.send_header('Content-Length','0');self.end_headers();return
        if route.path in ('/api/lab','/api/compare'):
            try:
                query=parse_qs(route.query,strict_parsing=True)
                if set(query)-{'scenario','budget','script','alice','bob','protected_slot','costs','prior'} or any(len(v)!=1 for v in query.values()):raise ValueError()
                scenario=query.get('scenario',['dinner'])[0]
                if scenario not in SCENARIOS:raise ValueError()
                spec=SCENARIOS[scenario]
                alice=int(query.get('alice',[spec['alice']])[0]);bob=int(query.get('bob',[spec['bob']])[0])
                protected=int(query.get('protected_slot',[0])[0])
                costs=tuple(int(x) for x in query.get('costs',['0,1,2,3'])[0].split(','))
                prior=tuple(int(x) for x in query.get('prior',['1,1'])[0].split(','))
                if route.path=='/api/compare':return self.reply({'scope':'public experiment limits, not private approval','results':comparison(alice,bob,query.get('script',['composition'])[0],protected,costs,prior)})
                return self.reply(run_scenario(alice,bob,query.get('budget',['strict'])[0],query.get('script',['composition'])[0],protected,costs,prior))
            except ValueError:return self.reply({'error':'Unsupported declared experiment'},400)
        if route.path=='/api/example' and route.query=='name=ordinary':
            return self.reply(json.loads((ROOT/'examples/ordinary-questions-correlated.json').read_text()))
        if route.path=='/api/intent':
            try:
                query=parse_qs(route.query,strict_parsing=True)
                if set(query)!={'text'} or len(query['text'])!=1:raise ValueError()
                parsed=parse_intent(query['text'][0])
                if not parsed['supported']:return self.reply(parsed)
                with LOCK:
                    c=load();before=c.evidence(parsed['intent']);event=c.step(ACTUAL.key,parsed['intent'])
                return self.reply({**parsed,'observation':event,'decision':before,
                    'note':'Counterfactual preview from current main history. Does not advance or save the conversation.'})
            except OSError:return self.reply({'error':'Local storage unavailable. No new answer was saved.'},503)
            except (ValueError,KeyError,TypeError,AttributeError):return self.reply({'error':'Unsupported intent preview'},400)
        if route.path=='/api/dialog/audit' and not route.query:
            with LOCK:
                try:
                    current=dialog_store.status(PATH.with_name('free-dialog.json'))
                    if not current['initialized']:raise ValueError()
                    return self.reply(audit_dialogue(current['state']))
                except OSError:return self.reply({'error':'Local storage unavailable. No new answer was saved.'},503)
                except (ValueError,TypeError,KeyError):return self.reply({'error':'Initialize a valid synthetic dialogue first'},409)
        if route.path=='/api/dialog' and not route.query:
            with LOCK:
                try:return self.reply(dialog_store.status(PATH.with_name('free-dialog.json')))
                except OSError:return self.reply({'error':'Local storage unavailable. No new answer was saved.'},503)
                except (ValueError,TypeError,KeyError):return self.reply({'error':'Invalid local dialogue state. Start a new synthetic session.'},409)
        if route.path=='/api/audit' and not route.query:
            return self.reply(export_guarded())
        if route.path=='/api/real/cases' and not route.query:
            try:return self.reply({'cases':real_demo.cases(),'note':'Real public schedule feeds, timing only. Model remains declared assumptions; not a real-world privacy guarantee.'})
            except OSError:return self.reply({'error':'Real-data cases unavailable in this copy'},503)
        if self.path == '/api/state':
            with LOCK:
                try: self.reply(snapshot(load()))
                except OSError:return self.reply({'error':'Local storage unavailable. No new answer was saved.'},503)
                except (ValueError,KeyError,TypeError,AttributeError,json.JSONDecodeError):
                    self.reply({'error':'Saved demonstration state is invalid. Reset the demo.'},409)
        elif route.path in ('/','/index.html','/alexa','/real') and (route.path in ('/alexa','/real') or not route.query):
            try:
                data=(ROOT/{'/alexa':'alexa.html','/real':'real.html'}.get(route.path,'index.html')).read_bytes()
                if route.path=='/alexa':data=data.replace(b'__OPERATOR_TOKEN__',OPERATOR_TOKEN.encode())
            except OSError:return self.reply({'error':'Local page file missing. Extract the complete package and run from its folder.'},503)
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8')
            self.end_headers(); self.wfile.write(data)
        else: self.reply({'error':'Unknown local route'},404)
    def do_DELETE(self):
        if not self.local_host(): return self.reply({'error':'Local host required'},403)
        if urlsplit(self.path).path!='/mcp':return self.reply({'error':'Unknown local route'},404)
        sid=self.headers.get('Mcp-Session-Id')
        if sid in SESSIONS:
            SESSIONS.discard(sid);self.send_response(200);self.send_header('Content-Length','0');self.end_headers();return
        return self.reply({'error':'Unknown session'},404)
    def do_POST(self):
        if not self.local_host(): return self.reply({'error':'Local host required'},403)
        if self.path in ('/mcp','/mcp-operator'):
            surface='assistant'
            if self.path=='/mcp-operator':
                if self.headers.get('X-Veil-Operator')!=OPERATOR_TOKEN:return self.reply({'error':'Operator token required'},403)
                surface='operator'
            origin=self.headers.get('Origin')
            if origin and origin not in ORIGINS:
                return self.reply({'error':'Local origin required'},403)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 1<=size<=65536:raise ValueError()
                msg=json.loads(self.rfile.read(size))
            except (ValueError,json.JSONDecodeError):
                return self.reply(mcp_server._error(None,-32700,'Parse error'),400)
            if isinstance(msg,list):return self.reply(mcp_server._error(None,-32600,'Batching is not supported'),400)
            extra={}
            if surface=='assistant':
                pv=self.headers.get('MCP-Protocol-Version')
                if pv and pv not in KNOWN_PROTOCOLS:return self.reply(mcp_server._error(None,-32600,'Unsupported MCP-Protocol-Version'),400)
                sid=self.headers.get('Mcp-Session-Id')
                if sid and sid not in SESSIONS:return self.reply(mcp_server._error(None,-32600,'Unknown session; send initialize again'),404)
            with LOCK:
                out=mcp_server.handle(msg,MCP,surface)
            if surface=='assistant' and isinstance(msg,dict) and msg.get('method')=='initialize' and out and 'result' in out:
                sid=uuid.uuid4().hex;SESSIONS.add(sid);extra['Mcp-Session-Id']=sid
            if out is None:
                self.send_response(202);self.send_header('Content-Length','0');self.end_headers();return
            return self.reply(out,headers=extra)
        if self.path=='/api/experiment':
            origin=self.headers.get('Origin')
            if origin and origin not in ORIGINS:
                return self.reply({'error':'Local origin required'},403)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 1<=size<=100000:raise ValueError()
                spec=json.loads(self.rfile.read(size))
                return self.reply(run_spec(spec))
            except (ValueError,TypeError,KeyError):return self.reply({'error':'Invalid synthetic experiment specification'},400)
        if self.path in ('/api/dialog/init','/api/dialog/ask'):
            origin=self.headers.get('Origin')
            if origin and origin not in ORIGINS:
                return self.reply({'error':'Local origin required'},403)
            with LOCK:
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 1<=size<=1024:raise ValueError()
                    body=json.loads(self.rfile.read(size))
                    if not isinstance(body,dict):raise ValueError()
                    path=PATH.with_name('free-dialog.json')
                    if self.path=='/api/dialog/init':
                        if set(body) not in ({'alice','bob','protected','budget'},{'alice','bob','protected','budget','costs','prior'}):raise ValueError()
                        return self.reply(dialog_store.initialize(path,body['alice'],body['bob'],body['protected'],body['budget'],body.get('costs',[0,1,2,3]),body.get('prior',[1,1])))
                    if set(body)!={'text','generation','expected'} or type(body['expected']) is not int or not 0<=body['expected']<=128:raise ValueError()
                    return self.reply(dialog_store.advance(path,body['text'],body['generation'],body['expected']))
                except OSError:return self.reply({'error':'Local storage unavailable. No new answer was saved.'},503)
                except (ValueError,TypeError,KeyError):return self.reply({'error':'Unsupported or invalid local dialogue request'},400)
        if self.path in ('/api/real/select','/api/real/ask'):
            origin=self.headers.get('Origin')
            if origin and origin not in ORIGINS:
                return self.reply({'error':'Local origin required'},403)
            with LOCK:
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 1<=size<=4096:raise ValueError()
                    body=json.loads(self.rfile.read(size))
                    if self.path=='/api/real/select':
                        if set(body)!={'case'}:raise ValueError()
                        guard=real_demo.select(STORE,body['case'])
                        REAL_GUARDS[body['case']]=guard
                        return self.reply(guard.public_windows())
                    if set(body)!={'case','request'} or body['request'] not in real_demo.ALLOWED_REQUESTS:raise ValueError()
                    guard=REAL_GUARDS.get(body['case']) or real_demo.select(STORE,body['case'])
                    REAL_GUARDS[body['case']]=guard
                    return self.reply(guard.ask(body['request']))
                except OSError:return self.reply({'error':'Local storage unavailable. No new answer was saved.'},503)
                except (ValueError,TypeError,KeyError) as e:return self.reply({'error':str(e) or 'Invalid real-data request'},400)
        if self.path not in ('/api/next','/api/reset'): return self.reply({'error':'Unknown local route'},404)
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size < 0 or size > 256: raise ValueError()
            body=json.loads(self.rfile.read(size)) if size else {}
            if not isinstance(body,dict) or set(body)-{'expected_step','expected_generation'}: raise ValueError()
            generation=body.get('expected_generation')
            if generation is not None and (not isinstance(generation,str) or len(generation)>64): raise ValueError()
            expected=body.get('expected_step')
            if expected is not None and (type(expected) is not int or not 0<=expected<=3): raise ValueError()
        except (ValueError,json.JSONDecodeError):
            return self.reply({'error':'Invalid local request'},400)
        # Browser cross-origin calls cannot mutate this local demonstration.
        origin=self.headers.get('Origin')
        if origin and origin not in ORIGINS:
            return self.reply({'error':'Local origin required'},403)
        with LOCK:
            try:
                c=Controller(KNOWN) if self.path=='/api/reset' else load()
                if self.path=='/api/reset': c.generation=uuid.uuid4().hex
                if self.path=='/api/next' and generation is not None and generation!=getattr(c,'generation','initial'):
                    return self.reply(snapshot(c))
                if self.path=='/api/next' and expected is not None and expected!=len(c.transcript):
                    return self.reply(snapshot(c))
                if self.path=='/api/next' and len(c.transcript)<len(REQUESTS):
                    c.step(ACTUAL.key,REQUESTS[len(c.transcript)])
                save(c); self.reply(snapshot(c))
            except OSError:return self.reply({'error':'Local storage unavailable. No new answer was saved.'},503)
            except (ValueError,KeyError,TypeError,AttributeError,json.JSONDecodeError):
                self.reply({'error':'Saved demonstration state is invalid. Reset the demo.'},409)

if __name__=='__main__':
    try:
        http=ThreadingHTTPServer((os.environ.get('VEIL_BIND','127.0.0.1'),int(os.environ.get('PORT','8765'))),Handler)
    except OSError as error:
        raise SystemExit('Cannot start local Veil on port8765. Check whether another local server is already using it. '+str(error))
    print('Veil local simulation: http://127.0.0.1:8765',flush=True)
    try:http.serve_forever()
    except KeyboardInterrupt:pass
    finally:http.server_close()
