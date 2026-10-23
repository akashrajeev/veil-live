"""One private four-window conversation, persisted before a response is released."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
from threading import Lock
from calendar_input import validate_snapshot, instant, private_write, SCOPE
from joint_engine import JointController, joint_worlds

class CalendarGuard:
    def __init__(self, snapshot, state_path, *, observer_mask=15, protected=(0,1), now=None):
        self.snapshot=validate_snapshot(snapshot)
        self.path=Path(state_path)
        self.clock=now or (lambda: datetime.now(timezone.utc))
        self.lock=Lock()
        self.worlds=joint_worlds(observer_mask,protected,[(1,1)] * len(protected))
        self.key=next(w.key for w in self.worlds if w.alice==snapshot['free_mask'])
        # Bind ALL inputs and model choices; refuse to reset silently after a calendar refresh.
        self.binding=hashlib.sha256(json.dumps({'snapshot':snapshot,'observer':observer_mask,
            'protected':protected,'model':'joint-uniform-v1'},sort_keys=True).encode()).hexdigest()
        self.controller=JointController(self.worlds,k=len(protected))
        if self.path.exists():
            if self.path.stat().st_size > 100000: raise ValueError('oversized ledger')
            saved=json.loads(self.path.read_text())
            if set(saved) != {'binding','ledger'} or saved['binding'] != self.binding:
                raise ValueError('snapshot/model changed: do not discard an existing shared transcript')
            self.controller=JointController.load(self.worlds,saved['ledger'],
                factory=lambda worlds,budget:JointController(worlds,budget=budget,k=len(protected)))
            if self.key not in {w.key for w in self.controller.compatible}:
                raise ValueError('ledger inconsistent with snapshot')
        else:
            self._save(self.controller)

    def _save(self, controller):
        private_write(self.path,{'binding':self.binding,'ledger':controller.dump()})

    def _fresh(self):
        if self.snapshot['source'] != 'live-google-freebusy': return True
        now=self.clock(); asof=instant(self.snapshot['as_of'])
        return asof <= now <= asof+timedelta(minutes=30) and now < instant(self.snapshot['coverage']['end'])

    def ask(self, request):
        with self.lock:
            if request not in ('slot0','slot1','slot2','slot3','parity','schedule'):
                raise ValueError('unsupported structured request')
            if not self._fresh():
                return {'answer':'PAUSED','reason':'Real snapshot is stale. A new approved read and transcript review are needed.', 'scope':SCOPE}
            if len(self.controller.transcript) >= 128:
                return {'answer':'PAUSED','reason':'Conversation limit reached; do not reset to evade prior answers.', 'scope':SCOPE}
            # Advance a copy; a persistence failure never releases an unrecorded answer.
            candidate=JointController.load(self.worlds,self.controller.dump(),
                factory=lambda worlds,budget:JointController(worlds,budget=budget,k=self.controller.k))
            event=candidate.step(self.key,request)
            self._save(candidate)
            self.controller=candidate
            return {'answer':event['output'],'events':len(candidate.transcript),'scope':SCOPE}

    def public_windows(self):
        # These slots were selected publicly before reading availability. No free/busy bits.
        return {'windows':[{'index':i,**window} for i,window in enumerate(self.snapshot['windows'])],
            'source':self.snapshot['source'],'scope':SCOPE,
            'snapshot_mode':'one-time read, not continuous live sync',
            'observer_availability':'public test assumption; no second person calendar was read'}
