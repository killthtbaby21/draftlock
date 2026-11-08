"""Split an over-long paragraph into sentence groups that each fit one request."""

from __future__ import annotations

import re

from .document import word_count

# Sentence ends: Latin . ! ? followed by space + capital/quote/digit, or CJK 。！？；
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"“'‘(A-Z0-9])|(?<=[。！？；])")


def split_sentences(text: str) -> list[str]:
    parts = [s for s in _SENT_SPLIT.split(text) if s and s.strip()]
    return parts or [text]


def chunk_paragraph(text: str, limit: int) -> list[str]:
    """Return the paragraph unchanged if it fits, otherwise consecutive groups of
    whole sentences, each at most `limit` words (a single longer sentence stays whole)."""
    if word_count(text) <= limit:
        return [text]
    chunks, current = [], []
    for sent in split_sentences(text):
        candidate = current + [sent]
        if current and word_count(_join(candidate)) > limit:
            chunks.append(_join(current))
            current = [sent]
        else:
            current = candidate
    if current:
        chunks.append(_join(current))
    return chunks


def _join(sentences: list[str]) -> str:
    out = ""
    for s in sentences:
        s = s.strip()
        if not out:
            out = s
        elif re.search(r"[㐀-鿿。！？；]$", out):
            out += s
        else:
            out += " " + s
    return out
