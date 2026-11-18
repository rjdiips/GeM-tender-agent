"""Pick the bids for the labelled evaluation set (spec 13) and write a text bundle for each.

Writes eval/labelling/candidates.json and eval/labelling/bids/<b_id>.txt (bid document, up to 2 RFPs, scope
sections). Read-only on the index and cache. Run: python eval/labelling/select.py
"""
import json
import random
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")
from gem_agent.cache.docs import DocCache
from gem_agent.config import get_settings
from gem_agent.index.cards import _rfp_sections
from gem_agent.index.db import BidIndex
from gem_agent.index.query import IndexQuery, search_index
from gem_agent.retrieval.bm25 import Term, count_matches, tokenize
from gem_agent.retrieval.embed import BgeM3Embedder
from gem_agent.tools.gem.titles import is_vague_title

random.seed(13)
OUT = Path("eval/labelling"); (OUT / "bids").mkdir(parents=True, exist_ok=True)
s = get_settings(); index = BidIndex(s.index_path); cache = DocCache(s.cache_dir)
db = sqlite3.connect(f"file:{s.index_path}?mode=ro", uri=True)
rows = {r[0]: r for r in db.execute("select b_id, bid_number, title, department, card from bids where state='open'")}
picked: dict[int, dict] = {}

def add(b_id, topic, why):
    if b_id in rows and b_id not in picked:
        r = rows[b_id]
        picked[b_id] = {"b_id": b_id, "bid_number": r[1], "title": r[2], "buyer": r[3] or "", "card": r[4],
                        "topic": topic, "why": why}

# keyword hits over cached documents (whole-word; acronyms in their written case)
KEY = {"AML": [Term.parse(t) for t in ("AML", "anti money laundering", "transaction monitoring", "suspicious transaction")],
       "LOS": [Term.parse(t) for t in ("LOS", "loan origination", "digital lending", "lending suite")]}
hits: dict[int, dict[str, set[int]]] = {}
for d in Path(s.cache_dir / "docs").iterdir():
    if not d.name.isdigit() or int(d.name) not in rows: continue
    f = cache.load(int(d.name)); got = {k: set() for k in KEY}
    for rec in f.files:
        if rec.role == "gtc" or rec.status != "ok": continue
        for p in cache.load_text(rec):
            toks = tokenize(p.text)
            for k, ts in KEY.items():
                if any(count_matches(t, toks) for t in ts): got[k].add(p.page)
    if any(got.values()): hits[int(d.name)] = got

for b_id, got in sorted(hits.items(), key=lambda kv: -len(kv[1]["AML"] | kv[1]["LOS"])):
    for topic in ("AML", "LOS"):
        if got[topic]: add(b_id, topic, f"documents mention {topic} ({len(got[topic])} pages)")

embedder = BgeM3Embedder(device="cpu")
TOPICS = {"LOS": ("Loan Origination System software for banks; loan origination; digital lending platform",
                  ["Loan Origination System", "LOS", "loan origination", "digital lending"]),
          "AML": ("Anti-Money Laundering software for banks; anti money laundering; transaction monitoring; KYC",
                  ["anti money laundering", "AML", "transaction monitoring", "KYC"])}
for topic, (text, terms) in TOPICS.items():
    for h in search_index(index, IndexQuery(text=text, terms=terms, limit=25), embedder):
        add(h.bid.b_id, topic, f"closest by meaning or title words for {topic}")

TRAPS = [(r"\bLOS\b", "look-alike LOS"), (r"\bloss\b", "loss"), (r"line of sight", "Line of Sight"),
         (r"level of service", "Level of Service"), (r"haematology|hematology|leukaemia|leukemia", "medical AML"),
         (r"\bCBS\b", "acronym CBS"), (r"\bAML\b", "acronym AML")]
for pattern, why in TRAPS:
    want = "LOS" if "LOS" in why or "Line of Sight" in why or "Level of Service" in why else "AML"
    cands = [b for b, r in rows.items() if re.search(pattern, r[2], 0 if pattern.isupper() else re.IGNORECASE) and b not in picked]
    for b_id in random.sample(cands, min(3, len(cands))): add(b_id, want, why)

for pattern, why in [(r"loan management|\bLMS\b", "loan management only"), (r"core banking", "core banking"),
                     (r"video kyc|video-based", "KYC only")]:
    topic = "LOS" if "loan" in why or "core" in why else "AML"
    cands = [b for b, r in rows.items() if re.search(pattern, r[2], re.IGNORECASE) and b not in picked]
    for b_id in random.sample(cands, min(3, len(cands))): add(b_id, topic, why)

for topic in ("AML", "LOS"):
    cands = [b for b, r in rows.items() if b not in picked and is_vague_title(r[2])]
    for b_id in random.sample(cands, min(4, len(cands))): add(b_id, topic, "random vague title")
    cands = [b for b, r in rows.items() if b not in picked]
    for b_id in random.sample(cands, min(6, len(cands))): add(b_id, topic, "random open bid")


class CacheOnly:
    def __init__(self, cache): self.cache = cache


def bundle(b_id: int) -> str | None:
    f = cache.load(b_id)
    if not f: return None
    docs = [r for r in f.files if r.role in ("bid_document", "parent_document") and r.status == "ok"]
    body = "\n".join(p.text for r in docs for p in cache.load_text(r)[:3])[:6000]
    rfp = _rfp_sections(CacheOnly(cache), f, body, 2) if docs else ""
    return f"# {rows[b_id][1]} — {rows[b_id][2]}\nBuyer department: {rows[b_id][3]}\nCard: {rows[b_id][4]}\n\n" \
           f"## Bid document\n{body}\n\n## RFP\n{rfp}\n"


missing = []
for b_id in list(picked):
    text = bundle(b_id)
    if text is None: missing.append(b_id); del picked[b_id]; continue
    (OUT / "bids" / f"{b_id}.txt").write_text(text)

(OUT / "candidates.json").write_text(json.dumps(
    [{**v, "text_file": f"eval/labelling/bids/{b}.txt"} for b, v in picked.items()], indent=1))
from collections import Counter

print(len(picked), Counter(v["why"].split(" (")[0] for v in picked.values()))
print("skipped (no cached documents):", missing)
