"""Export all declared guarded traces for independent verification.
An experiment artifact, not a private recipient API: contains every synthetic world.
"""
import argparse,json,sys,hashlib
from dataclasses import asdict
from fractions import Fraction
from engine import Controller,make_worlds
from oracle import audit

SCHEMA='veil-guarded-audit-v1'
SCRIPT=('parity','slot1','schedule')

def export(bob=5):
    if type(bob) is not int or not 0<=bob<=15:raise ValueError('bob must be four-slot bitset 0..15')
    worlds=tuple(w for w in make_worlds() if w.bob==bob)
    records=[asdict(w) for w in worlds]
    traces={}
    for w in worlds:
        c=Controller(worlds);events=[]
        for request in SCRIPT:
            evidence=c.evidence(request)
            event=c.step(w.key,request)
            events.append({**event,'policy':event['policy']+'|'+json.dumps(evidence,sort_keys=True)})
        traces[str(w.key)]=events
    model=hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()
    return {'schema':SCHEMA,'scope':'synthetic one-observer one-binary-fact; evidence observable',
            'model_hash':model,'budget':'0','world_records':records,'traces':traces}

def verify(artifact):
    if not isinstance(artifact,dict) or artifact.get('schema') not in (SCHEMA,'veil-dialog-audit-v1'):raise ValueError('unknown audit schema')
    records=artifact.get('world_records');traces=artifact.get('traces')
    if not isinstance(records,list) or not 1<=len(records)<=256 or not isinstance(traces,dict):raise ValueError('invalid audit domain')
    keys=[]
    for record in records:
        if not isinstance(record,dict) or set(record)!={'key','alice','bob','secret','weight'}:raise ValueError('invalid world record')
        if any(type(record[k]) is not int for k in record):raise ValueError('world values must be integers')
        if not 0<=record['alice']<=15 or not 0<=record['bob']<=15 or record['secret'] not in (0,1) or record['weight']<1:raise ValueError('invalid world values')
        keys.append(record['key'])
    if len(set(keys))!=len(keys):raise ValueError('duplicate world keys')
    lengths=set()
    for trace in traces.values():
        if not isinstance(trace,list) or len(trace)>128:raise ValueError('invalid trace length')
        lengths.add(len(trace))
        for event in trace:
            if not isinstance(event,dict) or set(event)!={'request','policy','output'}:raise ValueError('invalid visible event')
            if any(not isinstance(v,str) or len(v)>32768 for v in event.values()):raise ValueError('invalid visible event values')
    if len(lengths)!=1:raise ValueError('all world traces must cover equal observed depth')
    raw_budget=artifact.get('budget')
    if not isinstance(raw_budget,str) or len(raw_budget)>32:raise ValueError('invalid audit budget')
    try:budget=Fraction(raw_budget)
    except (ValueError,ZeroDivisionError):raise ValueError('invalid audit budget') from None
    if not 0<=budget<=1:raise ValueError('invalid audit budget')
    model=hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()
    if model!=artifact.get('model_hash'):raise ValueError('declared model hash mismatch')
    if artifact['schema']=='veil-dialog-audit-v1':
        configuration=artifact.get('public_configuration')
        if not isinstance(configuration,dict) or configuration.get('budget')!=artifact['budget']:raise ValueError('audit configuration mismatch')
        if set(configuration)!={'protected_slot','budget','public_costs','public_prior'}:raise ValueError('invalid public configuration schema')
        if type(configuration['protected_slot']) is not int or not 0<=configuration['protected_slot']<=3:raise ValueError('invalid protected slot metadata')
        prior=configuration['public_prior']
        if not isinstance(prior,list) or len(prior)!=2 or any(type(x) is not int or not 1<=x<=9 for x in prior):raise ValueError('invalid public prior metadata')
        costs=configuration.get('public_costs')
        if not isinstance(costs,list) or len(costs)!=4 or any(type(x) is not int or not 0<=x<=100 for x in costs):raise ValueError('invalid public cost metadata')
        protected=configuration['protected_slot']
        if len(records)!=16 or len({r['bob'] for r in records})!=1 or {r['alice'] for r in records}!=set(range(16)):
            raise ValueError('dialogue audit must cover its complete observer-conditioned calendar domain')
        for record in records:
            bit=(record['alice']>>protected)&1
            if record['secret']!=bit or record['weight']!=prior[bit] or record['key']!=record['alice']*16+record['bob']:
                raise ValueError('dialogue world records disagree with public configuration')

    if set(traces)!={str(r['key']) for r in records}:raise ValueError('audit trace coverage incomplete')
    # Oracle consumes observed events; does not call controller safety/policy helpers.
    result=audit(records,{int(key):value for key,value in traces.items()},budget) if next(iter(traces.values())) else {'worlds':len(records),'prefix_violation_cells':0,'invalid_meetings':0,'invalid_calendar_answers':0,'max_posterior_shift':'0'}
    result.pop('violations',None)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bob',type=int,default=5)
    parser.add_argument('--verify',metavar='JSON')
    args=parser.parse_args()
    try:
        if args.verify:
            from pathlib import Path
            path=Path(args.verify)
            if path.stat().st_size>16000000:raise ValueError('audit file exceeds supported size')
            with path.open() as f:artifact=json.load(f)
            result=verify(artifact);print(json.dumps(result,indent=2))
            if result['prefix_violation_cells'] or result['invalid_meetings'] or result['invalid_calendar_answers']:sys.exit(1)
        else:print(json.dumps(export(args.bob),indent=2))
    except (ValueError,TypeError,KeyError,OSError) as error:
        print('Audit not verified: '+str(error),file=sys.stderr);sys.exit(2)
