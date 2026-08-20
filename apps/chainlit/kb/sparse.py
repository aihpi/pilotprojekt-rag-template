"""Sparse (lexical) vectors for hybrid retrieval.

Dense embeddings fumble exactly what a technical German corpus is made of:
standard numbers ("BSI-Standard 200-2"), compound nouns and proper names. A
sparse term-frequency vector stored alongside the dense one lets Qdrant fuse a
lexical ranking with the semantic one (see ``retrieval.hybrid``).

Term frequencies only. Qdrant's ``Modifier.IDF`` applies the IDF weighting
server-side, so there is no corpus statistic to compute here, persist, or keep
in sync with the collection.
"""

from __future__ import annotations

import re
import unicodedata
import zlib
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qdrant_client.models import SparseVector

#: Name of the sparse vector in Qdrant. The dense vector stays unnamed so that a
#: non-hybrid collection is unchanged from before hybrid existed.
SPARSE_VECTOR = "text"

#: Version of the *stored* lexical format. The tokenizer and the token ids below
#: decide which indices land in every point, so a collection is only searchable
#: by code that tokenizes the way it was ingested. Recorded in the ingest
#: sentinel and compared on every run, exactly as ``embed_model`` is.
#:
#: **Bump this whenever ``_TOKEN``, the normalisation in ``tokenize``, or
#: ``_token_id`` changes.** Forgetting to means a query computes different ids
#: than the corpus was written with: zero lexical matches, no error, hybrid
#: quietly degrading to dense-only. That already happened twice during
#: development (a crc32 mask dropped, then NFKC and hyphen folding added).
SPARSE_FORMAT = 1

# Hyphenated compounds stay whole: splitting "BSI-Standard" into "bsi" + "standard"
# throws away the exact term the lexical leg exists to match. `[^\W_]` is
# "word character but not underscore", so Greek and umlauts come along (IFN-γ,
# Verfügbarkeit) while snake_case identifiers still split.
_TOKEN = re.compile(r"[^\W_]+(?:-[^\W_]+)*")

# Typography that a hyphen means. NFKC leaves these alone, but a PDF writing
# "BSI‑Standard" with a non-breaking hyphen has to match a query typed with "-",
# and "200–2" with an en dash has to match "200-2". The em dash is deliberately
# absent: it separates clauses rather than joining a compound.
_HYPHENS = str.maketrans({c: "-" for c in "‐‑‒–−"})
# Soft hyphen is a line-break hint inside a word; it has no textual meaning.
_SOFT_HYPHEN = "­"


def tokenize(text: str) -> list[str]:
    """Lowercased terms, hyphenated compounds kept as one token.

    NFKC first, because PDF-extracted German routinely differs from typed German
    in ways that are invisible on screen but split tokens: a decomposed umlaut
    (``u`` + U+0301) is two tokens because a combining mark is not a word
    character, and soft hyphens (U+00AD) or non-breaking hyphens (U+2011) from
    line breaks either split a word or fail to match a typed ``-``. Ingest and
    query must agree on the token, or the lexical vector matches nothing.
    """
    normalized = (
        unicodedata.normalize("NFKC", text)
        .replace(_SOFT_HYPHEN, "")
        .translate(_HYPHENS)
    )
    return [m.group(0).lower() for m in _TOKEN.finditer(normalized)]


def _token_id(token: str) -> int:
    # ponytail: crc32 hashing instead of a persisted vocabulary — nothing to
    # store, nothing to migrate, and ids stay stable across processes. Swap in a
    # real vocabulary if the corpus ever reaches millions of distinct terms and
    # collisions start costing precision. Qdrant's sparse indices are u32, which
    # is exactly crc32's range — no masking, masking would double collisions.
    return zlib.crc32(token.encode("utf-8"))


#: Function words stripped from the **query** only, never from stored chunks.
#: Scores sum across query terms, so a question like "Was ist X und wofür wurde es
#: verwendet?" lets a chunk matching seven common words outrank the one chunk that
#: actually contains X. IDF lowers each stopword's weight but does not stop seven of
#: them adding up — and because RRF treats both legs as equally authoritative, the
#: noisy leg's rank-0 hit scores 0.5 and displaces good dense results. Measured on
#: natural-language questions wrapping 30 rare identifiers: dense 76%, hybrid without
#: this 36%, hybrid with it 80%.
#:
#: German and English, because those are the languages the template ships prompts for.
#: Deliberately short and not configurable yet: a longer list risks dropping a term
#: that matters in a domain corpus. Extend it here if a corpus needs it.
_STOPWORDS = frozenset("""
was ist sind war waren und oder wofür wozu wie warum weshalb wer wen wem wo wann
welche welcher welches der die das den dem des ein eine einen einem eines
von zu mit für auf in im am an bei aus nach vor über unter durch um gegen ohne
er sie es ich du wir ihr man sich sein seine ihre ihren
hat habe haben hatte wird werden wurde wurden kann können soll sollen muss müssen
nicht auch noch nur als dass ob mehr sehr beim zur zum
what is are was were and or for how why who whom where when which
the a an of to with in on at by from that this these those
be been being has have had will would can could should must
not also only as more very about into
""".split())


def strip_stopwords(tokens: list[str]) -> list[str]:
    """Query-side only. Falls back to the input when everything would be dropped —
    a question made entirely of function words should still search for something
    rather than send an empty vector that matches nothing."""
    kept = [t for t in tokens if t not in _STOPWORDS]
    return kept or tokens


def sparse_query_vector(text: str) -> "SparseVector":
    """Sparse vector for a QUERY: content words only.

    Separate from :func:`sparse_vector` on purpose. Stored chunks keep every term —
    stripping them there would change the stored index and need a re-ingest, and a
    chunk's own function words are harmless because the query never asks for them.
    """
    from qdrant_client.models import SparseVector

    counts = Counter(_token_id(t) for t in strip_stopwords(tokenize(text)))
    indices = sorted(counts)
    return SparseVector(indices=indices, values=[float(counts[i]) for i in indices])


def sparse_vector(text: str) -> "SparseVector":
    """Term-frequency sparse vector; Qdrant weights it by IDF at query time.

    An empty or purely punctuational text yields an empty vector, which simply
    matches nothing rather than erroring.
    """
    from qdrant_client.models import SparseVector

    counts = Counter(_token_id(token) for token in tokenize(text))
    indices = sorted(counts)
    return SparseVector(indices=indices, values=[float(counts[i]) for i in indices])
