"""Persistent synthetic guarded dialogue CLI; one observer/one fact per session.
No real messaging, approval, authentication or privacy boundary. State can be reset
or altered by the local operator; do not use as production privacy storage.
"""
import argparse,json,hashlib,sys,os
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path
from engine import Controller,World,make_worlds
from intents import parse
from preference_lab import PreferenceController

def worlds(bob,protected,prior=(1,1)):
    return tuple(World(w.key,w.alice,w.bob,(w.alice>>protected)&1,prior[(w.alice>>protected)&1])
                 for w in make_worlds() if w.bob==bob)

def create(alice=7,bob=5,protected=0,budget='0',costs=(0,1,2,3),prior=(1,1)):
    if any(type(x) is not int or not 0<=x<=15 for x in (alice,bob)):raise ValueError('calendar bitsets must be 0..15')
    if type(protected) is not int or not 0<=protected<=3:raise ValueError('protected fact must be 0..3')
    if str(budget) not in ('0','1/4','1/2'):raise ValueError('unsupported public experiment limit')
    if not isinstance(prior,(list,tuple)) or len(prior)!=2 or any(type(x) is not int or not 1<=x<=9 for x in prior):raise ValueError('invalid public prior odds')
    domain=worlds(bob,protected,prior)
    controller=PreferenceController(domain,costs,Fraction(budget))
    return {'schema':'veil-dialog-v4','alice':alice,'bob':bob,'protected_slot':protected,
            'model':hashlib.sha256(json.dumps({'worlds':[asdict(w) for w in domain],'public_costs':list(costs),'protected_slot':protected,'prior':list(prior),'budget':str(Fraction(budget))},sort_keys=True).encode()).hexdigest(),
            'public_costs':list(costs),'public_prior':list(prior),'ledger':controller.dump()}

def restore(state):
    if is_joint(state):return restore_joint(state)
    basic={'schema','alice','bob','protected_slot','model','ledger'}
    if not isinstance(state,dict) or set(state) not in (basic,basic|{'public_costs','public_prior'}):
        raise ValueError('invalid dialogue schema')
    if state['schema'] not in ('veil-dialog-v1','veil-dialog-v2','veil-dialog-v3','veil-dialog-v4'):raise ValueError('unknown dialogue version')
    if state['schema'] in ('veil-dialog-v2','veil-dialog-v3','veil-dialog-v4') and set(state)!=basic|{'public_costs','public_prior'}:raise ValueError('v2 parameters missing')
    costs=state.get('public_costs',[0,1,2,3]);prior=state.get('public_prior',[1,1])
    if not isinstance(state['ledger'],str) or len(state['ledger'].encode())>65536:raise ValueError('invalid saved dialogue ledger')
    ledger_root=json.loads(state['ledger'])
    if not isinstance(ledger_root,dict):raise ValueError('invalid saved dialogue ledger root')
    saved_budget=ledger_root.get('budget')
    declared=create(state['alice'],state['bob'],state['protected_slot'],budget=saved_budget,costs=costs,prior=prior)
    expected_model=declared['model']
    if state['schema'] in ('veil-dialog-v1','veil-dialog-v2'):
        expected_model=hashlib.sha256(json.dumps([asdict(w) for w in worlds(state['bob'],state['protected_slot'],prior)],sort_keys=True).encode()).hexdigest()
    if state['schema']=='veil-dialog-v3':
        expected_model=hashlib.sha256(json.dumps({'worlds':[asdict(w) for w in worlds(state['bob'],state['protected_slot'],prior)],'public_costs':list(costs),'protected_slot':state['protected_slot'],'prior':list(prior)},sort_keys=True).encode()).hexdigest()
    if expected_model!=state['model']:raise ValueError('dialogue model mismatch')
    domain=worlds(state['bob'],state['protected_slot'],prior)
    factory=lambda domain,budget,complete:PreferenceController(domain,costs,budget,complete)
    c=Controller.load(domain,state['ledger'],factory=factory)
    key=state['alice']*16+state['bob']
    if key not in {w.key for w in c.compatible}:raise ValueError('dialogue impossible for declared actual world')
    return c,key


def create_joint(alice=7,bob=5,protected_slots=(0,),budget='0',costs=(0,1,2,3),priors=None):
    """veil-dialog-v5: joint protection of 1-2 windows; preserves the full JOINT distribution."""
    from joint_engine import joint_worlds,JointController
    if any(type(x) is not int or not 0<=x<=15 for x in (alice,bob)):raise ValueError('calendar bitsets must be 0..15')
    slots=tuple(protected_slots)
    if priors is None:priors=tuple((1,1) for _ in slots)
    priors=tuple(tuple(p) for p in priors)
    if str(budget) not in ('0','1/4','1/2'):raise ValueError('unsupported public experiment limit')
    domain=joint_worlds(bob,slots,priors)
    controller=JointController(domain,costs,Fraction(budget),k=len(slots))
    return {'schema':'veil-dialog-v5','alice':alice,'bob':bob,'protected_slots':list(slots),
            'model':hashlib.sha256(json.dumps({'worlds':[asdict(w) for w in domain],'public_costs':list(costs),'protected_slots':list(slots),'priors':[list(p) for p in priors],'budget':str(Fraction(budget))},sort_keys=True).encode()).hexdigest(),
            'public_costs':list(costs),'public_priors':[list(p) for p in priors],'ledger':controller.dump()}

def restore_joint(state):
    from joint_engine import joint_worlds,JointController
    basic={'schema','alice','bob','protected_slots','model','ledger'}
    if not isinstance(state,dict) or set(state) not in (basic,basic|{'public_costs','public_priors'}):
        raise ValueError('invalid joint dialogue schema')
    if state['schema']!='veil-dialog-v5':raise ValueError('unknown dialogue version')
    if set(state)!=basic|{'public_costs','public_priors'}:raise ValueError('v5 parameters missing')
    costs=state.get('public_costs',[0,1,2,3]);priors=state.get('public_priors',[[1,1]]*len(state['protected_slots']))
    if not isinstance(state['ledger'],str) or len(state['ledger'].encode())>65536:raise ValueError('invalid saved dialogue ledger')
    ledger_root=json.loads(state['ledger'])
    if not isinstance(ledger_root,dict):raise ValueError('invalid saved dialogue ledger root')
    declared=create_joint(state['alice'],state['bob'],state['protected_slots'],budget=ledger_root.get('budget'),costs=costs,priors=priors)
    if declared['model']!=state['model']:raise ValueError('dialogue model mismatch')
    domain=joint_worlds(state['bob'],tuple(state['protected_slots']),priors)
    factory=lambda worlds,budget:JointController(worlds,costs,budget,k=len(state['protected_slots']))
    c=JointController.load(domain,state['ledger'],factory=factory)
    key=state['alice']*16+state['bob']
    if key not in {w.key for w in c.compatible}:raise ValueError('dialogue impossible for declared actual world')
    return c,key

def is_joint(state):
    return isinstance(state,dict) and state.get('schema')=='veil-dialog-v5'

def ask(state,text):
    c,key=restore(state)
    if len(c.transcript)>=128:raise ValueError('dialogue event limit reached')
    intent=parse(text)
    request=intent['intent']
    evidence=c.evidence(request);event=c.step(key,request)
    if is_joint(state):
        updated=create_joint(state['alice'],state['bob'],state['protected_slots'],str(c.budget),state.get('public_costs',[0,1,2,3]),state.get('public_priors'))
        updated['ledger']=c.dump()
        joint={'joint_support':[str(x) for x in c.joint_support(c.compatible)]}
    else:
        updated=create(state['alice'],state['bob'],state['protected_slot'],str(c.budget),state.get('public_costs',[0,1,2,3]),state.get('public_prior',[1,1]))
        updated['ledger']=c.dump()
        joint={'protected_support':str(c.support(c.compatible))}
    return updated,{'intent':intent,'observation':event,'decision':evidence,
                    **joint,
                    'remaining_worlds':len(c.compatible),'events':len(c.transcript),'safe_alternatives':c.alternatives(),
                    'scope':'synthetic one-observer one-fact; unsupported input uniformly refused'}

def audit_dialogue(state):
    """Replay actual typed request sequence for all declared initial worlds."""
    from oracle import audit
    c,key=restore(state)
    domain=worlds(state['bob'],state['protected_slot'],state.get('public_prior',[1,1]))
    requests=[e['request'] for e in c.transcript]
    budget=c.budget
    traces={}
    for w in domain:
        other=PreferenceController(domain,state.get('public_costs',[0,1,2,3]),budget);events=[]
        for request in requests:
            evidence=other.evidence(request);event=other.step(w.key,request)
            visible={'observation':event,'decision':evidence,
                     'protected_support':str(other.support(other.compatible)),
                     'remaining_worlds':len(other.compatible),'events':len(other.transcript),'safe_alternatives':other.alternatives()}
            events.append({'request':request,'policy':'guarded-visible-dialogue|'+json.dumps(visible,sort_keys=True),
                           'output':event['output']})
        traces[str(w.key)]=events
    records=[asdict(w) for w in domain]
    result=audit(records,{int(k):v for k,v in traces.items()},budget) if requests else {
        'worlds':len(domain),'prefix_violation_cells':0,'invalid_meetings':0,'invalid_calendar_answers':0,'max_posterior_shift':'0'}
    result.pop('violations',None)
    return {'schema':'veil-dialog-audit-v1',
            'public_configuration':{'protected_slot':state['protected_slot'],'budget':str(budget),
                'public_costs':state.get('public_costs',[0,1,2,3]),'public_prior':state.get('public_prior',[1,1])},
            'scope':'declared synthetic observer channel; no private storage guarantee',
            'world_records':records,'model_hash':hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest(),'budget':str(budget),'traces':traces,'observed':result}

def write(path,state):
    temporary=path.with_suffix('.tmp')
    with temporary.open('w') as f:
        json.dump(state,f);f.flush();os.fsync(f.fileno())
    temporary.replace(path)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--state',default='.state/dialog.json')
    sub=p.add_subparsers(dest='command',required=True)
    init=sub.add_parser('init');init.add_argument('--alice',type=int,default=7);init.add_argument('--bob',type=int,default=5);init.add_argument('--protected',type=int,default=0);init.add_argument('--budget',default='0');init.add_argument('--replace',action='store_true')
    q=sub.add_parser('ask');q.add_argument('text');sub.add_parser('status')
    args=p.parse_args();path=Path(args.state)
    try:
        if args.command=='init':
            if path.exists() and not args.replace:raise ValueError('state exists; use --replace only for a new synthetic experiment')
            path.parent.mkdir(parents=True,exist_ok=True);state=create(args.alice,args.bob,args.protected,args.budget);write(path,state);print('Initialized synthetic dialogue.')
        else:
            if path.stat().st_size>65536:raise ValueError('state too large')
            state=json.loads(path.read_text())
            if args.command=='ask':
                updated,result=ask(state,args.text);write(path,updated);print(json.dumps(result,indent=2))
            else:
                c,key=restore(state);print(json.dumps({'events':len(c.transcript),'protected_support':str(c.support(c.compatible)),'possible_worlds':len(c.compatible)},indent=2))
    except (ValueError,OSError,KeyError,TypeError) as e:print(str(e),file=sys.stderr);sys.exit(1)
