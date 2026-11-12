FROM ghcr.io/astral-sh/uv:0.13.7 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home shop

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY fragment_bot ./fragment_bot
RUN uv sync --locked --extra providers --no-dev --no-editable && mkdir /app/data && chown shop:shop /app/data
ENV PATH="/app/.venv/bin:$PATH" DATA_DIR=/app/data PYTHONUNBUFFERED=1
USER shop
CMD ["fragment-bot", "run"]
