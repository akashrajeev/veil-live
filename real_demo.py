"""Fold of the real-input lab into the local demo: pinned PUBLIC real schedule feeds only.

Inputs carry busy/free timing derived from public feeds (no titles, places or
people). The joint guard is the same CalendarGuard the MCP SDK server wraps;
the counterfactual model and prior remain declared assumptions. No real-world
privacy guarantee. Synthetic scenarios stay on the main pages.
"""
import json
from pathlib import Path

from calendar_input import minimize
from calendar_guard import CalendarGuard

ROOT = Path(__file__).parent
CASES_DIR = ROOT / 'examples' / 'public-cases'
AS_OF = '2026-10-16T21:30:00+05:30'
ALLOWED_REQUESTS = ('slot0', 'slot1', 'slot2', 'slot3', 'parity', 'schedule')


def cases():
    out = []
    for d in sorted(CASES_DIR.iterdir()):
        meta = d / 'meta.json'
        if meta.exists():
            m = json.loads(meta.read_text())
            out.append({'name': m['name'], 'description': m['description'],
                        'source_url': m['source_url'], 'fetched': m['fetched'],
                        'conversion': m['conversion'],
                        'input': 'real public schedule feed; busy/free timing only, no titles, places or people',
                        'model': 'counterfactual worlds and independent uniform prior remain declared assumptions'})
    return out


def _case_dir(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 80 \
            or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in name):
        raise ValueError('unknown real-data case')
    d = CASES_DIR / name
    if not (d / 'freebusy.json').exists() or not (d / 'windows.json').exists():
        raise ValueError('unknown real-data case')
    return d


def select(state_dir, name):
    """Import the pinned public feed and open its guarded conversation.

    A persisted ledger is reused across restarts; a changed snapshot or model
    refuses to reuse it rather than silently resetting privacy history."""
    d = _case_dir(name)
    payload = json.loads((d / 'freebusy.json').read_text())
    windows = json.loads((d / 'windows.json').read_text())
    snapshot = minimize(payload, windows, source='real-public-ics', as_of=AS_OF)
    return CalendarGuard(snapshot, Path(state_dir) / f'real-{name}.json')
