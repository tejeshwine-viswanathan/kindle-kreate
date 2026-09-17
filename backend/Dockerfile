FROM python:3.12-slim

# Tesseract for scanned pages; a JRE for EPUBCheck validation of every generated EPUB.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts ./scripts
RUN python scripts/fetch_epubcheck.py
# keeps one EPUBCheck JVM warm per process (see app/tasks/validate.py)
COPY tools/epubcheck-server ./tools/epubcheck-server

COPY app ./app
ENV PDF2EPUB_DATA_DIR=/data
VOLUME /data
EXPOSE 8000
# The worker service overrides this with: celery -A app.celery_app worker
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
