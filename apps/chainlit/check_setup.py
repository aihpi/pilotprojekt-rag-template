"""Check that everything this app needs is actually reachable.

Run this when something does not work and you want to know *where* the problem is,
rather than reading a wall of tracebacks. It answers one question: is it my setup, my
connection, or the AI service?

    python -m check_setup          # or: make check

Each model is tried several times, because the interesting failure is not "it broke"
but "it broke sometimes". A shaky connection or a busy service shows up as a partial
score, which no single attempt would reveal.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# How many times to try each model. Enough to tell "unstable" from "broken", without
# spending real money: every probe is a handful of tokens.
ATTEMPTS = 5


@dataclass
class Result:
    name: str
    ok: bool = False
    attempts: int = 0
    passed: int = 0
    seconds: list[float] = field(default_factory=list)
    error: str = ""
    hint: str = ""
    skipped: str = ""

    @property
    def median(self) -> float:
        if not self.seconds:
            return 0.0
        ordered = sorted(self.seconds)
        return ordered[len(ordered) // 2]

    def line(self) -> str:
        if self.skipped:
            return f"  [skip] {self.name}: {self.skipped}"
        if self.ok and self.passed == self.attempts:
            return f"  [ ok ] {self.name}: {self.passed}/{self.attempts}, {self.median:.1f}s"
        if self.passed:
            return (
                f"  [WARN] {self.name}: only {self.passed} of {self.attempts} attempts "
                f"worked, {self.median:.1f}s"
            )
        return f"  [FAIL] {self.name}: {self.error}"


def _short(exc: Exception, limit: int = 140) -> str:
    text = " ".join(str(exc).split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _classify(exc: Exception, *, host_reachable: bool = True) -> str:
    """Turn a library exception into something worth acting on."""
    text = str(exc).lower()
    if "disconnected" in text or "connection error" in text or "timed out" in text:
        # litellm reports a DNS failure and a mid-request disconnect with the same
        # "Connection error", so the message alone cannot tell them apart. The
        # separate host probe can, and blaming the wrong thing sends people hunting
        # for a VPN problem when they have a typo in an address.
        if not host_reachable:
            return (
                "The address could not be reached at all. Check LITELLM_BASE_URL in "
                ".env, including http:// or https:// and the port."
            )
        return (
            "The service can be found, but it stopped answering part way through. "
            "That is usually the network in between, or a very busy service. Try "
            "turning off any VPN, or use a different connection."
        )
    if (
        "401" in text
        or "unauthorized" in text
        or "invalid api key" in text
        or "authentication" in text
        or "token" in text
    ):
        return "The key was rejected. Check LITELLM_API_KEY in .env."
    if "403" in text or "forbidden" in text:
        return (
            "Access refused. The key may lack permission for this model, or the model "
            "name may need to be written differently for your service."
        )
    if "404" in text or "not found" in text or "does not exist" in text:
        return (
            "The service does not know this model name. Ask it which models it offers "
            "and put one of those in your settings file."
        )
    if "429" in text or "rate limit" in text:
        return "The service is rate limiting you. Wait a moment and try again."
    if "getaddrinfo" in text or "name or service not known" in text or "dns" in text:
        return "The address could not be resolved. Check LITELLM_BASE_URL in .env."
    if "413" in text or "too large" in text:
        return "The request was rejected as too large."
    return "See the message above."


def check_service_host(base_url: str) -> Result:
    """Can we even find and open a connection to the AI service?

    Deliberately separate from the model probes. It distinguishes "the address is
    wrong" from "the connection is unstable", which the library's own error message
    cannot: both surface as "Connection error".
    """
    import socket
    from urllib.parse import urlparse

    result = Result(name="Reaching the AI service")
    if not base_url:
        result.error = "LITELLM_BASE_URL is not set"
        result.hint = "Put the address of your AI service into apps/chainlit/.env."
        return result

    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        result.error = f"cannot read a host out of {base_url!r}"
        result.hint = "LITELLM_BASE_URL should look like https://example.org/ or http://host:4000."
        return result

    result.attempts = 1
    start = time.time()
    try:
        socket.getaddrinfo(host, port)
    except OSError as exc:
        result.error = f"the name {host} could not be looked up ({_short(exc, 60)})"
        result.hint = (
            "Either the address is wrong or there is no working internet connection. "
            "Check LITELLM_BASE_URL in .env, then check that you are online."
        )
        return result
    try:
        with socket.create_connection((host, port), timeout=8):
            pass
    except OSError as exc:
        result.error = f"{host}:{port} did not accept a connection ({_short(exc, 60)})"
        result.hint = (
            "The name resolves but nothing answers on that port. A firewall, proxy or "
            "VPN is the usual reason, or the service is down."
        )
        return result
    result.passed = 1
    result.ok = True
    result.seconds.append(time.time() - start)
    result.name = f"Reaching the AI service ({host}:{port})"
    return result


def _probe(name: str, call, attempts: int = ATTEMPTS, *, host_reachable: bool = True) -> Result:
    result = Result(name=name)
    for _ in range(attempts):
        result.attempts += 1
        start = time.time()
        try:
            call()
        except Exception as exc:  # noqa: BLE001 — this tool reports, it does not raise
            if not result.error:
                result.error = _short(exc)
                result.hint = _classify(exc, host_reachable=host_reachable)
            continue
        result.passed += 1
        result.seconds.append(time.time() - start)
    result.ok = result.passed == result.attempts and result.attempts > 0
    return result


def _tiny_png_data_uri() -> str:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (96, 96), "white")
    ImageDraw.Draw(image).rectangle([16, 16, 80, 80], fill="red")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def check_qdrant(url: str) -> Result:
    def call() -> None:
        with urllib.request.urlopen(f"{url.rstrip('/')}/collections", timeout=10) as response:
            json.loads(response.read().decode("utf-8"))

    result = _probe(f"Search index at {url}", call, attempts=2)
    if not result.passed:
        result.hint = (
            "Not reachable. With Docker, start everything with 'docker compose up -d'. "
            "Note that inside a container 'localhost' means the container itself."
        )
    return result


def run(*, skip_vision: bool = False) -> list[Result]:
    from config import get_config
    from settings import LITELLM_API_KEY, LITELLM_BASE_URL, QDRANT_URL

    config = get_config()
    results: list[Result] = []

    print("What this app is set up to use")
    print(f"  settings file : {os.environ.get('RAG_CONFIG', '(default)')}")
    print(f"  AI service    : {LITELLM_BASE_URL or '(not set)'}")
    print(f"  access key    : {'set' if LITELLM_API_KEY else 'NOT SET'}")
    print(f"  chat model    : {config.models.chat_model}")
    print(f"  search model  : {config.models.embed_model}")
    print(f"  image model   : {config.images.vision_model} (images.mode: {config.images.mode})")
    print(f"  search index  : {QDRANT_URL}")
    print()

    if not LITELLM_BASE_URL or not LITELLM_API_KEY:
        print("Nothing can work without both an address and a key for the AI service.")
        print("Put LITELLM_BASE_URL and LITELLM_API_KEY into apps/chainlit/.env.")
        print()

    host = check_service_host(LITELLM_BASE_URL)
    results.append(host)
    print(f"Trying each one {ATTEMPTS} times, to see whether it works every time")

    import llm

    results.append(
        _probe(
            f"Search model ({config.models.embed_model})",
            lambda: llm.embed_sync(["probe"]),
            host_reachable=host.ok,
        )
    )

    def chat_call() -> None:
        import litellm

        litellm.completion(
            model=config.models.chat_model,
            messages=[{"role": "user", "content": "Reply with: OK"}],
            max_tokens=5,
            temperature=0,
            **llm._client_args(config.models.chat_model),
        )

    results.append(
        _probe(f"Chat model ({config.models.chat_model})", chat_call, host_reachable=host.ok)
    )

    vision_result = Result(name=f"Image model ({config.images.vision_model})")
    if skip_vision:
        vision_result.skipped = "not checked (--skip-vision)"
    elif config.images.mode == "none":
        vision_result.skipped = "not needed, images.mode is 'none'"
    else:
        uri = _tiny_png_data_uri()
        vision_result = _probe(
            vision_result.name,
            lambda: llm.describe_image_sync(uri, "Name the shape in one word.", config.images.vision_model),
            host_reachable=host.ok,
        )
    results.append(vision_result)

    results.append(check_qdrant(QDRANT_URL))

    for result in results:
        print(result.line())
    print()
    return results


def summarise(results: list[Result]) -> int:
    """Print a verdict and return the exit code."""
    checked = [r for r in results if not r.skipped]
    broken = [r for r in checked if r.passed == 0]
    flaky = [r for r in checked if 0 < r.passed < r.attempts]

    if not broken and not flaky:
        print("All good. Everything the app needs is reachable and answered every time.")
        print("If something still does not work, see docs/troubleshooting.md.")
        return 0

    if flaky and not broken:
        print("Everything answered, but not every time. That points at your connection")
        print("rather than your settings, because wrong settings fail every time, not")
        print("sometimes. You need a stable internet connection to the AI service; a VPN")
        print("or a busy shared network is the usual reason. This matters most when")
        print("reading documents, which makes hundreds of calls, so an occasional")
        print("failure there turns into many failures.")
    for result in broken + flaky:
        print()
        print(f"  {result.name}")
        if result.error:
            print(f"    the error : {result.error}")
        if result.hint:
            print(f"    what to do: {result.hint}")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Check that the AI service and search index are reachable.")
    parser.add_argument(
        "--skip-vision",
        action="store_true",
        help="Do not test the image model, even when images are switched on.",
    )
    args = parser.parse_args()
    raise SystemExit(summarise(run(skip_vision=args.skip_vision)))


if __name__ == "__main__":
    main()
