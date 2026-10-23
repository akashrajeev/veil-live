"""Local operator session persistence, not authenticated privacy storage."""
import json,uuid
from pathlib import Path
from dialog import create,create_joint,restore,ask,write,is_joint

def status(path):
    if not path.exists():return {'initialized':False}
    if path.stat().st_size>65536:raise ValueError('dialogue state too large')
    envelope=json.loads(path.read_text())
    if not isinstance(envelope,dict) or not {'generation','state'}<=set(envelope)<={'generation','state','approvals','legacy_approval_history'}:raise ValueError('bad envelope')
    if 'legacy_approval_history' in envelope and envelope['legacy_approval_history'] is not True:raise ValueError('bad envelope')
    if 'approvals' in envelope:
        if not isinstance(envelope['approvals'],list) or len(envelope['approvals'])>64:raise ValueError('bad envelope')
        for a in envelope['approvals']:
            if not isinstance(a,dict) or set(a)!={'id','request','text','status','events_at_request','approved_at_request'}:raise ValueError('bad envelope')
            if not isinstance(a['id'],str) or not isinstance(a['request'],str) or not isinstance(a['text'],str):raise ValueError('bad envelope')
            if a['status'] not in ('pending','approved','declined','stale'):raise ValueError('bad envelope')
            if not isinstance(a['events_at_request'],int) or not isinstance(a['approved_at_request'],int):raise ValueError('bad envelope')
    if not isinstance(envelope['generation'],str) or len(envelope['generation'])!=32:raise ValueError('bad generation')
    c,key=restore(envelope['state'])
    base={'initialized':True,'generation':envelope['generation'],'state':envelope['state'],
            'approvals':envelope.get('approvals'),'legacy_approval_history':envelope.get('legacy_approval_history','approvals' not in envelope),
            'events':len(c.transcript),'remaining_worlds':len(c.compatible),'history':list(c.transcript)}
    if is_joint(envelope['state']):
        base['joint_support']=[str(x) for x in c.joint_support(c.compatible)]
        base['configuration']={'alice':envelope['state']['alice'],'bob':envelope['state']['bob'],
                'protected_slots':envelope['state']['protected_slots'],'budget':str(c.budget),
                'public_priors':envelope['state'].get('public_priors'),
                'public_costs':envelope['state'].get('public_costs',[0,1,2,3])}
        return base
    base['protected_support']=str(c.support(c.compatible))
    base['configuration']={'alice':envelope['state']['alice'],'bob':envelope['state']['bob'],
                'protected_slot':envelope['state']['protected_slot'],'budget':str(c.budget),
                'public_prior':envelope['state'].get('public_prior',[1,1]),
                'public_costs':envelope['state'].get('public_costs',[0,1,2,3])}
    return base

def initialize(path,alice=7,bob=5,protected=0,budget='0',costs=(0,1,2,3),prior=(1,1),protected_slots=None,priors=None):
    if protected_slots is not None:
        value={'generation':uuid.uuid4().hex,'state':create_joint(alice,bob,protected_slots,budget,costs,priors),'approvals':[]}
    else:
        value={'generation':uuid.uuid4().hex,'state':create(alice,bob,protected,budget,costs,prior),'approvals':[]}
    path.parent.mkdir(parents=True,exist_ok=True);write(path,value)
    return status(path)

def advance(path,text,generation,expected):
    current=status(path)
    if not current['initialized']:raise ValueError('initialize dialogue first')
    if generation!=current['generation'] or expected!=current['events']:
        return {**current,'stale_request':True}
    updated,result=ask(current['state'],text)
    write(path,{**{k:v for k,v in current.items() if k in ('generation','approvals','legacy_approval_history') and v is not False},'state':updated})
    return {**status(path),'result':result}

def persist_approvals(path,generation,records,legacy=False):
    current=status(path)
    if not current['initialized']:raise ValueError('initialize dialogue first')
    if generation!=current['generation']:raise ValueError('stale generation')
    value={'generation':generation,'state':current['state'],'approvals':records}
    if legacy:value['legacy_approval_history']=True
    write(path,value)
    return status(path)
