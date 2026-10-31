FROM python:3.12.3-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    POETRY_NO_ANSI=1

WORKDIR /app

RUN pip install --no-cache-dir "poetry>=2.0,<3.0"

COPY pyproject.toml poetry.lock poetry.toml README.md ./
RUN poetry install --only main --no-root --no-interaction --no-ansi

COPY src ./src
RUN poetry install --only main --no-interaction --no-ansi

EXPOSE 8030

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "baked.superiorsix.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
