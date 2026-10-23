"""Independent observer: no engine imports, policy code or safety-helper calls.
Receives only complete observed transcript traces, world facts, integer prior
weights. Groups identical visible prefixes; independently counts posterior mass.
"""
from fractions import Fraction


def audit(world_records, traces, budget):
    total = sum(record['weight'] for record in world_records)
    initial = Fraction(sum(r['weight'] for r in world_records if r['secret']), total)
    by_id = {r['key']: r for r in world_records}
    max_delta = Fraction(0)
    violations = []
    prefix_cells = []
    nsteps = len(next(iter(traces.values())))
    for depth in range(1, nsteps + 1):
        groups = {}
        for key, events in traces.items():
            # Include policy identity, request, refusal, approval/error-like outputs.
            prefix = tuple((e['request'], e['policy'], e['output'])
                           for e in events[:depth])
            groups.setdefault(prefix, []).append(key)
        prefix_cells.append(len(groups))
        for prefix, keys in groups.items():
            denominator = sum(by_id[k]['weight'] for k in keys)
            numerator = sum(by_id[k]['weight'] for k in keys if by_id[k]['secret'])
            posterior = Fraction(numerator, denominator)
            delta = abs(posterior - initial)
            max_delta = max(max_delta, delta)
            if delta > budget:
                violations.append({'depth': depth, 'size': len(keys),
                                   'posterior': str(posterior), 'prefix': prefix})
    invalid_calendar_answers = 0
    for key,events in traces.items():
        record=by_id[key]
        for event in events:
            output=event['output']
            if output.startswith('ALICE_SLOT'):
                valid=False
                for slot in range(4):
                    expected='ALICE_SLOT'+str(slot)+':'+str((record['alice']>>slot)&1)
                    if output==expected:valid=True
                if not valid:invalid_calendar_answers+=1
            elif output.startswith('PARITY:'):
                expected=((record['alice']>>0)^(record['alice']>>1))&1
                if output!='PARITY:'+str(expected):invalid_calendar_answers+=1
    invalid_meetings = []
    completed = 0
    slot_sum = 0
    for key, events in traces.items():
        meetings = [e['output'] for e in events if e['output'].startswith('MEETING:')]
        if meetings:
            completed += 1
            r = by_id[key]
            slots = []
            invalid = False
            for meeting in meetings:
                value = meeting.split(':',1)[1]
                if value not in ('0','1','2','3'):
                    invalid = True
                    slots.append(None)
                    continue
                slot = int(value)
                slots.append(slot)
                if not ((r['alice'] >> slot) & 1 and (r['bob'] >> slot) & 1):
                    invalid = True
            # Completion is a trace metric. Truthfulness checks EVERY emitted
            # meeting, not merely a later corrected answer in the same trace.
            if slots[-1] is not None: slot_sum += slots[-1]
            if invalid: invalid_meetings.append(key)
    return {'worlds': len(traces), 'completed': completed,
            'completion_rate': completed / len(traces),
            'mean_selected_slot': slot_sum / completed if completed else None,
            'max_posterior_shift': str(max_delta),
            'prefix_violation_cells': len(violations),
            'invalid_meetings': len(invalid_meetings),
            'invalid_calendar_answers': invalid_calendar_answers,
            'prefix_cell_counts': prefix_cells, 'violations': violations}


def audit_audiences(world_records, traces, budget, audience_field):
    """Observer knows its own calendar before receiving the first message."""
    groups = {}
    for record in world_records:
        groups.setdefault(record[audience_field], []).append(record)
    results = []
    for audience, records in sorted(groups.items()):
        ids = {r['key'] for r in records}
        result = audit(records, {k: v for k, v in traces.items() if k in ids}, budget)
        result['audience'] = audience
        results.append(result)
    return results
