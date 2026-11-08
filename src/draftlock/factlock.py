"""Fact lock: things a rewrite must keep exactly.

We pull "facts" out of the original text - numbers, dates, IDs, citations,
URLs, quotations, acronyms and user-locked terms - and check that each one
still appears in the rewrite, and that the rewrite did not invent new ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MONTHS = ("January|February|March|April|May|June|July|August|September|October|"
          "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec")

PATTERNS: dict[str, re.Pattern] = {
    "url": re.compile(r"https?://[^\s<>\"'）)]+[^\s<>\"'）).,;:!?]"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    # In-text citations such as (Smith, 2021, p. 3) or (W3C, n.d.)
    "citation": re.compile(r"\((?=[^()]*(?:\b(?:19|20)\d{2}[a-z]?\b|n\.d\.))[^()]{2,120}\)"),
    # Quoted material must survive word for word.
    "quote": re.compile(r"\"[^\"]{3,300}\"|“[^”]{2,300}”|「[^」]{2,300}」|《[^》]{1,100}》"),
    # Commit hashes / hex IDs: 7-40 hex chars with at least one digit and one letter.
    "id": re.compile(r"(?<![A-Za-z0-9_])(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![A-Za-z0-9_])"),
    # Codes like NFR-04, ISO-27001, zh-TW, W3C, HIBP, SAPOL
    "code": re.compile(r"\b(?:[A-Z][A-Z0-9]{0,9}|[a-z]{1,4})-[A-Z0-9][A-Za-z0-9]{0,9}\b|\b[A-Z][A-Z0-9]{1,}[a-z]?\b"),
    # Words with a capital inside or digits: ThinkSecure, killthtbaby21, iPhone
    "name": re.compile(r"\b[a-z]+[A-Z]\w*\b|\b[A-Z][a-z]+[A-Z]\w*\b|\b[A-Za-z]+\d+\w*\b"),
    "month": re.compile(rf"\b(?:{MONTHS})\b"),
    # ASCII-only boundaries so "2026年" and "第7页" still yield 2026 and 7.
    "number": re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d(?:[\d,]*\d)?(?:\.\d+)?%?(?![A-Za-z0-9_])"),
    # Capitalised words that do not start a sentence: people, products, places.
    "proper": re.compile(r"(?<![.!?:]\s)(?<!^)(?<![\"“(]\s)(?<=\s)[A-Z][a-z]+(?:\s+(?:[A-Z][a-z]+|[AI](?=\s+[A-Z][a-z])))*"),
}

# Common all-caps words that are not facts.
_CODE_STOP = {"I", "A", "OK", "AM", "PM", "US", "UK", "TV", "AI"}
_PROPER_STOP = {"I", "Im", "The", "This", "That", "These", "Those", "It", "We", "They", "He", "She", "You"}


@dataclass
class FactCheck:
    missing: list[str]
    added: list[str]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.added

    def describe(self) -> str:
        parts = []
        if self.missing:
            parts.append("missing or changed: " + ", ".join(self.missing))
        if self.added:
            parts.append("new, not in the original: " + ", ".join(self.added))
        return "; ".join(parts)


def extract(text: str, locked_terms: list[str] | None = None) -> list[str]:
    """Return the distinct facts in `text`, in order of first appearance."""
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []   # spans already claimed by a broader pattern

    def overlaps(a: int, b: int) -> bool:
        return any(a < e and s < b for s, e in taken)

    for kind, pat in PATTERNS.items():
        for m in pat.finditer(text):
            s, e = m.span()
            value = m.group(0).strip()
            if kind == "code" and value in _CODE_STOP:
                continue
            if kind not in {"url", "email", "citation", "quote"} and overlaps(s, e):
                continue
            if kind == "proper" and (value in _PROPER_STOP or re.fullmatch(MONTHS, value)):
                continue
            if kind == "number" and value in {"-", "+"}:
                continue
            found.append((s, value))
            taken.append((s, e))
    for term in locked_terms or []:
        term = term.strip()
        if term:
            for m in re.finditer(re.escape(term), text):
                found.append((m.start(), term))
    seen, out = set(), []
    for _, v in sorted(found):
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _normalise(s: str) -> str:
    return (s.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
             .replace(" ", " "))


def check(original: str, rewrite: str, locked_terms: list[str] | None = None) -> FactCheck:
    before = extract(original, locked_terms)
    after = extract(rewrite, locked_terms)
    norm_rewrite = _normalise(rewrite)
    norm_before = {_normalise(f) for f in before}
    norm_original = _normalise(original)
    missing = [f for f in before if _normalise(f) not in norm_rewrite]
    # A fact counts as "added" only if it never appears anywhere in the original
    # text (a number moved out of a citation is fine; a new year or ID is not).
    added = [f for f in after if _normalise(f) not in norm_before and _normalise(f) not in norm_original]
    return FactCheck(missing=missing, added=added)
