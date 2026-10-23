"""Run a user-authored SYNTHETIC finite-world experiment, never arbitrary code.
JSON describes declared worlds, fixed typed requests, actual-world key, public
costs and disclosure limit. No files, URLs, eval, plugins or external actions.
"""
import argparse,json,sys,hashlib
from fractions import Fraction
from dataclasses import asdict
from engine import World,Controller
from preference_lab import PreferenceController
from oracle import audit

REQUESTS={'parity','slot0','slot1','slot2','slot3','schedule','unsupported'}
def run(spec):
    if not isinstance(spec,dict) or set(spec)!={'worlds','actual_key','requests','budget','public_costs'}:raise ValueError('exact experiment schema required')
    if type(spec['actual_key']) is not int:raise ValueError('actual key must be integer')
    records=spec['worlds']
    if not isinstance(records,list) or not 1<=len(records)<=256:raise ValueError('world domain must contain1..256 records')
    domain=[]
    for record in records:
        if not isinstance(record,dict) or set(record)!={'key','alice','bob','secret','weight'}:raise ValueError('invalid declared world schema')
        domain.append(World(**record))
    domain=tuple(domain)
    if not isinstance(spec['requests'],list) or len(spec['requests'])>32 or any(not isinstance(r,str) or r not in REQUESTS for r in spec['requests']):raise ValueError('at most32 supported typed requests')
    # This runner already assumes domain is conditioned on declared observer
    # knowledge. It cannot establish completeness or real-world applicability.
    if not isinstance(spec['budget'],str) or len(spec['budget'])>32:raise ValueError('budget must be a bounded rational string')
    try:budget=Fraction(spec['budget'])
    except (ValueError,ZeroDivisionError):raise ValueError('budget must be a finite rational') from None
    costs=spec['public_costs']
    actual=next((w for w in domain if w.key==spec['actual_key']),None)
    if actual is None:raise ValueError('actual world must belong to domain')
    traces={};visible_actual=[]
    for w in domain:
        c=PreferenceController(domain,costs,budget);events=[];visible=[]
        for request in spec['requests']:
            decision=c.evidence(request);event=c.step(w.key,request)
            item={'observation':event,'decision':decision}
            visible.append(item)
            events.append({**event,'policy':event['policy']+'|'+json.dumps(decision,sort_keys=True)})
        traces[w.key]=events
        if w.key==actual.key:visible_actual=visible
    metrics=audit([asdict(w) for w in domain],traces,budget) if spec['requests'] else {'prefix_violation_cells':0,'invalid_meetings':0,'invalid_calendar_answers':0}
    metrics.pop('violations',None)
    declared=json.loads(json.dumps(spec))
    return {'input_spec':declared,
            'input_sha256':hashlib.sha256(json.dumps(declared,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'scope':'explicit synthetic declared observer-conditioned finite domain, not real-world prior discovery',
            'actual_trace':visible_actual,'observed':metrics,
            'initial_protected_support':str(PreferenceController(domain,costs,budget).baseline),
            'prior_already_determines_fact':PreferenceController(domain,costs,budget).baseline in (0,1),
            'note':'Zero additional shift does not mean the protected fact was unknown initially.'}

def verify_result(result):
    """Rerun downloaded declared input; equality is not an authenticity claim."""
    if not isinstance(result,dict) or 'input_spec' not in result:raise ValueError('result has no replayable input')
    replay=run(result['input_spec'])
    if replay!=result:raise ValueError('downloaded result differs from exact declared-input replay')
    return {'verified':True,'input_sha256':replay['input_sha256'],
            'observed':replay['observed'],'scope':'exact synthetic rerun, not source authentication or real-prior validation'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('json_file');p.add_argument('--verify-result',action='store_true',help='replay a downloaded result and compare it exactly');args=p.parse_args()
    try:
        from pathlib import Path
        path=Path(args.json_file)
        if path.stat().st_size>(2000000 if args.verify_result else 100000):raise ValueError('input file too large')
        value=json.loads(path.read_text())
        result=verify_result(value) if args.verify_result else run(value)
        print(json.dumps(result,indent=2))
        if result['observed']['prefix_violation_cells'] or result['observed']['invalid_meetings'] or result['observed']['invalid_calendar_answers']:sys.exit(1)
    except (ValueError,TypeError,KeyError,OSError) as e:print(str(e),file=sys.stderr);sys.exit(1)
