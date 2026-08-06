# The evaluation service. Deliberately NOT built from the app Dockerfile: this image
# installs only the `eval` dependency group, so it carries deepeval + litellm + fastapi
# and none of chainlit, docling or torch. Keeping the two apart is the whole point of
# running scoring out of process — see eval_app/main.py.
#
# Build context is apps/chainlit (it needs pyproject.toml and uv.lock), so this is
# referenced from docker-compose.yml as `dockerfile: eval_app/Dockerfile`.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Same reasoning as the app image: the venv lives outside /app because compose
# bind-mounts the repo over it, which would shadow a /app/.venv.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV UV_LINK_MODE=copy
ENV UV_NO_CACHE=1

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.9.14 /uv /usr/local/bin/uv

# --only-group installs the group WITHOUT the project's own dependencies, which is what
# keeps this image slim: RAGAS pulls langchain and datasets, and none of that belongs in
# the app image. --frozen fails loudly if pyproject.toml and uv.lock drift apart, so the
# eval group must be present in the lockfile (`uv lock`).
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-install-project --only-group eval

# Only the service's own code and its one static page.
COPY eval_app /app/eval_app

EXPOSE 8001

CMD ["uvicorn", "eval_app.main:app", "--host", "0.0.0.0", "--port", "8001"]
