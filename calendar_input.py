"""Minimize free/busy input to four public windows. No account/OAuth code here."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

SCOPE = ('Real-input lab; counterfactual domain and independent uniform prior remain declared, '
         'not learned from real people. No real-world privacy guarantee. No Alexa+ or LLM connection.')

def instant(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('expected an offset-aware timestamp')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('invalid timestamp') from None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('timestamp needs an explicit UTC offset')
    return dt.astimezone(timezone.utc)

def period(value):
    if not isinstance(value, dict) or set(value) != {'start', 'end'}:
        raise ValueError('interval must contain only start and end')
    start, end = instant(value['start']), instant(value['end'])
    if start >= end:
        raise ValueError('interval must have positive duration')
    return start, end

def windows_checked(windows, coverage):
    if not isinstance(windows, list) or len(windows) != 4:
        raise ValueError('exactly four public windows required')
    intervals = [period(w) for w in windows]
    lo, hi = period(coverage)
    if any(a < lo or b > hi for a, b in intervals):
        raise ValueError('window outside verified free/busy coverage')
    if any(intervals[i][1] > intervals[i+1][0] for i in range(3)):
        raise ValueError('public windows must be sorted and non-overlapping')
    return intervals

def minimize(payload, windows, *, source, as_of):
    if source not in ('synthetic-fixture', 'live-google-freebusy', 'real-public-ics'):
        raise ValueError('explicit supported provenance required')
    instant(as_of)
    if not isinstance(payload, dict) or not {'start','end','calendars'} <= set(payload):
        raise ValueError('missing free/busy coverage or calendars')
    calendars = payload['calendars']
    if not isinstance(calendars, dict) or len(calendars) != 1:
        raise ValueError('exactly one approved primary calendar required')
    cal = next(iter(calendars.values()))
    if not isinstance(cal, dict) or cal.get('errors'):
        raise ValueError('free/busy error: availability unknown, not free')
    if 'busy' not in cal or not isinstance(cal['busy'], list):
        raise ValueError('missing busy list: availability unknown, not free')
    if len(cal['busy']) > 10000:
        raise ValueError('too many busy periods')
    coverage = {'start':payload['start'], 'end':payload['end']}
    intervals = windows_checked(windows, coverage)
    busy = [period({k:v for k,v in p.items() if k in ('start','end')}) for p in cal['busy']]
    # Half-open intervals: ending at window start or starting at window end is not overlap.
    mask = sum((1 << i) for i,(a,b) in enumerate(intervals)
               if not any(c < b and d > a for c,d in busy))
    result = {'schema':1, 'source':source, 'as_of':as_of, 'coverage':coverage,
              'windows':windows, 'free_mask':mask}
    validate_snapshot(result)
    return result

def validate_snapshot(value):
    if not isinstance(value, dict) or set(value) != {'schema','source','as_of','coverage','windows','free_mask'}:
        raise ValueError('invalid minimized snapshot schema')
    if type(value['schema']) is not int or value['schema'] != 1:
        raise ValueError('unsupported snapshot version')
    if value['source'] not in ('synthetic-fixture','live-google-freebusy','real-public-ics'):
        raise ValueError('unknown provenance')
    instant(value['as_of'])
    windows_checked(value['windows'],value['coverage'])
    if type(value['free_mask']) is not int or not 0 <= value['free_mask'] <= 15:
        raise ValueError('free mask must be 0..15')
    return value

def private_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(value,f,sort_keys=True);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if temp.exists():temp.unlink()

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True);p.add_argument('--windows',required=True)
    p.add_argument('--source',required=True,choices=['synthetic-fixture','live-google-freebusy','real-public-ics'])
    p.add_argument('--as-of',required=True);p.add_argument('--output',required=True)
    a=p.parse_args()
    result=minimize(json.loads(Path(a.input).read_text()),json.loads(Path(a.windows).read_text()),source=a.source,as_of=a.as_of)
    private_write(a.output,result)
    print('Minimized snapshot written privately; no event metadata retained.')
