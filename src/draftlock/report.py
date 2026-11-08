"""Side-by-side diff report (HTML) and a machine-readable JSON log."""

from __future__ import annotations

import difflib
import html
import json
import re
from datetime import datetime
from pathlib import Path

from . import factlock
from .document import Draft

_TOKEN = re.compile(r"[㐀-鿿]|\w+|[^\w\s]|\s+")

STATUS_LABEL = {
    "rewritten": ("Rewritten", "ok"),
    "unchanged": ("Unchanged", "muted"),
    "protected": ("Protected", "muted"),
    "reverted": ("Kept original", "warn"),
    "failed": ("Failed", "warn"),
}


def word_diff(a: str, b: str) -> str:
    ta, tb = _TOKEN.findall(a), _TOKEN.findall(b)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape("".join(ta[i1:i2])))
        if op in ("delete", "replace"):
            out.append(f"<del>{html.escape(''.join(ta[i1:i2]))}</del>")
        if op in ("insert", "replace"):
            out.append(f"<ins>{html.escape(''.join(tb[j1:j2]))}</ins>")
    return "".join(out)


def summary(draft: Draft) -> dict:
    counts: dict[str, int] = {}
    for p in draft.paragraphs:
        counts[p.status] = counts.get(p.status, 0) + 1
    return counts


def write_json(draft: Draft, path: Path, meta: dict) -> Path:
    data = {
        "source": str(draft.path), "created": datetime.now().isoformat(timespec="seconds"),
        **meta, "summary": summary(draft),
        "paragraphs": [{
            "index": p.index, "status": p.status, "reason": p.reason, "notes": p.notes,
            "original": p.text, "rewritten": p.rewritten,
        } for p in draft.paragraphs],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_html(draft: Draft, path: Path, meta: dict, locked: list[str]) -> Path:
    counts = summary(draft)
    rows = []
    for p in draft.paragraphs:
        if not p.text.strip():
            continue
        label, tone = STATUS_LABEL.get(p.status, (p.status, "muted"))
        reason = f" · {html.escape(p.reason)}" if p.reason else ""
        notes = "".join(f"<li>{html.escape(n)}</li>" for n in p.notes)
        if p.status == "rewritten":
            body = (f'<div class="cols"><div><h4>Original</h4><p>{html.escape(p.text)}</p></div>'
                    f'<div><h4>Changes</h4><p>{word_diff(p.text, p.rewritten or "")}</p></div></div>')
            facts = factlock.extract(p.text, locked)
            if facts:
                body += ('<p class="facts">Locked and kept: '
                         + ", ".join(f"<code>{html.escape(f)}</code>" for f in facts) + "</p>")
        else:
            body = f'<p class="plain">{html.escape(p.text)}</p>'
        rows.append(
            f'<section class="para {tone}" id="p{p.index}"><header><span class="idx">¶{p.index}</span>'
            f'<span class="tag {tone}">{label}</span><span class="why">{reason}</span></header>'
            f'{body}{"<ul class=notes>" + notes + "</ul>" if notes else ""}</section>')

    stat = " · ".join(f"{STATUS_LABEL.get(k, (k,))[0]}: {v}" for k, v in sorted(counts.items()))
    meta_line = " · ".join(f"{k}: {html.escape(str(v))}" for k, v in meta.items() if v not in (None, ""))
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>draftlock report</title>
<style>
:root {{ --bg:#fbfaf7; --fg:#1d1d1b; --muted:#6b6b66; --line:#e4e1d8; --card:#fff;
        --ins:#d9f2df; --del:#fbe0dc; --ok:#1f7a3d; --warn:#a4570f; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161614; --fg:#ecebe6; --muted:#a09f99; --line:#33322e;
        --card:#1f1e1b; --ins:#1f4a2c; --del:#5a2620; --ok:#7fd49a; --warn:#f0b06a; }} }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:22px; margin:0 0 4px; }} .sub {{ color:var(--muted); font-size:14px; margin-bottom:20px; }}
.para {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 16px; margin:12px 0; }}
.para.muted {{ opacity:.75; }}
header {{ display:flex; gap:10px; align-items:baseline; font-size:13px; color:var(--muted); }}
.idx {{ font-variant-numeric:tabular-nums; }} .tag {{ font-weight:600; }} .tag.ok {{ color:var(--ok); }} .tag.warn {{ color:var(--warn); }}
.cols {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
@media (max-width:720px) {{ .cols {{ grid-template-columns:1fr; }} }}
h4 {{ margin:8px 0 4px; font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
p {{ margin:4px 0; }} ins {{ background:var(--ins); text-decoration:none; }} del {{ background:var(--del); }}
.facts {{ font-size:13px; color:var(--muted); }} code {{ font-size:12px; }}
.notes {{ font-size:13px; color:var(--warn); margin:6px 0 0; padding-left:18px; }}
</style></head><body><main>
<h1>draftlock report: {html.escape(draft.path.name)}</h1>
<div class="sub">{stat}<br>{meta_line}</div>
{''.join(rows)}
</main></body></html>"""
    path.write_text(page, encoding="utf-8")
    return path
