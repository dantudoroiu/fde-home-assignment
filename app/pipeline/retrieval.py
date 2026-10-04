"""The knowledge base: loading, versioning, and keyword retrieval (BM25).

By default the whole KB goes into the draft prompt (grounding=full_context), so BM25 is only used when
the KB is too large for that (grounding=bm25, or the automatic fallback at startup). BM25 was chosen
over embeddings for that path because it needs no extra service, is explainable ("it matched on
'invoice' and 'proration'"), and is deterministic in tests.
"""

import hashlib
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

from app.models import Article

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be but by can do for from has have hi hello how i if in is it its me my no not of "
    "on or our please so that the their them there this to us was we what when where which who why will "
    "with you your thanks thank".split()
    # Support boilerplate: frequent in tickets, says nothing about the topic. In a 13-article KB these
    # still get a high IDF, so without this list "new" + "customer" was enough to match an article.
    + "new old one need needs want would like just also get got still any some all very really "
    "customer customers help issue issues problem problems question urgent urgently asap today "
    "writes about other".split()
)
_HEADER_RE = re.compile(r"^#\s*(KB-\d+):\s*(.+)$")


def tokenize(text: str) -> list[str]:
    # No stemming on purpose: measured on the sample tickets it added noise without improving recall.
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


def load_articles(kb_dir: Path) -> list[Article]:
    """Each file starts with a header line '# KB-001: Title', followed by the article body."""
    articles = []
    for path in sorted(kb_dir.glob("*.md")):
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        match = _HEADER_RE.match(lines[0]) if lines else None
        if not match:
            raise ValueError(f"{path.name}: first line must look like '# KB-001: Title'")
        articles.append(Article(id=match[1], title=match[2].strip(), body="\n".join(lines[1:]).strip()))
    return articles


class KnowledgeBase:
    """BM25 search with two relevance gates: a minimum score and a minimum number of distinct shared
    terms. Returning nothing is better than returning a wrong article: an empty result makes the
    draft say "we're looking into it" and forces review (no_kb_match), while a wrong article invites
    a confidently wrong reply."""

    def __init__(self, articles: list[Article], min_score: float = 1.0, min_shared_terms: int = 2):
        if not articles:
            raise ValueError("knowledge base is empty")
        self.articles = articles
        self.ids = frozenset(a.id for a in articles)
        self._min_score = min_score
        self._min_shared_terms = min_shared_terms
        # Title tokens are repeated to weight them above body text.
        docs = [tokenize(f"{a.title} {a.title} {a.body}") for a in articles]
        self._doc_terms = [frozenset(d) for d in docs]
        self._index = BM25Okapi(docs)

        content = "\n".join(f"{a.id}\n{a.title}\n{a.body}" for a in sorted(articles, key=lambda a: a.id))
        # Logged with every pipeline run so a quality change can be traced back to a KB edit.
        self.version = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
        # Rough estimate (~4 chars/token) used to decide whether full-context grounding still fits.
        self.estimated_tokens = len(content) // 4

    def get(self, article_ids: list[str]) -> list[Article]:
        by_id = {a.id: a for a in self.articles}
        return [by_id[i] for i in article_ids if i in by_id]

    @classmethod
    def from_dir(cls, kb_dir: Path) -> "KnowledgeBase":
        return cls(load_articles(kb_dir))

    def search(self, query: str, k: int = 3) -> list[Article]:
        tokens = tokenize(query)
        if not tokens:
            return []
        query_terms = set(tokens)
        scores = self._index.get_scores(tokens)
        ranked = sorted(zip(scores, self.articles, self._doc_terms), key=lambda x: x[0], reverse=True)
        return [
            article
            for score, article, terms in ranked[:k]
            if score >= self._min_score and len(query_terms & terms) >= self._min_shared_terms
        ]
