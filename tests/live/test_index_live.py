"""The index pipeline against the real site and the real local models. Run with: pytest -m live

3 listing requests; loads bge-m3 and the reranker on CPU (~20 s).
"""
import pytest

from gem_agent.index.db import BidIndex
from gem_agent.index.query import IndexQuery, search_index
from gem_agent.index.sync import NEXT_PAGE, embed_pending, full_load
from gem_agent.retrieval.embed import BgeM3Embedder
from gem_agent.retrieval.rerank import BgeReranker
from gem_agent.tools.http_client import BidplusSession, ClientPool

pytestmark = pytest.mark.live


async def test_partial_load_embed_and_query(tmp_path):
    index = BidIndex(tmp_path / "bids.sqlite")
    pool = ClientPool.shared()
    try:
        report = await full_load(index, BidplusSession(pool), max_pages=3)
        assert report.pages == 3 and not report.complete and report.new >= 25
        assert index.state(NEXT_PAGE) == "4"
        assert report.num_found > 20000

        embedder = BgeM3Embedder(device="cpu")
        assert embed_pending(index, embedder) == report.new
        ids, matrix = index.vectors(embedder.model_name)
        assert matrix.shape == (report.new, 1024)

        some = index.get(int(ids[0]))
        hits = search_index(index, IndexQuery(text=some.title, limit=5), embedder, BgeReranker(device="cpu"))
        assert hits and hits[0].bid.title == some.title
    finally:
        await pool.aclose()
        index.close()
