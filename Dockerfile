# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 — build the React/Vite dashboard (web-ui/ -> web-ui/dist)
# ---------------------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web

# Install deps first for layer caching, then build.
COPY web-ui/package.json web-ui/package-lock.json ./
RUN npm ci
COPY web-ui/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — Python 3.14 runtime (FastAPI backend + served SPA)
# The uv image bundles the correct interpreter and a fast, reproducible install.
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS runtime
WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    JDSS_WEB_UI_DIR=/app/web-ui/dist

# Resolve dependencies from the lockfile first (cached until deps change).
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Ship the pre-built dashboard so the API and UI are served from one container.
COPY --from=web /web/dist ./web-ui/dist

# Run as a non-root user.
RUN useradd --system --uid 10001 jdss && chown -R jdss /app
USER jdss

EXPOSE 8000

# JDSS_CONFIG (optional) points at a mounted YAML/TOML config; defaults are used otherwise.
CMD ["uvicorn", "jdssarrow.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
