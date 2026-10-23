"""Minimal MCP server (JSON-RPC 2.0 over Streamable HTTP, spec 2025-11-25), stdlib only.

Exposes Veil's disclosure guard as tools an assistant (a real MCP client, or the
simulated Alexa+ page in this repo) can call. The calendars behind it are synthetic."""
import json, math
from pathlib import Path
import dialog_store
from dialog import restore
from intents import parse as parse_intent

PROTOCOL = '2025-11-25'
SUPPORTED = ('2025-11-25',)
SLOTS = ['early prep', 'late prep', 'dinner', 'cleanup']
INFO = {'name': 'veil-household-guard', 'title': 'Veil household disclosure guard', 'version': '0.2.0'}
SCOPE = 'Synthetic one-observer, one-protected-fact model. Not a real calendar integration or privacy guarantee.'
JOINT_SCOPE = ('Synthetic one-observer model protecting a declared SET of 1-2 windows: the guard preserves the full JOINT '
               'distribution over the protected free/busy pattern, not each window\'s margin separately. The declared joint '
               'prior is the product of the per-window declared priors (independence declared). Not a real calendar integration or privacy guarantee.')

ALL_TOOLS = [
 {'name': 'veil_start_household',
  'title': 'Start a synthetic household session',
  'description': 'OPERATOR SURFACE ONLY. Begin a new synthetic shared-home conversation. Alice has a private calendar; Bob (the observer in the room) asks questions. One of Alice\'s four windows is protected. Resets any earlier session. Pass only_if_uninitialized=true to attach instead of resetting when a session already exists.',
  'inputSchema': {'type': 'object', 'properties': {
      'protected_window': {'type': 'string', 'enum': SLOTS, 'description': 'Which of Alice\'s windows must not be inferable from the conversation.'},
      'alice_calendar': {'type': 'integer', 'minimum': 0, 'maximum': 15, 'description': 'Which of the 16 synthetic calendars Alice actually has (the ground truth the guard protects). Defaults to 7.'},
      'prior': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1, 'maximum': 9}, 'minItems': 2, 'maxItems': 2,
                'description': 'Declared prior odds [busy, free] for the protected window, integer weights 1-9. Defaults to [1, 1] (uniform).'},
      'protected_windows': {'type': 'array', 'items': {'type': 'string', 'enum': SLOTS}, 'minItems': 1, 'maxItems': 2, 'uniqueItems': True,
                'description': 'JOINT protection: 1 or 2 distinct windows whose combined free/busy pattern must not be inferable. Use instead of protected_window.'},
      'priors': {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1, 'maximum': 9}, 'minItems': 2, 'maxItems': 2}, 'minItems': 1, 'maxItems': 2,
                'description': 'One [busy, free] declared prior per entry of protected_windows; the joint prior is their product (independence declared). Defaults to uniform.'},
      'only_if_uninitialized': {'type': 'boolean',
                'description': 'When true, start ONLY if no household session exists; if one exists, do not reset it - return already_running with its answered-count instead. Atomic under the server lock, so concurrent first boots cannot erase an answered session.'}},
    'additionalProperties': False},
  'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False}},
 {'name': 'veil_ask',
  'title': 'Ask about Alice\'s availability',
  'description': 'Ask a supported availability or scheduling question on Bob\'s behalf. Veil answers only if the whole conversation so far still leaves the protected window undetermined; otherwise it refuses uniformly and offers a private-approval route, or schedules a safe shared slot. Supported: "is alice free for early prep|late prep|dinner|cleanup", "can alice cover exactly one of the two prep windows", "find a shared window".',
  'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string', 'maxLength': 240}}, 'required': ['text'], 'additionalProperties': False},
  'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False}},
 {'name': 'veil_request_approval',
  'title': 'Ask Alice to approve one disclosure (simulated)',
  'description': 'Ask Alice, over a SIMULATED in-app private channel (not a real message or person), to approve disclosing the answer to ONE supported request. Returns a pending approval id. Alice may decline or never answer. A pending request goes stale if the shared conversation changes after it was asked (a new answer is given, or another disclosure is approved); a stale request can never be approved - ask again on the current conversation. If she approves, only that single answer is disclosed, and afterwards Veil stops answering directly until the household is reset.',
  'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string', 'maxLength': 240}}, 'required': ['text'], 'additionalProperties': False},
  'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False}},
 {'name': 'veil_check_approval',
  'title': 'Check an approval request',
  'description': 'Returns pending, stale, declined, or approved (with the single approved answer) for an approval id. Stale means the conversation changed after the request was asked, so it can no longer be approved.',
  'inputSchema': {'type': 'object', 'properties': {'approval_id': {'type': 'string', 'minLength': 1}}, 'required': ['approval_id'], 'additionalProperties': False},
  'annotations': {'readOnlyHint': True, 'openWorldHint': False}},
 {'name': 'veil_decide_approval',
  'title': 'Alice decides (simulated operator)',
  'description': 'OPERATOR SURFACE ONLY. Simulates Alice approving or declining a pending request. Before deciding, veil_operator_view lists what the room would be able to infer if approved.',
  'inputSchema': {'type': 'object', 'properties': {'approval_id': {'type': 'string', 'minLength': 1}, 'approve': {'type': 'boolean'}}, 'required': ['approval_id', 'approve'], 'additionalProperties': False},
  'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False}},
 {'name': 'veil_operator_view',
  'title': 'Demo operator view (not for the assistant model)',
  'description': 'OPERATOR SURFACE ONLY. Shows what an unguarded assistant would have let the observer infer from the same questions. This necessarily reveals the synthetic protected fact, so a real deployment must not expose it to the assistant or the room. Used by the demo page\'s operator panel.',
  'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
  'annotations': {'readOnlyHint': True, 'openWorldHint': False}},
 {'name': 'veil_status',
  'title': 'Conversation status',
  'description': 'How many questions have been answered in this conversation. Reveals nothing about the calendars.',
  'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
  'annotations': {'readOnlyHint': True, 'openWorldHint': False}},
]

ASSISTANT_TOOLS = ('veil_ask', 'veil_status', 'veil_request_approval', 'veil_check_approval')
OPERATOR_TOOLS = ('veil_start_household', 'veil_operator_view', 'veil_decide_approval')
def tools_for(surface):
    allowed = ASSISTANT_TOOLS if surface == 'assistant' else OPERATOR_TOOLS + ASSISTANT_TOOLS
    return [t for t in ALL_TOOLS if t['name'] in allowed]

def spoken(out):
    if out == 'NEEDS_PRIVATE_DECISION':
        return 'I can\'t answer that.'
    if out.startswith('ALICE_SLOT'):
        slot, val = out[len('ALICE_SLOT'):].split(':')
        return f'Alice is {"free" if val == "1" else "busy"} for {SLOTS[int(slot)]}.'
    if out.startswith('MEETING:'):
        return f'How about {SLOTS[int(out.split(":")[1])]}? That works for both.'
    if out == 'NO_MEETING':
        return 'I can\'t propose a shared window.'
    return 'Okay.'

def _truthful(req, alice, bob):
    """What a naive assistant that simply answers truthfully would say (same request vocabulary)."""
    if req.startswith('slot') and req[4:].isdigit():
        return (alice >> int(req[4:])) & 1
    if req == 'parity':
        return ((alice >> 0) & 1) ^ ((alice >> 1) & 1)
    if req == 'schedule':
        common = alice & bob
        return next((k for k in range(4) if (common >> k) & 1), None)
    return 'refused'


def _h2(p):
    return 0.0 if p <= 0.0 or p >= 1.0 else -(p * math.log2(p) + (1.0 - p) * math.log2(1.0 - p))

def information_bits(free_w, total_w, alive_weights, prior=(1, 1)):
    """Information gained so far: prior entropy minus current posterior entropy, under the
    session's DECLARED prior over the protected binary variable (default uniform (1,1)).
    protected_window_bits: the protected window's binary free/busy variable (prior entropy
    H2(p0); max H2(p0)). calendar_bits: calendar identity (prior entropy 3 + H2(p0): eight
    calendars per bit value, weighted by the declared prior; reduces to 4 - log2(n) under
    the uniform prior). Computed from world weights, exact for any declared prior. The two
    are distinct: guarded answers can carry calendar information while protected_window_bits
    stays 0 - the guard's invariant until Alice approves."""
    p0 = prior[1] / (prior[0] + prior[1])
    h0 = _h2(p0)
    p = (free_w / total_w) if total_w else p0
    ws = [w for w in alive_weights if w]
    tw = sum(ws)
    hid = -sum((w / tw) * math.log2(w / tw) for w in ws) if tw else 0.0
    return {'protected_window_bits': round(h0 - _h2(p), 3),
            'calendar_bits': round(3.0 + h0 - hid, 3),
            'protected_window_prior_bits': round(h0, 3),
            'calendar_prior_bits': round(3.0 + h0, 3)}

def _h_dist(fs):
    return -sum(f * math.log2(f) for f in fs if f > 0)

def joint_information_bits(prior_fr, post_fr, alive_weights=None, priors=None):
    """Prior entropy minus posterior entropy, exact for the declared product prior.
    protected_joint_bits: the protected pattern (2 free/busy variables, prior entropy
    H2(p0)+H2(p1)). calendar_bits: calendar identity (prior entropy 2+H2(p0)+H2(p1):
    four calendars per protected pattern under uniform other windows, weighted by the
    declared per-window priors). Distinct quantities: guarded answers can carry calendar
    information while protected_joint_bits stays 0 - the guard's invariant until Alice
    approves."""
    h0 = _h_dist([float(f) for f in prior_fr])
    h1 = _h_dist([float(f) for f in post_fr])
    out = {'protected_joint_bits': round(h0 - h1, 3), 'protected_joint_prior_bits': round(h0, 3)}
    if alive_weights is not None and priors is not None:
        ws = [w for w in alive_weights if w]
        tw = sum(ws)
        hid = -sum((w / tw) * math.log2(w / tw) for w in ws) if tw else 0.0
        hcal0 = 2.0 + h0
        out['calendar_bits'] = round(hcal0 - hid, 3)
        out['calendar_prior_bits'] = round(hcal0, 3)
    return out

def joint_records(joint_fr, slots):
    """Display records: pattern bits are listed in protected_windows order, b=busy f=free."""
    k = len(slots)
    out = []
    for p, f in enumerate(joint_fr):
        label = ','.join(('free' if (p >> i) & 1 else 'busy') for i in range(k))
        out.append({'pattern': label, 'fraction': str(f)})
    return out

def joint_unguarded_view(history, bob, slots, actual, priors):
    """Joint distribution over the protected pattern if every question had been answered truthfully."""
    alive = list(range(16))
    for ev in history:
        r = ev['request']
        t = _truthful(r, actual, bob)
        alive = [a for a in alive if _truthful(r, a, bob) == t]
    weights = []
    for a in alive:
        w = 1
        for i, slot in enumerate(slots):
            w *= priors[i][(a >> slot) & 1]
        weights.append(w)
    tot = sum(weights)
    cells = [0] * (1 << len(slots))
    for a, w in zip(alive, weights):
        secret = 0
        for i, slot in enumerate(slots):
            secret |= ((a >> slot) & 1) << i
        cells[secret] += w
    from fractions import Fraction
    joint = tuple(Fraction(c, tot) for c in cells)
    prior_fr = []
    ptot = 1
    for pr in priors:
        ptot *= pr[0] + pr[1]
    for p in range(1 << len(slots)):
        num = 1
        for i in range(len(slots)):
            num *= priors[i][(p >> i) & 1]
        prior_fr.append(Fraction(num, ptot))
    return {'possible_calendars': len(alive), 'joint_support': joint_records(joint, slots), 'alive_calendars': alive,
            'information_bits': joint_information_bits(prior_fr, joint, weights, priors)}

def unguarded_view(history, bob, protected, actual, prior=(1, 1)):
    """Observer's probability for the protected window if every question had been answered truthfully,
    under the declared prior (weights per calendar)."""
    truth = None
    alive = list(range(16))
    # Actual calendar is the session's alice bitset (7) in this synthetic fixture.
    for ev in history:
        r = ev['request']
        t = _truthful(r, actual, bob)
        alive = [a for a in alive if _truthful(r, a, bob) == t]
    weights = [prior[(a >> protected) & 1] for a in alive]
    freew = sum(w for a, w in zip(alive, weights) if (a >> protected) & 1)
    totw = sum(weights)
    return {'possible_calendars': len(alive), 'probability_free': f'{freew}/{totw}', 'alive_calendars': alive,
            'information_bits': information_bits(freew, totw, weights, prior)}

def approved_spoken(req, alice, bob):
    t = _truthful(req, alice, bob)
    if req.startswith('slot') and req[4:].isdigit():
        return f'Alice agreed: she is {"free" if t else "busy"} for {SLOTS[int(req[4:])]}.'
    if req == 'parity':
        return 'Alice agreed: ' + ('she can cover exactly one of the two prep windows.' if t else 'she can cover both or neither of the two prep windows.')
    if req == 'schedule':
        return f'Alice agreed: how about {SLOTS[t]}?' if t is not None else 'Alice agreed, but there is no shared window.'
    return 'Okay.'

MAX_APPROVALS = 8  # pending requests per household - Alice's decision queue
MAX_APPROVAL_RECORDS = 64  # retained records incl. stale, for display

class Session:
    def __init__(self, path, alice=7):
        self.path = Path(path)
        self.alice = alice
        self.approvals = []  # approval records, persisted with the session envelope
        self._restored = False
        self.legacy = False
    def _status(self):
        s = dialog_store.status(self.path)
        if s['initialized'] and not self._restored:
            self._restored = True
            if s['legacy_approval_history']:
                # Session predates approval persistence: its approval history cannot be
                # reconstructed from the persisted state (decisions never wrote to the
                # ledger), so the safe assumption is that the room may know more than the
                # ledger shows. Fail closed: answers stay paused until a new simulation.
                self.legacy = True
            elif s['approvals'] is not None:
                self.approvals = [dict(a) for a in s['approvals']]
                for a in self.approvals:
                    if a['status'] == 'pending': self._refresh(a)
        return s
    def _persist(self):
        records = [{k: a[k] for k in ('id','request','text','status','events_at_request','approved_at_request')} for a in self.approvals]
        s = self._status()
        if s['initialized']:
            dialog_store.persist_approvals(self.path, s['generation'], records, legacy=self.legacy)
    def start(self, window, alice=None, prior=None, windows=None, priors=None):
        if alice is not None:
            self.alice = alice
        if windows is not None:
            slots = [SLOTS.index(x) for x in windows]
            kw = {'priors': tuple(tuple(p) for p in priors)} if priors is not None else {}
            s = dialog_store.initialize(self.path, self.alice, 5, protected_slots=slots, **kw)
        else:
            prot = SLOTS.index(window) if window else 0
            kw = {'prior': tuple(prior)} if prior is not None else {}
            s = dialog_store.initialize(self.path, self.alice, 5, prot, '0', **kw)
        self.approvals = []
        self.legacy = False
        self._restored = True
        return self._view(s)
    def _view(self, s):
        """Operator view. Never returned on the assistant surface."""
        for a in self.approvals:
            self._refresh(a)
        c = s['configuration']
        if 'protected_slots' in c:
            return self._view_joint(s, c)
        out = {'scope': SCOPE, 'events': s['events'], 'remaining_possible_calendars': s['remaining_worlds'],
                'observer_probability_protected_window_free': s['protected_support'],
                'protected_window': SLOTS[c['protected_slot']], 'alice_calendar': c['alice'],
                'without_veil': unguarded_view(s['history'], c['bob'], c['protected_slot'], c['alice'], tuple(c.get('public_prior', [1, 1]))),
                'approvals': [{'approval_id': a['id'], 'request': a['text'], 'status': a['status']} for a in self.approvals],
                'with_veil_alive_calendars': sorted(w.alice for w in restore(s['state'])[0].compatible),
                'pending_previews': self.preview(), 'legacy_approval_history': self.legacy}
        free_s, tot_s = (int(x) for x in s['protected_support'].split('/'))
        prior = tuple(c.get('public_prior', [1, 1]))
        out['prior'] = list(prior)
        weights = [w.weight for w in restore(s['state'])[0].compatible]
        out['with_veil_information_bits'] = information_bits(free_s, tot_s, weights, prior)
        out['after_approved_disclosures'] = self._region([]) if any(a['status'] == 'approved' for a in self.approvals) else None
        return out
    def _view_joint(self, s, c):
        """Operator view for joint protection. Never returned on the assistant surface."""
        for a in self.approvals:
            self._refresh(a)
        ctrl, _ = restore(s['state'])
        slots = c['protected_slots']
        priors = [tuple(p) for p in c['public_priors']]
        joint = ctrl.joint_support(ctrl.compatible)
        out = {'scope': JOINT_SCOPE, 'events': s['events'], 'remaining_possible_calendars': s['remaining_worlds'],
                'protected_windows': [SLOTS[i] for i in slots],
                'joint_support': joint_records(joint, slots),
                'joint_pattern_column_order': [SLOTS[i] for i in slots],
                'alice_calendar': c['alice'],
                'declared_priors': {SLOTS[slot]: list(priors[i]) for i, slot in enumerate(slots)},
                'without_veil': joint_unguarded_view(s['history'], c['bob'], slots, c['alice'], priors),
                'approvals': [{'approval_id': a['id'], 'request': a['text'], 'status': a['status']} for a in self.approvals],
                'with_veil_alive_calendars': sorted(w.alice for w in ctrl.compatible),
                'pending_previews': self.preview(), 'legacy_approval_history': self.legacy}
        out['with_veil_information_bits'] = joint_information_bits(ctrl.baseline, joint, [w.weight for w in ctrl.compatible], priors)
        out['after_approved_disclosures'] = self._region([]) if any(a['status'] == 'approved' for a in self.approvals) else None
        return out

    def _public(self, s, result=None):
        """Assistant view: only what the room hears, plus the count of answered questions."""
        scope = JOINT_SCOPE if 'protected_slots' in s.get('configuration', {}) else SCOPE
        out = {'scope': scope, 'events': s['events']}
        if result:
            ob = result['observation']
            out.update({'spoken': spoken(ob['output']), 'output': 'REFUSED' if ob['output'] == 'NEEDS_PRIVATE_DECISION' else ob['output'],
                        'supported_request': result['intent']['supported']})
            if not result['intent']['supported']:
                out['suggestions'] = result['intent']['suggestions']
        return out
    def _disclosed(self):
        # A legacy session fails closed: whether a disclosure happened is unknowable,
        # so the pause applies as if one did.
        return self.legacy or any(a['status'] == 'approved' for a in self.approvals)
    def ask(self, text):
        s = self._status()
        if s['initialized'] and self._disclosed():
            scope = JOINT_SCOPE if 'protected_slots' in s.get('configuration', {}) else SCOPE
            if self.legacy:
                return {'scope': scope, 'events': s['events'],
                        'spoken': ('I can\'t answer that. This session\'s approval history was not persisted before a '
                                   'restart, so I can\'t rule out that the room knows more than the conversation shows. '
                                   'An operator can start a new simulation, or Alice can approve answers one at a time.'),
                        'output': 'REFUSED', 'supported_request': True, 'legacy_approval_history': True}
            return {'scope': scope, 'events': s['events'], 'spoken': 'I can\'t answer that. Alice approved a disclosure earlier, so I now need her approval for each further answer.',
                    'output': 'REFUSED', 'supported_request': True}
        if not s['initialized']:
            s = dialog_store.initialize(self.path, self.alice, 5, 0, '0')
        r = dialog_store.advance(self.path, text, s['generation'], s['events'])
        return self._public(r, r.get('result'))
    def request_approval(self, text):
        intent = parse_intent(text)
        if not intent['supported']:
            return {'scope': SCOPE, 'spoken': 'I can\'t ask about that.', 'supported_request': False, 'suggestions': intent['suggestions']}
        if len(self.approvals) >= MAX_APPROVAL_RECORDS:
            raise ValueError('approval record limit reached for this household')
        for x in self.approvals:
            self._refresh(x)
        if sum(1 for x in self.approvals if x['status'] == 'pending') >= MAX_APPROVALS:
            raise ValueError('pending approval request limit reached for this household')
        s = self._status()
        if not s['initialized']:
            raise ValueError('start a household first')
        # Opaque id bound to the full household generation: a delayed decide/check from an older
        # household (or an older process) cannot target a new request, and a string id avoids
        # JavaScript safe-integer limits in browser clients.
        a = {'id': s['generation'] + '-' + str(len(self.approvals) + 1),
             'request': intent['intent'], 'text': intent['normalized'], 'status': 'pending',
             'events_at_request': s['events'], 'approved_at_request': sum(1 for x in self.approvals if x['status'] == 'approved')}
        self.approvals.append(a)
        self._persist()
        scope = JOINT_SCOPE if 'protected_slots' in s.get('configuration', {}) else SCOPE
        return {'scope': scope, 'approval_id': a['id'], 'status': 'pending',
                'spoken': 'I\'ve asked Alice. She may say no, and I can\'t tell you what she\'ll decide.'}
    def _find(self, i):
        if type(i) is not str:
            raise ValueError('approval_id must be a string')
        for a in self.approvals:
            if a['id'] == i: return a
        raise ValueError('unknown approval_id')
    def _refresh(self, a):
        """A pending request goes stale once the basis Alice would decide on has moved: the shared
        transcript advanced, or another disclosure was approved after the request was made."""
        if a['status'] != 'pending':
            return
        s = self._status()
        if s['events'] != a['events_at_request'] or sum(1 for x in self.approvals if x['status'] == 'approved') != a['approved_at_request']:
            a['status'] = 'stale'
    def check_approval(self, i):
        a = self._find(i)
        self._refresh(a)
        out = {'scope': SCOPE, 'approval_id': i, 'status': a['status']}
        out['spoken'] = {'pending': 'Still waiting for Alice.', 'declined': 'Alice declined.',
                         'stale': 'That request went stale - the conversation changed after Alice was asked. Ask her again if you still need it.'}.get(a['status']) or approved_spoken(a['request'], self.alice, 5)
        return out
    def decide(self, i, approve):
        a = self._find(i)
        if type(approve) is not bool: raise ValueError('approve must be a boolean')
        self._refresh(a)
        if a['status'] == 'stale': raise ValueError('request is stale: the conversation changed since it was asked; request it again on the current conversation')
        if a['status'] != 'pending': raise ValueError('already decided')
        a['status'] = 'approved' if approve else 'declined'
        self._persist()
        return {'scope': SCOPE, 'approval_id': i, 'status': a['status'], 'simulated': True}
    def preview(self):
        """For each pending request: what the room could infer if Alice approved it, given everything visible so far."""
        s = self._status()
        c, _ = restore(s['state'])
        cfg = s['configuration']; bob = cfg['bob']
        joint_cfg = 'protected_slots' in cfg
        prot = cfg.get('protected_slot')
        prior = tuple(cfg.get('public_prior', [1, 1]))
        def region(extra):
            alive = [w for w in c.compatible]
            for req in [a['request'] for a in self.approvals if a['status'] == 'approved'] + extra:
                t = _truthful(req, self.alice, bob)
                alive = [w for w in alive if _truthful(req, w.alice, bob) == t]
            tot = sum(w.weight for w in alive)
            if joint_cfg:
                cells = [0] * (1 << len(cfg['protected_slots']))
                for w in alive:
                    cells[w.secret] += w.weight
                from fractions import Fraction
                joint = tuple(Fraction(x, tot) for x in cells)
                priors = [tuple(p) for p in cfg['public_priors']]
                ptot = 1
                for pr in priors:
                    ptot *= pr[0] + pr[1]
                prior_fr = []
                for p in range(1 << len(cfg['protected_slots'])):
                    num = 1
                    for i in range(len(cfg['protected_slots'])):
                        num *= priors[i][(p >> i) & 1]
                    prior_fr.append(Fraction(num, ptot))
                return {'possible_calendars': len(alive), 'joint_support': joint_records(joint, cfg['protected_slots']),
                        'alive_calendars': sorted(w.alice for w in alive),
                        'information_bits': joint_information_bits(prior_fr, joint, [w.weight for w in alive], priors)}
            freew = sum(w.weight for w in alive if (w.alice >> prot) & 1)
            return {'possible_calendars': len(alive), 'probability_free': f'{freew}/{tot}', 'alive_calendars': sorted(w.alice for w in alive),
                    'information_bits': information_bits(freew, tot, [w.weight for w in alive], prior)}
        self._region = region
        for a in self.approvals:
            self._refresh(a)
        return [{'approval_id': a['id'], 'request': a['text'], 'if_approved': region([a['request']])}
                for a in self.approvals if a['status'] == 'pending']
    def status(self, operator=False):
        s = self._status()
        if not s['initialized']:
            return {'scope': SCOPE, 'initialized': False}
        return self._view(s) if operator else self._public(s)

def _result(i, v): return {'jsonrpc': '2.0', 'id': i, 'result': v}
def _error(i, code, msg): return {'jsonrpc': '2.0', 'id': i, 'error': {'code': code, 'message': msg}}

def call_tool(session, name, args, surface='assistant'):
    if name not in [t['name'] for t in tools_for(surface)]:
        return None
    try:
        if name == 'veil_start_household':
            w = args.get('protected_window')
            if w is not None and w not in SLOTS: raise ValueError('protected_window must be one of ' + ', '.join(SLOTS))
            ac = args.get('alice_calendar')
            if 'alice_calendar' in args and (type(ac) is not int or not 0 <= ac <= 15):
                raise ValueError('alice_calendar must be an integer 0-15')
            pr = args.get('prior')
            if 'prior' in args and (type(pr) is not list or len(pr) != 2
                                    or any(type(x) is not int or not 1 <= x <= 9 for x in pr)):
                raise ValueError('prior must be [busy, free] integer weights 1-9')
            ws = args.get('protected_windows')
            prs = args.get('priors')
            if w is not None and ws is not None: raise ValueError('declare either protected_window or protected_windows, not both')
            if ws is not None:
                if type(ws) is not list or not 1 <= len(ws) <= 2 or any(type(x) is not str or x not in SLOTS for x in ws) or len(set(ws)) != len(ws):
                    raise ValueError('protected_windows must name 1 or 2 distinct windows from ' + ', '.join(SLOTS))
                if 'prior' in args: raise ValueError('use priors (one pair per window) with protected_windows')
                if prs is not None and (type(prs) is not list or len(prs) != len(ws)
                                        or any(type(p) is not list or len(p) != 2 or any(type(x) is not int or not 1 <= x <= 9 for x in p) for p in prs)):
                    raise ValueError('priors must be one [busy, free] pair of integer weights 1-9 per protected window')
            elif prs is not None:
                raise ValueError('priors requires protected_windows')
            oiu = args.get('only_if_uninitialized', False)
            if type(oiu) is not bool: raise ValueError('only_if_uninitialized must be a boolean')
            if set(args) - {'protected_window', 'alice_calendar', 'prior', 'protected_windows', 'priors', 'only_if_uninitialized'}: raise ValueError('unexpected argument')
            if oiu:
                cur = dialog_store.status(session.path)
                if cur['initialized']:
                    joint = 'protected_slots' in cur.get('configuration', {})
                    return {'content': [{'type': 'text', 'text': 'Household already running; not reset.'}],
                            'structuredContent': {'scope': JOINT_SCOPE if joint else SCOPE,
                                                  'already_running': True, 'events': cur['events']}, 'isError': False}
            data = session.start(w, ac, pr, windows=ws, priors=prs)
        elif name == 'veil_ask':
            if set(args) != {'text'} or not isinstance(args['text'], str): raise ValueError('text (string) is required')
            data = session.ask(args['text'])
        elif name == 'veil_status':
            data = session.status()
        elif name == 'veil_request_approval':
            if set(args) != {'text'} or not isinstance(args['text'], str): raise ValueError('text (string) is required')
            if not dialog_store.status(session.path)['initialized']: raise ValueError('no household session yet')
            data = session.request_approval(args['text'])
        elif name == 'veil_check_approval':
            if set(args) != {'approval_id'}: raise ValueError('approval_id is required')
            data = session.check_approval(args['approval_id'])
        elif name == 'veil_decide_approval':
            if set(args) != {'approval_id', 'approve'}: raise ValueError('approval_id and approve are required')
            data = session.decide(args['approval_id'], args['approve'])
        elif name == 'veil_operator_view':
            data = session.status(operator=True)
        else:
            return None
    except (ValueError, TypeError, KeyError, OSError) as e:
        return {'content': [{'type': 'text', 'text': f'Tool error: {e}'}], 'isError': True}
    text = data.get('spoken') or json.dumps(data)
    return {'content': [{'type': 'text', 'text': text}], 'structuredContent': data, 'isError': False}

def handle(msg, session, surface='assistant'):
    """Return a response dict, or None for notifications/responses (HTTP 202)."""
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0':
        return _error(None, -32600, 'Invalid Request')
    method, i = msg.get('method'), msg.get('id')
    if method is None:
        return None  # a client response; nothing to do
    if i is None:
        return None  # notification (e.g. notifications/initialized)
    if method == 'initialize':
        want = (msg.get('params') or {}).get('protocolVersion')
        ver = want if want in SUPPORTED else PROTOCOL
        return _result(i, {'protocolVersion': ver, 'capabilities': {'tools': {'listChanged': False}},
                           'serverInfo': INFO,
                           'instructions': 'Veil answers household calendar questions without letting the whole conversation reveal one protected window. ' + SCOPE})
    if method == 'ping':
        return _result(i, {})
    if method == 'tools/list':
        return _result(i, {'tools': tools_for(surface)})
    if method == 'tools/call':
        p = msg.get('params') or {}
        r = call_tool(session, p.get('name'), p.get('arguments') or {}, surface)
        return _error(i, -32602, f'Unknown tool: {p.get("name")}') if r is None else _result(i, r)
    return _error(i, -32601, f'Method not found: {method}')
