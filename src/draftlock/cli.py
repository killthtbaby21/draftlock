"""Command-line interface: `draftlock INPUT [options]`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, document, factlock, report
from .document import word_count
from .llm import DEFAULT_MODEL, ClaudeRewriter, FatalError, MockRewriter
from .pipeline import Options, run


def parse_ranges(spec: str | None) -> set[int] | None:
    """'4,9-12' -> {4, 9, 10, 11, 12}"""
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="draftlock",
        description="Rewrite your own draft paragraph by paragraph with Claude. Facts are locked, "
                    "headings and references are left alone, and every change is shown in a report.")
    ap.add_argument("input", help=".docx, .txt or .md file")
    ap.add_argument("-o", "--output", help="output file (default: <name>.draftlock.<ext>)")
    ap.add_argument("--lang", choices=["auto", "en", "zh"], default="auto",
                    help="language of the draft (default: detect)")
    ap.add_argument("--rounds", type=int, help="number of rewrite rounds (default: en 1, zh 2)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model id (default: {DEFAULT_MODEL})")
    ap.add_argument("--effort", choices=["low", "medium", "high"], default="medium",
                    help="how hard the model thinks per paragraph (default: medium)")
    ap.add_argument("--workers", type=int, default=4, help="paragraphs processed in parallel (default: 4)")
    ap.add_argument("--min-words", type=int, default=12,
                    help="skip paragraphs shorter than this (default: 12; CJK characters count as words)")
    ap.add_argument("--chunk-limit", type=int, default=300,
                    help="split paragraphs longer than this many words (default: 300)")
    ap.add_argument("--lock", action="append", default=[], metavar="TERM",
                    help="extra term that must survive exactly (repeatable), e.g. --lock 'Password Checker'")
    ap.add_argument("--lock-file", help="file with one locked term per line")
    ap.add_argument("--style", action="append", default=[], metavar="FILE",
                    help="sample of your own earlier writing to imitate (repeatable; .txt/.md/.docx)")
    ap.add_argument("--protect", action="append", default=[], metavar="REGEX",
                    help="never rewrite paragraphs matching this regex (repeatable)")
    ap.add_argument("--only", metavar="LIST",
                    help="rewrite only these paragraph numbers, e.g. 4,9-12 (numbers from the report)")
    ap.add_argument("--rewrite-rich", action="store_true",
                    help="also rewrite paragraphs with links/fields/mixed formatting (they lose that formatting)")
    ap.add_argument("--dry-run", action="store_true",
                    help="no API calls: show what would be rewritten and which facts are locked")
    ap.add_argument("--mock", action="store_true", help="use an offline mock model (for testing)")
    ap.add_argument("--no-fallbacks", action="store_true",
                    help="do not let the API retry declined requests on a fallback model")
    ap.add_argument("--report", help="report path (default: <name>.report.html)")
    ap.add_argument("--version", action="version", version=f"draftlock {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    src = Path(args.input)
    if not src.exists():
        print(f"draftlock: file not found: {src}", file=sys.stderr)
        return 2

    try:
        draft = document.load(src)
    except ValueError as e:
        print(f"draftlock: {e}", file=sys.stderr)
        return 2

    lang = draft.language if args.lang == "auto" else args.lang
    locked = list(args.lock)
    if args.lock_file:
        locked += [l.strip() for l in Path(args.lock_file).read_text(encoding="utf-8").splitlines() if l.strip()]
    styles = []
    for f in args.style:
        sd = document.load(f)
        styles.append("\n\n".join(p.text for p in sd.paragraphs if p.text.strip()))

    document.mark_protected(draft, min_words=args.min_words, protect_patterns=args.protect,
                            only=parse_ranges(args.only), rewrite_rich=args.rewrite_rich)
    todo = [p for p in draft.paragraphs if not p.protected]
    words = sum(word_count(p.text) for p in todo)
    print(f"{src.name}: {len(draft.paragraphs)} paragraphs, {len(todo)} to rewrite ({words} words), "
          f"language={lang}")

    ext = ".docx" if draft.kind == "docx" else src.suffix
    out = Path(args.output) if args.output else src.with_name(src.stem + ".draftlock" + ext)
    rep = Path(args.report) if args.report else src.with_name(src.stem + ".report.html")

    if args.dry_run:
        for p in draft.paragraphs:
            if not p.text.strip():
                continue
            mark = "  rewrite " if not p.protected else f"  skip    ({p.reason})"
            print(f"¶{p.index:<3}{mark}  {p.text[:60]!r}")
            if not p.protected:
                facts = factlock.extract(p.text, locked)
                if facts:
                    print("          locked: " + ", ".join(facts))
        report.write_html(draft, rep, {"mode": "dry run", "language": lang}, locked)
        print(f"\nDry run only. Report: {rep}")
        return 0

    rewriter = MockRewriter() if args.mock else ClaudeRewriter(
        model=args.model, effort=args.effort, fallbacks=not args.no_fallbacks)
    opts = Options(lang=lang, rounds=args.rounds, locked_terms=locked, style_samples=styles,
                   workers=max(1, args.workers), chunk_limit=args.chunk_limit)

    done = 0

    def progress(p):
        nonlocal done
        done += 1
        print(f"  [{done}/{len(todo)}] ¶{p.index} {p.status}", flush=True)

    try:
        run(draft, rewriter, opts, progress)
    except FatalError as e:
        print(f"draftlock: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted; nothing was written.", file=sys.stderr)
        return 130

    document.save(draft, out)
    meta = {"model": rewriter.model, "language": lang,
            "rounds": opts.rounds or (2 if lang == "zh" else 1), "requests": rewriter.usage.requests}
    cost = rewriter.usage.cost(getattr(rewriter, "model", ""))
    if cost is not None:
        meta["estimated cost (USD)"] = f"{cost:.3f}"
    report.write_html(draft, rep, meta, locked)
    report.write_json(draft, rep.with_suffix(".json"), meta)

    counts = report.summary(draft)
    print(f"\nWrote {out}\nReport {rep}")
    print("  " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    if counts.get("reverted"):
        print("  Some paragraphs kept their original text; the report explains why.")
    if cost is not None:
        print(f"  ~{rewriter.usage.input_tokens + rewriter.usage.cache_read_tokens} input / "
              f"{rewriter.usage.output_tokens} output tokens, about ${cost:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
