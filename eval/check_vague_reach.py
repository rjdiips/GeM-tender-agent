"""Does the Searcher's candidate pool reach the target bids hidden behind vague titles? (index only, no model calls)"""
import sys

sys.path.insert(0, ".")
from gem_agent.agents.searcher import SearchSettings, is_vague_title
from gem_agent.config import get_settings
from gem_agent.index.db import BidIndex
from gem_agent.index.query import IndexQuery, search_index
from gem_agent.retrieval.embed import BgeM3Embedder

TARGETS = {10115629: "Agri Digital Lending Suite", 10115998: "MSME Digital Lending Suite", 10115089: "Fraud Risk Mgmt (EFRMS)",
           10142481: "Financial Risk and Compliance"}
TOPICS = {
    "LOS": (("Loan Origination System: software banks use to capture, assess, approve and disburse loan applications; "
             "Loan Origination System; LOS; loan origination; digital lending platform"), ["Loan Origination System", "LOS", "loan origination", "digital lending"]),
    "AML": (("Anti-Money Laundering (AML) software: transaction monitoring, KYC and suspicious transaction reporting for banks; "
             "anti money laundering; AML; transaction monitoring"), ["anti money laundering", "AML", "transaction monitoring", "KYC"]),
}
s, pool_size = get_settings(), SearchSettings().semantic_pool
index, emb = BidIndex(s.index_path), BgeM3Embedder(device="cpu")
for name, (text, terms) in TOPICS.items():
    words = {h.bid.b_id for h in search_index(index, IndexQuery(terms=terms, limit=300), emb)}
    hits = search_index(index, IndexQuery(text=text, limit=2000), emb)
    rank = {h.bid.b_id: i for i, h in enumerate(hits)}
    vague_in_pool = sum(1 for h in hits[:pool_size] if is_vague_title(h.bid.title))
    print(f"{name}: {len(words)} word hits; vague titles in the top {pool_size} by meaning: {vague_in_pool}")
    for b_id, label in TARGETS.items():
        r = rank.get(b_id)
        print(f"   {label:32} meaning rank {r if r is not None else '>2000'}  {'IN POOL' if (r is not None and r < pool_size) or b_id in words else 'missed'}")
