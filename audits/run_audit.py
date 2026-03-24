import json, re, os
from collections import Counter, defaultdict

BASE = r"C:\Users\hp\arabic-base"
WORDS = os.path.join(BASE, "words.js")
OUT = os.path.join(BASE, "audits", "audit_2026-03-24.json")

# helpers
TASHKEEL_RE = re.compile(r"[\u064B-\u0652\u0670\u0640]")
CYR_RE = re.compile(r"[\u0400-\u04FF]")
AR_RE = re.compile(r"[\u0621-\u064A]")

def strip_tashkeel(s):
    return TASHKEEL_RE.sub('', s or '')

def normalize_ar_token(s):
    s = strip_tashkeel(s or '').strip()
    s = re.sub(r"[أإآٱ]", 'ا', s)
    s = re.sub(r"ى", 'ي', s)
    s = re.sub(r"ؤ", 'و', s)
    s = re.sub(r"ئ", 'ي', s)
    s = re.sub(r"ة", 'ه', s)
    s = re.sub(r"\s+", '', s)
    return s

# load
raw = open(WORDS, encoding='utf-8').read()
arr = json.loads(raw.split('=',1)[1].strip().rstrip(';'))

issues = {
    "missing_fields": [],
    "no_example_match": [],
    "ru_not_cyrillic": [],
    "root_len_out": [],
    "pos_unknown": [],
}

allowed_pos = {"اسم","فعل","حرف"}

for i,w in enumerate(arr):
    wid = w.get('w','')
    # missing
    for fld in ['w','en','ru','xa','xr','pos']:
        if not (w.get(fld) or '').strip():
            issues['missing_fields'].append({"idx":i,"w":wid,"field":fld})
            break
    # example contains word
    if wid and w.get('xa'):
        if normalize_ar_token(wid) not in normalize_ar_token(w.get('xa','')):
            issues['no_example_match'].append({"idx":i,"w":wid,"xa":w.get('xa','')})
    # ru cyrillic
    ru = (w.get('ru') or '').strip()
    if ru and not CYR_RE.search(ru):
        issues['ru_not_cyrillic'].append({"idx":i,"w":wid,"ru":ru})
    # root length
    root = strip_tashkeel(w.get('r',''))
    root = re.sub(r"[^\u0621-\u064A]", "", root)
    if root and len(root) not in (3,4):
        issues['root_len_out'].append({"idx":i,"w":wid,"r":w.get('r','')})
    # pos
    pos = (w.get('pos') or '').strip()
    if pos and pos not in allowed_pos and all(k not in pos for k in allowed_pos):
        issues['pos_unknown'].append({"idx":i,"w":wid,"pos":pos})

summary = {
    "total_words": len(arr),
    "missing_fields": len(issues['missing_fields']),
    "no_example_match": len(issues['no_example_match']),
    "ru_not_cyrillic": len(issues['ru_not_cyrillic']),
    "root_len_out": len(issues['root_len_out']),
    "pos_unknown": len(issues['pos_unknown']),
}

report = {
    "summary": summary,
    "samples": {k: v[:25] for k,v in issues.items()},
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print("Audit written:", OUT)
print(summary)
