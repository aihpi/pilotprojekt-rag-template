"""Headless benchmark replays: re-ask gold conversations, score every turn.

Runs **app-side** — the eval service cannot answer questions (its image has no
retrieval stack), so it queues jobs and this module executes them, POSTing each
scored turn back. That keeps the dependency edge one-way and the eval service the
sole writer of its database. Two entry points share one code path:

* the in-app poller (``app.py`` startup) claims jobs the dashboard's play button
  queued via ``/api/benchmark``;
* the CLI runs directly: ``uv run python benchmark.py --models A B [--judge M]``.

Deliberately **chainlit-free**, so it can run in a plain process. The answer loop
is a compact copy of the one in ``app.main()`` (its reference), minus everything a
benchmark does not need: cl.Steps, the no-tool retry nudge, the vision pass,
figure markers, citation link injection, and per-session retrieval filters.
ponytail: duplicated loop mechanics (~60 lines); if a third copy ever appears,
extract the loop from ``app.main()`` into a chainlit-free module instead. The
drift this buys is documented in docs/evaluation.md: replay scores are comparable
with each other, not with live rows — which the dashboard enforces by keeping
them in separate tables.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from config import get_config
from evaluation import config_signature
from llm import chat, message_to_dict
from rag_tool import context_with_source, extract_page, extract_source_file
from settings import MAX_TOP_K, SYSTEM_PROMPT_PATH, TOP_K
from tools import build_openai_tools
from tools.base import ToolContext

_MAX_TOOL_ROUNDS = 12  # same cap as app.main()'s MAX_TOOL_CALL_ROUNDS default
_TIMEOUT = httpx.Timeout(300.0, connect=5.0)


def _system_prompt(cfg) -> str | None:
    """The prompt the app would use: the configured file, else the generated cache.

    Never *generates* one — a benchmark should run against the prompt the app
    actually serves, not mint a new one mid-run.
    """
    candidates = [
        Path(SYSTEM_PROMPT_PATH),
        cfg.resolve_path(f".generated_system_prompt.{cfg.vector_store.collection}.md"),
    ]
    for path in candidates:
        try:
            text = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return None


def _result_key(item: Any) -> tuple[str, int | None, str]:
    # Mirror of app._result_key, so replay dedupes chunks the same way.
    metadata = getattr(item, "metadata", {}) or {}
    snippet = re.sub(r"\s+", " ", (getattr(item, "text", "") or "").strip())[:120]
    return (extract_source_file(metadata) or "", extract_page(metadata), snippet)


async def answer_question(
    question: str,
    history: list[dict[str, Any]],
    *,
    model: str,
    cfg,
    schemas: list[dict[str, Any]],
    router: dict[str, Any],
) -> tuple[str, list[Any]]:
    """One headless answer. Mutates ``history`` in place (as ``main()`` does).

    Returns ``(answer_text, retrieved_results)``.
    """
    history.append({"role": "user", "content": question})
    response = await chat(history, tools=schemas, tool_choice="required", model=model)
    current = response.choices[0].message

    aggregated: dict[tuple[str, int | None, str], Any] = {}
    cached: dict[str, tuple[list[Any], dict[str, Any]]] = {}

    for _round in range(_MAX_TOOL_ROUNDS):
        if not getattr(current, "tool_calls", None):
            break
        history.append(message_to_dict(current))
        for tool_call in current.tool_calls:
            name = getattr(getattr(tool_call, "function", None), "name", "")
            tool = router.get(name)
            if tool is None:
                payload: dict[str, Any] = {"error": f"Unsupported tool: {name}"}
                history.append({"role": "tool", "tool_call_id": tool_call.id,
                                "content": json.dumps(payload, ensure_ascii=False)})
                continue
            args = json.loads(tool_call.function.arguments or "{}")
            signature = f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
            if signature in cached:
                results, payload = cached[signature]
            else:
                ctx = ToolContext(
                    query_fallback=question,
                    # Empty, not None — the tools call dict(ctx.filters), and the app
                    # passes {} when no profile is active. Deliberately no profile
                    # filters in a replay: a benchmark measures the corpus, not one
                    # profile's slice of it.
                    filters={},
                    default_top_k=TOP_K,
                    max_top_k=MAX_TOP_K,
                    collection=None,
                    language=cfg.language,
                    fetch_max_chunks=cfg.tools.fetch_max_chunks,
                    expand_window=cfg.tools.expand_window,
                )
                tool_result = await tool.handler(args, ctx)
                results, payload = tool_result.results, tool_result.payload
                cached[signature] = (results, payload)
            for item in results:
                # First writer wins, mirroring app.py. The score comparison this
                # replaces preferred fetch_document's placeholder 1.0 over a real
                # similarity, so it swapped in that tool's longer 4000-char copy.
                aggregated.setdefault(_result_key(item), item)
            history.append({"role": "tool", "tool_call_id": tool_call.id,
                            "content": json.dumps(payload, ensure_ascii=False)})
        followup = await chat(history, tools=schemas, tool_choice="auto", model=model)
        current = followup.choices[0].message

    # Deliberately unsorted, mirroring app.py — a replay has to order results the
    # same way the app does or its scores describe a different pipeline. `score` is
    # not comparable across tools: search returns a similarity (or a fused rank
    # under retrieval.hybrid), while fetch_document and expand_context did no
    # relevance matching and report a placeholder 1.0. Insertion order is
    # meaningful instead: dicts preserve it and retrieve() appends in Qdrant's
    # relevance order.
    results = list(aggregated.values())

    if getattr(current, "tool_calls", None):
        # Round cap hit: force a final answer from what was collected, exactly as
        # the app's safety stop does (app.py, "tool_round_limit_reached").
        from rag_tool import build_context

        forced = [
            *history,
            {"role": "system", "content": (
                "Erstelle jetzt die finale Antwort ausschließlich aus dem Kontext. "
                "Keine weiteren Tool-Aufrufe."
            )},
            {"role": "user", "content": (
                f"Frage: {question}\n\n"
                f"Kontext:\n{build_context(results[: max(TOP_K, 8)])}\n\n"
                "Antworte auf Deutsch mit Quellenhinweisen [1], [2], ..."
            )},
        ]
        final = await chat(forced, model=model)
        answer = final.choices[0].message.content or ""
    else:
        answer = current.content or ""

    # The model's answer joins the history so the NEXT turn sees what the
    # replayed model actually said — not the gold answer. Conversation drift is
    # the thing a multi-turn benchmark measures.
    history.append({"role": "assistant", "content": answer})
    return answer, results


async def run_job(
    job: dict[str, Any],
    *,
    service_url: str,
    report: bool = True,
) -> dict[str, int]:
    """Replay the gold set with ``job['chat_model']`` and score every turn.

    The judge is resolved ONCE per job and never follows the replayed model:
    letting each model grade itself would void the comparison the benchmark
    exists to make.
    """
    cfg = get_config()
    model = job["chat_model"]
    judge = job.get("judge_model") or cfg.evaluation.judge_model or cfg.models.chat_model
    run_label = job["run_label"]
    signature = config_signature(cfg, chat_model=model)
    system_prompt = _system_prompt(cfg)
    schemas, router = build_openai_tools(cfg)
    base = service_url.rstrip("/")
    job_id = job.get("id")

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        # Checked, unlike the progress posts below: a failed fetch here otherwise
        # surfaces as KeyError('gold') from an error body, naming nothing useful.
        gold_response = await client.get(f"{base}/api/gold")
        gold_response.raise_for_status()
        gold = gold_response.json()["gold"]
        total = sum(len(g["turns"]) for g in gold)
        done = failed = 0
        if report and job_id:
            await client.post(f"{base}/api/benchmark/{job_id}",
                              json={"total_turns": total})

        for entry in gold:
            history: list[dict[str, Any]] = (
                [{"role": "system", "content": system_prompt}] if system_prompt else []
            )
            for turn_no, turn in enumerate(entry["turns"], start=1):
                try:
                    answer, results = await answer_question(
                        turn["user"], history,
                        model=model, cfg=cfg, schemas=schemas, router=router,
                    )
                    # Post even when retrieval came back empty: similarity against
                    # the gold answer still measures, and a NULL faithfulness on
                    # such a row is honest. (The live path skips those instead —
                    # it has no reference to fall back on.)
                    await client.post(f"{base}/api/score", json={
                        "question": turn["user"],
                        "answer": answer,
                        "contexts": [context_with_source(r) for r in results],
                        "metrics": ["faithfulness", "relevance", "similarity"],
                        "reference": turn["assistant"],
                        "judge_model": judge,
                        "embed_model": cfg.models.embed_model,
                        "config_signature": signature,
                        "source": "replay",
                        "run_label": run_label,
                        "gold_id": entry["id"],
                        "gold_turn": turn_no,
                    })
                except Exception as exc:  # noqa: BLE001 — one turn must not kill the run
                    failed += 1
                    print(f"[benchmark] turn failed ({entry['id']} #{turn_no}): {exc}")
                    # The gold answer keeps the conversation on track for the
                    # remaining turns instead of compounding a transport error.
                    history.append({"role": "user", "content": turn["user"]})
                    history.append({"role": "assistant", "content": turn["assistant"]})
                done += 1
                print(f"[benchmark] {run_label}: {done}/{total} turns")
                if report and job_id:
                    await client.post(f"{base}/api/benchmark/{job_id}",
                                      json={"done_turns": done})

        if report and job_id:
            await client.post(f"{base}/api/benchmark/{job_id}", json={
                "status": "done",
                "error": f"{failed} turn(s) failed" if failed else None,
            })
    return {"total": total, "done": done, "failed": failed}


async def _cli(models: list[str], judge: str | None, label: str | None) -> None:
    cfg = get_config()
    base = cfg.evaluation.service_url
    stamp = label or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    for model in models:
        summary = await run_job(
            {"id": None, "chat_model": model, "judge_model": judge,
             "run_label": f"{stamp} {model}"},
            service_url=base,
            report=False,
        )
        print(f"[benchmark] {model}: {summary}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay the gold set against one or more models.")
    parser.add_argument("--models", nargs="+", required=True,
                        help="chat models to benchmark (bare gateway names)")
    parser.add_argument("--judge", default=None,
                        help="judge model, pinned for the whole run "
                             "(default: evaluation.judge_model or models.chat_model)")
    parser.add_argument("--label", default=None, help="run label prefix (default: timestamp)")
    cli_args = parser.parse_args()
    asyncio.run(_cli(cli_args.models, cli_args.judge, cli_args.label))
