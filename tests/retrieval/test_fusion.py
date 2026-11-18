import pytest

from gem_agent.retrieval.fusion import reciprocal_rank_fusion


def test_item_ranked_well_in_both_lists_wins():
    fused = reciprocal_rank_fusion([[1, 2, 3], [2, 3, 1]], k=60)
    assert next(iter(fused)) == 2


def test_item_in_one_list_only_still_scores():
    fused = reciprocal_rank_fusion([[1], [2]], k=60)
    assert fused[1] == pytest.approx(1 / 61) and fused[2] == pytest.approx(1 / 61)
