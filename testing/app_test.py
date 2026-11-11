"""Offline tests: no API keys, no network.

Run from the repository root:

    python -m pytest testing/app_test.py -v

Part 1 tests the checks in run_eval.py on small hand-made responses, so we
know the evaluation itself catches what it claims to catch.

Part 2 tests the backend (briefing_generator.generate_briefing) with Tavily
and Bifrost replaced by fakes. It is skipped if briefing_generator.py is not
in the repository root.
"""

import copy
import json
import os
import sys
import types
from pathlib import Path

import pytest

TESTING = Path(__file__).resolve().parent
# Repository with the backend for Part 2. Defaults to this checkout;
# MEETINGBRIEF_REPO points it at another one (run_eval.py --repo sets it).
ROOT = Path(os.environ.get("MEETINGBRIEF_REPO") or TESTING.parent).resolve()
sys.path.insert(0, str(TESTING))
sys.path.insert(0, str(ROOT))

import run_eval  # noqa: E402

MEETING = "2026-11-09T10:00:00+01:00"  # window: 2026-10-10 .. 2026-11-09


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_case(**extra):
    case = {
        "id": "unit",
        "request": {
            "entity_type": "company",
            "name": "Example Co",
            "details": "",
            "meeting_datetime": MEETING,
            "timezone": "Europe/Rome",
            "goal": "Discuss a partnership",
        },
        "expected_facts": [],
    }
    case.update(extra)
    return case


def make_result(**overrides):
    result = {
        "is_demo": False,
        "researched_at": "2026-11-08T09:00:00+01:00",
        "summary": "Example Co announced a new product.",
        "events": [
            {
                "title": "Product launch",
                "date": "2026-11-01",
                "description": "Example Co launched a product.",
                "source_ids": ["S1"],
                "discussion_point": "Ask about target customers.",
            }
        ],
        "questions": ["Which customers is the product for?"],
        "conversation_starters": ["I saw the launch news."],
        "gaps": [],
        "sources": [
            {
                "id": "S1",
                "title": "Example Co launches product",
                "url": "https://example.com/news",
                "excerpt": "Example Co launched a product on November 1.",
            }
        ],
    }
    result.update(overrides)
    return result


def with_event(**event_overrides):
    result = make_result()
    result["events"][0].update(event_overrides)
    return result


def failures(result, case=None):
    return run_eval.deterministic_checks(case or make_case(), result)[0]


def warnings(result, case=None):
    return run_eval.deterministic_checks(case or make_case(), result)[1]


def has(messages, fragment):
    return any(fragment in m for m in messages)


# ===========================================================================
# Part 1. Evaluation checks (run_eval.py)
# ===========================================================================

def test_valid_result_passes():
    assert failures(make_result()) == []
    assert warnings(make_result()) == []


def test_unknown_source_reference_fails_contract():
    result = with_event(source_ids=["S1", "S3"])
    assert has(failures(result), "validate_result")


def test_duplicate_source_ids_fail_contract():
    result = make_result()
    result["sources"].append(dict(result["sources"][0]))
    assert has(failures(result), "validate_result")


def test_missing_required_field_fails_contract():
    result = make_result()
    del result["gaps"]
    assert has(failures(result), "validate_result")


def test_demo_flag_fails():
    assert has(failures(make_result(is_demo=True)), "is_demo")


@pytest.mark.parametrize("day, inside", [
    ("2026-10-09", False),  # one day before the window
    ("2026-10-10", True),   # first day of the window
    ("2026-11-09", True),   # meeting day
    ("2026-11-10", False),  # after the meeting
])
def test_window_boundaries(day, inside):
    result = with_event(date=day)
    assert has(failures(result), "outside window") is not inside


@pytest.mark.parametrize("bad", ["2026/11/01", "01.11.2026", "2026-11-1",
                                 "November 1"])
def test_bad_date_format_fails(bad):
    assert has(failures(with_event(date=bad)), "YYYY-MM-DD")


def test_undated_event_is_warning_not_failure():
    result = with_event(date=None)
    assert failures(result) == []
    assert has(warnings(result), "without date")


def test_event_without_sources_fails():
    assert has(failures(with_event(source_ids=[])), "without sources")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://example.com",
                                 "example.com/news"])
def test_unsafe_source_url_fails(url):
    result = make_result()
    result["sources"][0]["url"] = url
    assert has(failures(result), "non-http(s)")


def test_null_source_url_is_allowed():
    result = make_result()
    result["sources"][0]["url"] = None
    assert failures(result) == []


def test_researched_at_without_offset_fails():
    result = make_result(researched_at="2026-11-08T09:00:00")
    assert has(failures(result), "no UTC offset")


def test_invalid_researched_at_fails():
    assert has(failures(make_result(researched_at="yesterday")),
               "invalid researched_at")


def test_missing_researched_at_is_warning():
    result = make_result()
    del result["researched_at"]
    assert failures(result) == []
    assert has(warnings(result), "researched_at not provided")


def test_must_not_contain_is_case_insensitive():
    case = make_case(must_not_contain=["OpenAI"])
    result = make_result(summary="He previously worked at OPENAI.")
    assert has(failures(result, case), "forbidden term")


def test_must_not_contain_checks_source_excerpts():
    case = make_case(must_not_contain=["ChatGPT"])
    result = make_result()
    result["sources"][0]["excerpt"] = "The creator of ChatGPT said..."
    assert has(failures(result, case), "forbidden term")


def test_expect_no_events_fails_on_invented_events():
    case = make_case(expect_no_events=True)
    assert has(failures(make_result(gaps=["x"]), case), "expected no events")


def test_expect_no_events_requires_gaps():
    case = make_case(expect_no_events=True)
    result = make_result(events=[], sources=[], gaps=[])
    assert has(failures(result, case), "no gaps explaining")


def test_expect_no_events_passes_on_honest_empty_answer():
    case = make_case(expect_no_events=True)
    result = make_result(events=[], sources=[],
                         gaps=["No sources found for this company."])
    assert failures(result, case) == []


def test_no_events_requires_gaps_in_every_case():
    result = make_result(events=[], sources=[], gaps=[])
    assert has(failures(result), "no gaps explaining")


def test_whitespace_only_gaps_do_not_count_as_explanation():
    result = make_result(events=[], sources=[], gaps=["   "])
    msgs = failures(result)
    assert has(msgs, "empty string in gaps")
    assert has(msgs, "no gaps explaining")


def test_empty_gap_string_fails():
    assert has(failures(make_result(gaps=["Real gap.", ""])),
               "empty string in gaps")


def test_duplicate_gaps_are_warned_ignoring_case_and_punctuation():
    result = make_result(gaps=["No Q3 results found.",
                               "no q3   results found"])
    assert failures(result) == []
    assert has(warnings(result), "duplicate gap")


def test_different_gaps_are_not_duplicates():
    result = make_result(gaps=["No Q3 results found.",
                               "No Q2 results found."])
    assert not has(warnings(result), "duplicate gap")


def test_dataset_rejects_gaps_key(tmp_path):
    case = make_case(gaps=["should be expected_gaps"])
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([case]), encoding="utf-8")
    with pytest.raises(SystemExit, match="expected_gaps"):
        run_eval.load_dataset(path)


def test_dataset_rejects_blank_expected_gap(tmp_path):
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([make_case(expected_gaps=[" "])]),
                    encoding="utf-8")
    with pytest.raises(SystemExit, match="expected_gaps"):
        run_eval.load_dataset(path)


def test_judge_scores_gaps():
    class FakeJudge(run_eval.Judge):
        def __init__(self):
            self.prompts = []

        def ask(self, prompt):
            self.prompts.append(prompt)
            if "Expected limitation" in prompt:
                covered = "ambiguous" in prompt.split("Expected limitation")[1]
                return {"verdict": "covered" if covered else "not_covered"}
            if "\"items\"" in prompt:
                return {"items": [
                    {"n": 1, "specific": True, "is_limitation": True},
                    {"n": 2, "specific": False, "is_limitation": True},
                    {"n": 3, "specific": True, "is_limitation": False},
                ]}
            return {"verdict": "covered", "supported": True, "score": 3}

    case = make_case(expected_gaps=["The name is ambiguous.",
                                    "No financial data found."])
    result = make_result(gaps=["Two people share this name.",
                               "Some information may be missing.",
                               "The company is not profitable."])
    judge = FakeJudge()
    out = run_eval.summarize_scores(run_eval.judge_case(judge, case, result))
    assert out["gap_recall"] == 0.5
    assert out["vague_gaps"] == 1
    assert out["non_limitation_gaps"] == 1
    assert any("Discuss a partnership" in p and "\"items\"" in p
               for p in judge.prompts), "gap quality must see the goal"


def test_dataset_cases_without_events_expect_gaps():
    """Every 'nothing to find' case should say which gap we expect."""
    for case in run_eval.load_dataset(TESTING / "dataset.json"):
        if case.get("expect_no_events"):
            assert case.get("expected_gaps"), case["id"]


def test_uncited_source_is_warning():
    result = make_result()
    result["sources"].append({"id": "S2", "title": "Other", "url": None,
                              "excerpt": "..."})
    assert failures(result) == []
    assert has(warnings(result), "never cited")


def test_source_published_outside_window_is_warning():
    result = make_result()
    result["sources"][0]["published_date"] = "2026-05-28T10:00:00+01:00"
    assert failures(result) == []
    assert has(warnings(result), "published outside window")


def test_weighted_recall():
    scores = {
        "facts": [
            {"kind": "recent", "importance": 1.0, "verdict": "covered"},
            {"kind": "recent", "importance": 0.5, "verdict": "not_covered"},
            {"kind": "background", "importance": 0.5,
             "verdict": "contradicted"},
        ],
        "events": [{"supported": True}, {"supported": False}],
        "goal": {"score": 4},
    }
    out = run_eval.summarize_scores(scores)
    assert out["recall_all"] == 0.5          # 1.0 / 2.0
    assert out["recall_recent"] == 0.67      # 1.0 / 1.5
    assert out["recall_background"] == 0.0
    assert out["contradicted"] == 1
    assert out["groundedness"] == 0.5
    assert out["goal_score"] == 4


def test_dataset_is_valid():
    cases = run_eval.load_dataset(TESTING / "dataset.json")
    assert cases, "dataset is empty"


@pytest.mark.parametrize("fact_date, kind", [
    (None, "background"),
    ("2026-10-09", "background"),   # the day before the window
    ("2026-10-10", "recent"),       # first day of the window
    ("2026-11-09", "recent"),       # the meeting day counts
    ("2026-11-10", "future"),       # after the meeting
])
def test_fact_kind_follows_the_meeting_window(fact_date, kind):
    assert run_eval.fact_kind(fact_date, {"meeting_datetime": MEETING}) == kind


def _write_dataset(tmp_path, cases):
    path = tmp_path / "ds.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    return path


def _raw_case(**extra):
    case = make_case()
    case.update(extra)
    return case


def test_meetings_expand_into_cases_with_their_own_kinds(tmp_path):
    raw = _raw_case(id="ceo", expected_facts=[
        {"fact": "Scandal", "date": "2026-11-08", "importance": "high"},
        {"fact": "Is the CEO"}])
    del raw["request"]["meeting_datetime"]
    raw["meetings"] = [
        {"datetime": "2026-10-20T10:00:00+00:00",
         "must_not_contain": ["scandal"]},
        "2026-11-12T10:00:00+00:00",
    ]
    before, after = run_eval.load_dataset(_write_dataset(tmp_path, [raw]))
    assert [before["id"], after["id"]] == ["ceo@2026-10-20", "ceo@2026-11-12"]
    assert before["base_id"] == after["base_id"] == "ceo"
    assert before["request"]["meeting_datetime"] == "2026-10-20T10:00:00+00:00"
    assert [f["kind"] for f in before["expected_facts"]] == [
        "future", "background"]
    assert [f["kind"] for f in after["expected_facts"]] == [
        "recent", "background"]
    assert before["must_not_contain"] == ["scandal"]
    assert "must_not_contain" not in after
    assert [f["importance"] for f in after["expected_facts"]] == [1.0, 0.6]
    assert after["expected_facts"][1]["importance_label"] == "medium"


def test_dataset_rejects_facts_older_than_the_window(tmp_path):
    old = {"fact": "Old news", "date": "2026-09-01"}
    path = _write_dataset(tmp_path, [_raw_case(expected_facts=[old])])
    with pytest.raises(SystemExit, match="69 days before the meeting"):
        run_eval.load_dataset(path)

    # Allowed when marked as background on purpose, or for one meeting.
    path = _write_dataset(tmp_path, [_raw_case(
        expected_facts=[{**old, "background": True}])])
    assert run_eval.load_dataset(path)[0]["expected_facts"][0]["kind"] == \
        "background"
    raw = _raw_case(id="late", expected_facts=[old])
    del raw["request"]["meeting_datetime"]
    raw["meetings"] = [{"datetime": MEETING, "allow_old_facts": True}]
    assert run_eval.load_dataset(_write_dataset(tmp_path, [raw]))


@pytest.mark.parametrize("fact, message", [
    ({"fact": "x", "kind": "recent"}, "remove 'kind'"),
    ({"fact": "x", "importance": 0.9}, "importance must be one of"),
    ({"fact": "x", "date": "09.11.2026"}, "YYYY-MM-DD"),
])
def test_dataset_rejects_bad_facts(tmp_path, fact, message):
    path = _write_dataset(tmp_path, [_raw_case(expected_facts=[fact])])
    with pytest.raises(SystemExit, match=message):
        run_eval.load_dataset(path)


def test_partial_match_counts_half_and_future_facts_are_leaks():
    facts = [
        {"fact": "A", "kind": "recent", "importance": 1.0,
         "verdict": "partial"},
        {"fact": "B", "kind": "recent", "importance": 1.0,
         "verdict": "covered"},
        {"fact": "C", "kind": "future", "importance": 1.0, "date": "2026-12-01",
         "verdict": "covered"},
        {"fact": "D", "kind": "future", "importance": 1.0,
         "verdict": "not_covered"},
    ]
    scores = {"facts": facts, "events": [], "goal": None,
              "expected_gaps": [{"gap": "g", "verdict": "partial"}]}
    out = run_eval.summarize_scores(scores)
    assert out["recall_all"] == 0.75      # future facts are not in recall
    assert out["future_leaks"] == 1
    assert out["gap_recall"] == 0.5
    leaks = run_eval.leak_failures(scores)
    assert leaks == ["mentions a fact from after the meeting (2026-12-01): C"]

    entry = {"id": "x", "run": 1, "failures": leaks, "warnings": [],
             "judge": scores, "scores": out}
    d = run_eval.describe_entry(entry)
    assert d["verdict"] == "FAIL"
    assert [label for label, _ in d["problems"]] == [
        "mentions a fact from after the meeting"]


def test_facts_prompt_is_lenient_about_wording():
    judge = ScriptedJudge(['{"items": [{"n": 1, "verdict": "partial"}]}'])
    verdicts = judge.facts_coverage("text", ["A"])
    assert verdicts[0]["verdict"] == "partial"
    assert "Judge meaning, not wording" in judge.prompts[0]
    assert "rounded or approximate numbers" in judge.prompts[0]


def test_model_output_example_matches_contract():
    data = json.loads((TESTING / "model_output_example.json")
                      .read_text(encoding="utf-8"))
    run_eval.validate_result(data)


# ---------------------------------------------------------------------------
# LLM judge: criteria parsing, usage accounting, Excel reports
# ---------------------------------------------------------------------------

class ScriptedJudge(run_eval.Judge):
    """Runs the real ask() with canned replies instead of an API."""

    model = "claude-opus-5-5"

    def __init__(self, replies, usage=None):
        self.replies = list(replies)
        self.usage = usage or {}
        self.prompts = []
        self.calls = []

    def _complete(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0), dict(self.usage)


def test_facts_coverage_maps_verdicts_by_number():
    reply = ("Here you go:\n" + json.dumps({"items": [
        {"n": 2, "verdict": "contradicted", "reason": "wrong year"},
        {"n": "1", "verdict": "covered"},
    ]}) + "\nDone.")
    judge = ScriptedJudge([reply])
    verdicts = judge.facts_coverage("text", ["A", "B", "C"])
    assert [v["verdict"] for v in verdicts] == [
        "covered", "contradicted", "not_covered"]
    assert verdicts[2].get("judge_missing")
    assert "3. C" in judge.prompts[0]


def test_judge_records_usage_per_criterion():
    judge = ScriptedJudge(['{"score": 4}', '{"verdict": "covered"}'],
                          usage={"input_tokens": 1000, "output_tokens": 200})
    judge.goal_relevance("goal", make_result())
    judge.gap_coverage(["gap"], "expected")
    assert [c["stage"] for c in judge.calls] == [
        "judge:goal", "judge:expected_gap"]
    assert judge.calls[0]["model"] == "claude-opus-5-5"
    assert judge.calls[0]["seconds"] >= 0
    total = run_eval.usage_totals(judge.calls)
    assert (total["input_tokens"], total["output_tokens"]) == (2000, 400)
    assert total["cost_usd"] == pytest.approx(0.016)  # 2000*4/1M + 400*20/1M


def test_cost_estimate():
    call = {"model": "claude-sonnet-5-5", "input_tokens": 1_000_000,
            "output_tokens": 1_000_000, "cache_read_tokens": 1_000_000,
            "cache_write_tokens": 1_000_000}
    assert run_eval.estimate_cost(call) == pytest.approx(2 + 10 + 0.2 + 2.5)
    assert run_eval.estimate_cost({"model": "unknown", "input_tokens": 5}) \
        is None


def test_token_counts_sum_fallback_iterations():
    ns = types.SimpleNamespace
    usage = ns(
        input_tokens=120, output_tokens=50,
        iterations=[
            ns(input_tokens=100, output_tokens=0, cache_read_input_tokens=0,
               cache_creation_input_tokens=0),
            ns(input_tokens=120, output_tokens=50, cache_read_input_tokens=10,
               cache_creation_input_tokens=0),
        ],
        output_tokens_details=ns(thinking_tokens=30))
    counts = run_eval.token_counts(usage)
    assert counts["input_tokens"] == 220
    assert counts["output_tokens"] == 50
    assert counts["cache_read_tokens"] == 10
    assert counts["thinking_tokens"] == 30


def test_summarize_new_criteria():
    scores = {
        "facts": [],
        "events": [
            {"supported": True, "date_ok": True, "about_entity": True,
             "in_window": True},
            {"supported": False, "date_ok": False, "about_entity": True,
             "in_window": "unknown"},
            {"supported": True, "date_ok": True, "about_entity": False,
             "in_window": False},
        ],
        "goal": None,
        "summary": {"supported": False, "unsupported_claims": ["x", "y"]},
        "review": {"duplicates": [[1, 3], [3, 1], [2, 3], [5, 9], "bad"],
                   "overall_score": 4},
    }
    out = run_eval.summarize_scores(scores)
    assert out["groundedness"] == 0.67
    assert out["date_accuracy"] == 0.67
    assert out["entity_precision"] == 0.67
    assert out["in_window_rate"] == 0.5     # 1 of 2 events with known timing
    assert out["in_window_unknown"] == 1
    assert out["summary_supported"] is False
    assert out["summary_unsupported"] == 2
    assert out["duplicate_events"] == 2     # events 1, 2, 3 are one group
    assert out["overall_score"] == 4


def test_judge_case_passes_request_to_event_check():
    replies = [
        '{"supported": true, "date_ok": true, "about_entity": true, '
        '"in_window": true}',                       # event
        '{"supported": true, "unsupported_claims": []}',  # summary
        '{"score": 4}',                             # goal
        '{"duplicates": [], "overall_score": 5}',   # review
    ]
    judge = ScriptedJudge(replies)
    out = run_eval.summarize_scores(
        run_eval.judge_case(judge, make_case(), make_result()))
    assert "Example Co" in judge.prompts[0]
    assert "2026-10-10 to 2026-11-09" in judge.prompts[0]
    assert out["date_accuracy"] == 1.0 and out["overall_score"] == 5
    assert [c["stage"] for c in judge.calls] == [
        "judge:event", "judge:summary", "judge:goal", "judge:review"]


def test_event_relevance_scores():
    events = [{"supported": True, "goal_relevance": 5},
              {"supported": True, "goal_relevance": 2},
              {"supported": True, "goal_relevance": True},   # not a score
              {"supported": True}]
    out = run_eval.summarize_scores({"facts": [], "events": events,
                                     "goal": None})
    assert out["event_relevance"] == 3.5
    assert out["relevant_event_rate"] == 0.5


def test_event_prompt_includes_goal():
    judge = ScriptedJudge(['{"supported": true, "goal_relevance": 4}'])
    judge.event_support(make_result()["events"][0], ["text"],
                        make_case()["request"])
    assert "Meeting goal: Discuss a partnership" in judge.prompts[0]
    assert "goal_relevance" in judge.prompts[0]


# ---------------------------------------------------------------------------
# --fetch-sources: excerpts and events checked against the real page
# ---------------------------------------------------------------------------

PAGE_HTML = (b"<html><head><title>t</title><script>var x = 'hidden';</script>"
             b"</head><body><nav>Menu</nav><p>Example Co launched a "
             b"product on November&nbsp;1, the company said.</p>"
             + b"<p>Filler paragraph about other things.</p>" * 20
             + b"</body></html>")


def test_html_to_text_drops_scripts_and_keeps_paragraphs():
    text = run_eval.html_to_text(PAGE_HTML.decode())
    assert "hidden" not in text and "title" not in text.split()
    assert "Example Co launched a product on November 1" in text


@pytest.mark.parametrize("excerpt, found", [
    ("Example Co launched a product on November 1.", True),
    ("EXAMPLE CO — launched a product, on November 1!", True),  # punctuation
    ("Example Co was acquired by a rival for two billion dollars.", False),
])
def test_excerpt_match(excerpt, found):
    text = run_eval.html_to_text(PAGE_HTML.decode())
    score, offset = run_eval.excerpt_match(excerpt, text)
    assert (score >= run_eval.EXCERPT_MATCH_MIN) is found
    assert (offset is not None) is found


def test_excerpt_match_short_and_empty():
    assert run_eval.excerpt_match("product", "a product here")[0] == 1.0
    assert run_eval.excerpt_match("", "text") == (None, None)


def _fake_download(monkeypatch, pages):
    calls = []

    def download(url):
        calls.append(url)
        if isinstance(pages[url], Exception):
            raise pages[url]
        return pages[url], "text/html", "utf-8"
    monkeypatch.setattr(run_eval, "_download", download)
    return calls


def test_verify_sources_checks_excerpts_and_caches(tmp_path, monkeypatch):
    calls = _fake_download(monkeypatch, {"https://example.com/news": PAGE_HTML})
    result = make_result()
    records, pages = run_eval.verify_sources(result, tmp_path)
    assert records[0]["status"] == "ok" and records[0]["excerpt_found"]
    assert "https://example.com/news" in pages
    assert run_eval.source_metrics(records) == {
        "sources_unreachable": 0, "excerpt_verified": 1.0}

    # Second run reads the cache; an invented excerpt is flagged.
    result["sources"][0]["excerpt"] = "Example Co filed for bankruptcy today."
    records, _ = run_eval.verify_sources(result, tmp_path)
    assert calls == ["https://example.com/news"] and records[0]["cached"]
    assert records[0]["excerpt_found"] is False
    assert has(run_eval.source_warnings(records), "excerpt not found")
    assert run_eval.source_metrics(records)["excerpt_verified"] == 0.0


def test_verify_sources_reports_unreachable_pages(tmp_path, monkeypatch):
    _fake_download(monkeypatch, {
        "https://example.com/a": OSError("403 Forbidden"),
        "https://example.com/b": b"<html><body>Subscribe</body></html>",
    })
    result = make_result(sources=[
        {"id": "S1", "title": "a", "url": "https://example.com/a",
         "excerpt": "x"},
        {"id": "S2", "title": "b", "url": "https://example.com/b",
         "excerpt": "x"},
        {"id": "S3", "title": "c", "url": None, "excerpt": "x"},
    ])
    records, pages = run_eval.verify_sources(result, tmp_path)
    assert [r["status"] for r in records] == [
        "unavailable", "unreadable", "no_url"]
    assert pages == {}
    metrics = run_eval.source_metrics(records)
    assert metrics == {"sources_unreachable": 2}   # no excerpt was checkable
    warns = run_eval.source_warnings(records)
    assert has(warns, "unavailable: S1") and has(warns, "no readable text: S2")


def test_judge_sees_fetched_page_instead_of_backend_text():
    result = make_result()
    result["sources"][0]["article_text"] = "BACKEND TEXT"
    page_text = "REAL PAGE " + "x" * 50
    pages = {"https://example.com/news": {"text": page_text, "offset": 0}}
    judge = ScriptedJudge([
        '{"supported": false, "goal_relevance": 4}',
        '{"supported": true, "unsupported_claims": []}',
        '{"score": 4}',
        '{"duplicates": [], "overall_score": 3}',
    ])
    scores = run_eval.judge_case(judge, make_case(), result, pages)
    assert "REAL PAGE" in judge.prompts[0]
    assert "BACKEND TEXT" not in judge.prompts[0]
    assert scores["events"][0]["evidence"] == "page"

    judge = ScriptedJudge(judge.replies or [
        '{"supported": true}', '{"supported": true}', '{"score": 4}',
        '{"duplicates": [], "overall_score": 3}'])
    scores = run_eval.judge_case(judge, make_case(), result)
    assert "BACKEND TEXT" in judge.prompts[0]
    assert scores["events"][0]["evidence"] == "backend"


def test_evidence_window_keeps_the_excerpt():
    text = "a" * 1000 + "EXCERPT" + "b" * 1000
    window = run_eval.evidence_window(text, 1000, 400)
    assert len(window) == 400 and "EXCERPT" in window
    assert run_eval.evidence_window("short", None, 400) == "short"
    assert run_eval.evidence_window(text, None, 10) == "a" * 10


def test_backend_probe_times_steps_and_collects_usage(monkeypatch):
    backend = types.ModuleType("fake_backend")
    exec(
        "def search_sources(request):\n    return {}\n"
        "def generate_text(messages):\n    return 'text'\n"
        "def generate_briefing(request):\n"
        "    search_sources(request)\n"
        "    generate_text([])\n"
        "    return {}\n",
        backend.__dict__)
    client = types.ModuleType("anthropic_client")
    pending = [[{"model": "claude-sonnet-5-5", "input_tokens": 10,
                 "output_tokens": 5, "seconds": 9.9}]]
    client.pop_usage = lambda: pending.pop() if pending else []
    monkeypatch.setitem(sys.modules, "anthropic_client", client)

    probe = run_eval.BackendProbe(backend)
    backend.generate_briefing({})
    calls = probe.collect()
    assert [c["stage"] for c in calls] == ["tavily", "generation"]
    assert calls[1]["input_tokens"] == 10
    assert calls[1]["model"] == "claude-sonnet-5-5"
    assert calls[1]["seconds"] != 9.9, "timed call keeps the probe's seconds"
    assert probe.collect() == []


def _report_entry():
    return {
        "id": "unit", "run": 1, "seconds": 3.0, "error": None,
        "failures": [], "warnings": ["researched_at not provided"],
        "judge": {
            "facts": [{"fact": "A", "kind": "recent", "importance": 1.0,
                       "verdict": "covered", "reason": "stated"}],
            "events": [{"title": "Launch", "date": "2026-11-01",
                        "supported": True, "date_ok": True,
                        "about_entity": True, "in_window": True}],
            "summary": {"supported": True, "unsupported_claims": []},
            "goal": {"score": 4, "reason": "ok"},
            "expected_gaps": [{"gap": "No pricing.", "verdict": "covered"}],
            "gap_quality": [{"n": 1, "gap": "No pricing found.",
                             "specific": True, "is_limitation": True}],
            "review": {"duplicates": [], "overall_score": 4},
        },
        "scores": {"recall_all": 1.0, "goal_score": 4, "overall_score": 4},
        "usage": {
            "backend": [
                {"stage": "tavily", "seconds": 1.0},
                {"stage": "generation", "model": "claude-sonnet-5-5",
                 "input_tokens": 1000, "output_tokens": 100, "seconds": 2.0},
            ],
            "judge": [{"stage": "judge:facts", "model": "claude-opus-5-5",
                       "input_tokens": 500, "output_tokens": 50,
                       "seconds": 4.0}],
        },
    }


def test_entry_usage_splits_steps():
    usage = run_eval.entry_usage(_report_entry())
    assert usage["tavily_s"] == 1.0 and usage["tavily_searches"] == 1
    assert usage["llm_s"] == 2.0 and usage["judge_s"] == 4.0
    assert usage["gen_cost_usd"] == pytest.approx(0.003)
    assert usage["judge_cost_usd"] == pytest.approx(0.003)


def test_describe_good_run_is_ok():
    d = run_eval.describe_entry(_report_entry())
    assert d["verdict"] == "OK" and d["problems"] == []
    assert d["text"].startswith("OK\nWhat is fine:")
    assert "all 1 events supported by their sources" in d["text"]
    assert "overall 4/5" in d["text"]
    assert "1 warning(s), see the Checks sheet" in d["text"]


def test_describe_lists_what_is_broken():
    entry = _report_entry()
    judged = entry["judge"]
    judged["events"][0].update(supported=False, goal_relevance=1,
                               reason="source says March")
    judged["facts"][0]["verdict"] = "not_covered"
    judged["summary"] = {"supported": False, "unsupported_claims": ["CEO quit"]}
    judged["review"]["overall_score"] = 2
    entry["sources"] = [{"source_id": "S1", "url": "https://x.io",
                         "status": "ok", "excerpt_match": 0.1,
                         "excerpt_found": False, "error": None}]
    d = run_eval.describe_entry(entry)
    assert d["verdict"] == "ISSUES"
    labels = [label for label, _ in d["problems"]]
    assert labels == [
        "event not supported by its sources",
        "event not relevant to the goal",
        "summary claim not supported by sources",
        "missed an important expected fact",
        "low overall score",
        "excerpt not found on the source page",
    ]
    assert "'Launch': source says March" in d["text"]
    assert "What is fine:" in d["text"]       # dates etc. are still fine


def test_describe_failed_and_unjudged_runs():
    d = run_eval.describe_entry({"id": "x", "run": 1, "error": "Traceback\n"
                                 "RuntimeError: Tavily down",
                                 "failures": ["backend raised an exception"],
                                 "warnings": []})
    assert d["verdict"] == "FAIL"
    assert d["problems"] == [("backend error", "RuntimeError: Tavily down")]
    d = run_eval.describe_entry({"id": "x", "run": 1, "failures": [],
                                 "warnings": []})
    assert d["verdict"] == "OK" and "not judged" in d["text"]


def test_run_overview_counts_common_problems():
    good, bad = _report_entry(), _report_entry()
    bad["run"] = 2
    bad["judge"]["review"]["overall_score"] = 1
    failed = {"id": "other", "run": 1, "failures": ["is_demo is True"],
              "warnings": []}
    text = run_eval.run_overview([good, bad, failed])
    assert text.startswith("1 OK, 1 with issues, 1 failed, out of 3 runs.")
    assert "low overall score (1: unit#2)" in text
    assert "check failed (1: other#1)" in text


def test_model_warnings_flag_fallbacks():
    entry = {"usage": {
        "backend": [{"stage": "tavily"},
                    {"stage": "generation", "model": "claude-sonnet-5-5"},
                    {"stage": "generation", "model": "claude-haiku-5-5"}],
        "judge": [{"stage": "judge:event", "model": "claude-opus-5-5"}]}}
    warns = run_eval.model_warnings(entry, "claude-sonnet-5-5",
                                    "claude-opus-5-5")
    assert warns == ["model mismatch: agent ran on claude-haiku-5-5, "
                     "expected claude-sonnet-5-5"]
    assert run_eval.model_warnings(entry, None, None) == []
    entry["warnings"] = warns
    entry["failures"] = []
    d = run_eval.describe_entry(entry)
    assert d["verdict"] == "ISSUES" and d["problems"][0][0] == "model mismatch"


def test_default_models():
    assert run_eval.AGENT_MODEL == "claude-sonnet-5-5"
    assert run_eval.JUDGE_MODEL == "claude-opus-5-5"


def test_stability_rows_and_unstable_scores():
    runs = []
    for n, (overall, ground) in enumerate([(4, 1.0), (2, 0.5), (4, 0.9)], 1):
        entry = _report_entry()
        entry["run"] = n
        entry["scores"] = {"overall_score": overall, "groundedness": ground}
        runs.append(entry)
    rows = {r[1]: r for r in run_eval.stability_rows(runs)}
    assert rows["verdict"][2] == 3
    assert rows["overall_score"][2:7] == [3, 3.33, 2.0, 4.0, 2.0]
    assert rows["groundedness"][6] == 0.5
    unstable = run_eval.unstable_scores(runs)
    assert unstable == ["unit groundedness 0.5..1.0",
                        "unit overall_score 2.0..4.0"]
    assert run_eval.unstable_scores(runs[:1]) == []


def test_unit_summary_and_overview():
    unit = {"seconds": 2.0, "totals": {"passed": 5, "failed": 1, "error": 0,
                                       "skipped": 2, "xfailed": 3},
            "rows": [{"test": "test_x", "outcome": "failed"}]}
    text = run_eval.run_overview_text([_report_entry()], unit)
    assert text.startswith("Unit tests: 5 passed, 1 failed, 0 errors, "
                           "3 known gaps (xfail), 2 skipped. Failing: test_x.")
    assert "Cases: 1 OK" in text


def test_history_row_waits_while_file_is_open(tmp_path, monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    history = tmp_path / "history.xlsx"
    real_write = run_eval._write_history

    def locked(path, rows):
        raise PermissionError("open in Excel")
    monkeypatch.setattr(run_eval, "_write_history", locked)
    with pytest.raises(PermissionError):
        run_eval.append_history(history, {"run_id": "a"})
    pending = run_eval.pending_history_file(history)
    assert json.loads(pending.read_text(encoding="utf-8")) == [{"run_id": "a"}]

    monkeypatch.setattr(run_eval, "_write_history", real_write)
    run_eval.append_history(history, {"run_id": "b"})
    ws = openpyxl.load_workbook(history).active
    assert [r[0].value for r in ws.iter_rows()] == ["run_id", "a", "b"]
    assert not pending.exists()


def _timed_entry(run, seconds, tavily, llm, judge):
    backend = [{"stage": "tavily", "seconds": tavily}]
    if llm:
        backend.append({"stage": "generation", "seconds": llm})
    return {"id": "t", "run": run, "seconds": seconds, "failures": [],
            "warnings": [], "usage": {
                "backend": backend,
                "judge": [{"stage": "judge:facts", "seconds": judge}] if judge else []}}


def test_mean_times_skip_runs_where_the_step_did_not_run():
    report = [
        _timed_entry(1, 40.0, 5.0, 35.0, 60.0),
        _timed_entry(2, 20.0, 5.0, 15.0, 40.0),
        _timed_entry(3, 4.0, 4.0, 0, 0),      # no sources: no generation, no judge
        _timed_entry(4, 0.0, 0.0, 0, 0),      # nothing to search
    ]
    assert run_eval.mean_times(report) == {
        "backend_s": 21.3, "tavily_s": 4.7, "llm_s": 25.0,
        "fetch_s": None, "judge_s": 50.0}


def test_summary_total_row_shows_mean_time_and_total_cost(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    report = [_timed_entry(1, 40.0, 5.0, 35.0, 60.0),
              _timed_entry(2, 0.0, 0.0, 0, 0)]
    args = types.SimpleNamespace(from_cache=None, backend="fake",
                                 dataset="ds.json")
    info = run_eval.build_run_info(args, None, report,
                                   run_eval.datetime.now().astimezone(), 50.0,
                                   tmp_path)
    assert info["backend_s"] == 40.0 and info["mean_backend_s"] == 40.0
    path = tmp_path / "report.xlsx"
    run_eval.write_xlsx(path, info, report)
    summary = openpyxl.load_workbook(path)["Summary"]
    header = [c.value for c in summary[1]]
    total = {h: c.value for h, c in zip(header, summary[summary.max_row])}
    assert total["id"] == "TOTAL / MEAN"
    assert total["backend_s"] == 40.0     # mean over the run that did work
    assert total["tavily_searches"] == 2  # counts stay totals
    run_sheet = dict(openpyxl.load_workbook(path)["Run"].iter_rows(values_only=True))
    assert run_sheet["mean_judge_s"] == 60.0


def test_write_xlsx_and_history(tmp_path, monkeypatch):
    openpyxl = pytest.importorskip("openpyxl")
    report = [_report_entry()]
    args = types.SimpleNamespace(from_cache=None, backend="fake",
                                 dataset="ds.json")
    info = run_eval.build_run_info(args, None, report,
                                   run_eval.datetime.now().astimezone(), 7.5,
                                   tmp_path)
    path = tmp_path / "report.xlsx"
    run_eval.write_xlsx(path, info, report)

    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Summary", "Criteria", "Facts", "Events",
                             "Sources", "Gaps", "Review", "Checks", "Usage",
                             "Stability", "Run"]
    summary = wb["Summary"]
    header = [c.value for c in summary[1]]
    assert header[:5] == ["id", "run", "status", "verdict", "comments"]
    assert "overall_score" in header
    assert summary.cell(row=2, column=4).value == "OK"
    assert summary.cell(row=2, column=5).value.startswith("OK\nWhat is fine:")
    assert summary.cell(row=3, column=5).value.startswith("Cases: 1 OK, 0 with")
    assert summary.cell(row=2, column=1).value == "unit"
    assert summary.cell(row=3, column=1).value == "TOTAL / MEAN"
    assert wb["Usage"].max_row == 4        # header + tavily + generation + judge
    assert wb["Criteria"].max_row == len(run_eval.CRITERIA) + 1

    history = tmp_path / "history.xlsx"
    monkeypatch.setattr(run_eval, "HISTORY_FILE", history)
    run_eval.append_history(history, {"run_id": "a", "wall_s": 1})
    run_eval.append_history(history, {"run_id": "b", "wall_s": 2,
                                       "new_column": 3})
    ws = openpyxl.load_workbook(history).active
    assert [c.value for c in ws[1]] == ["run_id", "wall_s", "new_column"]
    assert [c.value for c in ws[3]] == ["b", 2, 3]


# ===========================================================================
# Part 2. Backend with fake Tavily and Bifrost
# ===========================================================================

def _import_backend():
    if not (ROOT / "briefing_generator.py").exists():
        pytest.skip("briefing_generator.py is not in the repository root")
    # Fake the external modules so importing needs no packages or keys.
    for name, attr in (("tavily_search", "search_sources"),
                       ("bifrost_client", "generate_text")):
        if name not in sys.modules:
            fake = types.ModuleType(name)
            setattr(fake, attr, lambda *a, **k: None)
            sys.modules[name] = fake
    import briefing_generator
    return briefing_generator


RESEARCHED_AT = "2026-11-08T08:00:00+00:00"


def tavily_response(sources=None, gaps=None):
    if sources is None:
        sources = [{
            "id": "S1",
            "title": "Example Co launches product",
            "url": "https://example.com/news",
            "excerpt": "Example Co launched a product on November 1.",
            "published_date": "2026-11-01T10:00:00+01:00",
            "published_date_note": "Tavily date estimate.",
            "article_text": "Example Co launched a product on November 1. "
                            * 100,
        }]
    return {
        "sources": sources,
        "gaps": list(gaps or []),
        "research_performed_at": RESEARCHED_AT,
        "research_window": {"start_date": "2026-10-10",
                            "end_date": "2026-11-09"},
    }


def model_answer(**overrides):
    answer = {
        "summary": "Example Co launched a product.",
        "events": [{
            "title": "Product launch",
            "date": "2026-11-01",
            "description": "Example Co launched a product.",
            "source_ids": ["S1"],
            "discussion_point": "Ask about target customers.",
        }],
        "questions": ["Which customers is it for?"],
        "conversation_starters": ["I saw the launch news."],
        "gaps": [],
    }
    answer.update(overrides)
    return json.dumps(answer)


@pytest.fixture
def backend(monkeypatch):
    """Backend module with controllable fakes.

    Set backend.research / backend.reply (a string, or an exception to raise)
    before calling backend.run().
    """
    bg = _import_backend()
    calls = {"tavily": [], "model": []}

    class Ctl:
        research = tavily_response()
        reply = model_answer()

        @staticmethod
        def run(request=None):
            return bg.generate_briefing(request or make_case()["request"])

    def fake_search(request):
        calls["tavily"].append(request)
        if isinstance(Ctl.research, Exception):
            raise Ctl.research
        return copy.deepcopy(Ctl.research)

    def fake_generate(messages):
        calls["model"].append(messages)
        if isinstance(Ctl.reply, Exception):
            raise Ctl.reply
        return Ctl.reply

    monkeypatch.setattr(bg, "search_sources", fake_search)
    monkeypatch.setattr(bg, "generate_text", fake_generate)
    Ctl.calls = calls
    return Ctl


def test_backend_happy_path_matches_contract(backend):
    result = backend.run()
    run_eval.validate_result(result)
    assert result["is_demo"] is False
    assert result["researched_at"] == RESEARCHED_AT
    assert [s["id"] for s in result["sources"]] == ["S1"]
    assert failures(result) == []


def test_backend_passes_meeting_goal_to_model(backend):
    backend.run()
    prompt = backend.calls["model"][0][-1]["content"]
    assert "Discuss a partnership" in prompt
    assert MEETING in prompt


def test_backend_truncates_article_text_in_prompt(backend):
    backend.run()
    prompt = backend.calls["model"][0][-1]["content"]
    full_text = backend.research["sources"][0]["article_text"]
    assert full_text not in prompt


def test_backend_keeps_tavily_gaps(backend):
    backend.research = tavily_response(gaps=["Paywalled source skipped."])
    backend.reply = model_answer(gaps=["No financial data found."])
    gaps = backend.run()["gaps"]
    assert "Paywalled source skipped." in gaps
    assert "No financial data found." in gaps


@pytest.mark.xfail(strict=True, reason=(
    "Backend appends Tavily gaps to model gaps without removing repeats "
    "(result['gaps'].extend(...)). When fixed, remove the xfail marker."))
def test_backend_does_not_repeat_gaps(backend):
    same = "Some search results had no detectable publication date."
    backend.research = tavily_response(gaps=[same])
    backend.reply = model_answer(gaps=[same])
    result = backend.run()
    assert not has(warnings(result), "duplicate gap")


@pytest.mark.xfail(strict=True, reason=(
    "Backend passes blank gap strings from the model through unchanged. "
    "When fixed, remove the xfail marker."))
def test_backend_drops_blank_gaps_from_model(backend):
    backend.reply = model_answer(gaps=["", "No pricing information found."])
    result = backend.run()
    assert not has(failures(result), "empty string in gaps")


def test_backend_accepts_json_in_code_fence(backend):
    backend.reply = "```json\n" + model_answer() + "\n```"
    assert backend.run()["events"]


def test_backend_accepts_text_around_json(backend):
    backend.reply = "Here is the briefing:\n" + model_answer() + "\nDone."
    assert backend.run()["events"]


def test_backend_no_sources_returns_honest_empty_answer(backend):
    backend.research = tavily_response(sources=[],
                                       gaps=["Nothing found."])
    result = backend.run()
    run_eval.validate_result(result)
    assert result["events"] == []
    assert result["gaps"]
    assert result["researched_at"] == RESEARCHED_AT
    assert backend.calls["model"] == [], "model must not be asked to invent"


@pytest.mark.parametrize("reply, why", [
    (model_answer(events=[{
        "title": "T", "date": "2026-11-01", "description": "D",
        "source_ids": ["S9"], "discussion_point": "P"}]),
     "unknown source id"),
    (model_answer(events=[{
        "title": "T", "date": "2026-11-01", "description": "D",
        "source_ids": [], "discussion_point": "P"}]),
     "event without sources"),
    (model_answer(events=[{
        "title": "T", "date": "1 Nov 2026", "description": "D",
        "source_ids": ["S1"], "discussion_point": "P"}]),
     "bad date format"),
    (json.dumps({"summary": "only a summary"}), "missing fields"),
    (json.dumps(["not", "an", "object"]), "not an object"),
    ("I could not find anything.", "no JSON at all"),
])
def test_backend_rejects_bad_model_output(backend, reply, why):
    backend.reply = reply
    with pytest.raises(Exception):
        backend.run()


def test_backend_raises_when_tavily_fails(backend):
    backend.research = RuntimeError("Tavily is down")
    with pytest.raises(RuntimeError):
        backend.run()


def test_backend_raises_when_model_fails(backend):
    backend.reply = TimeoutError("Bifrost timeout")
    with pytest.raises(TimeoutError):
        backend.run()


@pytest.mark.xfail(strict=True, reason=(
    "Backend does not yet drop events dated outside the 30-day window. "
    "When this starts passing, remove the xfail marker."))
def test_backend_drops_events_outside_window(backend):
    backend.reply = model_answer(events=[{
        "title": "Old news", "date": "2026-05-28",
        "description": "Happened long ago.", "source_ids": ["S1"],
        "discussion_point": "P"}])
    result = backend.run()
    assert all(e["date"] is None or e["date"] >= "2026-10-10"
               for e in result["events"])
