"""Rewrite every unprotected paragraph, round by round, with fact checks."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from importlib import resources
from typing import Callable

from . import factlock
from .chunking import chunk_paragraph
from .document import Draft, Paragraph, word_count
from .llm import FatalError, RewriteError

ROUND_PROMPTS = {"en": ["en.md"], "zh": ["zh_round1.md", "zh_round2.md"]}
CONTEXT_CHARS = 600


@dataclass
class Options:
    lang: str = "en"
    rounds: int | None = None          # None = default for the language
    locked_terms: list[str] = field(default_factory=list)
    style_samples: list[str] = field(default_factory=list)
    chunk_limit: int = 300
    workers: int = 4


def load_prompt(name: str) -> str:
    return resources.files("draftlock").joinpath("prompts", name).read_text(encoding="utf-8")


def build_system(prompt: str, style_samples: list[str]) -> str:
    if not style_samples:
        return prompt
    samples = "\n\n".join(f"<sample>\n{s.strip()}\n</sample>" for s in style_samples)
    return (prompt + "\n\nBelow are samples of the author's own earlier writing. Match their "
            "vocabulary level, sentence length and habits. Do not copy their content.\n"
            f"<style_samples>\n{samples}\n</style_samples>")


def build_user(text: str, before: str, after: str, facts: list[str], feedback: str = "") -> str:
    parts = []
    if before:
        parts.append(f"<context_before>\n{before[-CONTEXT_CHARS:]}\n</context_before>")
    if after:
        parts.append(f"<context_after>\n{after[:CONTEXT_CHARS]}\n</context_after>")
    parts.append(f"<paragraph>\n{text}\n</paragraph>")
    if facts:
        parts.append("<locked_facts>\n" + "\n".join(f"- {f}" for f in facts) + "\n</locked_facts>")
    instruction = ("Rewrite only the text inside <paragraph>. The context is there so the result "
                   "fits the surrounding text; do not rewrite it. Every item in <locked_facts> must "
                   "appear in your rewrite exactly as written. Output only the rewritten text.")
    if feedback:
        instruction += f"\nYour previous attempt had a problem: {feedback}. Fix it this time."
    parts.append(instruction)
    return "\n\n".join(parts)


_PREAMBLE = re.compile(r"^(here is|here's|sure|rewritten|revised|以下是|修改后)[^\n]*:\s*\n+", re.I)


def clean_output(text: str) -> str:
    text = text.strip()
    text = _PREAMBLE.sub("", text)
    m = re.fullmatch(r"<paragraph>\s*(.*?)\s*</paragraph>", text, re.S)
    if m:
        text = m.group(1)
    if len(text) > 2 and text[0] == text[-1] == '"' and text.count('"') == 2:
        text = text[1:-1]
    return text.strip()


def sanity_problem(original: str, rewrite: str) -> str:
    """Return a description of a structural problem, or '' if the output looks sane."""
    if not rewrite:
        return "the output was empty"
    a, b = word_count(original), word_count(rewrite)
    if a >= 20 and not (0.6 * a <= b <= 1.5 * a):
        return f"the length changed too much ({a} -> {b} words); stay within about 15 percent"
    if re.search(r"(^|\n)\s*(#{1,6} |\*\*|- \*\*|```)", rewrite) and not re.search(r"(#|\*\*|```)", original):
        return "Markdown formatting was added; output plain text only"
    if "\n\n" in rewrite and "\n\n" not in original:
        return "the paragraph was split into several paragraphs; keep it as one"
    return ""


def rewrite_once(rewriter, system: str, text: str, before: str, after: str,
                 locked: list[str]) -> tuple[str, list[str]]:
    """One round for one chunk, with a single retry. Returns (text, notes).
    If both attempts fail the checks, the input text is returned unchanged."""
    facts = factlock.extract(text, locked)
    notes: list[str] = []
    feedback = ""
    for attempt in (1, 2):
        out = clean_output(rewriter.rewrite(system, build_user(text, before, after, facts, feedback)))
        problem = sanity_problem(text, out)
        if not problem:
            fc = factlock.check(text, out, locked)
            problem = "" if fc.ok else fc.describe()
        if not problem:
            if attempt == 2:
                notes.append("fixed on retry")
            return out, notes
        feedback = problem
        notes.append(f"attempt {attempt}: {problem}")
    raise RewriteError("kept original after two attempts: " + notes[-1].split(": ", 1)[1])


def process_paragraph(p: Paragraph, rewriter, prompts: list[str], opts: Options,
                      before: str, after: str) -> None:
    chunks = chunk_paragraph(p.text, opts.chunk_limit)
    joiner = "" if opts.lang == "zh" else " "
    try:
        current = chunks
        for r, prompt in enumerate(prompts, 1):
            new_chunks = []
            for i, chunk in enumerate(current):
                prev = current[i - 1] if i else before
                nxt = current[i + 1] if i + 1 < len(current) else after
                out, notes = rewrite_once(rewriter, prompt, chunk, prev, nxt, opts.locked_terms)
                p.notes += [f"round {r}: {n}" for n in notes]
                new_chunks.append(out)
            current = new_chunks
        result = joiner.join(current)
        final = factlock.check(p.text, result, opts.locked_terms)
        if not final.ok:   # belt and braces across all rounds
            p.status, p.rewritten = "reverted", None
            p.notes.append("kept original: " + final.describe())
            return
        p.rewritten = result
        p.status = "rewritten" if result != p.text else "unchanged"
    except RewriteError as e:
        p.status, p.rewritten = "reverted", None
        p.notes.append(str(e))


def run(draft: Draft, rewriter, opts: Options,
        progress: Callable[[Paragraph], None] | None = None) -> None:
    names = ROUND_PROMPTS[opts.lang]
    if opts.rounds is not None:
        names = (names * opts.rounds)[:opts.rounds]
    prompts = [build_system(load_prompt(n), opts.style_samples) for n in names]

    paras = draft.paragraphs
    todo = [i for i, p in enumerate(paras) if not p.protected]

    def neighbour(i: int, step: int) -> str:
        j = i + step
        while 0 <= j < len(paras):
            if paras[j].text.strip():
                return paras[j].text
            j += step
        return ""

    def job(i: int) -> None:
        process_paragraph(paras[i], rewriter, prompts, opts, neighbour(i, -1), neighbour(i, 1))
        if progress:
            progress(paras[i])

    if opts.workers <= 1:
        for i in todo:
            job(i)
        return
    with ThreadPoolExecutor(max_workers=opts.workers) as pool:
        futures = [pool.submit(job, i) for i in todo]
        for f in futures:
            try:
                f.result()
            except FatalError:
                for g in futures:
                    g.cancel()
                raise
