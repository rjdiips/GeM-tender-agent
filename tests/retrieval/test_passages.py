import httpx

from gem_agent.cache.docs import DocCache
from gem_agent.retrieval.chunks import make_chunks
from gem_agent.retrieval.passages import find_passages
from tests.agents.helpers import bid_files


def files(tmp_path, *extra):
    cache = DocCache(tmp_path)
    return cache, bid_files(cache, 1, [
        ("bid.pdf", "bid_document", ["Bid details: item category Custom Bid for Services", "Buyer ATC"]),
        ("rfp.pdf", "attachment", ["Introduction to the bank", "Scope of work: implement a Loan Origination System",
                                   "Penalty for LOSS of data", "Eligibility: turnover 50 crore"]),
        ("gtc.pdf", "gtc", ["General terms and conditions"]),
        *extra,
    ])


def test_chunks_are_labelled_by_file_and_page_without_gtc(tmp_path):
    cache, bf = files(tmp_path)
    chunks = make_chunks(bf, cache)
    assert [(c.label, c.page) for c in chunks][:3] == [("F1", 1), ("F1", 2), ("F2", 1)]
    assert all(c.filename != "gtc.pdf" for c in chunks)


def test_long_page_is_split_into_windows(tmp_path):
    cache, bf = files(tmp_path, ("boq.xlsx", "attachment", ["row " * 2000]))
    chunks = [c for c in make_chunks(bf, cache, max_chars=3000) if c.filename == "boq.xlsx"]
    assert len(chunks) >= 3 and all(len(c.text) <= 3000 for c in chunks) and {c.page for c in chunks} == {1}


def test_word_search_finds_the_topic_page(tmp_path):
    cache, bf = files(tmp_path)
    top = find_passages(make_chunks(bf, cache), query="loan origination system", terms=["LOS", "Loan Origination System"],
                        top_k=2)
    assert (top[0].filename, top[0].page) == ("rfp.pdf", 2)


def test_acronym_does_not_match_loss(tmp_path):
    cache, bf = files(tmp_path)
    top = find_passages(make_chunks(bf, cache), query="", terms=["LOS"], top_k=5)
    assert all("LOSS" not in c.text for c in top)


def test_no_match_gives_nothing_without_semantic_search(tmp_path):
    cache, bf = files(tmp_path)
    assert find_passages(make_chunks(bf, cache), query="satellite imagery", terms=["satellite"], top_k=5) == []


class _Down:
    """A remote model server that refuses every call."""

    def encode(self, texts):
        raise httpx.ConnectError("server is down")

    def score(self, query, texts):
        raise httpx.ConnectError("server is down")


def test_dead_remote_models_leave_word_search_working(tmp_path):
    cache, bf = files(tmp_path)
    top = find_passages(make_chunks(bf, cache), query="loan origination system",
                        terms=["LOS", "Loan Origination System"], top_k=2, embedder=_Down(), reranker=_Down())
    assert (top[0].filename, top[0].page) == ("rfp.pdf", 2)
