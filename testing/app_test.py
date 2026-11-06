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
import sys
import types
from pathlib import Path

import pytest

TESTING = Path(__file__).resolve().parent
ROOT = TESTING.parent
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


def test_recent_facts_are_inside_window():
    """A fact marked 'recent' with a date must fall in the research window."""
    for case in run_eval.load_dataset(TESTING / "dataset.json"):
        start, end = run_eval.research_window(case["request"])
        for fact in case["expected_facts"]:
            if fact["kind"] == "recent" and fact.get("date"):
                assert start.isoformat() <= fact["date"] <= end.isoformat(), (
                    f"{case['id']}: '{fact['fact']}' dated {fact['date']} "
                    f"is marked recent but the window is {start}..{end}"
                )


def test_model_output_example_matches_contract():
    data = json.loads((TESTING / "model_output_example.json")
                      .read_text(encoding="utf-8"))
    run_eval.validate_result(data)


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
