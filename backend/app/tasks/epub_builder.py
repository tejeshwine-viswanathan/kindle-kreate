"""Normalized document -> EPUB 3 (with EPUB 2 NCX for older readers) via ebooklib."""

from __future__ import annotations

import html
import mimetypes
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ebooklib import epub

from .structure import Document, Element, Heading, Image, Notice, Paragraph, Table

# Keep each XHTML file small; large spine items make e-readers page slowly.
MAX_CHAPTER_CHARS = 100_000

CSS = """\
body { font-family: serif; line-height: 1.5; margin: 0 5%; }
h1, h2, h3 { font-family: sans-serif; line-height: 1.25; page-break-after: avoid; }
h1 { font-size: 1.6em; margin: 2em 0 1em; text-align: center; page-break-before: always; }
h2 { font-size: 1.3em; margin: 1.5em 0 0.75em; }
h3 { font-size: 1.1em; margin: 1.25em 0 0.5em; }
p { margin: 0; text-indent: 1.5em; text-align: justify; }
h1 + p, h2 + p, h3 + p, figure + p, p.notice + p { text-indent: 0; }
figure { margin: 1em 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; height: auto; }
table { border-collapse: collapse; margin: 1em auto; font-size: 0.9em; page-break-inside: avoid; }
th, td { border: 1px solid #999; padding: 0.2em 0.5em; text-align: left; vertical-align: top; }
th { font-weight: bold; background: #f0f0f0; }
table + p { text-indent: 0; }
p.notice { text-indent: 0; margin: 1em 0; font-style: italic; color: #777; text-align: center; }
"""


@dataclass
class _Chapter:
    title: str
    parts: list[list[str]] = field(default_factory=lambda: [[]])
    part_size: int = 0
    # level-2 headings shown nested in the TOC: (anchor id, title, part index)
    sections: list[tuple[str, str, int]] = field(default_factory=list)


def _render(el: Element, anchor: str | None = None) -> str:
    if isinstance(el, Heading):
        tag = f"h{min(el.level, 3)}"
        id_attr = f' id="{anchor}"' if anchor else ""
        return f"<{tag}{id_attr}>{html.escape(el.text)}</{tag}>"
    if isinstance(el, Paragraph):
        return f"<p>{html.escape(el.text)}</p>"
    if isinstance(el, Image):
        return f'<figure><img src="images/{html.escape(el.name)}" alt=""/></figure>'
    if isinstance(el, Table):
        header, *body = el.rows
        head = "".join(f"<th>{html.escape(cell)}</th>" for cell in header)
        rows = "".join("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in body)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
    return f'<p class="notice">{html.escape(el.text)}</p>'


def _chapters(doc: Document) -> list[_Chapter]:
    chapters = [_Chapter(title=doc.title)]
    for el in doc.elements:
        chapter = chapters[-1]
        if isinstance(el, Heading) and el.level == 1:
            if chapter.part_size:
                chapter = _Chapter(title=el.text)
                chapters.append(chapter)
            else:
                chapter.title = el.text  # a leading heading names the opening chapter
        elif chapter.part_size > MAX_CHAPTER_CHARS:
            chapter.parts.append([])
            chapter.part_size = 0

        anchor = None
        if isinstance(el, Heading) and el.level == 2:
            anchor = f"s{len(chapter.sections) + 1}"
            chapter.sections.append((anchor, el.text, len(chapter.parts) - 1))
        markup = _render(el, anchor)
        chapter.parts[-1].append(markup)
        chapter.part_size += len(markup)
    return [c for c in chapters if any(c.parts)]


def build_epub(doc: Document, image_dir: Path, output: Path, language: str = "en", author: str = "") -> None:
    book = epub.EpubBook()
    book.set_identifier(f"urn:uuid:{uuid.uuid4()}")
    book.set_title(doc.title)
    book.set_language(language)
    if author:
        book.add_author(author)

    css = epub.EpubItem(uid="style", file_name="style/main.css", media_type="text/css", content=CSS)
    book.add_item(css)

    used_images = {el.name for el in doc.elements if isinstance(el, Image)}
    for name in sorted(used_images):
        media_type = mimetypes.guess_type(name)[0] or "image/png"
        book.add_item(
            epub.EpubItem(
                uid=f"img-{Path(name).stem}",
                file_name=f"images/{name}",
                media_type=media_type,
                content=(image_dir / name).read_bytes(),
            )
        )

    toc = []
    spine: list = ["nav"]
    for number, chapter in enumerate(_chapters(doc), start=1):
        files = []
        for part_no, markup in enumerate(chapter.parts, start=1):
            suffix = "" if part_no == 1 else f"-{part_no}"
            item = epub.EpubHtml(
                title=chapter.title, file_name=f"ch{number:03d}{suffix}.xhtml", lang=language
            )
            item.content = f"<html><head><title>{html.escape(chapter.title)}</title></head><body>{''.join(markup)}</body></html>"
            item.add_item(css)
            book.add_item(item)
            spine.append(item)
            files.append(item.file_name)

        link = epub.Link(files[0], chapter.title, f"ch{number:03d}")
        if chapter.sections:
            children = [
                epub.Link(f"{files[part]}#{anchor}", title, f"ch{number:03d}-{anchor}")
                for anchor, title, part in chapter.sections
            ]
            toc.append((epub.Section(chapter.title, files[0]), children))
        else:
            toc.append(link)

    if len(spine) == 1:
        raise ValueError("No convertible content found in this PDF.")

    book.toc = toc
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine
    epub.write_epub(str(output), book)
