"""Restricted policy selection with declared PUBLIC slot inconvenience costs.
Private preferences would themselves need an observation/disclosure model.
"""
from fractions import Fraction
from engine import Controller,scheduling_policies

class PreferenceController(Controller):
    def __init__(self,worlds,costs=(0,1,2,3),budget=Fraction(0),complete=True):
        if not isinstance(costs,(tuple,list)) or len(costs)!=4 or any(type(x) is not int or not 0<=x<=100 for x in costs):
            raise ValueError('four public integer costs 0..100 required')
        self.costs=tuple(costs)
        super().__init__(worlds,budget,complete)
    def evidence(self,request,slots=4):
        evidence=super().evidence(request,slots)
        evidence['public_slot_costs']=list(self.costs)
        evidence['objective']='maximize successful prior weight, then minimize total declared public inconvenience'
        if request=='schedule':
            selected=self.choose(request,slots);weight=cost=0
            for w in self.compatible:
                output=selected.output(w)
                if output.startswith('MEETING:'):
                    weight+=w.weight;cost+=w.weight*self.costs[int(output.split(':')[1])]
            evidence['selected_success_weight']=weight
            evidence['selected_total_public_cost']=cost
            evidence['no_meeting_semantics']='No result from selected policy, not proof no actual common window exists.'
        return evidence
    def choose(self,request,slots=4):
        if not self.complete or request!='schedule':return super().choose(request,slots)
        safe=[p for p in scheduling_policies(slots) if self.safe(p)]
        if not safe:return super().choose(request,slots)
        def score(p):
            success=cost=0
            for w in self.compatible:
                out=p.output(w)
                if out.startswith('MEETING:'):
                    success+=w.weight;cost+=self.costs[int(out.split(':')[1])]*w.weight
            return (-success,cost,p.name)
        return min(safe,key=score)
