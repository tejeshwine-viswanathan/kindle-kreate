# kindle-kreate

Convert PDF → EPUB (clean, reflowable e-books, scanned PDFs included) and EPUB → PDF (printable, searchable). Everything runs locally; no paid APIs or cloud services.

See [PDF to EPUB Project Brief.md](PDF%20to%20EPUB%20Project%20Brief.md) for the full design and roadmap.

## Status

| Milestone | State |
| --- | --- |
| **M1: Text-layer fast path** | ✅ Upload → extract → structure → EPUB, with progress UI |
| **M2: OCR path (Tesseract)** | ✅ Scanned pages are deskewed, binarized and OCR'd; figures and ruled tables recovered from the scan; low-confidence pages flagged |
| **M3: Parallel processing + Celery/Valkey** | ✅ Pages convert in parallel: a process pool in the API (default) or Celery workers with Valkey (`PDF2EPUB_QUEUE=celery`, what Docker Compose runs) |
| **M4: Formatting fidelity pass** | ✅ Headers/footers, paragraphs, images, simple tables; every EPUB is validated with W3C EPUBCheck when Java is available |
| **M5: Frontend polish + error handling** | ✅ Multi-file drag and drop, upload progress, per-stage progress, cancel, per-page warnings |
| **M6: Deployment** | ✅ Docker Compose: web + api + worker + valkey, `--scale worker=N` |
| **EPUB → PDF** (added, not in the brief) | ✅ US Letter pages, embedded fonts, searchable text, TOC → PDF outline, DRM-protected EPUBs rejected |

What the PDF → EPUB pipeline handles:

- **Per-page detection** of text layer vs. scan (low text coverage *and* a page-sized image, so sparse title pages aren't misclassified); mixed documents take the fast path for text pages and OCR for the rest
- **OCR** at 300 DPI with Tesseract 5 (LSTM): projection-profile deskew, noise-aware binarization, upside-down and rotated pages detected and corrected, per-word confidences; pages below the confidence threshold are listed as "worth proofreading" in the result
- **Reading order** via recursive XY-cut: 1-, 2- and 3-column layouts, full-width headings above columns (same code for both paths, since OCR pages are converted to the same geometry as text-layer pages)
- **Running headers/footers and page numbers** stripped (repeated margin text across pages)
- **Headings → chapters + nested TOC** from font size/boldness relative to body text (OCR font sizes are estimated from letter shapes, so a line without descenders isn't mistaken for smaller type)
- **Paragraph reconstruction:** wrapped lines joined, line-end hyphens removed (real compounds like "well-known" kept), paragraphs merged across column and page breaks, Tesseract's unreliable paragraph splits repaired by indentation
- **Images** extracted from the text layer or cropped from the scan (OCR) and placed in the text flow; exotic formats (JPX, JBIG2) re-encoded, anything wider than 2000 px downscaled. The page-sized scan behind an OCR text layer (Internet Archive, Acrobat "searchable" PDFs) is recognised and dropped, so a 400-page scanned book gives a small EPUB, not a gigabyte of page pictures
- **Tables:** simple ruled grids become HTML tables; anything more complex is kept as an image rather than mangled text
- Unicode NFC normalization, ligature expansion, control-character cleanup
- Long chapters split into multiple XHTML files so e-readers stay responsive
- **One bad page never fails the book:** it becomes a notice in the EPUB and a warning in the job result
- **EPUBCheck** runs on every generated file; an invalid EPUB fails the job with the validator's message instead of shipping. A tiny Java wrapper (`backend/tools/epubcheck-server`) keeps one EPUBCheck JVM warm per process, so validation takes well under a second instead of the ~5 s EPUBCheck needs to initialise on every run

## Layout

```
backend/            FastAPI + PyMuPDF + Tesseract + ebooklib
  app/main.py         routes: POST/GET/DELETE /api/jobs, /download, /health
                      (POST accepts a PDF or an EPUB; the file's content picks the direction)
  app/runner.py       how jobs run: LocalRunner (threads + process pool) or CeleryRunner
  app/celery_app.py   Celery tasks: start_job -> process_page x N (chord) -> assemble
  app/storage.py      job files on disk (data/jobs/<id>/); job state in a file or in Valkey
  app/schema.py       normalized per-page format shared by text and OCR paths
  app/tasks/
    classify.py       text layer vs. scanned detection
    extract.py        text-layer extraction (lines, fonts, images)
    ocr.py            rasterize -> deskew -> binarize -> Tesseract -> blocks, figures, tables
    layout.py         reading order (XY-cut)
    structure.py      headers/footers, headings, paragraphs -> Document
    epub_builder.py   Document -> EPUB
    validate.py       EPUBCheck wrapper
    epub_to_pdf.py    EPUB -> PDF (MuPDF layout + per-page rendering)
    pipeline.py       the stages (plan_pages / process_page / assemble), independent of the scheduler
  scripts/fetch_epubcheck.py   downloads EPUBCheck into backend/tools/
  tools/epubcheck-server/       EpubCheckServer.java + .class: long-lived EPUBCheck JVM (compile with any JDK 11+)
  tests/              synthetic corpus with ground truth (tests/corpus.py), incl. "scanned" PDFs with skew/noise/blur
frontend/           React + Vite + TypeScript + Tailwind
docker-compose.yml  web + api + worker + valkey
```

## Run it

### One-time setup

```bash
# backend
cd backend
python -m venv .venv
.venv/Scripts/activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python scripts/fetch_epubcheck.py   # optional: EPUB validation (also needs Java 11+)

# frontend
cd ../frontend
npm install
```

**Tesseract** (for scanned PDFs): `winget install UB-Mannheim.TesseractOCR` on Windows (the default install path is auto-detected), `brew install tesseract` on macOS, `apt install tesseract-ocr tesseract-ocr-eng` on Debian/Ubuntu. Without it, text-layer PDFs still convert; scanned pages are reported as skipped.

**Java** (for EPUBCheck): any JRE 11+, e.g. `winget install EclipseAdoptium.Temurin.21.JRE`. Without it, validation is skipped (set `PDF2EPUB_EPUBCHECK=required` to fail jobs instead).

### VS Code

Open the folder, install the recommended extensions, then pick a Run and Debug config:

- **Full stack:** API + Vite. Pages run on a process pool inside the API; nothing else needed.
- **Full stack (Celery + Valkey):** API in Celery mode + a Celery worker + Vite. Starts a `kindle-kreate-valkey` Docker container automatically (the `Start Valkey` task).

The app is at http://kindle-kreate.localhost:5173 (or http://localhost:5173).

### Manually

```bash
# local mode (default)
cd backend && uvicorn app.main:app --port 8000
cd frontend && npm run dev                   # http://kindle-kreate.localhost:5173, proxies /api to :8000

# Celery mode
docker run -d --name kindle-kreate-valkey -p 6379:6379 valkey/valkey:8-alpine
cd backend
PDF2EPUB_QUEUE=celery uvicorn app.main:app --port 8000
PDF2EPUB_QUEUE=celery celery -A app.celery_app worker --loglevel=info                 # Linux/macOS
PDF2EPUB_QUEUE=celery celery -A app.celery_app worker --loglevel=info --pool=threads  # Windows
```

### Docker

```bash
docker compose up --build                     # http://kindle-kreate.localhost:8080
docker compose up --build --scale worker=3    # more page throughput
```

The images include Tesseract, a JRE and EPUBCheck, so scanned PDFs and validation work out of the box. Jobs and files live in the `pdf2epub-data` volume.

### Local host name

The app answers at **http://kindle-kreate.localhost:8080** (Docker) and **http://kindle-kreate.localhost:5173** (dev). Windows 11 and all major browsers resolve any `*.localhost` name to your own machine, so this needs no setup. If some tool on your machine cannot resolve it, add a hosts-file entry once (needs an admin prompt):

```powershell
.\scripts\add-hostname.ps1     # adds "127.0.0.1 kindle-kreate.localhost" to the hosts file
```

## Tests

```bash
cd backend
.venv/Scripts/python -m pytest
```

Tests generate PDFs on the fly from a synthetic corpus with known text (running headers, page numbers, hyphenation, page-spanning paragraphs, two columns, figures, tables) and check the pipeline and the HTTP API end to end. OCR tests render the corpus to degraded "scans" (skew, noise, blur, JPEG, upside down) and assert at least 99% word accuracy; they are skipped when Tesseract is not installed. Celery tests run the task chord eagerly against an in-memory Redis (`fakeredis`).

## Configuration

Environment variables (backend):

| Variable | Default | |
| --- | --- | --- |
| `PDF2EPUB_DATA_DIR` | `backend/data` | Uploads, page results, extracted images, outputs |
| `PDF2EPUB_MAX_UPLOAD_MB` | `500` | |
| `PDF2EPUB_MAX_PAGES` | `2000` | |
| `PDF2EPUB_JOB_TTL_HOURS` | `24` | Finished jobs are deleted after this |
| `PDF2EPUB_QUEUE` | `local` | `local`: jobs run inside the API process. `celery`: Celery workers + Valkey/Redis |
| `PDF2EPUB_REDIS_URL` | `redis://localhost:6379/0` | Celery broker, result backend and job state (Celery mode) |
| `PDF2EPUB_JOB_WORKERS` | `2` | Concurrent jobs (local mode) |
| `PDF2EPUB_PAGE_WORKERS` | CPU cores minus 1 | Parallel page processes (local mode); `0` processes pages inline |
| `PDF2EPUB_TESSERACT_CMD` | auto-detected | Path to the `tesseract` binary |
| `PDF2EPUB_OCR_LANG` | `eng` | Tesseract language codes, e.g. `eng+deu` (the language packs must be installed) |
| `PDF2EPUB_OCR_DPI` | `300` | Rasterization resolution for OCR |
| `PDF2EPUB_OCR_FLAG_CONFIDENCE` | `80` | Pages whose mean word confidence is below this are flagged for proofreading |
| `PDF2EPUB_EPUBCHECK` | `auto` | `auto`: validate when Java + EPUBCheck are present. `required`: fail jobs when they aren't. `off` |
| `PDF2EPUB_EPUBCHECK_JAR` | `backend/tools/epubcheck/epubcheck.jar` | |
| `PDF2EPUB_JAVA_CMD` | auto-detected | |
| `PDF2EPUB_UPLOAD_RATE_PER_MINUTE` | `10` | Uploads per client address per minute; `0` disables |
| `PDF2EPUB_MAX_ACTIVE_JOBS` | `20` | Jobs in flight across all clients before uploads get a 503; `0` disables |
| `PDF2EPUB_TRUST_PROXY` | `false` | Read the client address from `X-Forwarded-For` (Compose sets this; nginx fills the header) |
| `PDF2EPUB_AUTH_USER` / `PDF2EPUB_AUTH_PASSWORD` | unset | When both are set, `/api/*` requires HTTP Basic auth. Browsers prompt once and remember it |

## Public deployments

The stack is meant for your own machine, but if you expose it:

```bash
PDF2EPUB_AUTH_USER=me PDF2EPUB_AUTH_PASSWORD='a long passphrase' docker compose up -d
```

That puts a login on the API (the browser asks once), limits each address to 10 uploads a minute and caps in-flight jobs at 20. Put TLS in front (Caddy, Traefik, a cloud load balancer); the built-in nginx serves plain HTTP. To see how many workers you need:

```bash
python backend/scripts/load_test.py book.pdf --url http://localhost:8080 --jobs 8 --concurrency 4 --user me --password '...'
```
