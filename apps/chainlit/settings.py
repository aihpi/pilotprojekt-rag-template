"""Backward-compatible settings shim.

Historically this module read flat environment variables. It now derives the
same module-level constants from the typed config object (:func:`config.get_config`),
so existing imports across ``app.py``, ``rag_tool.py``, ``llm.py``, etc. keep
working unchanged. New code should prefer ``from config import get_config``.

Operational/deployment values (chat DB, auth, Postgres) stay pure-env here —
they are not part of a RAG *instance* config.
"""

from __future__ import annotations

import os
from pathlib import Path

from config import get_config

BASE_DIR = Path(__file__).resolve().parent

_cfg = get_config()


def _resolve(value: str | None, fallback: Path) -> Path:
    if not value:
        return fallback
    return _cfg.resolve_path(value)


# --- Models / LLM (llm.py) --------------------------------------------------
CHAT_MODEL = _cfg.models.chat_model
FALLBACK_CHAT_MODEL = _cfg.models.fallback_chat_model
EMBED_MODEL = _cfg.models.embed_model
LITELLM_BASE_URL = _cfg.models.litellm_base_url
LITELLM_API_KEY = _cfg.models.litellm_api_key

# --- Vector store (rag_tool.py, ingest*.py) ---------------------------------
QDRANT_URL = _cfg.vector_store.url
QDRANT_API_KEY = _cfg.vector_store.api_key
QDRANT_COLLECTION = _cfg.vector_store.collection

# --- Retrieval --------------------------------------------------------------
TOP_K = _cfg.retrieval.top_k
MAX_TOP_K = _cfg.retrieval.max_top_k
MAX_SOURCE_LINKS = _cfg.retrieval.max_source_links
SCORE_THRESHOLD = _cfg.retrieval.score_threshold

# --- Streaming --------------------------------------------------------------
STREAMING_ENABLED = _cfg.app.streaming_enabled
STREAMING_DOUBLE_PASS = _cfg.app.streaming_double_pass

# --- Chunking (consumed by the ported ingestion pipeline) -------------------
CHUNK_MAX_CHARS = _cfg.chunking.max_chars
CHUNK_OVERLAP = _cfg.chunking.overlap

# --- Prompt / citations / data (app.py, rag_tool.py) ------------------------
# Both paths are optional: a missing system prompt triggers auto-generation
# (see system_prompt_gen), and a missing citation map means no id remapping.
SYSTEM_PROMPT_PATH = _resolve(_cfg.prompt.system_prompt_path, BASE_DIR / "system.md")
STARTER_QUESTIONS = list(_cfg.prompt.starter_questions)
CITATION_MAP_PATH = _resolve(_cfg.citation.map_path, BASE_DIR / "citation_map.json")
SOURCE_PDF_FALLBACK = _cfg.citation.source_pdf_fallback or ""
DATA_RAW_DIR = _resolve(_cfg.sources.data_dir, BASE_DIR / "data" / "documents")

# --- Personalization --------------------------------------------------------
PERSONALIZATION_ENABLED = _cfg.app.personalization_enabled
PROFILE_MIN_MESSAGES = _cfg.app.profile_min_messages
PROFILE_TOPIC_LIMIT = _cfg.app.profile_topic_limit
PROFILE_RELEVANCE_THRESHOLD = _cfg.app.profile_relevance_threshold
PERSONALIZED_FOLLOWUPS_COUNT = _cfg.app.personalized_followups_count

# --- Operational / deployment (pure env — not part of the RAG instance) -----
CHAT_DB_PATH = Path(
    os.getenv("CHAT_DB_PATH", str((BASE_DIR / ".chainlit" / "chat_history.sqlite3").resolve()))
)
# Where the chat history used to live. Under Docker, CHAT_DB_PATH now points at a
# named volume instead, because .chainlit/ is bind-mounted from the host and SQLite
# in WAL mode is not safe on Docker Desktop's emulated filesystem. Kept so an
# existing history can be carried over once; see chat_history.migrate_legacy_db.
LEGACY_CHAT_DB_PATH = (BASE_DIR / ".chainlit" / "chat_history.sqlite3").resolve()
CHAT_EXPORT_DIR = Path(
    os.getenv("CHAT_EXPORT_DIR", str((BASE_DIR / ".files" / "chat_exports").resolve()))
)
DATABASE_URL = os.getenv("DATABASE_URL")
CHAINLIT_AUTH_USERNAME = os.getenv("CHAINLIT_AUTH_USERNAME", "admin")
CHAINLIT_AUTH_PASSWORD = os.getenv("CHAINLIT_AUTH_PASSWORD", "admin")
CHAINLIT_INIT_DB = (os.getenv("CHAINLIT_INIT_DB", "true") or "true").lower() == "true"

# Watch the document folders and index changes automatically. On by default and
# opt-OUT, so an existing .env written before this feature existed still gets it
# without being re-copied; only someone who wants it off adds the variable.
DOCUMENT_WATCH = (os.getenv("DOCUMENT_WATCH", "true") or "true").lower() == "true"
DOCUMENT_WATCH_INTERVAL = int(os.getenv("DOCUMENT_WATCH_INTERVAL", "15"))
"""Seconds between checks. Each check is a stat per source file, so this is cheap."""
DOCUMENT_WATCH_SETTLE = int(os.getenv("DOCUMENT_WATCH_SETTLE", "5"))
"""Ignore files modified within this many seconds, so half-copied files wait."""
