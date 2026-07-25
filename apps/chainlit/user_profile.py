"""User profile extraction and personalization utilities.

This module handles:
- Extracting user interest topics from chat history via LLM
- Computing topic embeddings for retrieval filtering
- Managing user keywords (auto-extracted and manual)
- Filtering retrieval results based on user profile
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chat_history import (
    get_user_message_count,
    get_user_message_history,
    get_user_profile,
    upsert_user_profile,
)
from llm import chat, embed
from settings import (
    CHAT_DB_PATH,
    PERSONALIZATION_ENABLED,
    PROFILE_MIN_MESSAGES,
    PROFILE_RELEVANCE_THRESHOLD,
    PROFILE_TOPIC_LIMIT,
)


def _kw_key(value: str) -> str:
    """Normalization key for case-insensitive keyword dedup.

    Folds Unicode hyphen variants (NON-BREAKING HYPHEN U+2011, EN DASH U+2013)
    to the regular hyphen-minus U+002D before lowercasing, so that
    'Multi‑Word' and 'Multi-Word' compare equal.
    """
    return value.lower().replace("‑", "-").replace("–", "-")


@dataclass
class UserProfile:
    """Represents extracted user profile for personalization."""

    user_id: str
    topics: list[str] = field(default_factory=list)
    topic_embeddings: list[list[float]] = field(default_factory=list)
    excluded_bausteine: list[str] = field(default_factory=list)
    message_count: int = 0
    keywords: list[dict[str, Any]] = field(default_factory=list)
    custom_prompt: str | None = None
    personalization_enabled: bool = True

    def has_sufficient_history(self) -> bool:
        """Check if user has enough history for personalization."""
        return self.message_count >= PROFILE_MIN_MESSAGES

    def to_context_string(self) -> str:
        """Format topics as a string for prompt injection."""
        if not self.topics:
            return ""
        return ", ".join(self.topics)

    def active_keywords(self) -> list[dict[str, Any]]:
        """Return only active keywords."""
        return [k for k in self.keywords if k.get("active", True)]

    def active_keyword_values(self) -> list[str]:
        """Return values of active keywords, deduplicated case-insensitively."""
        seen: set[str] = set()
        out: list[str] = []
        for k in self.active_keywords():
            v = k.get("value")
            if not v:
                continue
            lc = _kw_key(v)
            if lc in seen:
                continue
            seen.add(lc)
            out.append(v)
        return out


# Domain-neutral by design: the topics are derived from the user's own questions,
# so this works for any corpus without naming one.
TOPIC_EXTRACTION_PROMPT = """Analysiere die folgenden Nutzerfragen an einen Wissens-Assistenten und extrahiere die Hauptthemen, für die sich der Nutzer interessiert.

Nutzerfragen:
{messages}

Aufgabe:
1. Identifiziere die wichtigsten Themen/Bereiche aus den Fragen
2. Gib maximal {topic_limit} Themen zurück
3. Verwende prägnante, spezifische Begriffe aus dem Kontext der Fragen selbst

Antworte NUR mit einem JSON-Array der Themen, ohne weitere Erklärungen.
Beispiel: ["Messaufbau", "Kalibrierung", "Auswertungsverfahren"]

Themen:"""


BALANCE_DETERMINATION_PROMPT = """DEPRECATED: LLM-based balance determination has been removed.
Personalization is now controlled deterministically by the user via settings."""


async def extract_user_topics(
    user_id: str,
    db_path: Path | None = None,
    force: bool = False,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract interest topics from user's chat history using LLM.

    Args:
        user_id: The user identifier
        db_path: Path to chat database (defaults to CHAT_DB_PATH)
        force: Force re-extraction even if profile exists

    Returns:
        Tuple of (topic strings, keyword objects with {value, active, source})
    """
    db = db_path or CHAT_DB_PATH

    # Check if we already have a recent profile
    if not force:
        existing = get_user_profile(db, user_id)
        if existing and existing.get("topics"):
            # Build keyword objects from existing topics if keywords not yet populated
            keywords = existing.get("keywords", [])
            if not keywords:
                keywords = [{"value": t, "active": True, "source": "auto"} for t in existing["topics"]]
            return existing["topics"], keywords

    # Get user's recent messages
    messages = get_user_message_history(db, user_id, limit=100, role_filter="user")
    if not messages:
        return [], []

    # Format messages for prompt
    message_texts = "\n".join(
        f"- {msg['content'][:500]}" for msg in messages[:50]  # Limit context size
    )

    prompt = TOPIC_EXTRACTION_PROMPT.format(
        messages=message_texts,
        topic_limit=PROFILE_TOPIC_LIMIT,
    )

    try:
        response = await chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
        )
        content = response.choices[0].message.content or ""  # type: ignore[union-attr]
        content = content.strip()

        # Parse JSON response
        # Handle potential markdown code blocks
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        topics = json.loads(content)
        if isinstance(topics, list):
            topic_strings = [str(t).strip() for t in topics if t][:PROFILE_TOPIC_LIMIT]
            keywords = [{"value": t, "active": True, "source": "auto"} for t in topic_strings]
            return topic_strings, keywords
    except (json.JSONDecodeError, IndexError, KeyError, AttributeError) as e:
        print(f"[WARN] topic_extraction_failed: {e}")

    return [], []


async def update_user_profile(
    user_id: str,
    db_path: Path | None = None,
    force: bool = False,
) -> UserProfile:
    """Extract topics and update user profile in database.

    Args:
        user_id: The user identifier
        db_path: Path to chat database
        force: Force re-extraction

    Returns:
        Updated UserProfile instance
    """
    db = db_path or CHAT_DB_PATH

    # Get current message count
    message_count = get_user_message_count(db, user_id)

    # Check existing profile
    existing = get_user_profile(db, user_id)
    if existing and not force:
        # Only update if message count increased significantly (10+ new messages)
        existing_count = existing.get("message_count", 0)
        threshold = existing_count + 10
        if message_count < threshold:
            return UserProfile(
                user_id=user_id,
                topics=existing.get("topics", []),
                topic_embeddings=existing.get("topic_embeddings", []),
                excluded_bausteine=existing.get("excluded_bausteine", []),
                message_count=existing_count,
                keywords=existing.get("keywords", []),
                custom_prompt=existing.get("custom_prompt"),
                personalization_enabled=existing.get("personalization_enabled", True),
            )

    # Extract topics via LLM
    topics, auto_keywords = await extract_user_topics(user_id, db, force=True)

    # Merge: keep manual keywords, replace auto keywords (manual takes precedence on duplicate value)
    existing_keywords = existing.get("keywords", []) if existing else []
    manual_keywords = [k for k in existing_keywords if k.get("source") == "manual"]
    manual_values = {_kw_key(k["value"]) for k in manual_keywords if k.get("value")}
    deduped_auto = [k for k in auto_keywords if k.get("value") and _kw_key(k["value"]) not in manual_values]
    merged_keywords = manual_keywords + deduped_auto

    # Embed all active keyword values for similarity matching
    active_values = [k["value"] for k in merged_keywords if k.get("active", True) and k.get("value")]
    topic_embeddings: list[list[float]] = []
    if active_values:
        try:
            topic_embeddings = await embed(active_values)
        except (ConnectionError, TimeoutError, ValueError, RuntimeError) as e:
            print(f"[WARN] topic_embedding_failed: {e}")

    # Store in database
    upsert_user_profile(
        db,
        user_id,
        topics=topics,
        topic_embeddings=topic_embeddings,
        message_count=message_count,
        keywords=merged_keywords,
    )

    return UserProfile(
        user_id=user_id,
        topics=topics,
        topic_embeddings=topic_embeddings,
        excluded_bausteine=existing.get("excluded_bausteine", []) if existing else [],
        message_count=message_count,
        keywords=merged_keywords,
        custom_prompt=existing.get("custom_prompt") if existing else None,
        personalization_enabled=existing.get("personalization_enabled", True) if existing else True,
    )


async def load_user_profile(
    user_id: str,
    db_path: Path | None = None,
) -> UserProfile | None:
    """Load user profile from database.

    Args:
        user_id: The user identifier
        db_path: Path to chat database

    Returns:
        UserProfile instance or None if not found
    """
    db = db_path or CHAT_DB_PATH
    data = get_user_profile(db, user_id)

    if not data:
        return None

    topics = data.get("topics", [])
    keywords = data.get("keywords", [])

    # Migrate: if keywords column is empty but legacy topics exist,
    # synthesise keyword objects so the Tags widget and personalization
    # prompt have data immediately (without an LLM round-trip).
    if not keywords and topics:
        keywords = [{"value": t, "active": True, "source": "auto"} for t in topics]

    return UserProfile(
        user_id=user_id,
        topics=topics,
        topic_embeddings=data.get("topic_embeddings", []),
        excluded_bausteine=data.get("excluded_bausteine", []),
        message_count=data.get("message_count", 0),
        keywords=keywords,
        custom_prompt=data.get("custom_prompt"),
        personalization_enabled=data.get("personalization_enabled", True),
    )


async def determine_balance(
    query: str,
    user_profile: UserProfile | None,
    user_role: str = "",
) -> float:
    """Determine personalization balance for a query.

    Now deterministic: returns 1.0 (no personalization / no chunk filtering).
    Keywords are only used for the 'Bezug zu Ihren Interessen' section
    in the system prompt, not for retrieval filtering.
    """
    return 1.0


async def regenerate_keywords(
    user_id: str,
    db_path: Path | None = None,
) -> UserProfile:
    """Regenerate auto-extracted keywords from chat history.

    Overwrites only source='auto' keywords; manual keywords are preserved.
    """
    db = db_path or CHAT_DB_PATH
    existing = get_user_profile(db, user_id)
    existing_keywords = existing.get("keywords", []) if existing else []
    manual_keywords = [k for k in existing_keywords if k.get("source") == "manual"]

    # Extract fresh topics from history
    topics, auto_keywords = await extract_user_topics(user_id, db, force=True)
    manual_values = {_kw_key(k["value"]) for k in manual_keywords if k.get("value")}
    deduped_auto = [k for k in auto_keywords if k.get("value") and _kw_key(k["value"]) not in manual_values]
    merged_keywords = manual_keywords + deduped_auto

    # Embed all active keyword values
    active_values = [k["value"] for k in merged_keywords if k.get("active", True) and k.get("value")]
    topic_embeddings: list[list[float]] = []
    if active_values:
        try:
            topic_embeddings = await embed(active_values)
        except (ConnectionError, TimeoutError, ValueError, RuntimeError) as e:
            print(f"[WARN] keyword_embedding_failed: {e}")

    message_count = get_user_message_count(db, user_id)
    upsert_user_profile(
        db, user_id,
        topics=topics,
        topic_embeddings=topic_embeddings,
        keywords=merged_keywords,
        message_count=message_count,
    )

    return UserProfile(
        user_id=user_id,
        topics=topics,
        topic_embeddings=topic_embeddings,
        excluded_bausteine=existing.get("excluded_bausteine", []) if existing else [],
        message_count=message_count,
        keywords=merged_keywords,
        custom_prompt=existing.get("custom_prompt") if existing else None,
        personalization_enabled=existing.get("personalization_enabled", True) if existing else True,
    )


async def update_keyword_embeddings(user_profile: UserProfile, db_path: Path | None = None) -> UserProfile:
    """Re-embed active keywords and persist. Call after keyword changes."""
    db = db_path or CHAT_DB_PATH
    active_values = user_profile.active_keyword_values()
    topic_embeddings: list[list[float]] = []
    if active_values:
        try:
            topic_embeddings = await embed(active_values)
        except (ConnectionError, TimeoutError, ValueError, RuntimeError) as e:
            print(f"[WARN] keyword_embedding_failed: {e}")

    user_profile.topic_embeddings = topic_embeddings
    user_profile.topics = active_values

    upsert_user_profile(
        db,
        user_profile.user_id,
        topics=active_values,
        topic_embeddings=topic_embeddings,
        keywords=user_profile.keywords,
    )
    return user_profile


def compute_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


def compute_profile_relevance(
    chunk_embedding: list[float],
    user_profile: UserProfile,
) -> float:
    """Compute relevance score of a chunk to user's interest profile.

    Args:
        chunk_embedding: Embedding vector of the retrieved chunk
        user_profile: User's profile with topic embeddings

    Returns:
        Relevance score between 0.0 and 1.0
    """
    if not user_profile.topic_embeddings:
        return 0.5  # Neutral score if no profile embeddings

    # Compute max similarity to any topic
    max_sim = 0.0
    for topic_emb in user_profile.topic_embeddings:
        sim = compute_similarity(chunk_embedding, topic_emb)
        max_sim = max(max_sim, sim)

    return max_sim


def filter_by_profile_relevance(
    results: list[dict[str, Any]],
    user_profile: UserProfile,
    threshold: float = PROFILE_RELEVANCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Filter retrieval results by relevance to user profile.

    Removes chunks that are semantically distant from user's interests.

    Args:
        results: List of retrieval results with embeddings
        user_profile: User's profile
        threshold: Minimum relevance score to keep (default from settings)

    Returns:
        Filtered list of results
    """
    if not user_profile.topic_embeddings:
        return results

    filtered = []
    for result in results:
        embedding = result.get("embedding")
        if not embedding:
            # Keep results without embeddings
            filtered.append(result)
            continue

        relevance = compute_profile_relevance(embedding, user_profile)
        if relevance >= threshold:
            result["profile_relevance"] = relevance
            filtered.append(result)

    return filtered


def blend_retrieval_scores(
    base_results: list[dict[str, Any]],
    user_profile: UserProfile | None,
    balance: float = 0.5,
) -> list[dict[str, Any]]:
    """Blend base retrieval scores with profile relevance scores.

    Args:
        base_results: Results from standard retrieval with 'score' field
        user_profile: User's profile for computing relevance
        balance: Weight for base score (1.0 = only base, 0.0 = only personalized)

    Returns:
        Results with blended scores, re-sorted
    """
    if not user_profile or not user_profile.topic_embeddings or balance >= 1.0:
        return base_results

    for result in base_results:
        base_score = result.get("score", 0.0)
        embedding = result.get("embedding")

        if embedding:
            profile_relevance = compute_profile_relevance(embedding, user_profile)
        else:
            profile_relevance = 0.5  # Neutral

        # Blend scores
        blended_score = balance * base_score + (1 - balance) * profile_relevance
        result["original_score"] = base_score
        result["profile_relevance"] = profile_relevance
        result["score"] = blended_score

    # Re-sort by blended score
    return sorted(base_results, key=lambda x: x.get("score", 0), reverse=True)
