"""Load a draft (.docx / .txt / .md), decide which paragraphs may be rewritten,
and write the result back without disturbing the rest of the file."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import docx
from docx.oxml.ns import qn

REFERENCE_HEADINGS = re.compile(
    r"^\s*(references?|reference list|bibliography|works cited|sources|参考文献|引用文献|参考资料)\s*:?\s*$",
    re.IGNORECASE,
)
CJK = re.compile(r"[㐀-鿿豈-﫿]")

# XML elements whose presence means a paragraph holds more than plain text.
# Rewriting such a paragraph would destroy links, fields, images or notes.
RICH_TAGS = ("w:hyperlink", "w:fldSimple", "w:instrText", "w:drawing", "w:pict",
             "w:footnoteReference", "w:endnoteReference", "w:commentReference", "m:oMath")


@dataclass
class Paragraph:
    index: int                 # position among the document's paragraphs
    text: str
    style: str = ""
    protected: bool = False
    reason: str = ""           # why it is protected
    rewritten: str | None = None
    status: str = "unchanged"  # unchanged | protected | rewritten | reverted | failed
    notes: list[str] = field(default_factory=list)

    @property
    def final_text(self) -> str:
        return self.rewritten if self.rewritten is not None else self.text


@dataclass
class Draft:
    path: Path
    kind: str                  # "docx" or "text"
    paragraphs: list[Paragraph]
    _doc: object = None        # python-docx Document for write-back

    @property
    def language(self) -> str:
        body = "".join(p.text for p in self.paragraphs)
        if not body:
            return "en"
        cjk = len(CJK.findall(body))
        return "zh" if cjk / max(len(body), 1) > 0.15 else "en"


def word_count(text: str) -> int:
    """Words for Latin text; characters count as words for CJK text."""
    return len(CJK.findall(text)) + len(re.findall(r"[A-Za-z0-9][\w'’-]*", CJK.sub(" ", text)))


def load(path: str | Path) -> Draft:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        d = docx.Document(str(path))
        paras = []
        for i, p in enumerate(d.paragraphs):
            para = Paragraph(index=i, text=p.text, style=p.style.name if p.style is not None else "")
            xml = p._p.xml
            for tag in RICH_TAGS:
                if f"<{tag}" in xml:
                    para.protected, para.reason = True, "contains links, fields or objects"
                    break
            if not para.protected and _mixed_formatting(p):
                para.protected, para.reason = True, "mixed formatting (bold/italic inside)"
            paras.append(para)
        return Draft(path, "docx", paras, d)
    if suffix in {".txt", ".md", ".markdown", ""}:
        raw = path.read_text(encoding="utf-8")
        blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", raw)]
        paras = [Paragraph(index=i, text=b) for i, b in enumerate(blocks) if b.strip()]
        for n, p in enumerate(paras):
            p.index = n
            if suffix in {".md", ".markdown"} and re.match(r"^\s*(#|```|>|\||[-*+] |\d+\. )", p.text):
                p.style = "Heading" if p.text.lstrip().startswith("#") else "Markdown"
        return Draft(path, "text", paras)
    raise ValueError(f"Unsupported file type: {path.suffix} (use .docx, .txt or .md)")


def _mixed_formatting(p) -> bool:
    runs = [r for r in p.runs if r.text.strip()]
    if len(runs) < 2:
        return False
    first = (runs[0].bold, runs[0].italic, runs[0].underline)
    return any((r.bold, r.italic, r.underline) != first for r in runs[1:])


def mark_protected(draft: Draft, *, min_words: int, protect_patterns: list[str],
                   only: set[int] | None = None, rewrite_rich: bool = False) -> None:
    """Decide which paragraphs stay untouched. Rules, in order:
    headings/titles, the reference section, user patterns, short lines, --only."""
    regexes = [re.compile(p) for p in protect_patterns]
    in_refs = False
    for p in draft.paragraphs:
        style = (p.style or "").lower()
        is_heading = style.startswith(("heading", "title", "subtitle", "toc")) or style == "heading"
        if is_heading or (draft.kind == "text" and REFERENCE_HEADINGS.match(p.text)):
            in_refs = bool(REFERENCE_HEADINGS.match(p.text.lstrip("# ")))
            p.protected, p.reason = True, "heading"
            continue
        if p.protected and not rewrite_rich:
            continue
        if p.protected and rewrite_rich:
            p.protected, p.reason = False, ""
        if in_refs:
            p.protected, p.reason = True, "reference list"
        elif not p.text.strip():
            p.protected, p.reason = True, "empty"
        elif style == "markdown":
            p.protected, p.reason = True, "markdown block (list, table, code or quote)"
        elif any(r.search(p.text) for r in regexes):
            p.protected, p.reason = True, "matches --protect"
        elif word_count(p.text) < min_words:
            p.protected, p.reason = True, f"short (< {min_words} words)"
        elif only is not None and p.index not in only:
            p.protected, p.reason = True, "not in --only"
    for p in draft.paragraphs:
        if p.protected:
            p.status = "protected"


def save(draft: Draft, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    if draft.kind == "docx":
        d = draft._doc
        for p, para in zip(d.paragraphs, draft.paragraphs):
            if para.rewritten is not None and para.rewritten != para.text:
                _replace_paragraph_text(p, para.rewritten)
        d.save(str(out_path))
    else:
        out_path.write_text("\n\n".join(p.final_text for p in draft.paragraphs) + "\n", encoding="utf-8")
    return out_path


def _replace_paragraph_text(p, text: str) -> None:
    """Put new text into the paragraph, keeping the first run's formatting and
    the paragraph's own properties (style, spacing, indentation)."""
    runs = p.runs
    if not runs:
        p.add_run(text)
        return
    first = runs[0]
    for r in runs[1:]:
        r._r.getparent().remove(r._r)
    # A run can hold tabs/breaks; clear it fully before setting text.
    for child in list(first._r):
        if child.tag != qn("w:rPr"):
            first._r.remove(child)
    first.text = text
