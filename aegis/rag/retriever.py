"""Grounded retrieval over the rulebook, with citations and no free generation.

WHAT THIS DELIBERATELY DOES NOT DO.

It does not ask a language model to write an answer. In a product whose entire premise is
evidentiary rigour -- where the output may end up attached to a representment filed with a
card network, and where an Indian court would need it to satisfy Section 63 BSA -- a fluent
paraphrase that might be subtly wrong is a liability, not a feature. A model that
confidently states the wrong VAMP threshold costs a merchant real money.

So an answer here is always assembled from two things that are both verifiable:

  1. RETRIEVED PASSAGES from a hand-curated rulebook corpus, quoted and cited.
  2. THE CASE'S OWN COMPUTED FACTS, taken from the deterministic rules engine.

The retrieval is hybrid. Lexical scoring (TF-IDF over character and word n-grams) catches
exact rule terminology -- "10.4", "1.5%", "Section 63" -- where a dense embedding often
blurs the specific number that matters. Semantic scoring, when sentence-transformers is
available, catches the paraphrase a real analyst types ("why did this one fail?" against a
passage about Main anchors). Neither alone is adequate: the first misses intent, the second
misses digits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from aegis.rag.corpus import CORPUS, Passage

# Weighting between the two retrieval signals. Lexical is given the larger share because in
# this domain the discriminating token is frequently a literal number or statute reference.
LEXICAL_WEIGHT = 0.6
SEMANTIC_WEIGHT = 0.4

# Below this a passage is not shown at all. Returning the least-bad match to a question the
# corpus cannot answer is how a retrieval system starts inventing rules.
MIN_SCORE = 0.06


@dataclass
class Hit:
    passage: Passage
    score: float
    lexical: float
    semantic: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.passage.id,
            "topic": self.passage.topic,
            "text": self.passage.text,
            "source": self.passage.source,
            "url": self.passage.url,
            "score": round(self.score, 4),
            "lexical": round(self.lexical, 4),
            "semantic": round(self.semantic, 4),
        }


class RuleRetriever:
    def __init__(self, use_semantic: bool = True):
        self.passages = CORPUS
        self._docs = [f"{p.topic}. {p.text}" for p in self.passages]

        # Word n-grams for phrasing; the vectoriser also sees raw numerals, which is what
        # makes "1.5%" or "10.4" retrievable at all.
        self._vec = TfidfVectorizer(
            ngram_range=(1, 2), sublinear_tf=True, min_df=1,
            token_pattern=r"(?u)\b[\w.%()-]+\b", lowercase=True,
        )
        self._mat = self._vec.fit_transform(self._docs)

        self._encoder = None
        self._emb = None
        if use_semantic:
            try:
                from sentence_transformers import SentenceTransformer

                self._encoder = SentenceTransformer("all-MiniLM-L6-v2")
                self._emb = self._encoder.encode(
                    self._docs, normalize_embeddings=True, show_progress_bar=False
                )
            except Exception:
                # Falling back to lexical-only is a degradation worth reporting, not hiding;
                # `backend` below surfaces which mode actually ran.
                self._encoder = None
                self._emb = None

    @property
    def backend(self) -> str:
        return "hybrid (tf-idf + MiniLM embeddings)" if self._encoder else "lexical (tf-idf only)"

    def search(self, query: str, k: int = 4) -> list[Hit]:
        q = (query or "").strip()
        if not q:
            return []

        lex = (self._vec.transform([q]) @ self._mat.T).toarray().ravel()
        lex = lex / (lex.max() + 1e-9) if lex.max() > 0 else lex

        if self._encoder is not None and self._emb is not None:
            qe = self._encoder.encode([q], normalize_embeddings=True, show_progress_bar=False)
            sem = (self._emb @ qe.T).ravel()
            sem = np.clip(sem, 0, None)
            sem = sem / (sem.max() + 1e-9) if sem.max() > 0 else sem
            combined = LEXICAL_WEIGHT * lex + SEMANTIC_WEIGHT * sem
        else:
            sem = np.zeros_like(lex)
            combined = lex

        order = np.argsort(-combined)[:k]
        return [
            Hit(self.passages[i], float(combined[i]), float(lex[i]), float(sem[i]))
            for i in order if combined[i] >= MIN_SCORE
        ]


# --- grounding -----------------------------------------------------------------------

# Facts the rules engine has already computed, rendered as sentences. These are asserted
# only when the corresponding field is actually present -- an answer never claims a fact
# about a case that was not computed for it.
def case_facts(case: dict[str, Any] | None) -> list[str]:
    if not case:
        return []
    out: list[str] = []
    q = case.get("qualification") or {}

    if "qualified" in q:
        if q["qualified"]:
            els = ", ".join(q.get("matched_element_labels", [])) or "the required elements"
            out.append(f"This dispute QUALIFIES for CE 3.0. Matched elements: {els}.")
        else:
            out.append("This dispute does NOT qualify for CE 3.0.")
            gaps = q.get("blocking_gaps") or []
            if gaps:
                out.append(f"Blocking reason: {gaps[0].get('detail', gaps[0].get('code', ''))}")
            unlock = q.get("unlock_element_labels") or []
            if unlock:
                out.append(
                    "A single additional element would flip it: " + " or ".join(unlock) + "."
                )
    if q.get("naive_rule_disagrees"):
        out.append(
            "A naive 'any two of four' implementation would wrongly call this dispute "
            "winnable — the matched elements are Secondary only, with no Main anchor."
        )
    if case.get("win_prob") is not None and case.get("break_even") is not None:
        out.append(
            f"Modelled win probability {case['win_prob']:.1%} against a break-even of "
            f"{min(case['break_even'], 1):.1%} for this disputed amount, so contesting is "
            f"{'worth it' if case.get('worth_fighting') else 'not worth it'} on expected value."
        )
    ev = case.get("evidence")
    if ev:
        out.append(
            f"Customer-submitted evidence was assessed {ev.get('label')} "
            f"(driver: {ev.get('driver')}), tamper probability {ev.get('tamper_score', 0):.1%}."
        )
        for f in (ev.get("flags") or [])[:2]:
            out.append(f"Forensic finding — {f.get('code')}: {f.get('detail')}")
    rec = case.get("recommendation")
    if rec:
        out.append(f"Recommended action: {rec.get('action')} — {rec.get('rationale')}")
    return out


def answer(query: str, retriever: RuleRetriever, case: dict[str, Any] | None = None,
           k: int = 4) -> dict[str, Any]:
    """Assemble a grounded, cited answer. No text is generated -- only selected and quoted."""
    hits = retriever.search(query, k=k)
    facts = case_facts(case)

    if not hits and not facts:
        return {
            "query": query,
            "answered": False,
            "message": (
                "Nothing in the rulebook corpus matches that closely enough to answer "
                "safely, and no case context was supplied. Rephrasing with the specific rule "
                "or reason code usually helps."
            ),
            "citations": [],
            "case_facts": [],
            "backend": retriever.backend,
        }

    return {
        "query": query,
        "answered": True,
        "case_facts": facts,
        "citations": [h.as_dict() for h in hits],
        "backend": retriever.backend,
        "grounding_note": (
            "Every statement above is either a value computed by the deterministic rules "
            "engine for this case, or a verbatim passage from the cited rulebook source. "
            "Nothing is paraphrased by a language model, because a fluent-but-wrong "
            "statement about a network rule costs the merchant money."
        ),
    }


SUGGESTED_QUESTIONS = [
    "Why doesn't this dispute qualify for CE 3.0?",
    "What is a Main element and why does it matter?",
    "What is the VAMP threshold and what does it cost me?",
    "Does winning a representment lower my VAMP ratio?",
    "How do I know a submitted receipt is fake?",
    "What certificate do I need for Indian courts?",
    "Should I fight this dispute or refund it?",
    "What should I start capturing today?",
]
