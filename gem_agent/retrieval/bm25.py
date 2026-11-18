"""Word search with whole-word and acronym-case rules (spec 5.3).

- Text is split into words, so "LOS" never matches inside "LOST", "CLOSE" or "LOSS".
- An acronym (one all-capitals word of 4 letters or fewer) matches only in its written case.
- Everything else, phrases included, matches in any case, as whole consecutive words.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"\w+")
K1, B = 1.2, 0.75


def tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text)


@dataclass(frozen=True)
class Term:
    words: tuple[str, ...]
    case_sensitive: bool

    @classmethod
    def parse(cls, text: str) -> Term:
        words = tuple(tokenize(text))
        acronym = len(words) == 1 and len(words[0]) <= 4 and words[0].isupper()
        return cls(words if acronym else tuple(w.lower() for w in words), acronym)


def count_matches(term: Term, tokens: list[str]) -> int:
    if not term.words:
        return 0
    toks = tokens if term.case_sensitive else [t.lower() for t in tokens]
    n = len(term.words)
    return sum(1 for i in range(len(toks) - n + 1) if tuple(toks[i : i + n]) == term.words)


def lexical_scores(texts: list[str], terms: list[str]) -> list[float]:
    """BM25 over the given texts, one score per text; terms are parsed with the rules above."""
    parsed = [Term.parse(t) for t in terms if tokenize(t)]
    if not parsed or not texts:
        return [0.0] * len(texts)
    docs = [tokenize(t) for t in texts]
    avg_len = sum(len(d) for d in docs) / len(docs) or 1.0
    counts = [[count_matches(term, d) for term in parsed] for d in docs]
    n = len(docs)
    idf = []
    for j in range(len(parsed)):
        df = sum(1 for row in counts if row[j])
        idf.append(math.log(1 + (n - df + 0.5) / (df + 0.5)))
    scores = []
    for d, row in zip(docs, counts, strict=True):
        norm = K1 * (1 - B + B * len(d) / avg_len)
        scores.append(sum(idf[j] * tf * (K1 + 1) / (tf + norm) for j, tf in enumerate(row) if tf))
    return scores
