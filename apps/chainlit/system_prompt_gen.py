"""Auto-generate a system prompt at startup when none is configured.

When ``prompt.system_prompt_path`` is unset (and no fallback file exists), the
app asks the configured chat model (the LLM in ``.env``, via the gateway) to
write a system prompt grounded in (a) a structural template — by default the
repo-root ``system.md`` — and (b) a sample of the chunks actually indexed in the
vector DB for the active collection. The result is cached next to the config as
``.generated_system_prompt.<collection>.md`` so restarts reuse it; set
``REGENERATE_SYSTEM_PROMPT=true`` to force a fresh generation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config.schema import RagConfig

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent
DEFAULT_TEMPLATE = BASE_DIR / "config" / "prompts" / "default_system.md"

# Structural sections that carry no domain signal — skipped when picking excerpts.
_SKIP_SECTIONS = {
    "references", "acknowledgements", "acknowledgments", "author contributions",
    "funding", "data availability", "competing interests", "additional information",
    "open", "untitled section", "",
}


def _sample_corpus_digest(collection: str, sample_size: int) -> str:
    """Scroll the collection and build a compact digest (per-document section
    titles + a few representative excerpts). Returns "" if unavailable/empty."""
    try:
        from qdrant_client import QdrantClient

        from settings import QDRANT_API_KEY, QDRANT_URL

        client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
        points, _ = client.scroll(
            collection, limit=max(sample_size * 6, 300), with_payload=True
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] could not sample collection '{collection}': {exc}")
        return ""

    titles: dict[str, list[str]] = {}
    excerpts: dict[str, tuple[str, str]] = {}
    for point in points:
        payload = point.payload or {}
        if payload.get("_meta"):
            continue
        doc = payload.get("source_file") or payload.get("source") or "?"
        section = (payload.get("section_title") or "").strip()
        titles.setdefault(doc, []).append(section)
        if doc not in excerpts and section.lower() not in _SKIP_SECTIONS:
            text = " ".join((payload.get("text") or "").split())
            if len(text) >= 200:
                excerpts[doc] = (section, text[:320])

    if not titles:
        return ""

    lines = [f"The knowledge base contains {len(titles)} document(s)."]
    lines.append("\nDocuments and their section headings:")
    for doc in sorted(titles):
        uniq = list(dict.fromkeys(t for t in titles[doc] if t))[:12]
        lines.append(f"- {doc}: {', '.join(uniq) if uniq else '(no headings)'}")
    if excerpts:
        lines.append("\nRepresentative excerpts:")
        for doc, (section, text) in list(excerpts.items())[:sample_size]:
            lines.append(f"- [{doc} — {section}] {text}")
    return "\n".join(lines)


def _instructions(config: "RagConfig") -> str:
    # The app's citation and follow-up PARSERS are German-locked: citations must
    # use the raw token "Quelle N" and follow-ups must sit under an
    # "Anschlussfragen:" header, or neither renders. So for a German instance we
    # pin those exact markers (and answers) regardless of the documents' language
    # — the model reads the (possibly English) sources and answers in German.
    is_german = (config.language or "en").lower().startswith("de")
    if is_german:
        answer_language = "German (Deutsch), even when the source documents are in another language"
        citation_rule = (
            'Citations: cite each claim inline with the RAW token "Quelle N" '
            '(N = the source number), formatted "Quelle N: <section title> '
            '(S.<page>)" (page range "S.<start>-<end>" when applicable). No '
            "brackets around the token (it must stay clickable), and NO separate "
            'source list / "Quellenliste" at the end (a side-panel shows details).'
        )
        followup_rule = (
            'End EVERY answer with exactly 3 follow-up questions under the exact '
            'header "Anschlussfragen:", each on its own numbered line ending in '
            '"?", and none inside the body text.'
        )
    else:
        token = config.citation.token_word or "Source"
        answer_language = "the same language as the user's question"
        citation_rule = (
            f'Citations: cite claims inline using the raw token "{token} N" '
            f'(N = the source number), no brackets around it, and NO separate '
            "reference list at the end (a side-panel renders source details)."
        )
        followup_rule = (
            "End every answer with exactly 3 follow-up questions under their own "
            'header, each on a numbered line ending in "?", none inside the body."'
        )
    return f"""\
Write a SYSTEM PROMPT for a retrieval-augmented-generation (RAG) chatbot that
answers questions strictly from the indexed knowledge base described below.

Hard requirements for the prompt you produce:
- Output ONLY the system prompt as Markdown — no preamble, no explanation, no code fences.
- Write the system prompt itself in {answer_language.split(',')[0]}.
- The assistant must ANSWER in {answer_language}.
- Give the assistant an identity that fits the actual corpus domain (infer it from
  the documents); describe the domain naturally rather than listing every document.
- Grounding: the assistant MUST call the retrieval tool first and answer only from
  the retrieved passages; if the answer is not in the retrieved context it must say
  so plainly and not guess from prior knowledge.
- Preserve precision: keep exact quantities, units, and terminology from the sources;
  distinguish methods from results; never invent facts, numbers, or citations.
- {citation_rule}
- Be concise (target <= 250 words per answer).
- {followup_rule}
- Model the STRUCTURE and rigor of the template (identity/goal, steps, output rules,
  follow-up format) but adapt all content to this corpus. Do NOT copy the template's
  domain or wording."""


async def generate_system_prompt(config: "RagConfig", template_text: str) -> str | None:
    digest = _sample_corpus_digest(config.vector_store.collection, config.prompt.sample_size)
    if not digest:
        print("[startup] no indexed documents sampled; skipping prompt generation")
        return None

    from llm import chat

    messages = [
        {
            "role": "system",
            "content": "You are an expert prompt engineer who writes system prompts for RAG chatbots.",
        },
        {
            "role": "user",
            "content": (
                f"{_instructions(config)}\n\n"
                f"TEMPLATE (structure & rigor reference — adapt, do not copy its domain):\n\n"
                f"{template_text}\n\n"
                f"INDEXED CORPUS (what the knowledge base actually contains):\n{digest}"
            ),
        },
    ]
    try:
        response = await chat(messages, tools=None, tool_choice=None)
        content = response.choices[0].message.content
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] system prompt generation failed: {exc}")
        return None
    return (content or "").strip() or None


def _template_text(config: "RagConfig") -> str:
    if config.prompt.template_path:
        path = config.resolve_path(config.prompt.template_path)
    else:
        path = REPO_ROOT / "system.md"
    if not path.is_file():
        path = DEFAULT_TEMPLATE
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


async def ensure_system_prompt(
    config: "RagConfig", current: str | None
) -> tuple[str | None, Path | None]:
    """Return a system prompt, generating (and caching) one if none is set.

    Returns ``(prompt_text_or_None, cache_path_or_None)``.
    """
    if current or not config.prompt.auto_generate:
        return current, None

    cache = config.resolve_path(f".generated_system_prompt.{config.vector_store.collection}.md")
    force = os.getenv("REGENERATE_SYSTEM_PROMPT", "").lower() in {"1", "true", "yes"}
    if cache.is_file() and not force:
        cached = cache.read_text(encoding="utf-8").strip()
        if cached:
            return cached, cache

    prompt = await generate_system_prompt(config, _template_text(config))
    if not prompt:
        return current, None
    try:
        cache.write_text(prompt, encoding="utf-8")
    except OSError as exc:
        print(f"[startup] could not cache generated system prompt: {exc}")
    return prompt, cache
