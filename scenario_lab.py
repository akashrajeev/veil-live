"""Bounded counterfactual coordination lab. No private approval or real booking."""
from fractions import Fraction
from dataclasses import asdict
from engine import Controller,World,Policy,PARITY,BIT1,make_worlds,scheduling_policies
from oracle import audit
from preference_lab import PreferenceController
import json

BUDGETS={'strict':Fraction(0),'quarter':Fraction(1,4),'open':Fraction(1,2)}
SCRIPTS={'composition':('parity','slot1','schedule'),'direct':('schedule',)}
SCENARIOS={
 'dinner':{'label':'Dinner with a later safe window','alice':7,'bob':5},
 'early-only':{'label':'Only the protected window is shared','alice':1,'bob':1},
 'no-common':{'label':'No actual shared window','alice':3,'bob':12},
 'many-options':{'label':'Several windows to negotiate','alice':15,'bob':14},
}

def run_scenario(alice,bob,budget='strict',script='composition',protected_slot=0,costs=(0,1,2,3),prior_odds=(1,1)):
    if type(alice) is not int or type(bob) is not int or not 0<=alice<=15 or not 0<=bob<=15:
        raise ValueError('fictional calendar bitsets must be integers 0..15')
    if type(protected_slot) is not int or not 0<=protected_slot<=3:raise ValueError('protected slot must be 0..3')
    if not isinstance(prior_odds,(tuple,list)) or len(prior_odds)!=2 or any(type(x) is not int or not 1<=x<=9 for x in prior_odds):raise ValueError('public prior odds require two integers1..9')
    if budget not in BUDGETS or script not in SCRIPTS:raise ValueError('unsupported experiment option')
    worlds=tuple(World(w.key,w.alice,w.bob,(w.alice>>protected_slot)&1,prior_odds[(w.alice>>protected_slot)&1]) for w in make_worlds() if w.bob==bob)
    actual=next(w for w in worlds if w.alice==alice)
    c=PreferenceController(worlds,costs,BUDGETS[budget]);trace=[];evidence=[]
    trajectory=[{'prefix':0,'support':str(c.baseline),'worlds':len(worlds),'output':'INITIAL'}]
    for request in SCRIPTS[script]:
        evidence.append(c.evidence(request));trace.append(c.step(actual.key,request))
        trajectory.append({'prefix':len(trace),'support':str(c.support(c.compatible)),
                           'worlds':len(c.compatible),'output':trace[-1]['output']})
    all_traces={}
    for w in worlds:
        other=PreferenceController(worlds,costs,BUDGETS[budget]);events=[]
        for request in SCRIPTS[script]:
            e=json.dumps(other.evidence(request),sort_keys=True)
            event=other.step(w.key,request)
            events.append({**event,'policy':event['policy']+'|'+e})
        all_traces[w.key]=events
    metrics=audit([asdict(w) for w in worlds],all_traces,BUDGETS[budget]);metrics.pop('violations')
    naive_policies={'parity':PARITY,'slot1':BIT1,'schedule':Policy('unguarded-first-common','schedule',(0,1,2,3))}
    naive_traces={w.key:[{'request':r,'policy':naive_policies[r].name,'output':naive_policies[r].output(w)} for r in SCRIPTS[script]] for w in worlds}
    naive_actual=naive_traces[actual.key]
    naive_compatible=[w for w in worlds if naive_traces[w.key]==naive_actual]
    naive_support=Controller.support(naive_compatible)
    naive_metrics=audit([asdict(w) for w in worlds],naive_traces,BUDGETS[budget]);naive_metrics.pop('violations')
    baseline_result={'trace':naive_actual,'final_support':str(naive_support),
                     'remaining_worlds':len(naive_compatible),'observer_metrics':naive_metrics,
                     'note':'Unguarded earliest-slot policy; no private preferences or disclosure checks.'}
    last=trace[-1]['output'];actual_common=[i for i in range(4) if alice&bob&(1<<i)]
    return {'alice':alice,'bob':bob,'protected_slot':protected_slot,'public_slot_costs':list(costs),'public_prior_odds':list(prior_odds),'budget':str(BUDGETS[budget]),'script':script,
            'trace':trace,'trajectory':trajectory,'decisions':evidence,'possible_alice_calendars':[w.alice for w in c.compatible],
            'initial_support':str(c.baseline),'final_support':str(c.support(c.compatible)),
            'actual_common_slots':actual_common,
            'outcome':'scheduled' if last.startswith('MEETING:') else 'no_supported_safe_plan',
            'note':'Refusal is not proof no meeting exists; optimization only over supported policies.',
            'observer_metrics':metrics,'unguarded_baseline':baseline_result,
            'result_schema':'veil-counterfactual-result-v1',
            'reproduce':{'alice':alice,'bob':bob,'budget':budget,'script':script,'protected_slot':protected_slot,
                         'public_costs':list(costs),'public_prior_odds':list(prior_odds)},
            'interpretation':'Results are finite declared-model observations. Do not infer real-world privacy or secure storage.'}

def comparison(alice,bob,script="composition",protected_slot=0,costs=(0,1,2,3),prior_odds=(1,1)):
    return {mode:run_scenario(alice,bob,mode,script,protected_slot,costs,prior_odds) for mode in BUDGETS}

if __name__=='__main__':
    print(json.dumps({name:{'scenario':spec,'comparison':comparison(spec['alice'],spec['bob'])}
                      for name,spec in SCENARIOS.items()},indent=2))
