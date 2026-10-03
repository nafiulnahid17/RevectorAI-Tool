FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 INKSCAPE_PROFILE_DIR=/tmp/revector-inkscape
RUN apt-get update && apt-get install -y --no-install-recommends \
    inkscape potrace tesseract-ocr fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /engine
COPY pyproject.toml requirements.txt ./
COPY app ./app
RUN pip install --no-cache-dir '.[trace,r2]' && useradd --create-home --uid 10001 revector \
    && mkdir /engine/data && chown -R revector:revector /engine/data
USER revector
ENV REVECTOR_DATA_DIR=/engine/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
# Local queue requires one API process. Threads process different projects concurrently.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
