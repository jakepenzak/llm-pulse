FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml .
COPY llm_pulse/ llm_pulse/

RUN uv sync --no-dev

RUN mkdir -p /app/data

VOLUME /app/data

EXPOSE 8000

CMD ["uv", "run", "llm-pulse"]
