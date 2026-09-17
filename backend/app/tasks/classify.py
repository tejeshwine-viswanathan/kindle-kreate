"""Per-page detection: does the page have a usable text layer, or does it need OCR?"""

import pymupdf

# A page needs OCR when its text covers less than this fraction of the page...
MIN_TEXT_COVERAGE = 0.05
# ...and an image covers most of it. Sparse born-digital pages (a lone "Chapter 1"
# title, a blank page) have low text coverage too but no page-sized image.
SCAN_IMAGE_COVERAGE = 0.5


def page_needs_ocr(page: pymupdf.Page) -> bool:
    page_area = page.rect.width * page.rect.height or 1.0

    text_area = 0.0
    for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)["blocks"]:
        for line in block["lines"]:
            for span in line["spans"]:
                if span["text"].strip():
                    x0, y0, x1, y1 = span["bbox"]
                    text_area += max(0.0, x1 - x0) * max(0.0, y1 - y0)

    if text_area / page_area >= MIN_TEXT_COVERAGE:
        return False

    largest_image = max(
        (pymupdf.Rect(info["bbox"]).get_area() for info in page.get_image_info()),
        default=0.0,
    )
    return largest_image / page_area >= SCAN_IMAGE_COVERAGE


def classify_document(pdf_path: str) -> list[bool]:
    """Returns one flag per page: True if that page needs OCR."""
    with pymupdf.open(pdf_path) as doc:
        return [page_needs_ocr(page) for page in doc]
