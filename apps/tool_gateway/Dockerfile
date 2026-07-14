FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY apps/tool_gateway/requirements.txt /tmp/tool-gateway-requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/tool-gateway-requirements.txt

COPY apps/__init__.py /app/apps/__init__.py
COPY apps/tool_gateway /app/apps/tool_gateway
COPY packages/__init__.py /app/packages/__init__.py
COPY packages/tool_system /app/packages/tool_system

EXPOSE 8002

CMD ["python", "-m", "apps.tool_gateway.src.index"]
