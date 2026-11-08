import docx
import pytest

from draftlock import chunking, cli, document, factlock, pipeline
from draftlock.llm import MockRewriter, RewriteError

TEXT = ("On 9 September 2026 I added English and Chinese switching to the Password Checker "
        "(commit f60e264). Our development document says the languages \"were added first at "
        "SAPOL's request\" (ThinkSecure Team, 2026, p. 7). This falls under NFR-04 and zh-TW support.")


# ---------- fact lock ----------

def test_extract_finds_core_facts():
    facts = factlock.extract(TEXT)
    for f in ["9", "September", "2026", "f60e264", "Password Checker",
              "(ThinkSecure Team, 2026, p. 7)", "NFR-04", "zh-TW",
              "\"were added first at SAPOL's request\""]:
        assert f in facts, f
    assert "04" not in facts and "English-only" not in facts


def test_check_flags_missing_and_added():
    bad = TEXT.replace("f60e264", "f60e265").replace("9 September", "10 September")
    fc = factlock.check(TEXT, bad)
    assert not fc.ok
    assert "f60e264" in fc.missing and "9" in fc.missing
    assert "f60e265" in fc.added and "10" in fc.added


def test_check_accepts_reordering():
    moved = ("I added English and Chinese switching to the Password Checker on 9 September 2026 "
             "(commit f60e264). The languages \"were added first at SAPOL's request\", according to "
             "our development document (ThinkSecure Team, 2026, p. 7). It sits under NFR-04 and zh-TW support.")
    assert factlock.check(TEXT, moved).ok


def test_locked_terms():
    fc = factlock.check("Tommy built it.", "He built it.", locked_terms=["Tommy"])
    assert "Tommy" in fc.missing


def test_chinese_facts():
    zh = "2026年9月9日，我在“密码检测器”中加入了中文，参考《开发文档》第7页。"
    facts = factlock.extract(zh)
    assert {"2026", "9", "7", "“密码检测器”", "《开发文档》"} <= set(facts)


# ---------- chunking ----------

def test_chunking_keeps_all_text():
    para = " ".join(f"Sentence number {i} is here." for i in range(60))
    chunks = chunking.chunk_paragraph(para, 50)
    assert len(chunks) > 1
    assert " ".join(chunks) == para
    assert all(document.word_count(c) <= 50 for c in chunks)


# ---------- documents ----------

def make_docx(path):
    d = docx.Document()
    d.add_heading("Report title", 0)
    d.add_heading("Introduction", 1)
    p = d.add_paragraph("Moreover, " + TEXT)
    p.paragraph_format.first_line_indent = docx.shared.Inches(0.3)
    d.add_paragraph("Short line.")
    mixed = d.add_paragraph("This paragraph has ")
    mixed.add_run("bold words").bold = True
    mixed.add_run(" inside it, so it should be protected by default from any rewriting at all.")
    d.add_heading("References", 1)
    d.add_paragraph("Scamwatch. (n.d.). Help someone who's being scammed. National Anti-Scam Centre.")
    d.save(path)
    return path


def test_protection_rules(tmp_path):
    draft = document.load(make_docx(tmp_path / "a.docx"))
    document.mark_protected(draft, min_words=12, protect_patterns=[])
    status = {p.text[:12]: (p.protected, p.reason) for p in draft.paragraphs}
    assert status["Report title"][0] and status["Introduction"][0]
    assert not status["Moreover, On"][0]
    assert status["Short line."] == (True, "short (< 12 words)")
    assert status["This paragra"][1].startswith("mixed formatting")
    assert status["Scamwatch. ("] == (True, "reference list")


def test_docx_roundtrip_keeps_formatting(tmp_path):
    src = make_docx(tmp_path / "a.docx")
    draft = document.load(src)
    document.mark_protected(draft, min_words=12, protect_patterns=[])
    pipeline.run(draft, MockRewriter(), pipeline.Options(workers=1))
    out = document.save(draft, tmp_path / "out.docx")
    d = docx.Document(out)
    target = [p for p in d.paragraphs if "Password Checker" in p.text][0]
    assert not target.text.startswith("Moreover")          # the mock removed it
    assert target.paragraph_format.first_line_indent == docx.shared.Inches(0.3)
    assert d.paragraphs[0].style.name == "Title"
    assert [p.text for p in d.paragraphs if "bold words" in p.text][0].startswith("This paragraph has")


def test_bad_model_is_reverted(tmp_path):
    draft = document.load(make_docx(tmp_path / "a.docx"))
    document.mark_protected(draft, min_words=12, protect_patterns=[])
    pipeline.run(draft, MockRewriter(drop_facts=True), pipeline.Options(workers=1))
    p = [p for p in draft.paragraphs if "Password Checker" in p.text][0]
    assert p.status == "reverted" and p.rewritten is None
    assert any("2026" in n for n in p.notes)


def test_txt_and_markdown(tmp_path):
    md = tmp_path / "a.md"
    md.write_text("# Title\n\n" + "Moreover, " + TEXT + "\n\n- a list item that is long enough to "
                  "otherwise be rewritten by the tool here\n", encoding="utf-8")
    draft = document.load(md)
    document.mark_protected(draft, min_words=12, protect_patterns=[])
    assert [p.protected for p in draft.paragraphs] == [True, False, True]


# ---------- output cleaning and sanity ----------

def test_clean_output():
    assert pipeline.clean_output("Here is the rewritten paragraph:\n\nHello there.") == "Hello there."
    assert pipeline.clean_output("<paragraph>\nHi.\n</paragraph>") == "Hi."


def test_sanity_rejects_length_blowup():
    original = " ".join(["word"] * 40)
    assert pipeline.sanity_problem(original, original + " " + original)
    assert not pipeline.sanity_problem(original, original)


def test_retry_then_success():
    calls = []

    class Flaky:
        def rewrite(self, system, user):
            calls.append(user)
            return "Something else entirely about 1999." if len(calls) == 1 else "I did it in 2026."

    out, notes = pipeline.rewrite_once(Flaky(), "sys", "I did it in 2026.", "", "", [])
    assert out == "I did it in 2026." and "fixed on retry" in notes
    assert "previous attempt had a problem" in calls[1]


def test_retry_then_give_up():
    class Bad:
        def rewrite(self, system, user):
            return "No numbers here."

    with pytest.raises(RewriteError):
        pipeline.rewrite_once(Bad(), "sys", "Founded in 2026.", "", "", [])


# ---------- CLI ----------

def test_cli_dry_run_and_mock(tmp_path, capsys):
    src = make_docx(tmp_path / "a.docx")
    assert cli.main([str(src), "--dry-run"]) == 0
    assert "locked:" in capsys.readouterr().out
    assert cli.main([str(src), "--mock", "--workers", "2"]) == 0
    assert (tmp_path / "a.draftlock.docx").exists()
    assert (tmp_path / "a.report.html").exists() and (tmp_path / "a.report.json").exists()


def test_parse_ranges():
    assert cli.parse_ranges("4,9-11") == {4, 9, 10, 11}
    assert cli.parse_ranges(None) is None


def test_multiword_names_with_single_letters():
    facts = factlock.extract("We used Have I Been Pwned for the breach check.")
    assert "Have I Been Pwned" in facts
