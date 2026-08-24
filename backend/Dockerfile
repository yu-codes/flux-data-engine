# flux-data-engine backend
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so source edits do not invalidate the layer.
COPY backend/pyproject.toml /app/backend/pyproject.toml
RUN pip install --upgrade pip && pip install /app/backend

COPY backend /app/backend

WORKDIR /app/backend
ENV PYTHONPATH=/app/backend

EXPOSE 38000

# Probed with the interpreter that is already here, so the image needs no curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:38000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "38000"]
