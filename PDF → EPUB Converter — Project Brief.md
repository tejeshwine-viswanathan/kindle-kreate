# PDF → EPUB Converter — Project Brief

2026-09-17 · @Someone

An end-to-end web app: upload a PDF (including scanned image-only PDFs with no text layer), OCR it page-by-page, and generate a clean, accurate EPUB for download — optimized for speed and zero formatting issues.

## Goals & scope

**What it does:** user uploads a PDF (text-based or scanned/image-only), the system OCRs it page-by-page when needed, reconstructs clean semantic structure, and outputs a downloadable EPUB.

**Hard requirements**

- **Speed:** target < 5s/page for OCR on standard hardware (GPU optional), fully parallelized across pages; a 300-page scanned book should convert in well under 5 minutes.
- **Accuracy:** OCR word accuracy > 99% on clean scans (Tesseract 5 LSTM or a transformer OCR model); no dropped pages, no duplicated text, no garbled Unicode.
- **Formatting fidelity:** correct reading order (including multi-column layouts), preserved headings/chapter breaks, preserved paragraph boundaries, no mid-sentence line breaks, images retained and placed correctly, no stray OCR artifacts (page numbers, headers/footers leaking into body text).
- Must handle both PDF types transparently: PDFs with an embedded text layer (fast path, no OCR) and scanned image-only PDFs (OCR path) — auto-detected per document.

**Out of scope for v1**

- Editing/proofreading UI for OCR corrections (v2)
- Multi-language OCR beyond a configurable primary language set
- Table-heavy documents with complex nested tables (best-effort only)
- Handwriting recognition

## Architecture

```mermaid
flowchart LR
  A[Upload PDF] --> B[Detect: text layer?]
  B -- has text --> C[Fast extract: text + layout]
  B -- scanned/image --> D[Render pages to images]
  D --> E[Parallel OCR workers]
  E --> F[Layout & reading-order reconstruction]
  C --> F
  F --> G[Structure builder: headings, paragraphs, images]
  G --> H[EPUB packager]
  H --> I[Job store: status + file]
  I --> J[Download EPUB]
```

Upload hits the API, which creates an async job and returns a job id immediately (no blocking on long conversions). A worker pool picks up the job, splits it into per-page tasks, and processes pages in parallel (OCR is the bottleneck, so this is where concurrency matters most). The frontend polls job status and shows per-page progress, then offers the EPUB for download once packaging finishes.

## Tech stack

Every component below is free, open source, and runs entirely on your own machine — no paid APIs, no managed cloud services, nothing that requires an account or a bill.

| Layer | Choice | Why |
| --- | --- | --- |
| Frontend | React + Vite + TypeScript, Tailwind | Fast dev loop, simple drag-and-drop upload + progress UI |
| Backend API | Python, FastAPI | Async-native, great PDF/OCR ecosystem, typed request/response models |
| Job queue | Celery + Valkey (the fully open-source Redis fork) — or RQ for a simpler setup | Decouples upload from long-running OCR jobs; enables page-level parallelism |
| PDF parsing (text layer) | PyMuPDF (fitz) | Fastest Python PDF library; gives text + exact bounding boxes + embedded images |
| Scanned page detection | PyMuPDF: page has 0 text spans, or text spans cover <5% of page area | Cheap heuristic, run before choosing OCR path |
| Page rasterization | PyMuPDF `page.get_pixmap(dpi=300)` | 300 DPI is the accuracy/speed sweet spot for OCR |
| OCR engine | Tesseract 5 (LSTM) via `pytesseract`, with PaddleOCR or docTR as a swappable higher-accuracy backend | Tesseract is fast and free; PaddleOCR/docTR give better layout + accuracy for complex pages — pick per deployment budget |
| Layout/reading order | PyMuPDF's block/line geometry, or `layoutparser` for multi-column pages | Needed to avoid interleaved columns and out-of-order text |
| EPUB generation | `ebooklib` | Mature, gives full control over spine, TOC, CSS, and image embedding |
| Storage | Local disk only — uploads, intermediate page images, and output EPUBs all stay on your machine | No cloud dependency; workers and API share a local folder |
| Deployment | Docker Compose (API + worker + Valkey) — everything runs on your own machine with one command | No dependency on any paid or managed service |

Speed strategy: parallelize OCR across pages (process pool sized to CPU cores, or GPU batch inference if using a transformer OCR model), and skip OCR entirely on the fast path for text-layer PDFs.

## OCR & text extraction pipeline

Runs per-page, dispatched as independent Celery tasks so pages OCR in parallel rather than serially.

1. **Classify page.** For each page: does it have a real text layer (PyMuPDF text spans present)? If yes → fast-path extraction (text + bounding boxes, no OCR). If no → OCR path.
2. **Rasterize.** Render the page to a 300 DPI image. Deskew and binarize (OpenCV: grayscale, adaptive threshold, rotation correction via Hough transform) — this alone materially improves OCR accuracy on scanned books.
3. **Detect layout regions.** Segment the page into blocks (title, paragraph, image, table, header/footer) before OCR, using `layoutparser` (or Tesseract's own PSM 3 automatic page segmentation for simpler documents). This is what prevents multi-column text from interleaving.
4. **OCR each text region**, not the whole page as one blob — this preserves reading order and keeps headers/footers separable from body text.
5. **Strip running headers/footers.** Detect lines that repeat near-identically across many pages at the same vertical position (page numbers, book title in the margin) and drop them from body text.
6. **Reassemble reading order** using region positions (top-to-bottom, left-to-right within column groups) and merge line fragments into paragraphs (join lines that don't end in sentence-ending punctuation).
7. **Confidence flagging.** Tesseract/PaddleOCR return per-word confidence; pages or words below a threshold get flagged in job metadata (visible in the UI, even though inline correction is v2) rather than silently shipping garbled text.
8. **Emit a normalized per-page JSON** (paragraphs with style hints, images with position, detected heading candidates) — this is the common intermediate format both the text-layer path and the OCR path converge on, so the EPUB packager only ever deals with one schema.

Parallelism: pages are independent OCR units, so a worker pool (size = CPU core count, or a GPU batch queue if using a transformer model like docTR) processes many pages concurrently; a 300-page book across 8 workers at \~2-3s/page finishes in a few minutes rather than 10+.

## Backend API design

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/jobs` | POST | Upload a PDF (multipart), returns `{job_id, status: "queued"}` immediately |
| `/api/jobs/{job_id}` | GET | Poll job status: `queued \| classifying \| processing \| packaging \| done \| failed`, plus `{pages_total, pages_done}` for progress |
| `/api/jobs/{job_id}/download` | GET | Streams the finished EPUB once status is `done` |
| `/api/jobs/{job_id}` | DELETE | Cancel a running job and clean up its files |
| `/health` | GET | Liveness/readiness for deployment |

**Flow:** upload saves the PDF to storage and enqueues a `classify_and_split` Celery task. That task detects the page type per page and fans out one `process_page` task per page (a Celery `group`); a `chord` callback runs `assemble_epub` once every page task completes, updating job status as it goes. Job state lives in Valkey (or Postgres if you want durability across restarts) — both free and open source keyed by `job_id`, with a TTL so old files get garbage-collected.

**Error handling:** a single bad page (corrupt image, OCR crash) should not fail the whole job — catch per-page errors, mark that page as failed with a placeholder note in the EPUB, and surface a warning in job status rather than aborting. Validate uploads (file type, size limit, page count limit) before enqueueing.

## Frontend design

- **Upload screen:** drag-and-drop or file picker, PDF only, client-side size check before upload, shows filename + page count (read via `pdf-lib` or `pdf.js` client-side) before submitting.
- **Progress screen:** polls `/api/jobs/{id}` every 1-2s, shows a progress bar driven by `pages_done / pages_total`, current stage label ("Detecting layout…", "Running OCR (142/300 pages)…", "Packaging EPUB…"), and a cancel button.
- **Result screen:** on `done`, a Download EPUB button (hits the download endpoint directly, browser handles the file); on `failed`, a clear error message plus which pages (if any) had issues.
- **Tech:** React + Vite, plain `fetch` for polling (no need for websockets given short poll intervals — upgrade to SSE/websockets later if desired for finer-grained progress), Tailwind for styling, no client-side routing library needed for a single-flow app.

## EPUB generation & formatting fidelity

The packager consumes the normalized per-page JSON from the OCR/extraction stage (same schema for both paths) and builds the EPUB with `ebooklib`:

- **Heading detection → chapters/TOC.** Use font-size/boldness deltas (text-layer path) or relative text-height + isolation on the page (OCR path) to detect headings; large, isolated, larger-font lines become `<h1>`/`<h2>` and chapter boundaries in the EPUB spine + nav TOC.
- **Paragraph reconstruction.** Merge OCR line fragments into paragraphs using sentence-ending punctuation and indentation/line-spacing cues; never emit one `<p>` per raw line, which is the most common cause of "formatting issues" in naive PDF→EPUB tools.
- **Images.** Extract embedded images at native resolution (text-layer path) or crop detected image regions from the rendered page (OCR path), embed them in the EPUB package, and anchor them at their detected position in the flow (not all dumped at the end).
- **Tables.** Best-effort: reconstruct simple tables as HTML `<table>` from column-aligned OCR text; complex/nested tables fall back to a captioned image of that region rather than mangled text.
- **Cleanup pass before packaging:** strip page numbers and running headers/footers (already flagged in the OCR stage), fix common OCR ligature errors (rn→m, l→1 in numeric contexts) via a small correction dictionary, normalize whitespace and Unicode (NFC).
- **Validation.** Run every generated EPUB through `epubcheck` in CI and as a post-generation step; fail the job (with a clear error) rather than shipping an invalid EPUB.
- **Styling.** Ship one clean, readable default CSS (serif body font, sensible margins, styled headings) embedded in the EPUB — don't try to preserve the source PDF's exact visual styling, which is what causes most reflow/formatting breakage; aim for a clean reflowable e-book, not a pixel copy of the PDF.

## Repo structure & VS Code setup

```
pdf2epub/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, routes
│   │   ├── models.py            # Pydantic request/response models
│   │   ├── tasks/
│   │   │   ├── classify.py      # per-page text-layer vs scanned detection
│   │   │   ├── ocr.py           # rasterize + OCR + layout reconstruction
│   │   │   └── epub_builder.py  # normalized JSON -> EPUB
│   │   ├── storage.py           # local disk storage only
│   │   └── celery_app.py
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── components/ (Upload, ProgressBar, ResultCard)
│   │   ├── api.ts               # fetch wrappers for job endpoints
│   │   └── App.tsx
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml            # api + worker + redis, one command to run everything
├── .vscode/
│   ├── launch.json               # debug configs: FastAPI (uvicorn), Celery worker, Vite dev server
│   ├── extensions.json           # recommends Python, Pylance, ESLint, Prettier, Docker
│   └── settings.json             # format-on-save, Python interpreter path -> backend/.venv
└── README.md
```

**Getting started in VS Code:**

1. `git clone` the repo, open the folder in VS Code — it'll prompt to install the recommended extensions.
2. Backend: `python -m venv backend/.venv`, activate it, `pip install -r backend/requirements.txt` (includes `fastapi`, `uvicorn`, `celery`, `redis`, `pymupdf`, `pytesseract`, `ebooklib`, `opencv-python-headless`); install the Tesseract binary separately (`brew install tesseract` / `apt install tesseract-ocr`) since `pytesseract` only wraps it.
3. `docker compose up` valkey (or install Valkey locally) for the job queue — Valkey is a fully open-source, drop-in replacement for Redis, so redis-py works against it unchanged.
4. Frontend: `cd frontend && npm install`.
5. Use the `.vscode/launch.json` compound config to start FastAPI, the Celery worker, and the Vite dev server together with one "Run and Debug" click; set breakpoints in `tasks/ocr.py` to step through page processing.
6. `docker-compose up --build` runs the whole stack the way it'll run in production, for integration testing.

## Roadmap & milestones

| Milestone | Scope | Exit criteria |
| --- | --- | --- |
| M1 — Text-layer fast path | Upload → extract text-layer PDFs → basic EPUB (no OCR yet) | A clean, text-based PDF round-trips to a readable EPUB with correct chapters |
| M2 — OCR path (single-threaded) | Rasterize + Tesseract OCR + reading-order reconstruction for one scanned PDF at a time | A scanned book produces an EPUB with correct reading order and no garbled text |
| M3 — Parallel processing + job queue | Celery/Redis, per-page task fan-out, progress polling | A 300-page scanned book converts in well under 5 minutes |
| M4 — Formatting fidelity pass | Header/footer stripping, paragraph reconstruction, image placement, table handling, epubcheck validation | 10 diverse sample PDFs (novels, textbooks, scanned reports) all pass epubcheck with no visible formatting defects |
| M5 — Frontend polish + error handling | Progress UI, cancel, per-page error surfacing, upload validation | End-to-end flow works for a non-technical user with no console errors |
| M6 — Deployment | Docker Compose prod config, local storage volume sizing, basic auth/rate limiting if public-facing | Deployed and reachable, handles a realistic concurrent-upload load test |
