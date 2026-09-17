# Kindle Kreate

Turn PDFs into clean, reflowable EPUBs for your Kindle or e-reader, and EPUBs into printable PDFs. Scanned PDFs are OCR'd. Everything runs on your own machine: no accounts, no paid APIs, no cloud services.

For the full design, architecture decisions and the milestone roadmap, see [PDF to EPUB Project Brief.md](PDF%20to%20EPUB%20Project%20Brief.md).

## Quick start

With Docker installed:

```bash
git clone https://github.com/tejeshwine-viswanathan/kindle-kreate.git
cd kindle-kreate
docker compose up --build
```

Then open <http://localhost:8080>, drop in a PDF or an EPUB, and download the result. The images include Tesseract (OCR), a Java runtime and EPUBCheck, so scanned books and validation work with no further setup.

## What it does

**PDF to EPUB**

- Works on both kinds of PDF. Pages with a text layer are read directly. Scanned pages are detected per page and OCR'd with Tesseract 5, so a book that mixes both is handled transparently.
- Reconstructs the book, not the page: correct reading order for one, two and three column layouts, running headers, footers and page numbers removed, wrapped lines joined into paragraphs, line-end hyphens repaired, paragraphs continued across page breaks.
- Detects chapter and section headings and builds a nested table of contents.
- Keeps figures where they belong in the text and turns simple ruled tables into real tables. Complex tables are kept as images rather than mangled text.
- Handles "searchable" scans (Internet Archive, Acrobat OCR) properly: the page pictures behind the text layer are dropped, so a 400-page scanned book becomes a small EPUB rather than a gigabyte of images.
- Cleans up OCR: pages are deskewed and denoised, upside-down or rotated scans are corrected, and pages with low OCR confidence are listed as worth proofreading.
- Never loses a book to one bad page. A page that can't be converted becomes a short notice in the EPUB and a warning in the result.
- Validates every EPUB with the W3C EPUBCheck tool before you download it.

**EPUB to PDF**

- US Letter pages with embedded fonts, searchable text and a clickable outline built from the EPUB's table of contents. DRM-protected files are rejected with a clear message.

**The app**

- Drag and drop several files at once, watch per-page progress, cancel a conversion, and see warnings about pages that need attention.

## Running it for development

You need Python 3.12, Node 22, and optionally Tesseract and a Java runtime.

```bash
# backend
cd backend
python -m venv .venv
.venv/Scripts/activate              # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python scripts/fetch_epubcheck.py   # optional: downloads EPUBCheck for validation

# frontend (second terminal)
cd frontend
npm install
```

Optional tools:

- **Tesseract**, for scanned PDFs. Windows: `winget install UB-Mannheim.TesseractOCR`. macOS: `brew install tesseract`. Debian/Ubuntu: `apt install tesseract-ocr tesseract-ocr-eng`. Without it, text PDFs still convert and scanned pages are reported as skipped.
- **Java 11 or newer**, for EPUBCheck. For example `winget install EclipseAdoptium.Temurin.21.JRE`. Without it, validation is skipped.

Start the app:

```bash
cd backend && uvicorn app.main:app --port 8000
cd frontend && npm run dev          # http://localhost:5173
```

In VS Code, open the folder, install the recommended extensions, and run the **Full stack** launch configuration instead.

### Scaling out with Celery

By default, pages are converted on a process pool inside the API. For more throughput, or to run workers on other machines, switch to Celery with Valkey (an open-source Redis fork) as the queue. This is what Docker Compose runs.

```bash
docker run -d --name kindle-kreate-valkey -p 6379:6379 valkey/valkey:8-alpine
cd backend
PDF2EPUB_QUEUE=celery uvicorn app.main:app --port 8000
PDF2EPUB_QUEUE=celery celery -A app.celery_app worker --loglevel=info                 # Linux/macOS
PDF2EPUB_QUEUE=celery celery -A app.celery_app worker --loglevel=info --pool=threads  # Windows
```

The VS Code configuration **Full stack (Celery + Valkey)** does all of this, including starting the Valkey container.

With Docker Compose, add workers with `docker compose up --scale worker=3`.

## Configuration

All settings are environment variables on the backend.

| Variable | Default | Meaning |
| --- | --- | --- |
| `PDF2EPUB_DATA_DIR` | `backend/data` | Where uploads, intermediate files and results are stored |
| `PDF2EPUB_MAX_UPLOAD_MB` | `500` | Largest accepted upload |
| `PDF2EPUB_MAX_PAGES` | `2000` | Largest accepted PDF |
| `PDF2EPUB_JOB_TTL_HOURS` | `24` | Finished jobs and their files are deleted after this |
| `PDF2EPUB_QUEUE` | `local` | `local`: convert inside the API process. `celery`: use Celery workers and Valkey |
| `PDF2EPUB_REDIS_URL` | `redis://localhost:6379/0` | Valkey/Redis connection for Celery mode |
| `PDF2EPUB_JOB_WORKERS` | `2` | Conversions running at once (local mode) |
| `PDF2EPUB_PAGE_WORKERS` | CPU cores minus 1 | Pages converted in parallel (local mode). `0` disables the process pool |
| `PDF2EPUB_TESSERACT_CMD` | auto-detected | Path to the `tesseract` binary |
| `PDF2EPUB_OCR_LANG` | `eng` | Tesseract languages, e.g. `eng+deu`. The language packs must be installed |
| `PDF2EPUB_OCR_DPI` | `300` | Resolution scanned pages are rendered at for OCR |
| `PDF2EPUB_OCR_FLAG_CONFIDENCE` | `80` | Pages with a mean OCR confidence below this are flagged for proofreading |
| `PDF2EPUB_EPUBCHECK` | `auto` | `auto`: validate when EPUBCheck and Java are available. `required`: fail the job if they aren't. `off`: skip |
| `PDF2EPUB_EPUBCHECK_JAR` | `backend/tools/epubcheck/epubcheck.jar` | Path to the EPUBCheck jar |
| `PDF2EPUB_JAVA_CMD` | auto-detected | Path to `java` |
| `PDF2EPUB_UPLOAD_RATE_PER_MINUTE` | `10` | Uploads allowed per client address per minute. `0` disables the limit |
| `PDF2EPUB_MAX_ACTIVE_JOBS` | `20` | Conversions in flight before new uploads are refused. `0` disables the limit |
| `PDF2EPUB_TRUST_PROXY` | `false` | Take the client address from `X-Forwarded-For`. Set when behind a proxy you control (Compose does) |
| `PDF2EPUB_AUTH_USER`, `PDF2EPUB_AUTH_PASSWORD` | unset | When both are set, the API requires a login (HTTP Basic). Browsers ask once |

## Exposing it on a network

The app is designed for your own machine. If you do make it reachable by others, turn on the login and put TLS in front of it (Caddy, Traefik or a cloud load balancer), since the bundled nginx serves plain HTTP:

```bash
PDF2EPUB_AUTH_USER=me PDF2EPUB_AUTH_PASSWORD='a long passphrase' docker compose up -d
```

Uploads are then rate-limited per address and the number of conversions in flight is capped. To find out how many workers you need, there is a load test:

```bash
python backend/scripts/load_test.py book.pdf --url http://localhost:8080 --jobs 8 --concurrency 4 --user me --password '...'
```

## How it works

```
upload ──> classify each page ──┬─ text layer ──> extract text, fonts, images ──┐
                                └─ scanned ─────> render, deskew, OCR ──────────┤
                                                                                 v
                    reading order ──> strip headers/footers ──> headings, paragraphs, tables
                                                                                 v
                                                  EPUB packaging ──> EPUBCheck ──> download
```

Pages are independent, so they are converted in parallel and both paths produce the same per-page format. Everything after that is shared, which is why scanned and text PDFs come out looking the same.

```
backend/app/
  main.py            HTTP API: POST /api/jobs, GET /api/jobs/{id}, GET .../download, DELETE
  runner.py          runs jobs on a local process pool or hands them to Celery
  celery_app.py      Celery tasks: one per page, then assembly
  storage.py         job files on disk; job state in a file or in Valkey
  tasks/classify.py  text layer or scan?
  tasks/extract.py   text-layer extraction
  tasks/ocr.py       rasterize, deskew, binarize, Tesseract, figure and table recovery
  tasks/layout.py    reading order (recursive XY-cut)
  tasks/structure.py headers/footers, headings, paragraph reconstruction
  tasks/epub_builder.py, validate.py, epub_to_pdf.py, pipeline.py
frontend/            React, Vite, TypeScript, Tailwind
```

The design notes and original roadmap are in [PDF to EPUB Project Brief.md](PDF%20to%20EPUB%20Project%20Brief.md).

## Tests

```bash
cd backend
.venv/Scripts/python -m pytest
```

The tests build PDFs on the fly from a synthetic corpus with known text: running headers, page numbers, hyphenation, paragraphs spanning pages, two-column layouts, figures and tables. OCR tests degrade the same pages into realistic scans (skew, noise, blur, JPEG artefacts, upside down) and require at least 99% word accuracy. Tests that need Tesseract or Java are skipped when those aren't installed.

## License

MIT. See [LICENSE](LICENSE).
