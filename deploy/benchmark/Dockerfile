FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn>=0.30.0" \
    "pydantic>=2.0.0" \
    "psycopg2-binary>=2.9.0" \
    "jsonschema>=4.0.0"

COPY app /app/app
COPY benchmark_service /app/benchmark_service
COPY scripts /app/scripts
COPY data/bench/requests /app/data/bench/requests
COPY data/eval /app/data/eval
COPY deploy/bot_models.json /app/deploy/bot_models.json

CMD ["bash", "-lc", "python -m benchmark_service.migrate && uvicorn benchmark_service.api:app --host 0.0.0.0 --port 8080"]
