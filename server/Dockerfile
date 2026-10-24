# Agent Commons server image for hosted mode (db/compose.hosted.yml). Build context: repo root.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY server ./server
RUN uv sync --locked --no-dev --package commons
ENV PATH=/app/.venv/bin:$PATH
RUN useradd --system --uid 10001 commons
USER commons
# Cloud Run sets $PORT. Migrations are applied on start (serialized by an advisory lock).
CMD ["commons-server", "--host", "0.0.0.0", "--migrate"]
