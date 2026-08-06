"""Turn a thumbs-down comment into a countable failure category.

The comment box is Chainlit's own — the app never prompts for one — so this is
only about classifying free text that already exists. It runs here rather than in
the app for the same reason scoring does: this service owns every LLM call whose
job is to judge the app, and a thumbs-click should not wait on one.

Categories deliberately map to where you would go looking:

* ``hallucination``  — the answer, so check the prompt and faithfulness scores
* ``wrong_document`` — retrieval, so check chunking and the embedding model
* ``incomplete``     — coverage, so check top_k and chunk size
* ``irrelevant``     — the question was misread, so check query handling
"""

from __future__ import annotations

import logging
from typing import Literal

from eval_app.metrics import litellm_model_name

logger = logging.getLogger(__name__)

FailureCategory = Literal["hallucination", "wrong_document", "incomplete", "irrelevant"]
CATEGORIES: tuple[FailureCategory, ...] = (
    "hallucination",
    "wrong_document",
    "incomplete",
    "irrelevant",
)

_PROMPT = """Classify this complaint about an AI assistant's answer into exactly one category.

hallucination: the answer claimed something the source documents do not support
wrong_document: the assistant retrieved or cited the wrong source
incomplete: the answer was right as far as it went but left out key information
irrelevant: the answer did not address the question that was asked

Reply with the category name alone and nothing else. The complaint may be written
in any language.

Complaint: {comment}"""


async def classify(
    comment: str,
    *,
    judge_model: str,
    base_url: str | None = None,
    api_key: str | None = None,
) -> FailureCategory | None:
    """Return one of ``CATEGORIES``, or ``None`` if the model said anything else.

    An unrecognised reply is discarded rather than stored. A category nobody can
    count is worse than a null: it would show up in the dashboard as its own bogus
    bar and quietly split the counts it should have joined.
    """
    if not comment.strip():
        return None

    import litellm

    try:
        response = await litellm.acompletion(
            model=litellm_model_name(judge_model),
            messages=[{"role": "user", "content": _PROMPT.format(comment=comment)}],
            api_base=base_url,
            api_key=api_key,
            temperature=0.0,
        )
        raw = (response.choices[0].message.content or "").lower()
    except Exception as exc:
        logger.warning("evaluation: feedback classification failed (%s)", exc)
        return None

    # Accept a category mentioned exactly once, which tolerates the trailing full
    # stop and the occasional "Category: x". Two mentions means the model hedged
    # ("not hallucination, more incomplete") and we would be guessing which it
    # meant, so that counts as no answer.
    hits = [category for category in CATEGORIES if category in raw]
    if len(hits) != 1:
        logger.warning("evaluation: unusable failure category %r", raw[:80])
        return None
    return hits[0]
