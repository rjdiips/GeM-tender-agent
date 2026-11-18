"""Pick ~80 open bids for the labelled evaluation set (spec 13). Read-only on the index; writes eval/candidates.json."""
import json
import random
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")
from gem_agent.config import get_settings
from gem_agent.index.db import BidIndex
from gem_agent.index.query import IndexQuery, search_index
from gem_agent.retrieval.embed import BgeM3Embedder

random.seed(13)
s = get_settings()
index = BidIndex(s.index_path)
embedder = BgeM3Embedder(device="cpu")
db = sqlite3.connect(f"file:{s.index_path}?mode=ro", uri=True)
rows = db.execute("select b_id, title, ministry, department from bids where state='open'").fetchall()
picked: dict[int, dict] = {}

def add(b_id, group, why):
    if b_id not in picked:
        r = next(x for x in rows if x[0] == b_id)
        picked[b_id] = {"b_id": b_id, "title": r[1], "buyer": f"{r[2] or ''} / {r[3] or ''}", "group": group, "why": why}

def semantic(text, terms, group, n):
    for h in search_index(index, IndexQuery(text=text, terms=terms, limit=n), embedder):
        add(h.bid.b_id, group, "closest by meaning / words")

semantic("Anti-Money Laundering (AML) software: transaction monitoring, KYC, suspicious transaction reporting for banks",
         ["anti money laundering", "AML", "transaction monitoring", "suspicious transaction", "KYC", "fraud risk"], "aml_likely", 12)
semantic("Loan Origination System: software banks use to capture, assess, approve and disburse loan applications; digital lending",
         ["loan origination", "LOS", "digital lending", "lending suite", "loan processing"], "los_likely", 12)
for b_id in (r[0] for r in rows if re.search(r"digital lending|fraud risk management", r[1], re.IGNORECASE)):
    add(b_id, "survey_find", "vague-title survey: target kind")

traps = [(r"\bLOS\b", "LOS in another sense"), (r"\bloss\b", "loss"), (r"line of sight", "Line of Sight"),
         (r"level of service", "Level of Service"), (r"\bAML\b", "AML acronym"), (r"leukaemia|leukemia|haematology", "medical AML"),
         (r"\bCBS\b", "CBS acronym"), (r"\blost\b", "LOST")]
for pattern, why in traps:
    hits = [r[0] for r in rows if re.search(pattern, r[1], re.IGNORECASE if pattern.islower() else 0)]
    for b_id in random.sample(hits, min(3, len(hits))):
        add(b_id, "trap", why)

related = [(r"loan management|\bLMS\b", "loan management only"), (r"core banking", "core banking"),
           (r"\bKYC\b|video kyc", "KYC only"), (r"treasury|ALM\b|asset liability", "treasury / ALM")]
for pattern, why in related:
    hits = [r[0] for r in rows if re.search(pattern, r[1], re.IGNORECASE if pattern.islower() else 0)]
    for b_id in random.sample(hits, min(3, len(hits))):
        add(b_id, "related", why)

fin = [r[0] for r in rows if (r[2] == "Ministry of Finance" or re.search(r"bank", r[3] or "", re.IGNORECASE)) and r[0] not in picked]
for b_id in random.sample(fin, 14):
    add(b_id, "negative_fin", "random financial-sector buyer")
for b_id in random.sample([r[0] for r in rows if r[0] not in picked], 10):
    add(b_id, "negative", "random")

Path("eval/candidates.json").write_text(json.dumps(list(picked.values()), indent=1))
from collections import Counter

print(len(picked), Counter(p["group"] for p in picked.values()))
for p in picked.values():
    print(f"{p['group']:12} {p['why'][:24]:24} {p['title'][:80]}")
