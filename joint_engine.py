"""Joint multi-fact privacy engine: protects a SET of Alice's windows by preserving the
full JOINT distribution over the protected bit-pattern, not each margin independently.
Implements the same controller interface as engine.Controller/PreferenceController."""
import json
from fractions import Fraction
from engine import Policy, World, make_worlds, scheduling_policies, REFUSE, UNKNOWN, PARITY, BIT1

def joint_worlds(bob, protected_slots, priors):
    """protected_slots: tuple of 1-2 distinct slot indices. priors: per-slot (busy_w, free_w)
    integer weights 1-9; the declared joint prior is the PRODUCT (independence declared)."""
    if type(bob) is not int or not 0 <= bob <= 15:
        raise ValueError('observer calendar bitset must be 0..15')
    if not isinstance(protected_slots, (tuple, list)) or not 1 <= len(protected_slots) <= 2:
        raise ValueError('protected set must name 1..2 windows')
    slots = tuple(protected_slots)
    if len(set(slots)) != len(slots) or any(type(s) is not int or not 0 <= s <= 3 for s in slots):
        raise ValueError('protected windows must be distinct slot indices 0..3')
    if not isinstance(priors, (tuple, list)) or len(priors) != len(slots):
        raise ValueError('one declared prior pair per protected window')
    for p in priors:
        if not isinstance(p, (tuple, list)) or len(p) != 2 or any(type(x) is not int or not 1 <= x <= 9 for x in p):
            raise ValueError('each declared prior must be integer weights 1..9')
    out = []
    for w in make_worlds():
        if w.bob != bob:
            continue
        secret = 0
        weight = 1
        for i, slot in enumerate(slots):
            bit = (w.alice >> slot) & 1
            secret |= bit << i
            weight *= priors[i][bit]
        out.append(World(w.key, w.alice, w.bob, secret, weight))
    return tuple(out)

class JointController:
    def __init__(self, worlds, costs=(0, 1, 2, 3), budget=Fraction(0), k=None):
        if not isinstance(costs, (tuple, list)) or len(costs) != 4 or any(type(x) is not int or not 0 <= x <= 100 for x in costs):
            raise ValueError('four public integer costs 0..100 required')
        self.costs = tuple(costs)
        self.worlds = tuple(worlds)
        if not self.worlds:
            raise ValueError('declared world set must be non-empty')
        self.k = k if k is not None else max(1, max(w.secret.bit_length() for w in self.worlds))
        if type(self.k) is not int or not 1 <= self.k <= 2:
            raise ValueError('joint protected set size must be 1..2')
        if any(not 0 <= w.secret < (1 << self.k) or w.weight < 1 for w in self.worlds):
            raise ValueError('joint worlds require positive weights and secrets within the declared pattern space')
        if type(budget) is bool:
            raise ValueError('budget must not be boolean')
        self.budget = Fraction(budget)
        if not 0 <= self.budget <= 1:
            raise ValueError('public budget must be in [0,1]')
        self.baseline = self.joint_support(self.worlds)
        self.compatible = self.worlds
        self.transcript = []

    def joint_support(self, worlds):
        tot = sum(w.weight for w in worlds)
        cells = [0] * (1 << self.k)
        for w in worlds:
            cells[w.secret] += w.weight
        return tuple(Fraction(c, tot) for c in cells)

    def partitions(self, policy):
        cells = {}
        for w in self.compatible:
            cells.setdefault(policy.output(w), []).append(w)
        return cells

    def safe(self, policy):
        for cell in self.partitions(policy).values():
            sup = self.joint_support(cell)
            if any(abs(a - b) > self.budget for a, b in zip(sup, self.baseline)):
                return False
        return True

    def choose(self, request, slots=4):
        if request in ('slot0', 'slot2', 'slot3'):
            policy = Policy('alice-slot' + request[-1] + '-availability', 'availability', (int(request[-1]),))
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
                success = cost = 0
                for w in self.compatible:
                    out = p.output(w)
                    if out.startswith('MEETING:'):
                        success += w.weight
                        cost += self.costs[int(out.split(':')[1])] * w.weight
                return (-success, cost, p.name)
            return min(safe, key=score)
        return REFUSE

    def step(self, actual_key, request, slots=4):
        w = next((w for w in self.compatible if w.key == actual_key), None)
        if w is None:
            raise ValueError('actual world inconsistent with declared transcript')
        p = self.choose(request, slots)
        out = p.output(w)
        event = {'request': request, 'policy': p.name, 'output': out}
        self.compatible = tuple(self.partitions(p)[out])
        self.transcript.append(event)
        return event

    def evidence(self, request, slots=4):
        """Public-class decision evidence, NEVER computed from the actual world."""
        selected = self.choose(request, slots)
        def inspect(policy):
            cells = self.partitions(policy)
            return [{'output': output, 'world_count': len(cell),
                     'prior_weight': sum(w.weight for w in cell),
                     'joint_support': [str(x) for x in self.joint_support(cell)],
                     'max_joint_shift_from_initial': str(max(abs(a - b) for a, b in zip(self.joint_support(cell), self.baseline)))}
                    for output, cell in sorted(cells.items())]
        evidence = {'initial_joint_support': [str(x) for x in self.baseline], 'budget': str(self.budget),
                    'compatible_worlds': len(self.compatible), 'selected_policy': selected.name,
                    'selected_cells': inspect(selected), 'domain_complete': True,
                    'contract': 'joint distribution over the protected window pattern, not per-window margins'}
        if request == 'schedule':
            policies = scheduling_policies(slots)
            evidence['candidate_policies'] = len(policies)
            evidence['safe_candidates'] = sum(self.safe(p) for p in policies)
        return evidence

    def alternatives(self):
        requests = ('parity', 'slot0', 'slot1', 'slot2', 'slot3', 'schedule')
        return [{'request': request, 'policy': self.choose(request).name}
                for request in requests if self.choose(request).kind not in ('refuse', 'unknown')]

    def dump(self):
        return json.dumps({'budget': str(self.budget), 'transcript': self.transcript}, sort_keys=True)

    @classmethod
    def load(cls, worlds, data, factory=None):
        if not isinstance(data, str) or len(data.encode('utf-8')) > 65536:
            raise ValueError('saved ledger exceeds supported size')
        saved = json.loads(data)
        if not isinstance(saved, dict) or set(saved) != {'budget', 'transcript'}:
            raise ValueError('invalid saved joint ledger schema')
        if not isinstance(saved['transcript'], list) or len(saved['transcript']) > 128:
            raise ValueError('saved ledger exceeds supported events')
        try:
            budget = Fraction(saved['budget'])
        except (ValueError, ZeroDivisionError):
            raise ValueError('invalid saved budget') from None
        c = factory(worlds, budget) if factory else cls(worlds, budget=budget)
        for event in saved['transcript']:
            if not isinstance(event, dict) or set(event) != {'request', 'policy', 'output'}:
                raise ValueError('invalid saved event schema')
            if any(not isinstance(value, str) or len(value) > 256 for value in event.values()):
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
