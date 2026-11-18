"""Text from HTML attachments (bidsla, showCatalogue) and office files (xlsx bills of quantities, docx RFPs)."""
from __future__ import annotations

import io
import logging
import zipfile
from html.parser import HTMLParser
from typing import Any

from gem_agent.tools.pdf.text import PageText

log = logging.getLogger(__name__)

_SKIP_TAGS = {"script", "style", "noscript", "head", "template"}
_BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"}


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self.skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def _tidy(text: str) -> str:
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def html_text(content: bytes) -> list[PageText]:
    parser = _TextParser()
    parser.feed(content.decode("utf-8", errors="replace"))
    return [PageText(page=1, text=_tidy("".join(parser.parts)), method="text")]


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
PART_CHARS = 3000  # about a printed page: a docx without page marks is cut into parts this long


def _is_break(el: Any) -> bool:
    """Word's mark of where a page began when the file was last saved, or a hard page break."""
    return el.tag == _W + "lastRenderedPageBreak" or (el.tag == _W + "br" and el.get(_W + "type") == "page")


def _text_of(el: Any) -> str:
    out = []
    for node in el.iter():
        if node.tag == _W + "t":
            out.append(node.text or "")
        elif node.tag == _W + "tab":
            out.append(" ")
        elif node.tag in (_W + "br", _W + "cr") and not _is_break(node):
            out.append("\n")
        elif node.tag == _W + "p" and out:
            out.append(" ")
    return "".join(out)


def _docx_pages(body: Any) -> list[str]:
    """The body in order (paragraphs and tables as written), split where Word's pages began."""
    pages: list[list[str]] = [[]]

    def new_page() -> None:
        if any(line.strip() for line in pages[-1]):
            pages.append([])

    def blocks(parent: Any) -> Any:  # content controls (a table of contents) hold paragraphs and tables of their own
        for el in parent:
            if el.tag == _W + "sdt":
                content = el.find(_W + "sdtContent")
                if content is not None:
                    yield from blocks(content)
            else:
                yield el

    for block in blocks(body):
        if block.tag == _W + "p":
            line: list[str] = []
            for node in block.iter():
                if _is_break(node):
                    pages[-1].append("".join(line))
                    line = []
                    new_page()
                elif node.tag == _W + "t":
                    line.append(node.text or "")
                elif node.tag == _W + "tab":
                    line.append(" ")
                elif node.tag in (_W + "br", _W + "cr"):
                    line.append("\n")
            pages[-1].append("".join(line))
        elif block.tag == _W + "tbl":
            for row in block.findall(_W + "tr"):
                if any(_is_break(node) for node in row.iter()):  # every cell of a row on a new page carries a mark
                    new_page()
                cells = [" ".join(_text_of(cell).split()) for cell in row.findall(_W + "tc")]
                if any(cells):
                    pages[-1].append(" | ".join(cells))
    return [text for text in (_tidy("\n".join(page)) for page in pages) if text]


def _parts(text: str, max_chars: int = PART_CHARS) -> list[str]:
    """Text cut at line ends into parts of about max_chars."""
    parts, current = [], ""
    for line in text.splitlines():
        if current and len(current) + len(line) > max_chars:
            parts.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    return [*parts, current] if current else parts


def docx_text(content: bytes) -> list[PageText]:
    """One entry per page as Word last laid it out (its page marks), so a quote's page is the page a reader sees.
    A file saved without them is cut into parts of about a page."""
    import docx

    try:
        document = docx.Document(io.BytesIO(content))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        log.warning("unreadable docx: %s", exc)
        return []
    pages = _docx_pages(document.element.body)
    if len(pages) == 1 and len(pages[0]) > PART_CHARS * 1.2:
        pages = _parts(pages[0])
    return [PageText(page=n, text=text, method="text") for n, text in enumerate(pages, start=1)]


def xlsx_text(content: bytes) -> list[PageText]:
    """One page per sheet, cells joined row by row."""
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        log.warning("unreadable xlsx: %s", exc)
        return []
    pages = []
    for n, sheet in enumerate(workbook.worksheets, start=1):
        rows = (" | ".join(str(c) for c in row if c is not None) for row in sheet.iter_rows(values_only=True))
        pages.append(PageText(page=n, text=f"[{sheet.title}]\n" + "\n".join(r for r in rows if r), method="text"))
    workbook.close()
    return pages
