# Production image for the web application. Models and datasets remain mounted
# volumes / external services; they must not be baked into an application image.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    OUTPUT_DIR=/var/lib/ai-analyzer/outputs \
    CACHE_DIR=/var/lib/ai-analyzer/cache \
    LOG_DIR=/var/log/ai-analyzer \
    STORE_PATH=/var/lib/ai-analyzer/analyzer.db \
    BACKEND=ollama \
    OLLAMA_AUTO_CREATE=false

WORKDIR /opt/ai-analyzer

COPY requirements.lock.txt README.md LICENSE pyproject.toml ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.lock.txt

COPY app ./app
COPY .streamlit ./.streamlit
RUN python -m pip install . --no-deps \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /data /var/lib/ai-analyzer /var/log/ai-analyzer \
    && chown -R appuser:appuser /opt/ai-analyzer /data /var/lib/ai-analyzer /var/log/ai-analyzer

USER appuser
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"

CMD ["ai-analyzer-ui", "--server.address", "0.0.0.0", "--server.port", "8501"]
