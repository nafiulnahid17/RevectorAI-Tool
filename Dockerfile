# syntax=docker/dockerfile:1
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 INKSCAPE_PROFILE_DIR=/tmp/revector-inkscape
RUN apt-get update && apt-get install -y --no-install-recommends \
    inkscape potrace tesseract-ocr fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /engine
COPY pyproject.toml requirements.txt ./
COPY app ./app
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir '.[trace,r2]' && useradd --create-home --uid 10001 revector \
    && mkdir /engine/data && chown -R revector:revector /engine/data \
    && chmod -R a+rX /engine/app
# The launcher initializes the mounted volume then drops to UID 10001 before
# starting the API. Engine requests never execute as root.
ENV REVECTOR_DATA_DIR=/engine/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health/ready', timeout=3)"
# Local queue requires one API process. Threads process different projects concurrently.
CMD ["python", "-m", "app.server"]
