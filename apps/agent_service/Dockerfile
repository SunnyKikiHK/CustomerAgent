FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt

COPY apps /app/apps
COPY packages /app/packages
COPY skills /app/skills

# Temporal worker: serves signal workflows + activities (replaces the old
# Redis-polling queue-drain worker).
CMD ["python", "-m", "apps.temporal_worker.src.worker"]
