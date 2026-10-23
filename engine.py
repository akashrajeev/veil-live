"""Veil finite-world prototype. stdlib only; no real calendar or security boundary."""
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations, permutations
from functools import lru_cache
import json

@dataclass(frozen=True)
class World:
    key: int
    alice: int
    bob: int
    secret: int
    weight: int = 1

@dataclass(frozen=True)
class Policy:
    name: str
    kind: str
    slots: tuple = ()

    def output(self, w):
        if self.kind == 'parity':
            return 'PARITY:' + str(((w.alice >> 0) ^ (w.alice >> 1)) & 1)
        if self.kind == 'availability':
            slot=self.slots[0]
            return 'ALICE_SLOT'+str(slot)+':'+str((w.alice>>slot)&1)
        if self.kind == 'bit1':
            return 'ALICE_SLOT1:' + str((w.alice >> 1) & 1)
        if self.kind == 'schedule':
            for slot in self.slots:
                if (w.alice & w.bob) & (1 << slot):
                    return 'MEETING:' + str(slot)
            return 'NO_MEETING'
        if self.kind == 'refuse':
            return 'NEEDS_PRIVATE_DECISION'
        if self.kind == 'unknown':
            return 'UNKNOWN_DECLARED_DOMAIN'
        raise ValueError('public invalid policy kind')

REFUSE = Policy('uniform-refusal', 'refuse')
UNKNOWN = Policy('uniform-unknown', 'unknown')
PARITY = Policy('calendar-parity-0-1', 'parity')
BIT1 = Policy('alice-slot1-availability', 'bit1')


def make_worlds(slots=4):
    if type(slots) is not int or not 1 <= slots <= 4:
        raise ValueError("declared slots must be 1..4")
    # Explicit synthetic prior: all combinations equally likely. Protected fact:
    # Alice's availability at slot0. A real calendar prior is not established.
    return tuple(World(a * (1 << slots) + b, a, b, a & 1)
                 for a in range(1 << slots) for b in range(1 << slots))


@lru_cache(maxsize=4)
def scheduling_policies(slots=4):
    if type(slots) is not int or not 1 <= slots <= 4:
        raise ValueError("declared slots must be 1..4")
    result = []
    for count in range(1, slots + 1):
        for subset in combinations(range(slots), count):
            for order in permutations(subset):
                result.append(Policy('first-common-' + '-'.join(map(str, order)),
                                     'schedule', order))
    return tuple(result)

class Controller:
    def __init__(self, worlds, budget=Fraction(0), complete=True):
        self.worlds = tuple(worlds)
        if type(complete) is not bool:
            raise ValueError("complete-domain flag must be boolean")
        if not self.worlds or len(self.worlds) > 256:
            raise ValueError('public domain must contain 1..256 worlds')
        if any(type(w.key) is not int or type(w.alice) is not int or type(w.bob) is not int
               or not 0 <= w.alice <= 15 or not 0 <= w.bob <= 15
               or type(w.secret) is not int or type(w.weight) is not int for w in self.worlds):
            raise ValueError('declared world requires integer key, four-slot bitsets, secret and weight')
        if len({w.key for w in self.worlds}) != len(self.worlds):
            raise ValueError('public world keys must be unique')
        if any(w.secret not in (0, 1) or w.weight < 1 for w in self.worlds):
            raise ValueError('public prior requires positive integer weights and binary secret')
        if type(budget) is bool:raise ValueError('budget must not be boolean')
        try:self.budget = Fraction(budget)
        except (ValueError,ZeroDivisionError):raise ValueError('public budget must be a finite rational') from None
        if not 0 <= self.budget <= 1:
            raise ValueError('public budget must be in [0,1]')
        self.complete = complete
        self.baseline = self.support(self.worlds)
        self.compatible = self.worlds
        self.transcript = []
        self._partition_cache = {}

    @staticmethod
    def support(worlds):
        return Fraction(sum(w.weight * w.secret for w in worlds),
                        sum(w.weight for w in worlds))

    def partitions(self, policy):
        key=(tuple(w.key for w in self.compatible),policy)
        if key in self._partition_cache:return dict(self._partition_cache[key])
        cells = {}
        for w in self.compatible:
            cells.setdefault(policy.output(w), []).append(w)
        if len(self._partition_cache)>=256:self._partition_cache.clear()
        self._partition_cache[key]=tuple((output,tuple(cell)) for output,cell in cells.items())
        return dict(self._partition_cache[key])

    def safe(self, policy):
        return all(abs(self.support(cell) - self.baseline) <= self.budget
                   for cell in self.partitions(policy).values())

    def evidence(self, request, slots=4):
        """Public-class decision evidence, NEVER computed from the actual world."""
        selected = self.choose(request, slots)
        proposed = Policy('alice-slot'+request[-1]+'-availability','availability',(int(request[-1]),)) if request in ('slot0','slot2','slot3') else PARITY if request in ('parity','parity-again') else BIT1 if request in ('slot1','slot1-paraphrase') else None
        def inspect(policy):
            cells = self.partitions(policy)
            return [{'output': output, 'world_count': len(cell),
                     'prior_weight': sum(w.weight for w in cell),
                     'protected_support': str(self.support(cell)),
                     'shift_from_initial': str(abs(self.support(cell)-self.baseline))}
                    for output,cell in sorted(cells.items())]
        evidence = {'initial_support': str(self.baseline), 'budget': str(self.budget),
                    'compatible_worlds': len(self.compatible), 'selected_policy': selected.name,
                    'selected_cells': inspect(selected), 'domain_complete': self.complete}
        if proposed:
            evidence['requested_policy'] = proposed.name
            evidence['requested_cells'] = inspect(proposed)
            evidence['requested_supported'] = self.complete and self.safe(proposed)
        if request == 'schedule':
            policies=scheduling_policies(slots)
            evidence['candidate_policies'] = len(policies)
            evidence['safe_candidates'] = sum(self.safe(p) for p in policies) if self.complete else 0
        return evidence

    def alternatives(self):
        """Class-wide suggestions, observable and not chosen from actual secrets."""
        requests=('parity','slot0','slot1','slot2','slot3','schedule')
        return [{'request':request,'policy':self.choose(request).name}
                for request in requests if self.choose(request).kind not in ('refuse','unknown')]

    def choose(self, request, slots=4):
        # Selection depends only on observer-compatible class, never actual world.
        if not self.complete:
            return UNKNOWN
        if request in ('slot0','slot2','slot3'):
            policy=Policy('alice-slot'+request[-1]+'-availability','availability',(int(request[-1]),))
            return policy if self.safe(policy) else REFUSE
        if request in ('parity', 'parity-again'):
            return PARITY if self.safe(PARITY) else REFUSE
        if request in ('slot1', 'slot1-paraphrase'):
            return BIT1 if self.safe(BIT1) else REFUSE
        if request == 'schedule':
            safe = [p for p in scheduling_policies(slots) if self.safe(p)]
            if not safe:
                return REFUSE
            def score(p):
                success_weight = 0
                disruption = 0
                for w in self.compatible:
                    out = p.output(w)
                    if out.startswith('MEETING:'):
                        success_weight += w.weight
                        disruption += int(out.split(':')[1]) * w.weight
                # Maximize success, then minimize public slot index cost. All scores
                # are class-wide expectations, not actual-world personal costs.
                return (-success_weight, disruption, p.name)
            return min(safe, key=score)
        return REFUSE  # unsupported intent, same shape across all worlds

    def step(self, actual_key, request, slots=4):
        w = next((w for w in self.compatible if w.key == actual_key), None)
        if w is None:
            raise ValueError('actual world inconsistent with declared transcript')
        p = self.choose(request, slots)
        out = p.output(w)
        # Policy identity is observable and included, though invariant within class.
        event = {'request': request, 'policy': p.name, 'output': out}
        self.compatible = tuple(self.partitions(p)[out])
        self.transcript.append(event)
        return event

    def dump(self):
        return json.dumps({'budget': str(self.budget), 'complete': self.complete,
                           'transcript': self.transcript}, sort_keys=True)

    @classmethod
    def load(cls, worlds, data, factory=None):
        if not isinstance(data,str) or len(data.encode('utf-8')) > 65536:
            raise ValueError('saved ledger exceeds supported size')
        saved = json.loads(data)
        if not isinstance(saved,dict) or set(saved) != {'budget','complete','transcript'}:
            raise ValueError('invalid saved ledger schema')
        if not isinstance(saved['transcript'],list) or len(saved['transcript'])>128:
            raise ValueError('saved ledger exceeds supported events')
        if not isinstance(saved['budget'],str) or len(saved['budget'])>64:
            raise ValueError('invalid saved budget')
        try:budget=Fraction(saved['budget'])
        except (ValueError,ZeroDivisionError):raise ValueError('invalid saved budget') from None
        c = (factory or cls)(worlds, budget, saved['complete'])
        for event in saved['transcript']:
            if not isinstance(event,dict) or set(event) != {'request','policy','output'}:
                raise ValueError('invalid saved event schema')
            if any(not isinstance(value,str) or len(value)>256 for value in event.values()):
                raise ValueError('invalid saved event field')
            p = c.choose(event['request'])
            if p.name != event['policy']:
                raise ValueError('invalid saved policy')
            cells = c.partitions(p)
            if event['output'] not in cells:
                raise ValueError('invalid saved output')
            c.compatible = tuple(cells[event['output']])
            c.transcript.append(dict(event))
        return c
