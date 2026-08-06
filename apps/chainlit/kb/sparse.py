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
import zlib
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qdrant_client.models import SparseVector

#: Name of the sparse vector in Qdrant. The dense vector stays unnamed so that a
#: non-hybrid collection is unchanged from before hybrid existed.
SPARSE_VECTOR = "text"

# Hyphenated compounds stay whole: splitting "BSI-Standard" into "bsi" + "standard"
# throws away the exact term the lexical leg exists to match. `[^\W_]` is
# "word character but not underscore", so Greek and umlauts come along (IFN-γ,
# Verfügbarkeit) while snake_case identifiers still split.
_TOKEN = re.compile(r"[^\W_]+(?:-[^\W_]+)*")


def tokenize(text: str) -> list[str]:
    """Lowercased terms, hyphenated compounds kept as one token."""
    return [m.group(0).lower() for m in _TOKEN.finditer(text)]


def _token_id(token: str) -> int:
    # ponytail: crc32 hashing instead of a persisted vocabulary — nothing to
    # store, nothing to migrate, and ids stay stable across processes. Swap in a
    # real vocabulary if the corpus ever reaches millions of distinct terms and
    # collisions start costing precision.
    return zlib.crc32(token.encode("utf-8")) & 0x7FFFFFFF


def sparse_vector(text: str) -> "SparseVector":
    """Term-frequency sparse vector; Qdrant weights it by IDF at query time.

    An empty or purely punctuational text yields an empty vector, which simply
    matches nothing rather than erroring.
    """
    from qdrant_client.models import SparseVector

    counts = Counter(_token_id(token) for token in tokenize(text))
    indices = sorted(counts)
    return SparseVector(indices=indices, values=[float(counts[i]) for i in indices])
