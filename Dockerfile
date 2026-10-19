FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN useradd --create-home --uid 10001 dostuk

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY worker ./worker

USER dostuk
EXPOSE 8000

# По умолчанию — API. Worker: arq worker.main.WorkerSettings; миграции: alembic upgrade head
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
