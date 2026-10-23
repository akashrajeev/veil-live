"""Small explicit language gateway, not general natural-language understanding."""
import re
ALIASES={
 'parity':('can alice cover exactly one of the two prep windows','exactly one prep window','one prep window'),
 'slot0':('is alice free for early prep','early prep availability'),
 'slot2':('is alice free for dinner','dinner availability'),
 'slot3':('is alice free for cleanup','cleanup availability'),
 'slot1':('is alice free for late prep','is alice available for late prep','late prep availability'),
 'schedule':('find a shared window','find a time for us','schedule dinner','find a shared dinner planning window'),
}
SUGGESTIONS=[phrases[0] for phrases in ALIASES.values()]

def parse(text):
    if not isinstance(text,str) or len(text)>240:raise ValueError('intent must be text up to 240 characters')
    normalized=' '.join(re.sub(r'[^a-z0-9\s]',' ',text.casefold()).split())
    if normalized.startswith('alexa '):normalized=normalized[6:]
    for intent,phrases in ALIASES.items():
        if normalized in phrases:return {'intent':intent,'supported':True,'normalized':normalized}
    return {'intent':'unsupported','supported':False,'suggestions':SUGGESTIONS,
            'message':'This simulator supports only the listed availability, prep and scheduling requests. No calendar answer was disclosed.'}
