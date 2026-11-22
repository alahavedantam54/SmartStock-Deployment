
FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Application files
COPY webi.py .
COPY gdrive_storage.py .
COPY static ./static

ENV PYTHONUNBUFFERED=1

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 10000

CMD ["sh", "-c", "uvicorn webi:app --host 0.0.0.0 --port ${PORT:-10000}"]
