from gem_agent.retrieval.bm25 import Term, count_matches, lexical_scores, tokenize


def test_acronym_matches_only_whole_word_in_written_case():
    los = Term.parse("LOS")
    assert los.case_sensitive
    assert count_matches(los, tokenize("Supply of LOS software")) == 1
    for text in ("LOST and found", "CLOSE the bid", "loss of service", "Los Angeles"):
        assert count_matches(los, tokenize(text)) == 0, text


def test_long_word_matches_any_case_but_whole_word_only():
    term = Term.parse("laundering")
    assert not term.case_sensitive
    assert count_matches(term, tokenize("Anti Money LAUNDERING solution")) == 1
    assert count_matches(term, tokenize("launderings")) == 0


def test_phrase_matches_any_case_as_consecutive_words():
    term = Term.parse("loan origination system")
    assert count_matches(term, tokenize("Digital Loan Origination System (LOS) for RRBs")) == 1
    assert count_matches(term, tokenize("loan system origination")) == 0


def test_lowercase_short_word_is_not_treated_as_acronym():
    assert not Term.parse("aml").case_sensitive


def test_scores_rank_rarer_terms_higher():
    texts = ["Supply of LOS software", "Supply of chairs", "Supply of tables"]
    scores = lexical_scores(texts, ["LOS", "supply"])
    assert scores[0] > scores[1] > 0 and scores[1] == scores[2]


def test_no_terms_scores_zero():
    assert lexical_scores(["a", "b"], []) == [0.0, 0.0]
